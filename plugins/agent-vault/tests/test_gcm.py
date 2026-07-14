"""Tests for the core GCM git-credential delegation."""

from __future__ import annotations

import pytest

from agent_vault import extensions as ext
from agent_vault import gcm
from agent_vault.extensions import ExtensionRegistry
from agent_vault.service import VaultService


@pytest.fixture
def empty_registry():
    reg = ExtensionRegistry()
    reg._loaded = True
    ext._REGISTRY = reg
    yield reg
    ext.reset_registry()


# ---------------------------------------------------------------------------
# allowlist + host normalization
# ---------------------------------------------------------------------------


def test_normalize_host_strips_default_port():
    assert gcm.normalize_host("GitHub.com:443") == "github.com"
    assert gcm.normalize_host("  Example.COM ") == "example.com"


def test_default_allowlist(monkeypatch):
    monkeypatch.delenv(gcm.GCM_HOSTS_ENV, raising=False)
    assert gcm.is_gcm_allowed("github.com")
    assert gcm.is_gcm_allowed("dev.azure.com")
    assert gcm.is_gcm_allowed("myorg.visualstudio.com")  # glob
    assert not gcm.is_gcm_allowed("example.com")


def test_allowlist_env_override(monkeypatch):
    monkeypatch.setenv(gcm.GCM_HOSTS_ENV, "example.com *.internal")
    assert gcm.is_gcm_allowed("example.com")
    assert gcm.is_gcm_allowed("host.internal")
    assert not gcm.is_gcm_allowed("github.com")


def test_empty_allowlist_disables(monkeypatch):
    monkeypatch.setenv(gcm.GCM_HOSTS_ENV, "")
    assert not gcm.is_gcm_allowed("github.com")


# ---------------------------------------------------------------------------
# credential-output parsing
# ---------------------------------------------------------------------------


def test_parse_credential_output_valid():
    out = "protocol=https\nhost=github.com\nusername=x\npassword=y\n"
    parsed = gcm._parse_credential_output(out)
    assert parsed == {"ok": True, "protocol": "https", "host": "github.com",
                      "username": "x", "password": "y"}


def test_parse_credential_output_missing_password():
    assert gcm._parse_credential_output("username=x\n") is None


def test_parse_credential_output_empty():
    assert gcm._parse_credential_output("") is None


# ---------------------------------------------------------------------------
# the action
# ---------------------------------------------------------------------------


def test_action_requires_host():
    assert gcm.git_credential_action({"host": ""})["ok"] is False


def test_action_rejects_non_allowlisted(monkeypatch):
    monkeypatch.setenv(gcm.GCM_HOSTS_ENV, "github.com")
    resp = gcm.git_credential_action({"host": "evil.example.com"})
    assert resp["ok"] is False
    assert "allowlist" in resp["error"].lower()


def test_action_returns_filled_credentials(monkeypatch):
    monkeypatch.setenv(gcm.GCM_HOSTS_ENV, "github.com")
    monkeypatch.setattr(gcm, "git_credential_fill",
                        lambda *a, **k: {"ok": True, "username": "x", "password": "y"})
    resp = gcm.git_credential_action({"host": "github.com", "protocol": "https"})
    assert resp == {"ok": True, "username": "x", "password": "y"}


def test_action_reports_gcm_miss(monkeypatch):
    monkeypatch.setenv(gcm.GCM_HOSTS_ENV, "github.com")
    monkeypatch.setattr(gcm, "git_credential_fill", lambda *a, **k: None)
    resp = gcm.git_credential_action({"host": "github.com"})
    assert resp["ok"] is False
    assert "no credentials" in resp["error"].lower()


def test_action_forwards_allow_prompt(monkeypatch):
    monkeypatch.setenv(gcm.GCM_HOSTS_ENV, "github.com")
    captured = {}

    def fake_fill(protocol, host, path, username, allow_prompt=True):
        captured["allow_prompt"] = allow_prompt
        return {"ok": True, "username": "x", "password": "y"}

    monkeypatch.setattr(gcm, "git_credential_fill", fake_fill)
    gcm.git_credential_action({"host": "github.com", "allow_prompt": False})
    assert captured["allow_prompt"] is False


# ---------------------------------------------------------------------------
# first-party daemon action dispatch
# ---------------------------------------------------------------------------


def test_daemon_dispatches_git_credential(empty_registry, monkeypatch):
    svc = VaultService()
    captured = {}

    def fake_action(request):
        captured.update(request)
        return {"ok": True, "username": "x", "password": "y"}

    monkeypatch.setattr("agent_vault.service.git_credential_action", fake_action)
    resp = svc.handle_request({"action": "git-credential", "host": "github.com"})
    assert resp["ok"] is True
    # git-credential is independent of the vault lock: no unlock needed.
    assert captured["host"] == "github.com"
    # allow_prompt is threaded from the request context (fail-fast default False).
    assert captured["allow_prompt"] is False


def test_daemon_git_credential_does_not_unlock(empty_registry, monkeypatch):
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: False)
    monkeypatch.setattr("agent_vault.service.prompt_password",
                        lambda _m: (_ for _ in ()).throw(AssertionError("no prompt")))
    monkeypatch.setattr("agent_vault.service.git_credential_action",
                        lambda request: {"ok": True, "username": "x", "password": "y"})
    resp = svc.handle_request({"action": "git-credential", "host": "github.com"})
    assert resp["ok"] is True
