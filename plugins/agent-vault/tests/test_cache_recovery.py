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


def test_population_during_a_legacy_transaction_reports_a_bounded_failure(
    isolated, monkeypatch, capsys,
):
    cache = PersistentCache(isolated)
    assert cache.put("Example/rotating", "password", "previous")
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *_a, **_kw: pytest.fail("uncoordinated RPC"))
    with cache.replacement_lock():
        assert cache.begin_replace("Example/rotating", "password", "candidate", 2)
        assert cli.main(["cache-populate", "--entry", "Example/rotating"]) == 1
    output = capsys.readouterr()
    assert "cache update failed" in output.err
    assert "previous" not in output.out + output.err
    assert "candidate" not in output.out + output.err


def test_recovered_readonly_read_never_recreates_a_missing_coordination_lock(isolated):
    corrupt(isolated)
    cache = PersistentCache(isolated)
    cache.prepare()
    assert cache.recover_missing("Example/current", "password", "live", 1)
    cache._replacement_lock_file.unlink()
    with pytest.raises(OSError):
        cache.get("Example/current", readonly=True)
    assert not cache._replacement_lock_file.exists()


def test_reentrant_replacement_guard_still_bounds_another_thread(isolated):
    import threading

    cache = PersistentCache(isolated)
    failures = []

    def blocked():
        try:
            with cache.replacement_lock():
                failures.append("unexpected acquisition")
        except TimeoutError:
            failures.append("busy")

    with cache.replacement_lock(), cache.replacement_lock():
        worker = threading.Thread(target=blocked)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert failures == ["busy"]


@pytest.mark.parametrize("completed", [False, True])
def test_rotation_spanning_adoption_keeps_coordination_and_pending_protection(
    isolated, monkeypatch, completed,
):
    legacy = PersistentCache(isolated)
    assert legacy.put("Example/rotating", "password", "previous", 1)
    # Model the already-running legacy writer, which cannot observe the selection.
    monkeypatch.setattr(legacy, "_refresh_selection", lambda: None)
    with legacy.replacement_lock():
        assert legacy.begin_replace("Example/rotating", "password", "candidate", 2)
        current = PersistentCache(isolated)
        assert current.prepare()["state"] == "recovering"
        assert current._base_dir != isolated
        assert current._replacement_lock_file == legacy._replacement_lock_file
        assert not current.recover_missing("Example/rotating", "password", "previous", 1)
        with pytest.raises((TimeoutError, CacheProtectionError)):
            current.get("Example/rotating", readonly=True)
        if completed:
            assert legacy.complete_replace("Example/rotating", "password", "candidate", 2, 3)
    current.prepare()
    if completed:
        assert current.recover_missing("Example/rotating", "password", "candidate", 3)
        assert current.get("Example/rotating", readonly=True) == "candidate"
    else:
        assert current.pending_replace("Example/rotating", "password")["operation"] == 2
        assert not current.recover_missing("Example/rotating", "password", "previous", 1)
        assert current.get("Example/rotating", readonly=True) is None


def test_real_helper_ignores_hostile_cwd_and_pythonpath(isolated, monkeypatch):
    corrupt(isolated)
    hostile = isolated.parent / "hostile"
    package = hostile / "agent_vault"
    package.mkdir(parents=True)
    marker = isolated.parent / "injected"
    program = f"from pathlib import Path; Path({str(marker)!r}).touch(); raise RuntimeError('injected')"
    (package / "__init__.py").write_text(program, encoding="utf-8")
    (hostile / "sitecustomize.py").write_text(program, encoding="utf-8")
    monkeypatch.chdir(hostile)
    monkeypatch.setenv("PYTHONPATH", str(hostile))
    cache = PersistentCache(isolated)
    assert cache.prepare()["original"]["state"] == "copied"
    assert not marker.exists()
    assert cache.recover_missing("Example/current", "password", "live", 1)


