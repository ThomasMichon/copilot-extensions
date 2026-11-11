"""Tests for the persistent on-disk credential cache and its CLI wiring."""

from __future__ import annotations

import contextlib
import io
import json
import threading
from types import SimpleNamespace

import pytest

from agent_vault import cache as cache_mod
from agent_vault import cli, keepassxc, service
from agent_vault.cache import PersistentCache, cache_enabled, get_cache

pytest.importorskip("Cryptodome")


@pytest.fixture
def enabled_cache(monkeypatch, tmp_path):
    """Enable the persistent cache in an isolated temp directory."""
    monkeypatch.setenv(cache_mod.CACHE_ENABLE_ENV, "1")
    monkeypatch.setenv(cache_mod.CACHE_DIR_ENV, str(tmp_path / "vcache"))
    return tmp_path / "vcache"


@pytest.fixture
def disabled_env(monkeypatch):
    monkeypatch.delenv(cache_mod.CACHE_ENABLE_ENV, raising=False)
    monkeypatch.delenv(cache_mod.CACHE_DIR_ENV, raising=False)


# ---------------------------------------------------------------------------
# Enablement
# ---------------------------------------------------------------------------


def test_cache_disabled_by_default(disabled_env):
    assert cache_enabled() is False
    c = get_cache()
    assert c.enabled is False
    # Disabled cache is a safe no-op.
    assert c.put("Foo/bar", "password", "s3cret") is False
    assert c.get("Foo/bar", "password") is None


def test_cache_dir_env_enables(monkeypatch, tmp_path, disabled_env):
    monkeypatch.setenv(cache_mod.CACHE_DIR_ENV, str(tmp_path))
    assert cache_enabled() is True


def test_enable_flag_enables(monkeypatch, disabled_env):
    monkeypatch.setenv(cache_mod.CACHE_ENABLE_ENV, "yes")
    assert cache_enabled() is True


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_put_get_roundtrip(enabled_cache):
    c = get_cache()
    assert c.enabled is True
    assert c.put("Aperture/HA", "password", "portal-gun") is True
    assert c.get("Aperture/HA", "password") == "portal-gun"
    # A fresh instance reads the same on-disk store (persistence).
    assert PersistentCache().get("Aperture/HA", "password") == "portal-gun"


def test_get_rejects_non_object_record(enabled_cache, monkeypatch):
    persistent = get_cache()
    monkeypatch.setattr(
        persistent,
        "_read_store",
        lambda: {"v": 1, "entries": {"A/x": {"password": "legacy"}}},
    )

    assert persistent.get("A/x", "password") is None


def test_cache_paths_repair_non_object_entry_record(enabled_cache, monkeypatch):
    persistent = get_cache()
    monkeypatch.setattr(
        persistent,
        "_read_store",
        lambda: {"v": 1, "entries": {"A/x": "legacy"}},
    )

    assert persistent.get("A/x", "password") is None
    assert persistent.pending_replace("A/x", "password") is None


def test_put_repairs_non_object_field_record(enabled_cache):
    persistent = get_cache()
    store = {"v": 1, "entries": {"A/x": {"password": "legacy"}}}
    assert persistent._write_store(store)

    assert persistent.put("A/x", "password", "normalized", 1)
    assert persistent.get("A/x", "password") == "normalized"


def test_encrypted_on_disk(enabled_cache):
    c = get_cache()
    c.put("Aperture/HA", "password", "portal-gun")
    blob = (enabled_cache / "credential-cache.enc").read_bytes()
    assert b"portal-gun" not in blob  # value is not stored in the clear


def test_invalidate_and_clear(enabled_cache):
    c = get_cache()
    c.put("A/x", "password", "v1")
    c.put("A/y", "username", "user")
    assert c.invalidate("A/x", "password") is True
    assert c.get("A/x", "password") is None
    assert c.get("A/y", "username") == "user"
    assert c.clear() is True
    assert c.get("A/y", "username") is None


def test_status_counts(enabled_cache):
    c = get_cache()
    c.put("A/x", "password", "v1")
    c.put("A/x", "username", "u")
    st = c.status()
    assert st["enabled"] is True
    assert st["entry_count"] == 1
    assert st["field_count"] == 2
    assert st["newest"] is not None


# ---------------------------------------------------------------------------
# CLI: get cache-through / --cache-only / --refresh
# ---------------------------------------------------------------------------


class _Args:
    def __init__(self, **kw):
        self.entry = kw.get("entry")
        self.field = kw.get("field", "password")
        self.password = kw.get("password")
        self.prompt = kw.get("prompt", False)
        self.refresh = kw.get("refresh", False)
        self.cache_only = kw.get("cache_only", False)
        self.max_cache_age = kw.get("max_cache_age")


def test_get_cache_only_hit(enabled_cache, capsys):
    get_cache().put("A/x", "password", "cached-val")
    rc = cli.cmd_get(_Args(entry="A/x"))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "cached-val"


def test_get_cache_only_miss_never_contacts_service(enabled_cache, monkeypatch):
    calls = {"ensure": 0}
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: calls.__setitem__("ensure", 1) or True)
    rc = cli.cmd_get(_Args(entry="A/missing", cache_only=True))
    assert rc == 1
    assert calls["ensure"] == 0  # --cache-only must not start/contact the service


