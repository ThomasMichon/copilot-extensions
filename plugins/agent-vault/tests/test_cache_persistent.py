"""Tests for the persistent on-disk credential cache and its CLI wiring."""

from __future__ import annotations

import contextlib
import threading
import time
from types import SimpleNamespace

import pytest

from agent_vault import cache as cache_mod
from agent_vault import cli, keepassxc, service
from agent_vault.cache import PersistentCache, cache_enabled, get_cache

cryptography = pytest.importorskip("cryptography")


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


def test_legacy_read_cannot_overwrite_pending_rotation(
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

    assert cli.cmd_get(_Args(entry="A/x", refresh=True)) == 0
    assert capsys.readouterr().out == "old\n"
    assert persistent.get("A/x", "password") is None
    assert persistent.pending_replace("A/x", "password")["candidate"] == "new"


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

    class Backend:
        @staticmethod
        def edit_password(_kpdb, _entry, password):
            writes.append(password)
            return True, "updated"

    monkeypatch.setattr(service, "get_cache", lambda: persistent)
    svc = _rotation_service(Backend(), monkeypatch)
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
        rotator = threading.Thread(target=rotate)
        rotator.start()
        time.sleep(0.05)
    rotator.join(timeout=5)

    assert result == {
        "ok": False,
        "error": "Password mutation expired before the backend write",
    }
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
    from cryptography.fernet import Fernet

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
