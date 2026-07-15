"""Tests for the persistent on-disk credential cache and its CLI wiring."""

from __future__ import annotations

import pytest

from agent_vault import cache as cache_mod
from agent_vault import cli
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
        self.prompt = kw.get("prompt", False)
        self.refresh = kw.get("refresh", False)
        self.cache_only = kw.get("cache_only", False)


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