def test_get_cache_through_populates(enabled_cache, monkeypatch):
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: True)
    monkeypatch.setattr(cli, "send_command", lambda req, timeout=None: {"ok": True, "value": "live-val"})
    rc = cli.cmd_get(_Args(entry="A/live"))
    assert rc == 0
    # The live fetch was written through to the persistent cache.
    assert get_cache().get("A/live", "password") == "live-val"


def test_get_refresh_bypasses_cache(enabled_cache, monkeypatch):
    get_cache().put("A/x", "password", "stale")
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: True)
    monkeypatch.setattr(cli, "send_command", lambda req, timeout=None: {"ok": True, "value": "fresh"})
    rc = cli.cmd_get(_Args(entry="A/x", refresh=True))
    assert rc == 0
    assert get_cache().get("A/x", "password") == "fresh"  # cache updated with fresh value


def test_get_refresh_replaces_newer_generation_from_legacy_daemon(
    enabled_cache,
    monkeypatch,
):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "stale", 7)
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: True)
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda req, timeout=None: {"ok": True, "value": "fresh"},
    )

    assert cli.cmd_get(_Args(entry="A/x", refresh=True)) == 0
    assert persistent.get("A/x", "password") == "fresh"


def test_authoritative_generation_cannot_overwrite_newer_normal_record(
    enabled_cache,
):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "newer", 8)

    assert persistent.reconcile_authoritative("A/x", "password", "older", 7)
    assert persistent.get("A/x", "password") == "newer"


def test_get_refresh_reconciles_post_commit_journal(
    enabled_cache,
    monkeypatch,
):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)
    assert persistent.begin_replace("A/x", "password", "committed", 10)
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: True)
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda req, timeout=None: {
            "ok": True,
            "value": "committed",
            "generation": 2,
        },
    )

    assert cli.cmd_get(_Args(entry="A/x", refresh=True)) == 0
    assert persistent.get("A/x", "password") == "committed"
    assert persistent.pending_replace("A/x", "password") is None


def test_get_refresh_holds_replacement_lock_across_rpc_and_reconcile(
    enabled_cache,
    monkeypatch,
):
    persistent = get_cache()
    events = []

    @contextlib.contextmanager
    def observed_lock():
        events.append("lock-enter")
        yield
        events.append("lock-exit")

    original_reconcile = persistent.reconcile_authoritative

    def reconcile(*args, **kwargs):
        events.append("reconcile")
        return original_reconcile(*args, **kwargs)

    monkeypatch.setattr(persistent, "replacement_lock", observed_lock)
    monkeypatch.setattr(persistent, "reconcile_authoritative", reconcile)
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: True)
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda req, timeout=None: (
            events.append("rpc") or {"ok": True, "value": "fresh"}
        ),
    )

    assert cli.cmd_get(_Args(entry="A/x", refresh=True)) == 0
    assert events == ["lock-enter", "rpc", "reconcile", "lock-exit"]


def test_get_refresh_fails_when_authoritative_reconciliation_cannot_persist(
    enabled_cache,
    monkeypatch,
    capsys,
):
    persistent = get_cache()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(
        persistent,
        "reconcile_authoritative",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: True)
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda req, timeout=None: {"ok": True, "value": "fresh"},
    )

    assert cli.cmd_get(_Args(entry="A/x", refresh=True)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "persistent cache could not be reconciled" in captured.err


def test_cache_only_migrates_legacy_raw_key_without_service(
    enabled_cache,
    monkeypatch,
    capsys,
):
    persistent = get_cache()
    assert persistent.put("token", "password", "offline", 4)
    monkeypatch.setattr(
        cli.config,
        "resolve_context",
        lambda: SimpleNamespace(group="A"),
    )
    monkeypatch.setattr(
        cli,
        "ensure_service",
        lambda *a, **k: pytest.fail("cache-only read contacted the service"),
    )

    assert cli.cmd_get(_Args(entry="token", cache_only=True)) == 0
    assert capsys.readouterr().out == "offline\n"
    assert persistent.get("A/token", "password") == "offline"
    assert persistent.get("token", "password") is None


def test_batch_get_preserves_cached_order_duplicates_and_values(
    enabled_cache, monkeypatch, capsys,
):
    persistent = get_cache()
    assert persistent.put("Example/one", "password", "line one\nline two")
    assert persistent.put("Example/two", "username", "")
    requests = [
        {"entry": "Example/one"},
        {"entry": "Example/two", "field": "username"},
        {"entry": "Example/one", "field": "password"},
    ]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(requests)))
    monkeypatch.setattr(
        cli, "ensure_service",
        lambda: pytest.fail("cached batch contacted the service"),
    )

    assert cli.main(["batch-get"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"results": [
        {"entry": "Example/one", "field": "password", "value": "line one\nline two",
         "ok": True, "source": "cache"},
        {"entry": "Example/two", "field": "username", "value": "",
         "ok": True, "source": "cache"},
        {"entry": "Example/one", "field": "password", "value": "line one\nline two",
         "ok": True, "source": "cache"},
    ]}


@pytest.mark.parametrize("failure", [OSError, PermissionError])
def test_batch_cache_io_failure_keeps_successes_and_continues(
    enabled_cache, monkeypatch, capsys, failure,
):
    persistent = get_cache()
    assert persistent.put("Example/one", "password", "cached-value")
    cache_before = persistent._cache_file.read_bytes()
    key_before = persistent._key_file.read_bytes()
    original_cipher = persistent._get_fernet
    reads = []

    def cipher(**kwargs):
        reads.append(None)
        if len(reads) == 2:
            raise failure("private-cache-path-diagnostic")
        return original_cipher(**kwargs)

    monkeypatch.setattr(persistent, "_get_fernet", cipher)
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(
        cli, "ensure_service",
        lambda: pytest.fail("cache I/O failure fell back to the service"),
    )
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps([
        {"entry": "Example/one"}, {"entry": "Example/two"}, {"entry": "Example/one"},
    ])))

    assert cli.main(["batch-get"]) == 2
    captured = capsys.readouterr()
    results = json.loads(captured.out)["results"]
    assert [row["entry"] for row in results] == [
        "Example/one", "Example/two", "Example/one",
    ]
    assert [row["ok"] for row in results] == [True, False, True]
    assert [row["value"] for row in results] == ["cached-value", None, "cached-value"]
    assert results[1]["source"] == "cache"
    assert results[1]["error"].startswith("cache_read_failed;")
    assert "private-cache-path-diagnostic" not in captured.out
    assert captured.err == ""
    assert persistent._cache_file.read_bytes() == cache_before
    assert persistent._key_file.read_bytes() == key_before

    reads[:] = [None]
    with pytest.raises(failure, match="private-cache-path-diagnostic"):
        cli.cmd_get(SimpleNamespace(entry="Example/one", field="password"))