@pytest.mark.skipif(os.name != "nt", reason="actual Windows deny-sharing handles")
@pytest.mark.parametrize("name", ["credential-cache.lock", "credential-replacement.lock"])
def test_exclusively_open_legacy_lock_does_not_veto_fresh_state(isolated, name):
    import ctypes

    before = corrupt(isolated)
    path = isolated / name
    path.touch(exist_ok=True)
    windows = cache_recovery.WindowsSecurity()
    handle = windows.kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        cache = PersistentCache(isolated)
        assert cache.prepare()["original"]["state"] == "preserved-in-place"
        assert cache._base_dir != isolated
        assert cache._cache_file.exists()
        assert not cache.recover_missing("Example/current", "password", "unconfirmed", 1)
    finally:
        windows.kernel.CloseHandle(handle)
    cache.prepare()
    assert cache.recover_missing("Example/current", "password", "live", 2)
    assert cache.get("Example/current", readonly=True) == "live"
    assert {name: (isolated / name).read_bytes() for name in before} == before


@pytest.mark.skipif(os.name != "nt", reason="actual Windows deny-sharing handle")
def test_exclusively_open_original_retains_pending_coordination(isolated):
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
        assert not cache.recover_missing("Example/current", "password", "unconfirmed", 2)
        assert not (cache._base_dir.parent / "coordinated.json").exists()
    finally:
        windows.kernel.CloseHandle(handle)
    cache.prepare()
    assert cache.recover_missing("Example/current", "password", "live", 2)
    assert PersistentCache(isolated).get("Example/current", readonly=True) == "live"
    assert {name: (isolated / name).read_bytes() for name in before} == before


def test_real_finite_owner_settles_blocked_decoder(isolated, monkeypatch):
    from agent_procutil.owned_process import run_caller_owned

    legacy = PersistentCache(isolated)
    assert legacy.put("Example/rotating", "password", "previous", 1)
    assert legacy.begin_replace("Example/rotating", "password", "candidate", 2)
    before = {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}
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
    cache = PersistentCache(isolated)
    assert not (cache._base_dir.parent / "coordinated.json").exists()
    assert not cache.recover_missing("Example/current", "password", "unconfirmed", 2)
    assert {name: (isolated / name).read_bytes() for name in before} == before
    monkeypatch.setattr("agent_procutil.owned_process.run_caller_owned", run_caller_owned)
    cache.prepare()
    assert cache.pending_replace("Example/rotating", "password")["operation"] == 2
    assert cache.get("Example/rotating", readonly=True) is None


def test_adoption_during_old_store_transaction_cannot_overwrite_current_journal(
    isolated, monkeypatch,
):
    legacy = PersistentCache(isolated)
    assert legacy.put("Example/rotating", "password", "previous")
    stale = PersistentCache(isolated)
    read_store = stale._read_store
    selected = []

    def publish_before_read(**kwargs):
        current = PersistentCache(isolated)
        current.prepare()
        assert current._base_dir != isolated
        compartment = PersistentCache(current._base_dir)
        assert compartment.begin_replace("Example/rotating", "password", "candidate", 2)
        selected.append(compartment)
        return read_store(**kwargs)

    monkeypatch.setattr(stale, "_read_store", publish_before_read)
    with legacy.replacement_lock():
        assert stale.put("Example/rotating", "password", "late old write")
    current = selected[0]
    assert current.pending_replace("Example/rotating", "password")["operation"] == 2
    assert current.get("Example/rotating", readonly=True) is None


@pytest.mark.parametrize(("name", "value"), [
    ("AGENT_VAULT_ENDPOINT", "tcp://127.0.0.1:41234"),
    ("AGENT_VAULT_HOST", "192.0.2.10"),
    ("KPDB", "other.kdbx"),
])
def test_changed_authority_never_falls_back_to_readable_originals(
    isolated, monkeypatch, name, value,
):
    original = PersistentCache(isolated)
    assert original.put("Example/shared", "password", "stale original", 1)
    preserved = {filename: (isolated / filename).read_bytes() for filename in cache_recovery.FILES}
    original._cache_file.write_bytes(b"fixture invalid ciphertext")
    active = PersistentCache(isolated)
    active.prepare()
    assert active.recover_missing("Example/shared", "password", "current authority", 2)
    for filename, raw in preserved.items():
        (isolated / filename).write_bytes(raw)
    if name == "KPDB":
        value = str(isolated.parent / value)
    current_bytes = active._cache_file.read_bytes()
    monkeypatch.setenv(name, value)
    assert not active.recover_missing("Example/late", "password", "fetched before switch", 3)
    assert active._cache_file.read_bytes() == current_bytes
    with pytest.raises(CacheProtectionError):
        active.get("Example/shared", readonly=True)
    new = PersistentCache(isolated)
    with pytest.raises(cache_recovery.CacheSelectionRequired):
        new.get("Example/shared", readonly=True)
    assert not new.put("Example/shared", "password", "unprepared")
    assert new.prepare()["original"]["reason"] == "unselected-source"
    assert new.get("Example/shared", readonly=True) is None
    assert new._base_dir != active._base_dir
    assert new.recover_missing("Example/shared", "password", "new authority", 3)
    assert new.get("Example/shared", readonly=True) == "new authority"
    assert preserved == {
        filename: (isolated / filename).read_bytes() for filename in cache_recovery.FILES
    }


