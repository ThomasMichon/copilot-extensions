"""Tests for the fail-fast unlock default and opt-in prompt."""

from __future__ import annotations

import pytest

from agent_vault import extensions as ext
from agent_vault.extensions import ExtensionRegistry
from agent_vault.service import VaultService


@pytest.fixture
def empty_registry():
    """Install a fresh, empty, pre-loaded registry (no ambient providers)."""
    reg = ExtensionRegistry()
    reg._loaded = True
    ext._REGISTRY = reg
    yield reg
    ext.reset_registry()


@pytest.fixture
def kpdb(tmp_path):
    p = tmp_path / "vault.kdbx"
    p.write_text("x", encoding="utf-8")
    return str(p)


def _no_prompt(monkeypatch):
    import agent_vault.service as service_mod

    def _fail(_msg):
        raise AssertionError("interactive prompt must not run in fail-fast mode")

    monkeypatch.setattr(service_mod, "prompt_password", _fail)


# ---------------------------------------------------------------------------
# Fail-fast default
# ---------------------------------------------------------------------------


def test_ensure_unlocked_failfast_by_default(empty_registry, monkeypatch, kpdb):
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: False)
    _no_prompt(monkeypatch)
    # No allow_prompt on the context and none passed -> fail-fast, no prompt.
    assert svc.ensure_unlocked(kpdb) is False
    assert "agent-vault unlock" in (svc._last_error(kpdb) or "")


def test_get_on_locked_returns_actionable_needs_unlock(empty_registry, monkeypatch, kpdb):
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: False)
    _no_prompt(monkeypatch)
    resp = svc.handle_request({"action": "get", "entry": "x", "kpdb": kpdb})
    assert resp["ok"] is False
    assert resp["needs_unlock"] is True
    assert "agent-vault unlock" in resp["error"]


def test_search_on_locked_fails_fast(empty_registry, monkeypatch, kpdb):
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: False)
    _no_prompt(monkeypatch)
    resp = svc.handle_request({"action": "search", "query": "x", "kpdb": kpdb})
    assert resp["ok"] is False
    assert resp["needs_unlock"] is True


# ---------------------------------------------------------------------------
# Opt-in prompt
# ---------------------------------------------------------------------------


def test_unlock_with_prompt_opts_into_prompt(empty_registry, monkeypatch, kpdb):
    svc = VaultService()
    stored: dict = {}
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: db in stored)
    monkeypatch.setattr(svc.cli, "verify_password", lambda db, pw: pw == "pw")
    monkeypatch.setattr(svc.cli, "set_password", lambda db, pw: stored.__setitem__(db, pw))

    import agent_vault.service as service_mod

    monkeypatch.setattr(service_mod, "prompt_password", lambda _msg: "pw")

    resp = svc.handle_request({"action": "unlock", "prompt": True, "kpdb": kpdb})
    assert resp["ok"] is True
    assert stored[kpdb] == "pw"


def test_explicit_allow_prompt_true_runs_prompt(empty_registry, monkeypatch, kpdb):
    svc = VaultService()
    stored: dict = {}
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: db in stored)
    monkeypatch.setattr(svc.cli, "verify_password", lambda db, pw: pw == "pw")
    monkeypatch.setattr(svc.cli, "set_password", lambda db, pw: stored.__setitem__(db, pw))

    import agent_vault.service as service_mod

    monkeypatch.setattr(service_mod, "prompt_password", lambda _msg: "pw")

    assert svc.ensure_unlocked(kpdb, allow_prompt=True) is True
    assert stored[kpdb] == "pw"


# ---------------------------------------------------------------------------
# Providers still run under fail-fast (inline resolution)
# ---------------------------------------------------------------------------


def test_provider_resolves_inline_even_when_failfast(empty_registry, monkeypatch, kpdb):
    svc = VaultService()
    stored: dict = {}
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: db in stored)
    monkeypatch.setattr(svc.cli, "verify_password", lambda db, pw: pw == "broker-pw")
    monkeypatch.setattr(svc.cli, "set_password", lambda db, pw: stored.__setitem__(db, pw))
    _no_prompt(monkeypatch)  # must resolve via provider, never prompt

    empty_registry.register_unlock_provider(lambda ctx: "broker-pw", name="broker")

    # allow_prompt=False (fail-fast), yet the provider still unlocks inline.
    assert svc.ensure_unlocked(kpdb, allow_prompt=False) is True
    assert stored[kpdb] == "broker-pw"