@pytest.mark.parametrize("succeed", [False, True])
def test_batch_get_refresh_returns_ordered_partial_or_total_failure(
    enabled_cache, monkeypatch, capsys, succeed,
):
    persistent = get_cache()
    assert persistent.put("Example/one", "password", "stale")
    calls = []

    def send(request, timeout=None):
        calls.append(request)
        if succeed and request["entry"] == "Example/one":
            return {"ok": True, "value": "fresh"}
        return {"ok": False, "error": "locked; run agent-vault unlock"}

    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", send)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps([
        {"entry": "Example/one"}, {"entry": "Example/two"},
    ])))

    assert cli.main(["batch-get", "--refresh"]) == (2 if succeed else 1)
    captured = capsys.readouterr()
    results = json.loads(captured.out)["results"]
    assert captured.err == ""
    assert [row["entry"] for row in results] == ["Example/one", "Example/two"]
    assert [row["ok"] for row in results] == [succeed, False]
    assert results[1]["value"] is None
    assert results[1]["error"] == "locked; run agent-vault unlock"
    assert len(calls) == 2
    assert all(call["refresh"] and "allow_prompt" not in call for call in calls)
    if succeed:
        assert set(results[0]) == {"entry", "field", "value", "ok", "source"}
        assert persistent.get("Example/one", "password") == "fresh"


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("failure", [
    "ciphertext", "schema", "key", "orphan-key", "entry", "field", "rotation",
    "journal-empty", "journal-candidate", "journal-operation", "journal-previous",
])
def test_batch_refuses_corrupt_fernet_cache_without_replacement(
    enabled_cache, monkeypatch, capsys, failure, refresh,
):
    from agent_vault.protected_crypto import Fernet

    from agent_vault import kek

    monkeypatch.delenv("AGENT_VAULT_PINNED_CONTEXT", raising=False)
    enabled_cache.mkdir()
    persistent = get_cache()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    persistent._key_file.write_bytes(kek.WRAP_MAGIC + b"R" + key)
    persistent._cache_file.write_bytes(cipher.encrypt(json.dumps({
        "v": 1, "entries": {"Example/one": {"password": {"value": "cached-value"}}},
    }).encode()))
    if failure == "ciphertext":
        persistent._cache_file.write_bytes(b"corrupt-ciphertext")
    elif failure == "schema":
        persistent._cache_file.write_bytes(cipher.encrypt(b'{"v":1,"entries":[]}'))
    elif failure in ("key", "orphan-key"):
        persistent._key_file.write_bytes(kek.WRAP_MAGIC + b"Rmalformed-fernet-key")
        if failure == "orphan-key":
            persistent._cache_file.unlink()
    else:
        malformed = {
            "entry": [],
            "field": {"password": None},
            "rotation": {"password": {"rotation": "invalid"}},
        }
        journals = {
            "journal-empty": {},
            "journal-candidate": {"candidate": 42, "operation": 1, "previous": None},
            "journal-operation": {"candidate": "new", "operation": True, "previous": None},
            "journal-previous": {"candidate": "new", "operation": 1, "previous": {"value": 42}},
        }
        fields = malformed[failure] if failure in malformed else {
            "password": {"rotation": journals[failure]},
        }
        persistent._cache_file.write_bytes(cipher.encrypt(json.dumps({
            "v": 1, "entries": {"Example/one": fields, "Example/two": fields},
        }).encode()))
    cache_before = persistent._cache_file.read_bytes() if persistent._cache_file.exists() else None
    key_before = persistent._key_file.read_bytes()
    persistent = PersistentCache()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(
        cli, "ensure_service",
        lambda: pytest.fail("corrupt cache fell back to the service"),
    )
    requests = [
        {"entry": "Example/one"}, {"entry": "Example/two"}, {"entry": "Example/one"},
    ]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(requests)))

    assert cli.main(["batch-get", *(["--refresh"] if refresh else [])]) == 1
    captured = capsys.readouterr()
    results = json.loads(captured.out)["results"]
    assert [row["entry"] for row in results] == [row["entry"] for row in requests]
    assert all(row["ok"] is False and row["value"] is None for row in results)
    assert all(row["error"].startswith("cache_protection_failed;") for row in results)
    assert captured.err == ""
    if cache_before is None:
        assert not persistent._cache_file.exists()
    else:
        assert persistent._cache_file.read_bytes() == cache_before
    assert persistent._key_file.read_bytes() == key_before
    if failure == "key":
        with pytest.raises(ValueError):
            cli.cmd_get(SimpleNamespace(entry="Example/one", field="password"))
    else:
        assert persistent.get("Example/one") is None


