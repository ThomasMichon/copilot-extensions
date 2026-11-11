"""Preserved cache originals and current, live-source-only recovery."""

from __future__ import annotations

import base64
import builtins
import json
import os
from pathlib import Path
import sys
import time

import pytest

from agent_vault import cache_recovery, cli, identity_key
from agent_vault.cache import PersistentCache
from agent_vault.cache_dpapi import CacheProtectionError

pytest.importorskip("Cryptodome")


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith("AGENT_VAULT") or name in {"KPDB", "VAULT_GROUP"}:
            monkeypatch.delenv(name)
    database = tmp_path / "example.kdbx"
    database.write_bytes(b"fixture database; never unlocked")
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setenv("KPDB", str(database))
    monkeypatch.setenv("AGENT_VAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(root))
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.chdir(tmp_path)
    return root


def corrupt(root):
    cache = PersistentCache(root)
    assert cache.put("Example/old", "password", "retained original")
    cache._cache_file.write_bytes(b"unreadable original cache")
    return {name: (root / name).read_bytes() for name in cache_recovery.FILES if (root / name).exists()}


@pytest.mark.parametrize("kind", ["ciphertext", "schema"])
@pytest.mark.parametrize("raw_key", [False, True])
def test_invalid_fernet_never_becomes_an_empty_write(isolated, kind, raw_key):
    from agent_vault import kek

    cache = PersistentCache(isolated)
    assert cache.put("Example/old", "password", "preserve")
    if kind == "ciphertext":
        cache._cache_file.write_bytes(b"invalid Fernet")
    else:
        cache._cache_file.write_bytes(cache._get_fernet().encrypt(b'{"v":99,"entries":{}}'))
    if raw_key:
        cache._key_file.write_bytes(kek.unwrap_bytes(cache._key_file.read_bytes()))
    before = {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}
    assert not cache.put("Example/new", "password", "must not overwrite")
    with pytest.raises(CacheProtectionError):
        cache.get("Example/old")
    assert before == {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}


def test_real_helper_preserves_pair_and_reopens_current_cache(isolated):
    before = corrupt(isolated)
    cache = PersistentCache(isolated)
    result = cache.prepare()
    assert result["state"] == "recovering"
    assert result["original"]["state"] == "copied"
    compartment = cache._base_dir.parent
    snapshot = compartment / result["original"]["snapshot"]
    assert {name: (snapshot / name).read_bytes() for name in before} == before
    assert {name: (isolated / name).read_bytes() for name in before} == before
    assert cache.get("Example/old", readonly=True) is None
    assert cache.recover_missing("Example/current", "password", "live", 3)
    reopened = PersistentCache(isolated)
    assert reopened.get("Example/current", readonly=True) == "live"
    assert reopened.prepare() == result
    if os.name == "nt":
        windows = cache_recovery.WindowsSecurity()
        for directory in (compartment.parent, compartment, snapshot, cache._base_dir):
            windows.require(directory, directory=True)
        windows.require(compartment / "recovery.json", directory=False)
    else:
        assert snapshot.stat().st_mode & 0o777 == 0o700
        assert all((snapshot / name).stat().st_mode & 0o777 == 0o600 for name in before)


def test_compatible_cache_keeps_original_values_journals_and_unknown_fields(isolated):
    cache = PersistentCache(isolated)
    store = {
        "v": 1, "extension": {"keep": True},
        "entries": {
            "Example/value": {"password": {"value": "current", "generation": 9, "future": 1}},
            "Example/pending": {"password": {"rotation": {
                "candidate": "candidate", "operation": 123, "previous": {"value": "previous"},
            }}},
        },
    }
    assert cache._write_store(store)
    before = {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}
    assert cache.prepare() == {"state": "ready"}
    assert cache._read_store(readonly=True, strict=True) == store
    assert cache._base_dir == isolated
    assert before == {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}


def test_compatible_assessment_does_not_make_offline_reads_require_vault_selection(
    isolated, monkeypatch,
):
    cache = PersistentCache(isolated)
    assert cache.put("Example/current", "password", "offline")
    assert cache.prepare() == {"state": "ready"}
    monkeypatch.delenv("KPDB")
    assert PersistentCache(isolated).get("Example/current", readonly=True) == "offline"


def test_healthy_cache_population_retains_legacy_unconfigured_client_support(isolated, monkeypatch):
    cache = PersistentCache(isolated)
    assert cache.put("Example/current", "password", "offline")
    before = cache._cache_file.read_bytes()
    monkeypatch.delenv("KPDB")
    assert cache.prepare() == {"state": "ready"}
    assert cache.get("Example/current", readonly=True) == "offline"
    assert cache._cache_file.read_bytes() == before
    assert not (isolated / cache_recovery.DIRECTORY).exists()


def test_recovery_never_overwrites_current_or_pending_records(isolated):
    corrupt(isolated)
    cache = PersistentCache(isolated)
    cache.prepare()
    assert cache.put("Example/current", "password", "newer", 50)
    assert cache.begin_replace("Example/pending", "password", "candidate", 51)
    before = cache._cache_file.read_bytes()
    assert cache.recover_missing("Example/current", "password", "stale", 1)
    assert not cache.recover_missing("Example/pending", "password", "stale", 1)
    assert cache._cache_file.read_bytes() == before
    assert cache.get("Example/current") == "newer"
    assert cache.pending_replace("Example/pending", "password")["candidate"] == "candidate"


@pytest.mark.parametrize(("value", "generation"), [(42, 1), ("live", True), ("live", -1)])
def test_recovery_rejects_malformed_live_values_without_mutation(isolated, value, generation):
    corrupt(isolated)
    cache = PersistentCache(isolated)
    cache.prepare()
    before = cache._cache_file.read_bytes()
    assert not cache.recover_missing("Example/current", "password", value, generation)
    assert cache._cache_file.read_bytes() == before


def test_interrupted_publication_reuses_existing_current_bytes(isolated, monkeypatch):
    corrupt(isolated)
    original_write = cache_recovery._write

    def interrupted(directory, target, value, windows):
        if target.name == "recovery.json":
            raise OSError("simulated publication interruption")
        return original_write(directory, target, value, windows)

    monkeypatch.setattr(cache_recovery, "_write", interrupted)
    with pytest.raises(OSError):
        PersistentCache(isolated).prepare()
    compartment = isolated / cache_recovery.DIRECTORY / cache_recovery.source_key(isolated)
    current = PersistentCache(compartment / "current")
    assert current.put("Example/current", "password", "newer", 20)
    before = current._cache_file.read_bytes()
    monkeypatch.setattr(cache_recovery, "_write", original_write)
    resumed = PersistentCache(isolated)
    assert resumed.prepare()["state"] == "recovering"
    assert resumed._cache_file.read_bytes() == before
    assert resumed.get("Example/current") == "newer"


def test_context_change_during_recovery_preserves_current_cache(isolated, monkeypatch):
    corrupt(isolated)
    cache = PersistentCache(isolated)
    cache.prepare()
    before = cache._cache_file.read_bytes()
    monkeypatch.setenv("KPDB", str(isolated.parent / "other.kdbx"))
    assert not cache.recover_missing("Example/current", "password", "wrong-source")
    assert cache._cache_file.read_bytes() == before


def test_actual_replacement_lock_is_finite_and_retained_in_place(isolated):
    before = corrupt(isolated)
    cache = PersistentCache(isolated)
    started = time.monotonic()
    with cache.replacement_lock():
        result = PersistentCache(isolated).prepare()
    assert time.monotonic() - started < 15
    assert result["state"] == "recovering"
    assert result["original"]["state"] == "preserved-in-place"
    assert result["original"]["reason"] == "legacy-lock-busy"
    assert {name: (isolated / name).read_bytes() for name in before} == before


@pytest.mark.skipif(os.name != "nt", reason="actual Windows deny-sharing handle")
def test_exclusively_open_original_is_retained_and_fresh_state_is_usable(isolated):
    import ctypes

    before = corrupt(isolated)
    windows = cache_recovery.WindowsSecurity()
    handle = windows.kernel.CreateFileW(
        str(isolated / "credential-cache.enc"), 0x80000000, 0, None, 3, 0x80, None,
    )
    assert handle != ctypes.c_void_p(-1).value
    try:
        cache = PersistentCache(isolated)
        result = cache.prepare()
        assert result["original"]["state"] == "preserved-in-place"
        assert cache.recover_missing("Example/current", "password", "live", 2)
        assert PersistentCache(isolated).get("Example/current", readonly=True) == "live"
    finally:
        windows.kernel.CloseHandle(handle)
    assert {name: (isolated / name).read_bytes() for name in before} == before


def test_real_finite_owner_settles_blocked_decoder(isolated, monkeypatch):
    from agent_procutil.owned_process import run_caller_owned

    before = corrupt(isolated)
    calls = []

    def blocked(argv, **kwargs):
        calls.append(argv)
        kwargs["timeout"] = 0.3
        kwargs.pop("input_text")
        return run_caller_owned(
            [sys.executable, "-c", "import time; time.sleep(60)"], **kwargs,
        )

    monkeypatch.setattr("agent_procutil.owned_process.run_caller_owned", blocked)
    result = PersistentCache(isolated).prepare()
    assert len(calls) == 1
    assert result["original"] == {
        "state": "preserved-in-place", "reason": "legacy-read-timeout",
    }
    assert {name: (isolated / name).read_bytes() for name in before} == before


def test_unknown_cleanup_is_not_a_fresh_start(isolated, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError

    corrupt(isolated)

    def unknown(*_args, **_kwargs):
        raise OwnedCleanupError(None)

    monkeypatch.setattr("agent_procutil.owned_process.run_caller_owned", unknown)
    with pytest.raises(OwnedCleanupError):
        PersistentCache(isolated).prepare()
    assert not list(isolated.glob(f"{cache_recovery.DIRECTORY}/*/recovery.json"))


def test_malformed_selection_is_not_ignored(isolated):
    corrupt(isolated)
    cache = PersistentCache(isolated)
    cache.prepare()
    marker = cache._base_dir.parent / "recovery.json"
    marker.write_text('{"version":99}', encoding="utf-8")
    with pytest.raises(CacheProtectionError, match="selection"):
        PersistentCache(isolated)


def test_linked_selection_is_not_followed(isolated):
    corrupt(isolated)
    cache = PersistentCache(isolated)
    cache.prepare()
    marker = cache._base_dir.parent / "recovery.json"
    original = marker.read_bytes()
    os.link(marker, marker.with_suffix(".retained"))
    with pytest.raises(PermissionError, match="link"):
        PersistentCache(isolated)
    assert marker.read_bytes() == original


def test_current_selection_survives_a_real_cli_restart(isolated):
    from agent_procutil.owned_process import run_caller_owned

    corrupt(isolated)
    cache = PersistentCache(isolated)
    cache.prepare()
    assert cache.recover_missing("Example/current", "password", "private fixture value", 4)
    result = run_caller_owned(
        [sys.executable, "-m", "agent_vault", "cache-status", "--json"],
        timeout=15, max_stdout_bytes=8192, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["field_count"] == 1
    assert status["recovery"]["original"]["state"] == "copied"
    assert "private fixture value" not in result.stdout + result.stderr


def test_cache_only_hit_never_rewraps_a_legacy_key(isolated, capsys):
    from agent_vault import kek

    cache = PersistentCache(isolated)
    assert cache.put("Example/current", "password", "cached")
    key = kek.unwrap_bytes(cache._key_file.read_bytes())
    cache._key_file.write_bytes(key)
    assert cli.main(["get", "Example/current", "--cache-only"]) == 0
    assert capsys.readouterr().out == "cached\n"
    assert cache._key_file.read_bytes() == key
    assert not (isolated / cache_recovery.DIRECTORY).exists()


def test_failed_key_migration_keeps_legacy_bytes_and_usable_value(isolated, monkeypatch):
    from agent_vault import cache as cache_module, kek

    cache = PersistentCache(isolated)
    assert cache.put("Example/current", "password", "cached")
    key = kek.unwrap_bytes(cache._key_file.read_bytes())
    cache._key_file.write_bytes(key)
    original_replace = cache_module.os.replace

    def fail_key(source, target):
        if Path(target) == cache._key_file:
            raise OSError("fixture key publication failure")
        return original_replace(source, target)

    monkeypatch.setattr(cache_module.os, "replace", fail_key)
    assert cache.get("Example/current") == "cached"
    assert cache._key_file.read_bytes() == key
    assert not list(isolated.glob(".cache-key-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="actual Windows DPAPI fresh-cache fallback")
def test_fresh_recovery_uses_dpapi_when_optional_backend_is_unavailable(isolated, monkeypatch):
    from agent_vault.cache_dpapi import MAGIC

    before = corrupt(isolated)
    original_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("Cryptodome") or name == "protected_crypto":
            raise ImportError("fixture optional-backend absence")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    cache = PersistentCache(isolated)
    assert cache.prepare()["state"] == "recovering"
    assert cache._cache_file.read_bytes().startswith(MAGIC)
    assert not cache._key_file.exists()
    assert cache.recover_missing("Example/current", "password", "live", 3)
    assert PersistentCache(isolated).get("Example/current", readonly=True) == "live"
    assert {name: (isolated / name).read_bytes() for name in before} == before


def test_cache_only_and_status_do_not_start_recovery(isolated, monkeypatch, capsys):
    before = corrupt(isolated)
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("read-only service activation"))
    with pytest.raises(CacheProtectionError):
        cli.main(["get", "Example/old", "--cache-only"])
    assert cli.main(["cache-status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["readable"] is False
    assert status["recovery"] is None
    assert not (isolated / cache_recovery.DIRECTORY).exists()
    assert {name: (isolated / name).read_bytes() for name in before} == before


def test_population_uses_live_owner_and_preserves_a_racing_current_write(
    isolated, monkeypatch, capsys,
):
    before = corrupt(isolated)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    requests = []

    def reply(request, timeout=None, **kwargs):
        assert kwargs == {"strict_endpoint": True}
        requests.append(request)
        assert PersistentCache(isolated).put("Example/live", "password", "racing newer", 100)
        return {"ok": True, "value": "stale response", "generation": 2}

    monkeypatch.setattr(cli, "send_command", reply)
    assert cli.main(["cache-populate", "--entry", "Example/live"]) == 0
    assert requests == [{"action": "get", "entry": "Example/live", "field": "password", "refresh": True}]
    assert PersistentCache(isolated).get("Example/live") == "racing newer"
    assert {name: (isolated / name).read_bytes() for name in before} == before
    captured = capsys.readouterr()
    assert "recovering" in captured.err
    assert "stale response" not in captured.out + captured.err


def test_owner_setup_locked_wait_has_no_recovery_or_creation(isolated, monkeypatch, capsys):
    metadata = identity_key.source_context()
    root = Path(metadata["env"]["AGENT_VAULT_CACHE_DIR"])
    root.mkdir(parents=True)
    before = corrupt(root)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    requests = []

    def locked(request, **_kwargs):
        requests.append(request)
        return {"ok": False, "code": "needs_unlock"}

    monkeypatch.setattr(cli, "send_command", locked)
    assert cli.main(["identity-key", "setup", "--entry", "Identity/example"]) == 1
    assert len(requests) == 1
    assert requests[0]["initialize_owner"] is False
    assert json.loads(capsys.readouterr().out)["error"] == "needs_unlock"
    assert not (root / cache_recovery.DIRECTORY).exists()
    assert {name: (root / name).read_bytes() for name in before} == before


def test_owner_setup_recovers_existing_key_without_rotation(isolated, monkeypatch, capsys):
    metadata = identity_key.source_context()
    root = Path(metadata["env"]["AGENT_VAULT_CACHE_DIR"])
    root.mkdir(parents=True)
    before = corrupt(root)
    key = base64.b64encode(bytes(range(32))).decode()
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    requests = []

    def existing(request, **_kwargs):
        requests.append(request)
        return {"ok": True, "status": "reused", "value": key, "generation": 5}

    monkeypatch.setattr(cli, "send_command", existing)
    assert cli.main(["identity-key", "setup", "--entry", "Identity/example"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"version": 1, "status": "reused", "cached": True, "cache_recovery": "required"}
    assert len(requests) == 1 and requests[0]["initialize_owner"] is False
    assert PersistentCache(root).get("Identity/example", readonly=True) == key
    assert {name: (root / name).read_bytes() for name in before} == before
