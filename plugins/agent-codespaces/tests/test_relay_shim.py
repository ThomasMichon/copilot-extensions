"""Tests for the codespace relay shim: per-codespace token + scope broker (#44)."""

from __future__ import annotations

import socket
import stat
import os
import base64
import gzip
import subprocess
import sys
import threading
import re

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
    def test_enables_ado_rest_azure_gated_by_codespace_token(self, isolated_tokens):
        from agent_codespaces.relay_provider import register_relay
        from credential_relay import RelayBuilder

        b = RelayBuilder()
        register_relay(b)
        srv = b.build()

        # An az-login source is present, but the default allowlist is just the
        # public ADO REST + Azure Storage resources -- not a wildcard broker.
        az = [s for s in srv.sources if s.name == "az-login"]
        assert len(az) == 1
        assert az[0]._is_allowed("499b84ac-1321-427f-aa17-267ca6975798") is True
        assert (
            az[0]._is_allowed("499b84ac-1321-427f-aa17-267ca6975798/.default")
            is True
        )
        assert az[0]._is_allowed("https://storage.azure.com/.default") is True
        assert az[0]._is_allowed("https://graph.microsoft.com/.default") is False

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

    def test_additional_azure_resources_are_config_opt_in(
        self, isolated_tokens, monkeypatch
    ):
        """Config may add exact Azure resources; defaults stay allowed."""
        from agent_codespaces import config as cfg
        from agent_codespaces.relay_provider import register_relay
        from credential_relay import RelayBuilder

        merged = cfg.CodespacesConfig()
        merged.credentials.sources["az-login"] = cfg.CredentialSourceConfig(
            enabled=True,
            allowed_resources=["https://graph.microsoft.com/"],
        )
        monkeypatch.setattr(cfg, "load_merged_config", lambda: merged)

        b = RelayBuilder()
        register_relay(b)
        srv = b.build()
        az = [s for s in srv.sources if s.name == "az-login"][0]

        assert az._is_allowed("499b84ac-1321-427f-aa17-267ca6975798") is True
        assert az._is_allowed("https://storage.azure.com/.default") is True
        assert az._is_allowed("https://graph.microsoft.com/.default") is True

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
        cmd = build_provision_command()
        assert "/etc/profile.d/10-codespaces-noninteractive-git.sh" in cmd
        assert "sudo tee" in cmd
        assert "env-loginInteractiveShell.json" in cmd
        # The hardening is wrapped so a sudo failure cannot abort provisioning.
        assert ") || true" in cmd
        # GIT_TERMINAL_PROMPT=0 rides in one of the base64 payloads.
        assert any(
            "GIT_TERMINAL_PROMPT=0" in payload
            for payload in _decoded_chunked_payloads(cmd)
        )

    def test_provision_scrubs_stale_azure_artifacts_npm_tokens(self):
        """A baked ~/.npmrc token for an Azure Artifacts feed is removed so npm
        flows re-borrow instead of trusting a stale file after resume (#184)."""
        cmd = build_provision_command()
        decoded = "\n".join(_decoded_chunked_payloads(cmd))

        assert ".npmrc" in decoded
        assert "_authtoken" in decoded
        assert "pkgs.dev.azure.com" in decoded
        assert ".visualstudio.com" in decoded

    def test_relay_client_has_scoped_azure_branch(self):
        client = asset_text("ado-auth-helper-relay")
        assert 'SCOPE="${2:-}"' in client
        assert 'HELPER_NAME="${LC_GIT_CREDENTIAL_RELAY_HELPER:-}"' in client
        assert 'RELAY_TOKEN="${LC_GIT_CREDENTIAL_RELAY_TOKEN:-}"' in client
        # Scoped get-access-token routes to the gated get-azure-token action.
        assert "get-azure-token" in client
        assert "scope=" in client
        assert "auth=" in client

    def test_relay_client_defaults_unscoped_azure_helper_to_ado_resource(self):
        client = asset_text("ado-auth-helper-relay")
        assert 'ADO_REST_RESOURCE="499b84ac-1321-427f-aa17-267ca6975798"' in client
        assert '[ "$HELPER_NAME" = "azure-auth-helper" ]' in client
        assert 'SCOPE="$ADO_REST_RESOURCE"' in client

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
        the on-device cache fallback can run."""
        client = asset_text("ado-auth-helper-relay")
        assert 'elif [ "$ACTION" = "get-access-token" ] || [ "$ACTION" = "get" ]' in client
        # The python treats an empty/invalid port as "no relay" -> cache only.
        assert "if not port:" in client

    def test_relay_client_caches_git_credentials_for_offline_fallback(self):
        """Git credential `get` uses the same short-TTL fallback cache."""
        client = asset_text("ado-auth-helper-relay")
        assert 'elif [ "$ACTION" = "get-access-token" ] || [ "$ACTION" = "get" ]' in client
        assert "python3 -c '" in client
        assert 'python3 - "$RELAY_PORT" "$RELAY_TOKEN_CACHE_DIR"' not in client
        assert 'keymat = "%s|%s|%s"' in client
        assert '".gitcred"' in client
        assert "served git credential from short-TTL cache" in client
        assert "os.umask(0o177)" in client

    def test_wrapper_routes_dead_relay_cache_backed_actions_to_cache(self):
        """A relay-launched headless helper call still reaches the relay client
        after the live port drops for both git creds and bare tokens."""
        wrapper = asset_text("ado-auth-helper-wrapper")
        assert "isCacheBackedAction" in wrapper
        assert 'action === "get" || action === "get-access-token"' in wrapper
        assert "env && isCacheBackedAction() && isExecutable(RELAY_CLIENT)" in wrapper
        assert "return { port: \"\", token: \"\", adoHost: \"\" }" in wrapper
        assert "Plain VS Code shells with no relay env keep" in wrapper
        assert "LC_GIT_CREDENTIAL_RELAY" in wrapper
        assert "short-TTL on-CodeSpace cache" in wrapper

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
        # The relay client can distinguish ado-auth-helper from azure-auth-helper.
        assert "LC_GIT_CREDENTIAL_RELAY_HELPER" in wrapper
        assert "path.basename(process.argv[1]" in wrapper


def _git_cache_python() -> str:
    client = asset_text("ado-auth-helper-relay")
    marker = "python3 -c '\n"
    start = client.index(marker) + len(marker)
    end = client.index('\n\' "$RELAY_PORT" "$RELAY_TOKEN_CACHE_DIR"', start)
    return client[start:end]


def _decoded_chunked_payloads(cmd: str) -> list[str]:
    payloads = []
    block_re = re.compile(
        r'_f="\$HOME/[^"]+"; : > "\$_f";\n(?P<body>.*?)\nbase64 -d "\$_f"',
        re.S,
    )
    for block in block_re.finditer(cmd):
        chunks = re.findall(r"printf %s '([^']+)' >> \"\$_f\"", block.group("body"))
        payloads.append(
            gzip.decompress(base64.b64decode("".join(chunks))).decode("utf-8")
        )
    return payloads


class _OneShotRelay:
    def __init__(self, response: str) -> None:
        self.response = response.encode("utf-8")
        self.request = b""
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self.port = 0

    def __enter__(self):
        self._thread.start()
        if not self._ready.wait(timeout=5):  # pragma: no cover
            raise RuntimeError("relay did not start")
        return self

    def __exit__(self, *_exc):
        self._thread.join(timeout=5)

    def _serve(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            self.port = sock.getsockname()[1]
            self._ready.set()
            conn, _addr = sock.accept()
            with conn:
                while b"\n\n" not in self.request:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    self.request += chunk
                conn.sendall(self.response)


def _run_git_cache(cache_dir, port: int, ttl: int, request: str):
    return subprocess.run(
        [sys.executable, "-c", _git_cache_python(), str(port), str(cache_dir), str(ttl)],
        input=request,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


class TestGitCredentialCache:
    REQUEST = "protocol=https\nhost=github.com\nusername=alice\n\n"
    RESPONSE = "username=alice\npassword=fresh-token\n\n"

    def test_write_on_success_and_0600(self, tmp_path):
        cache_dir = tmp_path / "cache"
        with _OneShotRelay(self.RESPONSE) as relay:
            result = _run_git_cache(cache_dir, relay.port, 1500, self.REQUEST)

        assert result.returncode == 0
        assert result.stdout == self.RESPONSE
        assert relay.request.decode("utf-8") == self.REQUEST
        files = list(cache_dir.glob("*.gitcred"))
        assert len(files) == 1
        if os.name != "nt":
            assert stat.S_IMODE(files[0].stat().st_mode) == 0o600

    def test_piped_git_protocol_stdin_is_forwarded_to_relay(self, tmp_path):
        """Regression: the helper program must be passed with `python3 -c` so
        sys.stdin remains the git-credential protocol piped into `get`."""
        request = "protocol=https\nhost=example.com\n\n"
        response = "username=agent\npassword=relay-token\n\n"
        with _OneShotRelay(response) as relay:
            result = _run_git_cache(tmp_path / "cache", relay.port, 1500, request)

        assert result.returncode == 0
        assert result.stdout == response
        assert relay.request.decode("utf-8") == request

    def test_serves_from_cache_when_relay_down(self, tmp_path):
        cache_dir = tmp_path / "cache"
        with _OneShotRelay(self.RESPONSE) as relay:
            assert _run_git_cache(cache_dir, relay.port, 1500, self.REQUEST).returncode == 0

        result = _run_git_cache(cache_dir, 0, 1500, self.REQUEST)

        assert result.returncode == 0
        assert result.stdout == self.RESPONSE
        assert "served git credential from short-TTL cache" in result.stderr

    def test_never_serves_past_ttl(self, tmp_path):
        cache_dir = tmp_path / "cache"
        with _OneShotRelay(self.RESPONSE) as relay:
            assert _run_git_cache(cache_dir, relay.port, 1, self.REQUEST).returncode == 0
        cache_file = next(cache_dir.glob("*.gitcred"))
        lines = cache_file.read_text(encoding="utf-8").splitlines(keepends=True)
        cache_file.write_text("1\n" + "".join(lines[1:]), encoding="utf-8")

        result = _run_git_cache(cache_dir, 0, 1, self.REQUEST)

        assert result.returncode == 1
        assert result.stdout == ""

    def test_relay_preferred_over_cache(self, tmp_path):
        cache_dir = tmp_path / "cache"
        with _OneShotRelay("username=alice\npassword=old-token\n\n") as relay:
            assert _run_git_cache(cache_dir, relay.port, 1500, self.REQUEST).returncode == 0

        with _OneShotRelay(self.RESPONSE) as relay:
            result = _run_git_cache(cache_dir, relay.port, 1500, self.REQUEST)

        assert result.returncode == 0
        assert result.stdout == self.RESPONSE
        assert "fresh-token" in next(cache_dir.glob("*.gitcred")).read_text(encoding="utf-8")