@pytest.mark.parametrize("existing_key", [False, True])
def test_batch_can_populate_fresh_cache_without_rekeying(
    enabled_cache, monkeypatch, capsys, existing_key,
):
    persistent = get_cache()
    key_before = None
    if existing_key:
        assert persistent.put("Example/one", "password", "previous")
        key_before = persistent._key_file.read_bytes()
        persistent._cache_file.unlink()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: PersistentCache())
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *_args, **_kwargs: {
        "ok": True, "value": "authoritative",
    })
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('[{"entry":"Example/one"}]'))

    assert cli.main(["batch-get"]) == 0
    result, = json.loads(capsys.readouterr().out)["results"]
    assert result["value"] == "authoritative"
    if existing_key:
        assert persistent._key_file.read_bytes() == key_before
    else:
        assert persistent._key_file.exists()
    assert PersistentCache().get("Example/one") == "authoritative"


@pytest.mark.parametrize("stage", ["startup", "read"])
@pytest.mark.parametrize("failure", ["os", "value", "timeout", "type", "attribute"])
def test_batch_service_errors_are_not_reported_as_cache_failures(
    enabled_cache, monkeypatch, capsys, stage, failure,
):
    import subprocess

    error = {
        "os": OSError("private-service-diagnostic"),
        "value": ValueError("private-service-diagnostic"),
        "timeout": subprocess.TimeoutExpired(["private-helper"], 5),
        "type": TypeError("private-service-diagnostic"),
        "attribute": AttributeError("private-service-diagnostic"),
    }[failure]

    def failed(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(cli, "ensure_service", failed if stage == "startup" else lambda: True)
    monkeypatch.setattr(cli, "send_command", failed)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(
        '[{"entry":"Example/one"},{"entry":"Example/two"}]',
    ))

    assert cli.main(["batch-get"]) == 1
    captured = capsys.readouterr()
    results = json.loads(captured.out)["results"]
    assert [row["entry"] for row in results] == ["Example/one", "Example/two"]
    assert all(row["ok"] is False and row["value"] is None for row in results)
    assert all(row["source"] == "service" for row in results)
    expected = "service_unavailable;" if stage == "startup" else "service_read_failed;"
    assert all(row["error"].startswith(expected) for row in results)
    assert captured.err == ""
    assert "private-service-diagnostic" not in captured.out
    assert "private-helper" not in captured.out
    with pytest.raises(type(error)):
        cli.cmd_get(SimpleNamespace(entry="Example/one", field="password"))


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("legacy_alias", [False, True])
def test_batch_preserves_valid_pending_journal_semantics(
    enabled_cache, monkeypatch, capsys, refresh, legacy_alias,
):
    from agent_vault import config

    entry = "token" if legacy_alias else "Example/one"
    monkeypatch.delenv("AGENT_VAULT_PINNED_CONTEXT", raising=False)
    monkeypatch.setattr(config, "resolve_context", lambda: SimpleNamespace(group="Example"))
    persistent = get_cache()
    assert persistent.put(entry, "password", "previous")
    assert persistent.begin_replace(entry, "password", "candidate", 1)
    cache_before = persistent._cache_file.read_bytes()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *_args, **_kwargs: {
        "ok": True, "value": "authoritative", "generation": 1,
    })
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps([{"entry": entry}])))

    assert cli.main(["batch-get", *(["--refresh"] if refresh else [])]) == 0
    result, = json.loads(capsys.readouterr().out)["results"]
    assert result["value"] == "authoritative"
    if refresh:
        assert persistent.pending_replace(entry, "password") is None
        assert persistent.get(entry) == "authoritative"
    else:
        assert persistent.pending_replace(entry, "password")["candidate"] == "candidate"
        assert persistent.get(entry) is None
        assert persistent._cache_file.read_bytes() == cache_before
    if legacy_alias:
        assert persistent.get("Example/token") is None


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("response", [
    {"ok": True}, {"ok": True, "value": 42}, [],
    {"ok": "yes", "value": "not-published"},
    *[
        {"ok": True, "value": "not-published", "generation": generation}
        for generation in ([], float("inf"), "not-an-integer", True)
    ],
])
def test_batch_rejects_malformed_service_values_before_cache_write(
    enabled_cache, monkeypatch, capsys, response, refresh,
):
    persistent = get_cache()
    assert persistent.put("Example/retained", "password", "cached-value")
    cache_before = persistent._cache_file.read_bytes()
    key_before = persistent._key_file.read_bytes()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(
        '[{"entry":"Example/one"},{"entry":"Example/two"}]',
    ))

    assert cli.main(["batch-get", *(["--refresh"] if refresh else [])]) == 1
    captured = capsys.readouterr()
    results = json.loads(captured.out)["results"]
    assert [row["entry"] for row in results] == ["Example/one", "Example/two"]
    assert all(row["ok"] is False and row["value"] is None for row in results)
    assert all(row["source"] == "service" for row in results)
    assert all(row["error"].startswith("service_response_invalid;") for row in results)
    assert "not-published" not in captured.out
    assert persistent._cache_file.read_bytes() == cache_before
    assert persistent._key_file.read_bytes() == key_before


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("failure,key_only", [
    ("ciphertext", False), ("key", False), ("changed-key", False), ("deleted-key", False),
    ("ciphertext", True), ("key", True), ("deleted-key", True),
])
def test_batch_does_not_replace_cache_corrupted_during_service_read(
    enabled_cache, monkeypatch, capsys, refresh, key_only, failure,
):
    from agent_vault.protected_crypto import Fernet

    from agent_vault import kek

    persistent = get_cache()
    assert persistent.put("Example/retained", "password", "cached-value")
    if key_only:
        persistent._cache_file.unlink()
    cache_before = persistent._cache_file.read_bytes() if persistent._cache_file.exists() else None
    key_before = persistent._key_file.read_bytes()
    changed_key = kek.wrap_bytes(Fernet.generate_key())

    def send(*_args, **_kwargs):
        if failure == "ciphertext":
            persistent._cache_file.write_bytes(b"corrupted-during-read")
        elif failure == "deleted-key":
            persistent._key_file.unlink()
        else:
            persistent._key_file.write_bytes(
                changed_key if failure == "changed-key" else b"corrupted-during-read",
            )
        return {"ok": True, "value": "not-published"}

    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", send)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('[{"entry":"Example/new"}]'))

    assert cli.main(["batch-get", *(["--refresh"] if refresh else [])]) == 1
    captured = capsys.readouterr()
    result, = json.loads(captured.out)["results"]
    assert result["ok"] is False
    assert result["value"] is None
    assert "could not be reconciled" in result["error"]
    assert "not-published" not in captured.out + captured.err
    expected_cache = b"corrupted-during-read" if failure == "ciphertext" else cache_before
    if expected_cache is None:
        assert not persistent._cache_file.exists()
    else:
        assert persistent._cache_file.read_bytes() == expected_cache
    if failure == "deleted-key":
        assert not persistent._key_file.exists()
    else:
        assert persistent._key_file.read_bytes() == (
            key_before if failure == "ciphertext"
            else changed_key if failure == "changed-key" else b"corrupted-during-read"
        )


