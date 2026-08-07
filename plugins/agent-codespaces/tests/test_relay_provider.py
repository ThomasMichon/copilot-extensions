"""Tests for the codespace relay profile + register_relay (dotfiles #892 Inc 2)."""

from __future__ import annotations

import json
from unittest.mock import patch

from agent_codespaces.relay_provider import relay_profile, register_relay
from agent_codespaces.__main__ import main


class _FakeBuilder:
    def __init__(self):
        self.sources, self.port, self.ado_host = [], None, None
        self.azure, self.gated, self.validator = None, None, None

    def add_source(self, s):
        self.sources.append(getattr(s, "name", type(s).__name__))

    def set_port(self, p):
        if p is not None:
            self.port = p

    def set_ado_host(self, h):
        if h:
            self.ado_host = h

    def allow_azure_resources(self, r):
        self.azure = list(r)

    def require_token(self, actions, validator):
        self.gated, self.validator = list(actions), validator


def test_relay_profile_shape():
    prof = relay_profile()
    assert prof["sources"] == ["git-credential"]
    assert prof["gated_actions"] == ["get-azure-token"]
    assert prof["token_store"].endswith("relay-tokens.json")
    assert isinstance(prof["azure_resources"], list) and prof["azure_resources"]


def test_register_relay_applies_profile_data():
    b = _FakeBuilder()
    register_relay(b)
    prof = relay_profile()
    assert "git-credential" in b.sources
    assert b.azure == prof["azure_resources"]
    assert b.gated == ["get-azure-token"]
    assert callable(b.validator)  # the in-process fallback validator


def test_relay_profile_cli_emits_json(capsys):
    fake = {"sources": ["git-credential"], "gated_actions": ["get-azure-token"],
            "azure_resources": ["r"], "port": None, "ado_host": None,
            "token_store": "/x/relay-tokens.json"}
    with patch("agent_codespaces.relay_provider.relay_profile", return_value=fake):
        rc = main(["relay-profile"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == fake
