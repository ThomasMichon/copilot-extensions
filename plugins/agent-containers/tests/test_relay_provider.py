"""Tests for the container relay profile + register_relay (dotfiles #892 Inc 2)."""

from __future__ import annotations

import json
import os
import stat
from unittest.mock import patch

import pytest

from agent_containers import private_state, relay_provider
from agent_containers.__main__ import main
from agent_containers.relay_provider import register_relay, relay_profile


class _FakeBuilder:
    def __init__(self):
        self.sources, self.azure, self.gated, self.validator = [], None, None, None

    def add_source(self, s):
        self.sources.append(getattr(s, "name", type(s).__name__))

    def set_port(self, p):
        pass

    def set_ado_host(self, h):
        pass

    def allow_azure_resources(self, r):
        self.azure = list(r)

    def require_token(self, actions, validator):
        self.gated, self.validator = list(actions), validator


def test_relay_profile_shape():
    prof = relay_profile()
    assert set(prof["sources"]) == {"git-credential", "gh-auth"}
    assert prof["gated_actions"] == ["get-azure-token"]
    assert prof["token_store"].endswith("relay-tokens.json")


def test_register_relay_applies_profile_data():
    b = _FakeBuilder()
    register_relay(b)
    prof = relay_profile()
    assert len(b.sources) == 2
    assert b.azure == prof["azure_resources"]
    assert b.gated == ["get-azure-token"]
    assert callable(b.validator)


def test_relay_profile_cli_emits_json(capsys):
    fake = {"sources": ["git-credential", "gh-auth"], "gated_actions": ["get-azure-token"],
            "azure_resources": ["*"], "port": None, "ado_host": None,
            "token_store": "/x/relay-tokens.json"}
    with patch("agent_containers.relay_provider.relay_profile", return_value=fake):
        rc = main(["relay-profile"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == fake


def test_relay_token_file_is_owner_only_under_open_umask(monkeypatch, tmp_path):
    token_file = tmp_path / "state" / "relay-tokens.json"
    monkeypatch.setattr(relay_provider, "_TOKENS_FILE", token_file)
    monkeypatch.setattr(relay_provider, "_LEGACY_TOKENS_FILE", token_file)
    fsync_calls = []
    monkeypatch.setattr(
        private_state,
        "fsync_directory",
        lambda path: fsync_calls.append(path),
    )
    previous = os.umask(0)
    try:
        relay_provider._write_tokens({"example": "secret"})
    finally:
        os.umask(previous)

    assert json.loads(token_file.read_text(encoding="utf-8")) == {
        "example": "secret"
    }
    if os.name != "nt":
        assert stat.S_IMODE(token_file.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert fsync_calls == [token_file.parent]


def test_legacy_token_store_permissions_repaired_on_read_without_rewrite(
    monkeypatch,
    tmp_path,
):
    current = tmp_path / "relocated" / "relay-tokens.json"
    legacy = tmp_path / "legacy" / "relay-tokens.json"
    legacy.parent.mkdir(mode=0o777)
    legacy.parent.chmod(0o777)
    legacy.write_text('{"example":"existing"}', encoding="utf-8")
    legacy.chmod(0o644)
    monkeypatch.setattr(relay_provider, "_TOKENS_FILE", current)
    monkeypatch.setattr(relay_provider, "_LEGACY_TOKENS_FILE", legacy)
    monkeypatch.setattr(
        relay_provider,
        "atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("read must not rewrite token JSON"),
    )

    assert relay_provider._read_tokens() == {"example": "existing"}
    assert not current.exists()
    if os.name != "nt":
        assert stat.S_IMODE(legacy.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(legacy.stat().st_mode) == 0o600