@pytest.mark.parametrize("value,ok", [("cached-value", True), (42, False)])
def test_batch_validates_legacy_alias_without_migrating(
    enabled_cache, monkeypatch, capsys, value, ok,
):
    from agent_vault import config

    monkeypatch.delenv("AGENT_VAULT_PINNED_CONTEXT", raising=False)
    monkeypatch.setattr(config, "resolve_context", lambda: SimpleNamespace(group="Example"))
    persistent = get_cache()
    assert persistent.put("token", "password", value)
    cache_before = persistent._cache_file.read_bytes()
    key_before = persistent._key_file.read_bytes()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(
        cli, "ensure_service",
        lambda: pytest.fail("legacy cached alias contacted the service"),
    )
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('[{"entry":"token"}]'))

    assert cli.main(["batch-get"]) == (0 if ok else 1)
    result, = json.loads(capsys.readouterr().out)["results"]
    assert result["ok"] is ok
    assert result["value"] == (value if ok else None)
    assert result["source"] == "cache"
    if not ok:
        assert result["error"].startswith("cache_protection_failed;")
    assert persistent._cache_file.read_bytes() == cache_before
    assert persistent._key_file.read_bytes() == key_before


def test_batch_get_does_not_publish_unreconciled_refresh_value(
    enabled_cache, monkeypatch, capsys,
):
    persistent = get_cache()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(persistent, "reconcile_authoritative", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *_args, **_kw: {
        "ok": True, "value": "not-published",
    })
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('[{"entry":"Example/one"}]'))

    assert cli.main(["batch-get", "--refresh"]) == 1
    captured = capsys.readouterr()
    assert "not-published" not in captured.out + captured.err
    result = json.loads(captured.out)["results"][0]
    assert result["ok"] is False
    assert result["value"] is None
    assert "could not be reconciled" in result["error"]


