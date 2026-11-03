"""Tests for the credential relay server and sources."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from credential_relay.server import (
    CredentialRelayServer,
    RelayPolicy,
)
from credential_relay.sources import CredentialSource
from credential_relay.sources.gh_auth import GhAuthSource
from credential_relay.sources.git_credential import (
    GitCredentialSource,
)


# ---------------------------------------------------------------------------
# Relay Policy Tests
# ---------------------------------------------------------------------------
class TestRelayPolicy:

    def test_default_policy_allows_known_actions(self) -> None:
        policy = RelayPolicy()
        assert policy.check("get", {"host": "github.com"}) is None
        assert policy.check("store", {"host": "github.com"}) is None
        assert policy.check("erase", {"host": "github.com"}) is None
        assert policy.check("get-github-token", {}) is None

    def test_unknown_action_rejected(self) -> None:
        policy = RelayPolicy()
        result = policy.check("exec-arbitrary", {"host": "evil.com"})
        assert result is not None
        assert "not in allowed list" in result

    def test_host_allowlist_blocks_unlisted(self) -> None:
        policy = RelayPolicy(allowed_hosts=["github.com", "*.github.com"])
        assert policy.check("get", {"host": "github.com"}) is None
        assert policy.check("get", {"host": "api.github.com"}) is None
        result = policy.check("get", {"host": "evil.com"})
        assert result is not None
        assert "not in allowed list" in result

    def test_empty_host_allowlist_allows_all(self) -> None:
        policy = RelayPolicy(allowed_hosts=[])
        assert policy.check("get", {"host": "anything.example.com"}) is None

    def test_hostless_token_actions_skip_host_allowlist(self) -> None:
        """get-azure-token/get-github-token carry no host -> not host-gated."""
        policy = RelayPolicy(allowed_hosts=["dev.azure.com"])
        # A host-scoped action with a non-matching host is still rejected...
        assert policy.check("get", {"host": "github.com"}) is not None
        # ...but the dedicated hostless token actions pass regardless.
        assert policy.check("get-azure-token", {"scope": "https://storage.azure.com/.default"}) is None
        assert policy.check("get-github-token", {}) is None

    def test_host_glob_patterns(self) -> None:
        policy = RelayPolicy(allowed_hosts=["*.visualstudio.com", "dev.azure.com"])
        assert policy.check("get", {"host": "myorg.visualstudio.com"}) is None
        assert policy.check("get", {"host": "dev.azure.com"}) is None
        result = policy.check("get", {"host": "github.com"})
        assert result is not None

    def test_restricted_actions(self) -> None:
        policy = RelayPolicy(allowed_actions=frozenset({"get", "store"}))
        assert policy.check("get", {}) is None
        assert policy.check("store", {}) is None
        result = policy.check("erase", {})
        assert result is not None


# ---------------------------------------------------------------------------
# Request Parsing Tests
# ---------------------------------------------------------------------------
class TestRequestParsing:

    def setup_method(self) -> None:
        self.server = CredentialRelayServer(sources=[])

    def test_action_on_first_line(self) -> None:
        action, fields = self.server._parse_request(
            "get\nprotocol=https\nhost=github.com"
        )
        assert action == "get"
        assert fields == {"protocol": "https", "host": "github.com"}

    def test_no_action_defaults_to_get(self) -> None:
        action, fields = self.server._parse_request(
            "protocol=https\nhost=github.com"
        )
        assert action == "get"
        assert fields == {"protocol": "https", "host": "github.com"}

    def test_store_with_credentials(self) -> None:
        action, fields = self.server._parse_request(
            "store\nprotocol=https\nhost=github.com\nusername=user\npassword=tok123"
        )
        assert action == "store"
        assert fields["username"] == "user"
        assert fields["password"] == "tok123"

    def test_erase_action(self) -> None:
        action, fields = self.server._parse_request("erase\nprotocol=https\nhost=github.com")
        assert action == "erase"

    def test_get_github_token_action(self) -> None:
        action, fields = self.server._parse_request("get-github-token\nhost=github.com")
        assert action == "get-github-token"
        assert fields["host"] == "github.com"

    def test_empty_lines_ignored(self) -> None:
        action, fields = self.server._parse_request(
            "get\n\nprotocol=https\n\nhost=github.com\n"
        )
        assert fields == {"protocol": "https", "host": "github.com"}

    def test_value_with_equals(self) -> None:
        """Values containing = should be preserved."""
        action, fields = self.server._parse_request(
            "protocol=https\nhost=github.com\npassword=tok=123=abc"
        )
        assert fields["password"] == "tok=123=abc"

    def test_empty_request(self) -> None:
        action, fields = self.server._parse_request("")
        assert action == "get"
        assert fields == {}


# ---------------------------------------------------------------------------
# Ping / Serving-Liveness Tests
# ---------------------------------------------------------------------------
class TestPingAction:

    @pytest.mark.asyncio
    async def test_ping_returns_pong_without_policy_token_or_sources(self) -> None:
        source = MagicMock(spec=CredentialSource)
        source.name = "source"
        source.supports.return_value = True
        token_validator = MagicMock(return_value=False)
        server = CredentialRelayServer(
            port=0,
            sources=[source],
            policy=RelayPolicy(allowed_actions=frozenset({"get"})),
            token_validator=token_validator,
            token_required_actions=frozenset({"ping"}),
        )
        await server.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.write(b"ping\n\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b"\n\n"), timeout=1)
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

        assert response == b"pong\n\n"
        source.supports.assert_not_called()
        token_validator.assert_not_called()

    @pytest.mark.asyncio
    async def test_capabilities_advertise_username_keyed_git_cache(self) -> None:
        source = MagicMock(spec=CredentialSource)
        source.name = "source"
        server = CredentialRelayServer(
            port=0,
            sources=[source],
            policy=RelayPolicy(allowed_actions=frozenset({"get"})),
        )
        await server.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            writer.write(b"capabilities\n\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b"\n\n"), timeout=1)
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

        assert response == b"capabilities=git-credential-username-cache\n\n"
        source.supports.assert_not_called()


# ---------------------------------------------------------------------------
# Source Routing Tests
# ---------------------------------------------------------------------------
class TestSourceRouting:

    @pytest.mark.asyncio
    async def test_first_matching_source_wins(self) -> None:
        source_a = MagicMock(spec=CredentialSource)
        source_a.name = "a"
        source_a.supports.return_value = False

        source_b = MagicMock(spec=CredentialSource)
        source_b.name = "b"
        source_b.supports.return_value = True
        source_b.resolve = AsyncMock(return_value="protocol=https\nhost=x\n\n")

        server = CredentialRelayServer(sources=[source_a, source_b])
        result = await server._route_to_source("get", {"host": "x"})

        assert result == "protocol=https\nhost=x\n\n"
        source_a.supports.assert_called_once()
        source_b.supports.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_matching_source_returns_none(self) -> None:
        source = MagicMock(spec=CredentialSource)
        source.name = "a"
        source.supports.return_value = False

        server = CredentialRelayServer(sources=[source])
        result = await server._route_to_source("get", {"host": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_source_exception_falls_through(self) -> None:
        """If a source raises, routing continues to next source."""
        bad_source = MagicMock(spec=CredentialSource)
        bad_source.name = "bad"
        bad_source.supports.return_value = True
        bad_source.resolve = AsyncMock(side_effect=RuntimeError("boom"))

        good_source = MagicMock(spec=CredentialSource)
        good_source.name = "good"
        good_source.supports.return_value = True
        good_source.resolve = AsyncMock(return_value="ok\n\n")

        server = CredentialRelayServer(sources=[bad_source, good_source])
        result = await server._route_to_source("get", {"host": "x"})
        assert result == "ok\n\n"

    @pytest.mark.asyncio
    async def test_source_returns_none_falls_through(self) -> None:
        """If a source returns None, routing continues."""
        source_a = MagicMock(spec=CredentialSource)
        source_a.name = "a"
        source_a.supports.return_value = True
        source_a.resolve = AsyncMock(return_value=None)

        source_b = MagicMock(spec=CredentialSource)
        source_b.name = "b"
        source_b.supports.return_value = True
        source_b.resolve = AsyncMock(return_value="result\n\n")

        server = CredentialRelayServer(sources=[source_a, source_b])
        result = await server._route_to_source("get", {"host": "x"})
        assert result == "result\n\n"


# ---------------------------------------------------------------------------
# GitCredentialSource Tests
# ---------------------------------------------------------------------------
class TestGitCredentialSource:

    def test_supports_standard_actions(self) -> None:
        source = GitCredentialSource()
        assert source.supports("get", {})
        assert source.supports("store", {})
        assert source.supports("erase", {})
        assert source.supports("fill", {})
        assert source.supports("approve", {})
        assert source.supports("reject", {})
        assert not source.supports("get-github-token", {})

    def test_name(self) -> None:
        assert GitCredentialSource().name == "git-credential"

    def test_field_filtering(self) -> None:
        source = GitCredentialSource()
        fields = {
            "protocol": "https",
            "host": "github.com",
            "username": "user",
            "capability[]": "authtype",
            "wwwauth[]": "Basic realm=test",
        }
        filtered = source._filter_fields(fields)
        assert "protocol=https" in filtered
        assert "host=github.com" in filtered
        assert "username=user" in filtered
        assert "capability" not in filtered
        assert "wwwauth" not in filtered

    def test_cache_key(self) -> None:
        source = GitCredentialSource()
        key = source._cache_key({
            "protocol": "https",
            "host": "github.com",
            "username": "user",
        })
        assert key == ("https", "github.com", "user")

    @pytest.mark.asyncio
    async def test_bound_github_username_is_injected_before_gcm(self) -> None:
        source = GitCredentialSource(github_username="bound-user")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"protocol=https\nhost=github.com\nusername=bound-user\npassword=token123\n",
                b"",
            )
        )

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await source.resolve("get", {
                "protocol": "https", "host": "github.com",
            })

        sent = mock_proc.communicate.call_args.kwargs["input"].decode()
        assert "username=bound-user" in sent
        assert result is not None and "password=token123" in result

    @pytest.mark.asyncio
    async def test_existing_github_username_is_preserved(self) -> None:
        source = GitCredentialSource(github_username="bound-user")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"protocol=https\nhost=github.com\nusername=existing\npassword=token123\n",
                b"",
            )
        )

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            await source.resolve("get", {
                "protocol": "https", "host": "github.com", "username": "existing",
            })

        sent = mock_proc.communicate.call_args.kwargs["input"].decode()
        assert "username=existing" in sent
        assert "username=bound-user" not in sent

    @pytest.mark.asyncio
    async def test_no_profile_username_leaves_github_request_unchanged(self) -> None:
        source = GitCredentialSource()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b"protocol=https\nhost=github.com\npassword=token123\n", b"")
        )

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            await source.resolve("get", {"protocol": "https", "host": "github.com"})

        sent = mock_proc.communicate.call_args.kwargs["input"].decode()
        assert "username=" not in sent

    @pytest.mark.asyncio
    async def test_resolve_fill_calls_git(self) -> None:
        """Fill action should call git credential fill."""
        source = GitCredentialSource()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"protocol=https\nhost=github.com\nusername=user\npassword=token123\n",
                b"",
            )
        )

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec:
            result = await source.resolve("get", {
                "protocol": "https", "host": "github.com",
            })

        assert result is not None
        assert "password=token123" in result
        call_args = mock_exec.call_args[0]
        assert call_args[:3] == ("git", "credential", "fill")

    @pytest.mark.asyncio
    async def test_resolve_fill_uses_noninteractive_env(self) -> None:
        """Fill must run git with interactive prompts disabled (fail-fast)."""
        source = GitCredentialSource()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b"password=tok\n", b""),
        )

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec:
            await source.resolve("get", {
                "protocol": "https", "host": "dev.azure.com",
            })

        env = mock_exec.call_args.kwargs["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GCM_INTERACTIVE"] == "never"
        # Must preserve the rest of the environment (e.g. PATH), not replace it.
        assert "PATH" in env or "Path" in env

    @pytest.mark.asyncio
    async def test_resolve_caches_successful_fill(self) -> None:
        """Successful fill should be cached."""
        source = GitCredentialSource(cache_ttl=60.0)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"protocol=https\nhost=github.com\npassword=cached-tok\n",
                b"",
            )
        )

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec:
            # First call hits GCM
            result1 = await source.resolve("get", {
                "protocol": "https", "host": "github.com",
            })
            assert result1 is not None
            assert "cached-tok" in result1

            # Second call should use cache
            result2 = await source.resolve("get", {
                "protocol": "https", "host": "github.com",
            })
            assert result2 == result1

        # GCM should only be called once
        assert mock_exec.call_count == 1

    @pytest.mark.asyncio
    async def test_store_invalidates_cache(self) -> None:
        """Store action should invalidate cached credentials."""
        source = GitCredentialSource(cache_ttl=300.0)

        call_count = 0

        async def make_proc(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            proc.returncode = 0
            if call_count <= 2:
                # First two calls: fill + store
                proc.communicate = AsyncMock(
                    return_value=(
                        b"protocol=https\nhost=github.com\npassword=old-tok\n",
                        b"",
                    )
                )
            else:
                # Third call: fill after invalidation
                proc.communicate = AsyncMock(
                    return_value=(
                        b"protocol=https\nhost=github.com\npassword=new-tok\n",
                        b"",
                    )
                )
            return proc

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            side_effect=make_proc,
        ):
            # Fill and cache
            await source.resolve("get", {
                "protocol": "https", "host": "github.com",
            })

            # Store should invalidate
            await source.resolve("store", {
                "protocol": "https", "host": "github.com",
                "username": "user", "password": "new-tok",
            })

            # Next get should hit GCM again (not cache)
            result = await source.resolve("get", {
                "protocol": "https", "host": "github.com",
            })
            assert result is not None
            assert "new-tok" in result

    @pytest.mark.asyncio
    async def test_resolve_timeout(self) -> None:
        """Timeout should return None, not raise."""
        source = GitCredentialSource()

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        async def slow_communicate(input: bytes | None = None) -> tuple[bytes, bytes]:
            await asyncio.sleep(100)
            return (b"", b"")

        mock_proc.communicate = slow_communicate

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            # Use _run_directly to test timeout without fill's max(timeout, 60)
            result = await source._run_directly(
                "fill", "protocol=https\nhost=x\n", timeout=0.1,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_resolve_git_not_found(self) -> None:
        """FileNotFoundError should return None."""
        source = GitCredentialSource()

        with patch(
            "credential_relay.sources.git_credential"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("git not found"),
        ):
            result = await source.resolve("get", {
                "protocol": "https", "host": "github.com",
            })
            assert result is None


# ---------------------------------------------------------------------------
# GhAuthSource Tests
# ---------------------------------------------------------------------------
class TestGhAuthSource:

    def test_supports_only_github_token(self) -> None:
        source = GhAuthSource(account="user")
        assert source.supports("get-github-token", {})
        assert not source.supports("get", {"protocol": "https", "host": "github.com"})
        assert not source.supports("fill", {"protocol": "https", "host": "github.com"})
        assert not source.supports("get", {"protocol": "https", "host": "example.com"})
        assert not source.supports("store", {})

    def test_name(self) -> None:
        assert GhAuthSource().name == "gh-auth"

    @pytest.mark.asyncio
    async def test_resolve_returns_key_value(self) -> None:
        """Response should be in key=value format."""
        source = GhAuthSource(account="user")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b"gho_test_token_123\n", b"")
        )

        with patch(
            "credential_relay.sources.gh_auth"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await source.resolve(
                "get-github-token", {"host": "github.com"},
            )

        assert result is not None
        assert "token=gho_test_token_123" in result
        assert "username=user" in result
        assert "protocol=https" in result
        assert "host=github.com" in result

    @pytest.mark.asyncio
    async def test_resolve_git_get_is_declined_even_with_username(self) -> None:
        source = GhAuthSource(account="bound-user")

        with patch(
            "credential_relay.sources.gh_auth"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            result = await source.resolve(
                "get", {
                    "protocol": "https", "host": "github.com",
                    "username": "existing",
                },
            )

        assert result is None
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_git_get_without_username_returns_none(self) -> None:
        source = GhAuthSource()

        with patch(
            "credential_relay.sources.gh_auth"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            result = await source.resolve(
                "get", {"protocol": "https", "host": "github.com"},
            )

        assert result is None
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_github_token_without_account_uses_plain_gh_auth_token(self) -> None:
        source = GhAuthSource()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"gho_active\n", b""))

        with patch(
            "credential_relay.sources.gh_auth"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec:
            result = await source.resolve("get-github-token", {})

        assert result is not None
        assert "token=gho_active" in result
        assert "username=" not in result
        assert mock_exec.call_args[0] == ("gh", "auth", "token")

    @pytest.mark.asyncio
    async def test_get_github_token_without_account_pins_an_explicit_host(self) -> None:
        source = GhAuthSource()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"gho_active\n", b""))

        with patch(
            "credential_relay.sources.gh_auth"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec:
            await source.resolve("get-github-token", {"host": "github.com"})

        assert mock_exec.call_args[0] == (
            "gh", "auth", "token", "--hostname", "github.com",
        )

    @pytest.mark.asyncio
    async def test_git_get_does_not_route_to_gh_auth(self) -> None:
        git_source = MagicMock(spec=CredentialSource)
        git_source.name = "git-credential"
        git_source.supports.return_value = True
        git_source.resolve = AsyncMock(return_value=None)

        gh_source = GhAuthSource(account="bound-user")

        with patch(
            "credential_relay.sources.gh_auth"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            server = CredentialRelayServer(sources=[git_source, gh_source])
            result = await server._route_to_source(
                "get", {"protocol": "https", "host": "github.com"},
            )

        assert result is None
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_gh_not_found(self) -> None:
        """Missing gh CLI should return None."""
        source = GhAuthSource()

        with patch(
            "credential_relay.sources.gh_auth"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("gh not found"),
        ):
            result = await source.resolve("get-github-token", {})
            assert result is None

    @pytest.mark.asyncio
    async def test_resolve_gh_failure(self) -> None:
        """Non-zero exit should return None."""
        source = GhAuthSource()

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"not logged in")
        )

        with patch(
            "credential_relay.sources.gh_auth"
            ".asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await source.resolve("get-github-token", {})
            assert result is None


# ---------------------------------------------------------------------------
# Integration: Server End-to-End
# ---------------------------------------------------------------------------
class TestServerIntegration:

    @pytest.mark.asyncio
    async def test_server_start_stop(self) -> None:
        """Server should start and stop cleanly."""
        server = CredentialRelayServer(port=0, sources=[])
        await server.start()
        assert server.running
        await server.stop()
        assert not server.running

    @pytest.mark.asyncio
    async def test_server_handles_request(self) -> None:
        """Server should route a request to a source and return the response."""
        source = MagicMock(spec=CredentialSource)
        source.name = "test"
        source.supports.return_value = True
        source.resolve = AsyncMock(
            return_value="protocol=https\nhost=github.com\npassword=tok\n\n"
        )

        server = CredentialRelayServer(port=0, sources=[source])
        await server.start()

        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"protocol=https\nhost=github.com\n\n")
            await writer.drain()

            # Read response
            data = b""
            try:
                while True:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=5.0)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n\n" in data:
                        break
            except (ConnectionResetError, asyncio.TimeoutError):
                pass

            response_text = data.decode()
            assert "password=tok" in response_text
            assert server.stats.total_requests == 1

            writer.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_policy_rejection(self) -> None:
        """Requests to disallowed hosts should be rejected (no response)."""
        source = MagicMock(spec=CredentialSource)
        source.name = "test"
        source.supports.return_value = True
        source.resolve = AsyncMock(return_value="should not reach\n\n")

        policy = RelayPolicy(allowed_hosts=["github.com"])
        server = CredentialRelayServer(port=0, sources=[source], policy=policy)
        await server.start()

        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"protocol=https\nhost=evil.com\n\n")
            await writer.drain()

            # Server should close connection without sending credential data
            data = b""
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=2.0)
            except (ConnectionResetError, asyncio.TimeoutError):
                pass

            # No credential data should have been sent
            assert b"should not reach" not in data
            assert server.stats.policy_rejections == 1

            writer.close()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_stats_tracking(self) -> None:
        """Server should track request statistics."""
        source = MagicMock(spec=CredentialSource)
        source.name = "test"
        source.supports.return_value = True
        source.resolve = AsyncMock(return_value="ok=yes\n\n")

        server = CredentialRelayServer(port=0, sources=[source])
        await server.start()

        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"protocol=https\nhost=github.com\n\n")
            await writer.drain()

            # Wait for server to process
            try:
                await asyncio.wait_for(reader.read(4096), timeout=2.0)
            except (ConnectionResetError, asyncio.TimeoutError):
                pass

            # Allow server handler to finish
            await asyncio.sleep(0.1)

            assert server.stats.total_requests >= 1
            assert server.stats.start_time is not None

            writer.close()
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Fail-fast Tests
# ---------------------------------------------------------------------------
async def _exchange(server: CredentialRelayServer, request: bytes, *, timeout: float = 2.0) -> bytes:
    """Send a request to a running server and read the full response."""
    assert server._server is not None
    port = server._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = b""
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not chunk:
                break
            data += chunk
            if b"\n\n" in data:
                break
    except (ConnectionResetError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()
    # Let the server handler finish bookkeeping.
    await asyncio.sleep(0.05)
    return data


class TestFailFast:
    """Unresolved git `get` requests must return quit=1, never hang."""

    @pytest.mark.asyncio
    async def test_get_with_no_source_returns_quit(self) -> None:
        """No source -> quit=1 so git aborts instead of prompting."""
        server = CredentialRelayServer(port=0, sources=[])
        await server.start()
        try:
            data = await _exchange(
                server, b"protocol=https\nhost=your-org.visualstudio.com\n\n",
            )
            assert b"quit=1" in data
            assert server.stats.failfast_responses == 1
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_get_with_unresolving_source_returns_quit(self) -> None:
        """Source returns None -> quit=1."""
        source = MagicMock(spec=CredentialSource)
        source.name = "test"
        source.supports.return_value = True
        source.resolve = AsyncMock(return_value=None)

        server = CredentialRelayServer(port=0, sources=[source])
        await server.start()
        try:
            data = await _exchange(
                server, b"protocol=https\nhost=dev.azure.com\n\n",
            )
            assert b"quit=1" in data
            assert server.stats.failfast_responses == 1
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_policy_rejection_returns_quit_for_get(self) -> None:
        """A policy-rejected git `get` host still gets quit=1 (fail-fast)."""
        source = MagicMock(spec=CredentialSource)
        source.name = "test"
        source.supports.return_value = True
        source.resolve = AsyncMock(return_value="password=nope\n\n")

        policy = RelayPolicy(allowed_hosts=["github.com"])
        server = CredentialRelayServer(port=0, sources=[source], policy=policy)
        await server.start()
        try:
            data = await _exchange(server, b"protocol=https\nhost=evil.com\n\n")
            assert b"quit=1" in data
            assert b"password=nope" not in data
            assert server.stats.policy_rejections == 1
            assert server.stats.failfast_responses == 1
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_get_access_token_failure_no_quit(self) -> None:
        """get-access-token (non-git) must NOT emit quit=1 (callers exit non-zero)."""
        server = CredentialRelayServer(port=0, sources=[], ado_host="x.visualstudio.com")
        await server.start()
        try:
            data = await _exchange(server, b"get-access-token\n\n")
            assert b"quit=1" not in data
            assert server.stats.failfast_responses == 0
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_resolved_get_does_not_emit_quit(self) -> None:
        """A successfully resolved get returns the credential, no quit sentinel."""
        source = MagicMock(spec=CredentialSource)
        source.name = "test"
        source.supports.return_value = True
        source.resolve = AsyncMock(
            return_value="protocol=https\nhost=github.com\npassword=tok\n\n",
        )
        server = CredentialRelayServer(port=0, sources=[source])
        await server.start()
        try:
            data = await _exchange(server, b"protocol=https\nhost=github.com\n\n")
            assert b"password=tok" in data
            assert b"quit=1" not in data
            assert server.stats.failfast_responses == 0
        finally:
            await server.stop()



def test_git_credential_kills_a_helper_that_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from credential_relay.sources import git_credential as gc

    killed: list[bool] = []

    class HungProc:
        returncode = None

        async def communicate(self, input: bytes | None = None) -> None:
            await asyncio.sleep(30)

        def kill(self) -> None:
            killed.append(True)

    async def fake_exec(*_a: object, **_k: object) -> HungProc:
        return HungProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    src = gc.GitCredentialSource()
    out = asyncio.run(src._run_directly("fill", "protocol=https\nhost=github.com\n", timeout=0.1))
    assert out is None and killed == [True]
