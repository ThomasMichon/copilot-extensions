"""Tests for the codespace relay shim: per-codespace token + scope broker (#44)."""

from __future__ import annotations

import pytest

from agent_codespaces import relay_token
from agent_codespaces.codespace_assets import asset_text, build_provision_command


@pytest.fixture
def isolated_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(relay_token, "_TOKENS_FILE", tmp_path / "relay-tokens.json")
    return tmp_path


class TestRelayToken:
    def test_mint_is_stable_per_codespace(self, isolated_tokens):
        a = relay_token.token_for("cs-1")
        b = relay_token.token_for("cs-1")
        assert a == b and len(a) >= 32  # reused, not re-minted

    def test_distinct_codespaces_distinct_tokens(self, isolated_tokens):
        assert relay_token.token_for("cs-1") != relay_token.token_for("cs-2")

    def test_validate_accepts_minted_rejects_others(self, isolated_tokens):
        tok = relay_token.token_for("cs-1")
        assert relay_token.validate(tok) is True
        assert relay_token.validate("nope") is False
        assert relay_token.validate("") is False

    def test_revoke_invalidates(self, isolated_tokens):
        tok = relay_token.token_for("cs-1")
        relay_token.revoke("cs-1")
        assert relay_token.validate(tok) is False


class TestRegisterRelay:
    def test_enables_any_scope_azure_gated_by_codespace_token(self, isolated_tokens):
        from agent_codespaces.relay_provider import register_relay
        from credential_relay import RelayBuilder

        b = RelayBuilder()
        register_relay(b)
        srv = b.build()

        # An az-login source is present and any-scope is allowed.
        az = [s for s in srv.sources if s.name == "az-login"]
        assert len(az) == 1
        assert az[0]._is_allowed("https://storage.azure.com/.default") is True

        # get-azure-token is gated, and a minted per-codespace token passes.
        assert "get-azure-token" in srv.token_required_actions
        tok = relay_token.token_for("cs-x")
        assert srv.token_validator(tok) is True
        assert srv.token_validator("wrong") is False

    def test_coexists_with_container_token_validator(self, isolated_tokens):
        """Both providers gate get-azure-token; either provider's token works."""
        from agent_codespaces.relay_provider import register_relay
        from credential_relay import RelayBuilder

        b = RelayBuilder()
        # Simulate the containers provider gating with its own token store.
        b.require_token(["get-azure-token"], lambda t: t == "container-secret")
        register_relay(b)
        srv = b.build()

        assert srv.token_validator("container-secret") is True       # container
        assert srv.token_validator(relay_token.token_for("cs-y")) is True  # codespace
        assert srv.token_validator("neither") is False

    def test_sets_ado_host_from_config(self, isolated_tokens, monkeypatch):
        """A configured ado_host is plumbed to the relay so host-less
        ``get-access-token`` requests resolve a default org (#64)."""
        from agent_codespaces import config as cfg
        from agent_codespaces.relay_provider import register_relay
        from credential_relay import RelayBuilder

        merged = cfg.CodespacesConfig()
        merged.credentials.ado_host = "example.visualstudio.com"
        monkeypatch.setattr(cfg, "load_merged_config", lambda: merged)

        b = RelayBuilder()
        register_relay(b)
        srv = b.build()

        assert srv.ado_host == "example.visualstudio.com"

    def test_no_ado_host_when_unconfigured(self, isolated_tokens, monkeypatch):
        """Unset ado_host leaves the relay default (None) -- never hardcoded."""
        from agent_codespaces import config as cfg
        from agent_codespaces.relay_provider import register_relay
        from credential_relay import RelayBuilder

        merged = cfg.CodespacesConfig()  # ado_host defaults to None
        monkeypatch.setattr(cfg, "load_merged_config", lambda: merged)
        monkeypatch.delenv("CODESPACES_ADO_HOST", raising=False)

        b = RelayBuilder()
        register_relay(b)
        srv = b.build()

        assert srv.ado_host is None


class TestProvisioningAndClient:
    def test_provision_symlinks_helpers_onto_path(self):
        cmd = build_provision_command()
        # Bare-name helpers symlinked into ~/.local/bin (on PATH).
        assert 'ln -sf "$HOME/$_n" "$HOME/.local/bin/$_n"' in cmd

    def test_relay_client_has_scoped_azure_branch(self):
        client = asset_text("ado-auth-helper-relay")
        assert 'SCOPE="${2:-}"' in client
        assert 'RELAY_TOKEN="${LC_GIT_CREDENTIAL_RELAY_TOKEN:-}"' in client
        # Scoped get-access-token routes to the gated get-azure-token action.
        assert "get-azure-token" in client
        assert "scope=" in client
        assert "auth=" in client

    def test_relay_client_discovers_ado_host_for_bare_token(self):
        """The host-less get-access-token path supplies an ADO host so the
        relay can resolve which org to mint a token for (#64)."""
        client = asset_text("ado-auth-helper-relay")
        # Explicit env override, then git-remote discovery (never hardcoded).
        assert 'ADO_HOST="${LC_GIT_CREDENTIAL_RELAY_ADO_HOST:-}"' in client
        assert "remote -v" in client
        assert "visualstudio" in client and "azure.com" in client
        # The discovered host is sent as a request field and passed to python.
        assert "'host=' + host" in client
        assert '"$RELAY_PORT" "$ADO_HOST"' in client