@pytest.mark.parametrize("payload", [
    '[{"entry":"Example/one"},{"entry":false}]',
    '[{"entry":"Example/one","entry":"Example/two"}]',
    '[{"entry":"Example/one","field":NaN}]',
    '{"entry":"Example/one"}',
    '[{"entry":"Example/one","field":""}]',
    '[{"entry":"Example/one","refresh":true}]',
])
def test_batch_get_validates_all_input_before_reads(monkeypatch, capsys, payload):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(payload))
    monkeypatch.setattr(
        cache_mod, "get_cache",
        lambda: pytest.fail("invalid batch reached credential storage"),
    )

    assert cli.main(["batch-get"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid batch input" in captured.err


def test_batch_get_rejects_unknown_prompt_option(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('[{"entry":"Example/one"}]'))
    monkeypatch.setattr(
        cache_mod, "get_cache",
        lambda: pytest.fail("invalid option reached credential storage"),
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["batch-get", "--promtp"])
    assert error.value.code == 2


def test_empty_batch_does_not_open_credential_storage(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("[]"))
    monkeypatch.setattr(
        cache_mod, "get_cache",
        lambda: pytest.fail("empty batch opened credential storage"),
    )
    assert cli.main(["batch-get"]) == 0
    assert json.loads(capsys.readouterr().out) == {"results": []}


def test_get_parser_preserves_existing_handler_and_options(monkeypatch):
    observed = []
    monkeypatch.setattr(cli, "cmd_get", lambda args: observed.append(args) or 7)
    assert cli.main([
        "get", "Example/one", "username", "--cache-only", "--max-cache-age", "60",
        "--refresh", "--prompt",
    ]) == 7
    args = observed[0]
    assert (args.entry, args.field, args.max_cache_age) == ("Example/one", "username", 60)
    assert args.cache_only and args.refresh and args.prompt


# ---------------------------------------------------------------------------
# Password replacement transactions
# ---------------------------------------------------------------------------


def _rotation_service(backend, monkeypatch):
    svc = service.VaultService()
    svc.cli = backend
    monkeypatch.setattr(svc, "ensure_unlocked", lambda *_args, **_kwargs: True)
    return svc


def test_cache_through_write_cannot_overwrite_pending_rotation(enabled_cache):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)
    assert persistent.begin_replace("A/x", "password", "new", 0)

    assert persistent.put("A/x", "password", "old", 99)

    assert persistent.get("A/x", "password") is None
    assert persistent.pending_replace("A/x", "password")["candidate"] == "new"


def test_ordinary_legacy_read_cannot_overwrite_pending_rotation(
    enabled_cache,
    monkeypatch,
    capsys,
):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)
    assert persistent.begin_replace("A/x", "password", "new", 0)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda request, timeout: {"ok": True, "value": "old"},
    )
    monkeypatch.setattr(
        cli.config,
        "resolve_context",
        lambda: SimpleNamespace(group=""),
    )

    assert cli.cmd_get(_Args(entry="A/x")) == 0
    assert capsys.readouterr().out == "old\n"
    assert persistent.get("A/x", "password") is None
    assert persistent.pending_replace("A/x", "password")["candidate"] == "new"


def test_cache_populate_reconciles_legacy_response(
    enabled_cache,
    monkeypatch,
    capsys,
):
    from agent_vault import extensions as ext

    registry = ext.ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(ext, "_REGISTRY", registry)
    persistent = get_cache()
    assert persistent.put("A/x", "password", "stale", 7)
    requests = []
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: True)
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda request, timeout=None: (
            requests.append(request) or {"ok": True, "value": "fresh"}
        ),
    )
    monkeypatch.setattr(
        cli.config,
        "resolve_context",
        lambda: SimpleNamespace(group=""),
    )

    args = SimpleNamespace(
        entry=["A/x"],
        manifest=None,
        machine=None,
        prompt=False,
        verify=False,
    )
    assert cli.cmd_cache_populate(args) == 0
    assert capsys.readouterr().out.endswith("Cached 1 credential(s), 0 failed\n")
    assert requests == [
        {
            "action": "get",
            "entry": "A/x",
            "field": "password",
            "refresh": True,
        }
    ]
    assert persistent.get("A/x", "password") == "fresh"


def test_cache_populate_reports_authoritative_reconciliation_failure(
    enabled_cache,
    monkeypatch,
    capsys,
):
    from agent_vault import extensions as ext

    registry = ext.ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(ext, "_REGISTRY", registry)
    persistent = get_cache()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(
        persistent,
        "reconcile_authoritative",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(cli, "ensure_service", lambda *a, **k: True)
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda request, timeout=None: {"ok": True, "value": "fresh"},
    )
    monkeypatch.setattr(
        cli.config,
        "resolve_context",
        lambda: SimpleNamespace(group=""),
    )

    args = SimpleNamespace(
        entry=["A/x"],
        manifest=None,
        machine=None,
        prompt=False,
        verify=False,
    )
    assert cli.cmd_cache_populate(args) == 1
    captured = capsys.readouterr()
    assert captured.out.endswith("Cached 0 credential(s), 1 failed\n")
    assert "cache update failed: A/x [password]" in captured.err


def test_failed_rotation_restores_prior_offline_value(enabled_cache, monkeypatch):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)

    class Backend:
        @staticmethod
        def edit_password(_kpdb, _entry, _password):
            return False, "backend rejected update"

    monkeypatch.setattr(service, "get_cache", lambda: persistent)
    svc = _rotation_service(Backend(), monkeypatch)

    result = svc._set_password("vault.kdbx", "A/x", "new", "", "test")

    assert result == {"ok": False, "error": "backend rejected update"}
    assert persistent.get("A/x", "password") == "old"


