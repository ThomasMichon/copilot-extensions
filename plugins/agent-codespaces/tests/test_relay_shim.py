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

    def test_provision_hardens_headless_boot_git(self):
        """#18: provision persists GIT_TERMINAL_PROMPT=0 for login shells and
        invalidates the stale userEnvProbe cache, best-effort (never fails the
        whole provision)."""
        import base64 as _b64m
        import re as _re
        cmd = build_provision_command()
        assert "/etc/profile.d/10-codespaces-noninteractive-git.sh" in cmd
        assert "sudo tee" in cmd
        assert "env-loginInteractiveShell.json" in cmd
        # The hardening is wrapped so a sudo failure cannot abort provisioning.
        assert ") || true" in cmd
        # GIT_TERMINAL_PROMPT=0 rides in one of the base64 payloads.
        blobs = _re.findall(r"printf %s (\S+) \| base64 -d", cmd)
        assert any(
            "GIT_TERMINAL_PROMPT=0" in _b64m.b64decode(b).decode("utf-8")
            for b in blobs
        )

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
        # The discovered host becomes the request's host field and is passed to
        # python as the direct-mode key material.
        assert '"host=" + keymat' in client
        assert '_KEYMAT="$ADO_HOST"' in client

    def test_relay_client_caches_bare_tokens_for_offline_fallback(self):
        """A recently-served bare token is cached (0600) and served as a fallback
        when the relay is briefly unreachable, so an in-flight rush/npm/nuget
        fetch survives a relay-host sleep / tunnel drop (#145/#617)."""
        client = asset_text("ado-auth-helper-relay")
        # Cache location + conservative env-overridable TTL, 0600 files.
        assert 'RELAY_TOKEN_CACHE_DIR="$HOME/.agent-bridge/token-cache"' in client
        assert (
            'RELAY_TOKEN_CACHE_TTL="${LC_GIT_CREDENTIAL_RELAY_CACHE_TTL:-1500}"'
            in client
        )
        assert "os.umask(0o177)" in client
        # A live relay is preferred and refreshes the cache; the cache is only a
        # fallback, and an expired entry is never served.
        assert "_write_cache(tok)" in client
        assert "_read_cache()" in client
        assert "expired" in client
        # Both the scoped (azure) and direct (access) bare-token modes are cached.
        assert '_MODE="azure"' in client and '_MODE="access"' in client

    def test_relay_client_offline_bare_token_falls_through_to_cache(self):
        """With no live relay, a bare-token request falls through (empty port) so
        the on-device cache fallback can run, instead of failing like git `get`."""
        client = asset_text("ado-auth-helper-relay")
        assert 'elif [ "$ACTION" = "get-access-token" ]; then' in client
        # The python treats an empty/invalid port as "no relay" -> cache only.
        assert "if not port:" in client

    def test_relay_client_discovers_live_port_from_mappings(self):
        """The relay client resolves a *live* relay via the port-mapping files
        the launch prelude publishes -- so a dispatched tool shell that never
        inherited LC_GIT_CREDENTIAL_RELAY (or inherited a dead port) still finds
        an active channel back to the caller (dotfiles #489/#187/#19)."""
        client = asset_text("ado-auth-helper-relay")
        assert 'RELAY_PORTS_DIR="$HOME/.agent-bridge/relay-ports"' in client
        assert "_relay_live()" in client
        # The inherited env port is re-validated for liveness, not trusted.
        assert '! _relay_live "$RELAY_PORT"' in client
        # Discovery enumerates mappings and prunes a dead channel's stale file.
        assert "glob.glob" in client
        assert "os.unlink" in client
        # Legacy default-port probe remains as the final fallback.
        assert "DEFAULT_RELAY_PORT" in client

    def test_wrapper_discovers_live_port_from_mappings(self):
        """The Node wrapper mirrors the relay client's discovery so it picks the
        relay path (vs the VS Code helper) whenever a live channel exists."""
        wrapper = asset_text("ado-auth-helper-wrapper")
        assert "RELAY_PORTS_DIR" in wrapper
        assert "discoverFromMappings" in wrapper
        assert "resolveRelay" in wrapper
        assert "unlinkSync" in wrapper  # prune a dead channel's stale mapping
        # A discovered token/host is restored into the relay client's env.
        assert "LC_GIT_CREDENTIAL_RELAY_TOKEN" in wrapper
