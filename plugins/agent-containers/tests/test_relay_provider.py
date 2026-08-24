"""Tests for the container relay profile + register_relay (dotfiles #892 Inc 2)."""

from __future__ import annotations

import json
from unittest.mock import patch

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