def test_interrupted_rotation_reconciles_before_retry(enabled_cache, monkeypatch):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)
    assert persistent.begin_replace("A/x", "password", "new", 10)

    class Backend:
        value = "old"

        def get_entry(self, _kpdb, _entry, _field):
            return self.value

        def edit_password(self, _kpdb, _entry, password):
            self.value = password
            return True, "updated"

    monkeypatch.setattr(service, "get_cache", lambda: persistent)
    svc = _rotation_service(Backend(), monkeypatch)

    result = svc._set_password("vault.kdbx", "A/x", "new", "", "test")

    assert result["ok"] is True
    assert persistent.get("A/x", "password") == "new"
    assert persistent.pending_replace("A/x", "password") is None


def test_ambiguous_rotation_confirms_committed_candidate(enabled_cache, monkeypatch):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)

    class Backend:
        value = "old"

        def get_entry(self, _kpdb, _entry, _field):
            return self.value

        def edit_password(self, _kpdb, _entry, password):
            self.value = password
            raise keepassxc.PasswordMutationAmbiguous("backend timed out")

    monkeypatch.setattr(service, "get_cache", lambda: persistent)
    svc = _rotation_service(Backend(), monkeypatch)

    result = svc._set_password("vault.kdbx", "A/x", "new", "", "test")

    assert result["ok"] is True
    assert persistent.get("A/x", "password") == "new"


def test_ambiguous_rotation_evicts_memory_when_live_read_fails(
    enabled_cache,
    monkeypatch,
):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)

    class Backend:
        @staticmethod
        def get_entry(_kpdb, _entry, _field):
            return None

        @staticmethod
        def edit_password(_kpdb, _entry, _password):
            raise keepassxc.PasswordMutationAmbiguous("backend timed out")

    monkeypatch.setattr(service, "get_cache", lambda: persistent)
    svc = _rotation_service(Backend(), monkeypatch)
    cache_key = ("vault.kdbx", "A/x", "password")
    svc.cache[cache_key] = "old"
    svc._credential_generations[cache_key] = 1

    result = svc._set_password("vault.kdbx", "A/x", "new", "", "test")

    assert result["cache_pending"] is True
    assert persistent.get("A/x", "password") is None
    assert cache_key not in svc.cache
    assert cache_key not in svc._credential_generations


def test_queued_rotation_expires_before_backend_write(enabled_cache, monkeypatch):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)
    writes = []
    unlocks = []

    class ObservedLock:
        def __init__(self):
            self.inner = threading.RLock()
            self.rotator_attempted = threading.Event()
            self.rotator_completed = threading.Event()

        def acquire(self, *args, **kwargs):
            is_rotator = threading.current_thread().name == "queued-rotation"
            if is_rotator:
                self.rotator_attempted.set()
            acquired = self.inner.acquire(*args, **kwargs)
            if is_rotator:
                self.rotator_completed.set()
            return acquired

        def release(self):
            self.inner.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_args):
            self.release()

    class Backend:
        @staticmethod
        def edit_password(_kpdb, _entry, password):
            writes.append(password)
            return True, "updated"

    monkeypatch.setattr(service, "get_cache", lambda: persistent)
    svc = service.VaultService()
    svc.cli = Backend()
    observed_lock = ObservedLock()
    svc._credential_lock = observed_lock
    monkeypatch.setattr(
        svc,
        "ensure_unlocked",
        lambda *_args, **_kwargs: unlocks.append(True) or True,
    )
    result = {}

    def rotate():
        result.update(svc._set_password(
            "vault.kdbx",
            "A/x",
            "new",
            "",
            "test",
            max_queue_seconds=0.01,
        ))

    with svc._credential_lock:
        rotator = threading.Thread(
            target=rotate,
            name="queued-rotation",
            daemon=True,
        )
        rotator.start()
        assert observed_lock.rotator_attempted.wait(timeout=5)
        assert observed_lock.rotator_completed.wait(timeout=5)
    rotator.join(timeout=5)

    assert not rotator.is_alive()
    assert result == {
        "ok": False,
        "error": "Password mutation expired before the backend write",
    }
    assert unlocks == []
    assert writes == []
    assert persistent.get("A/x", "password") == "old"


def test_cli_keeps_journal_after_ambiguous_transport(enabled_cache, monkeypatch, capsys):
    persistent = get_cache()
    assert persistent.put("A/x", "password", "old", 1)
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "_ensure_unlocked_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda request, timeout: None)
    monkeypatch.setattr(
        cli.config,
        "resolve_context",
        lambda: SimpleNamespace(group=""),
    )

    assert cli.cmd_set_password(_Args(entry="A/x", password="new")) == 1
    assert "service unreachable" in capsys.readouterr().err
    assert persistent.get("A/x", "password") is None
    assert persistent.pending_replace("A/x", "password") is not None


def test_cli_password_rotation_uses_bounded_rpc_timeouts(
    enabled_cache,
    monkeypatch,
    capsys,
):
    persistent = get_cache()
    calls = []

    def send(request, timeout):
        calls.append((request["action"], timeout))
        if request["action"] == "get":
            return {"ok": True, "value": "old", "generation": 1}
        return {"ok": True, "message": "updated", "generation": 2}

    assert persistent.put("A/x", "password", "old", 1)
    assert persistent.begin_replace("A/x", "password", "interrupted", 10)
    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "_ensure_unlocked_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", send)
    monkeypatch.setattr(
        cli.config,
        "resolve_context",
        lambda: SimpleNamespace(group=""),
    )

    assert cli.cmd_set_password(_Args(entry="A/x", password="new")) == 0
    assert capsys.readouterr().out == "updated\n"
    assert calls == [
        ("get", cli.PASSWORD_MUTATION_TIMEOUT),
        ("set-password", cli.PASSWORD_MUTATION_TIMEOUT),
    ]