def test_missing_recovery_marker_never_exposes_originals(isolated):
    corrupt(isolated)
    cache = PersistentCache(isolated)
    cache.prepare()
    assert cache.recover_missing("Example/current", "password", "current")
    (cache._base_dir.parent / "recovery.json").unlink()
    before = {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}
    reopened = PersistentCache(isolated)
    with pytest.raises(cache_recovery.CacheSelectionRequired):
        reopened.get("Example/current", readonly=True)
    with pytest.raises(cache_recovery.CacheSelectionRequired):
        reopened.prepare()
    assert not reopened.put("Example/current", "password", "must not write originals")
    assert before == {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}


@pytest.mark.parametrize("error", [OSError, ImportError])
def test_transient_original_read_failure_keeps_pending_journal_until_retry(
    isolated, monkeypatch, error,
):
    legacy = PersistentCache(isolated)
    assert legacy.put("Example/rotating", "password", "previous", 1)
    assert legacy.begin_replace("Example/rotating", "password", "candidate", 2)
    before = {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}
    original_read = PersistentCache._read_store
    unavailable = True

    def fail_original(self, **kwargs):
        if self._root_dir == isolated and unavailable:
            raise CacheProtectionError("fixture decoder failed") from error("fixture unavailable")
        return original_read(self, **kwargs)

    monkeypatch.setattr(PersistentCache, "_read_store", fail_original)
    monkeypatch.setattr(cache_recovery, "_inspect_bounded", cache_recovery._inspect_original)
    current = PersistentCache(isolated)
    assert current.prepare()["state"] == "recovering"
    assert not (current._base_dir.parent / "coordinated.json").exists()
    assert not current.recover_missing("Example/rotating", "password", "unconfirmed", 3)
    current.prepare()
    assert not (current._base_dir.parent / "coordinated.json").exists()
    unavailable = False
    current.prepare()
    assert current.pending_replace("Example/rotating", "password")["operation"] == 2
    assert current.get("Example/rotating", readonly=True) is None
    assert before == {name: (isolated / name).read_bytes() for name in cache_recovery.FILES}


@pytest.mark.skipif(os.name != "nt", reason="actual Windows deny-sharing handle")
def test_lock_open_failure_does_not_break_optional_raw_key_migration(isolated):
    import ctypes
    from agent_vault import kek

    cache = PersistentCache(isolated)
    assert cache.put("Example/current", "password", "cached")
    key = kek.unwrap_bytes(cache._key_file.read_bytes())
    cache._key_file.write_bytes(key)
    windows = cache_recovery.WindowsSecurity()
    handle = windows.kernel.CreateFileW(
        str(cache._lock_file), 0x80000000, 0, None, 3, 0x80, None,
    )
    assert handle != ctypes.c_void_p(-1).value
    try:
        assert cache.get("Example/current") == "cached"
        assert cache._key_file.read_bytes() == key
    finally:
        windows.kernel.CloseHandle(handle)


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
    assert len(requests) == 2 and requests[0]["initialize_owner"] is False
    assert requests[1]["action"] == "get" and requests[1]["refresh"] is True
    assert PersistentCache(root).get("Identity/example", readonly=True) == key
    assert {name: (root / name).read_bytes() for name in before} == before