def test_cli_serializes_concurrent_rotations(enabled_cache, monkeypatch):
    persistent = get_cache()
    first_started = threading.Event()
    release_first = threading.Event()
    second_lock_attempted = threading.Event()
    second_rpc_started = threading.Event()
    requests = []
    results = []

    def send(request, timeout):
        requests.append(request["password"])
        if request["password"] == "first":
            first_started.set()
            assert release_first.wait(timeout=5)
            return {"ok": True, "message": "first updated", "generation": 10}
        second_rpc_started.set()
        return {"ok": True, "message": "second updated", "generation": 20}

    monkeypatch.setattr(cache_mod, "get_cache", lambda: persistent)
    monkeypatch.setattr(cli, "_ensure_unlocked_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", send)
    monkeypatch.setattr(
        cli.config,
        "resolve_context",
        lambda: SimpleNamespace(group=""),
    )
    replacement_lock = persistent.replacement_lock

    @contextlib.contextmanager
    def observed_replacement_lock():
        if threading.current_thread().name == "second-rotation":
            second_lock_attempted.set()
        with replacement_lock():
            yield

    monkeypatch.setattr(persistent, "replacement_lock", observed_replacement_lock)
    first = threading.Thread(
        target=lambda: results.append(
            cli.cmd_set_password(_Args(entry="A/x", password="first"))
        ),
        name="first-rotation",
    )
    second = threading.Thread(
        target=lambda: results.append(
            cli.cmd_set_password(_Args(entry="A/x", password="second"))
        ),
        name="second-rotation",
    )

    first.start()
    assert first_started.wait(timeout=5)
    second.start()
    assert second_lock_attempted.wait(timeout=5)
    assert not second_rpc_started.wait(timeout=0.1)
    assert requests == ["first"]
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert sorted(results) == [0, 0]
    assert requests == ["first", "second"]
    assert persistent.get("A/x", "password") == "second"


# ---------------------------------------------------------------------------
# CLI: cache-verify / cache-clear / cache-status
# ---------------------------------------------------------------------------


class _VerifyArgs:
    def __init__(self, **kw):
        self.entry = kw.get("entry")
        self.manifest = kw.get("manifest")
        self.machine = kw.get("machine")
        self.json = kw.get("json", False)


def test_cache_verify_exit_codes(enabled_cache, monkeypatch):
    from agent_vault import extensions as ext

    reg = ext.ExtensionRegistry()
    reg._loaded = True
    monkeypatch.setattr(ext, "_REGISTRY", reg)
    get_cache().put("A/have", "password", "v")

    assert cli.cmd_cache_verify(_VerifyArgs(entry=["A/have"])) == 0
    assert cli.cmd_cache_verify(_VerifyArgs(entry=["A/have", "A/missing"])) == 2
    ext.reset_registry()


def test_cache_clear_and_status_cli(enabled_cache, capsys):
    get_cache().put("A/x", "password", "v")

    class _S:
        json = False

    assert cli.cmd_cache_status(_S()) == 0
    assert "enabled" in capsys.readouterr().out

    class _C:
        pass

    assert cli.cmd_cache_clear(_C()) == 0
    assert get_cache().get("A/x", "password") is None


# ---------------------------------------------------------------------------
# Fernet-key wrapping at rest (DPAPI on Windows / passthrough elsewhere) + migration
# ---------------------------------------------------------------------------


def test_cache_key_is_wrapped_at_rest(enabled_cache):
    """The persisted Fernet key file carries the wrapped-key magic (not plaintext)."""
    from agent_vault import kek

    c = get_cache()
    assert c.put("Svc/one", "password", "v1") is True  # forces key creation
    key_blob = (enabled_cache / "credential-cache.key").read_bytes()
    assert kek.is_wrapped(key_blob)  # magic-tagged, not a raw Fernet key
    # A brand-new cache instance unwraps the key and reads the value back.
    assert get_cache().get("Svc/one", "password") == "v1"


def test_cache_migrates_legacy_plaintext_key(enabled_cache):
    """A pre-existing plaintext Fernet key is read, then rewrapped on next use."""
    from agent_vault.protected_crypto import Fernet

    from agent_vault import kek

    base = enabled_cache
    base.mkdir(parents=True, exist_ok=True)
    legacy_key = Fernet.generate_key()
    key_file = base / "credential-cache.key"
    key_file.write_bytes(legacy_key + b"\n")  # old plaintext format

    # Seed a cache file encrypted under the legacy key so we can prove continuity.
    seeded = get_cache()
    assert seeded.put("Legacy/x", "password", "kept") is True

    # The key file is now migrated to the wrapped form, and the value survives.
    assert kek.is_wrapped(key_file.read_bytes())
    assert get_cache().get("Legacy/x", "password") == "kept"
