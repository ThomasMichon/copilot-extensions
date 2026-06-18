"""Tests for post-connect remote-domain auth verification."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent_codespaces.auth_preflight import (
    host_from_url,
    host_has_auth,
    parse_remote_hosts,
    verify_remote_auth,
)


class TestHostFromUrl:

    def test_https(self):
        assert host_from_url(
            "https://your-org.visualstudio.com/YourProject/_git/your-repo"
        ) == "your-org.visualstudio.com"

    def test_https_with_user(self):
        assert host_from_url("https://user@github.com/org/repo.git") == "github.com"

    def test_ssh_scp_like(self):
        assert host_from_url("git@github.com:org/repo.git") == "github.com"

    def test_ssh_scheme(self):
        assert host_from_url("ssh://git@ssh.dev.azure.com/v3/org/proj/repo") == (
            "ssh.dev.azure.com"
        )

    def test_empty(self):
        assert host_from_url("") is None
        assert host_from_url("   ") is None

    def test_local_path(self):
        assert host_from_url("/local/path/repo") is None


class TestParseRemoteHosts:

    def test_typical_git_remote_v(self):
        output = (
            "origin\thttps://your-org.visualstudio.com/YourProject/_git/your-repo (fetch)\n"
            "origin\thttps://your-org.visualstudio.com/YourProject/_git/your-repo (push)\n"
            "upstream\thttps://github.com/org/repo.git (fetch)\n"
            "upstream\thttps://github.com/org/repo.git (push)\n"
        )
        assert parse_remote_hosts(output) == [
            "your-org.visualstudio.com",
            "github.com",
        ]

    def test_empty(self):
        assert parse_remote_hosts("") == []

    def test_ignores_garbage_lines(self):
        assert parse_remote_hosts("not a remote line\n\n") == []


class TestHostHasAuth:

    @pytest.mark.asyncio
    async def test_true_when_password_present(self):
        source = AsyncMock()
        source.resolve = AsyncMock(
            return_value="protocol=https\nhost=h\npassword=tok\n\n",
        )
        assert await host_has_auth("github.com", source=source) is True

    @pytest.mark.asyncio
    async def test_false_when_none(self):
        source = AsyncMock()
        source.resolve = AsyncMock(return_value=None)
        assert await host_has_auth("github.com", source=source) is False

    @pytest.mark.asyncio
    async def test_false_when_quit_sentinel(self):
        source = AsyncMock()
        source.resolve = AsyncMock(return_value="quit=1\n\n")
        assert await host_has_auth("github.com", source=source) is False

    @pytest.mark.asyncio
    async def test_false_on_exception(self):
        source = AsyncMock()
        source.resolve = AsyncMock(side_effect=RuntimeError("boom"))
        assert await host_has_auth("github.com", source=source) is False


class TestVerifyRemoteAuth:

    @pytest.mark.asyncio
    async def test_reports_missing_domains(self):
        async def run_remote(_cmd):
            return (
                "origin\thttps://your-org.visualstudio.com/x/_git/y (fetch)\n"
                "upstream\thttps://github.com/org/repo.git (fetch)\n"
            )

        source = AsyncMock()

        async def resolve(_action, fields, **_kw):
            if fields["host"] == "github.com":
                return "password=tok\n\n"
            return None  # ADO missing

        source.resolve = AsyncMock(side_effect=resolve)

        hosts, missing = await verify_remote_auth(run_remote, source=source)
        assert hosts == ["your-org.visualstudio.com", "github.com"]
        assert missing == ["your-org.visualstudio.com"]

    @pytest.mark.asyncio
    async def test_all_present(self):
        async def run_remote(_cmd):
            return "origin\thttps://github.com/org/repo.git (fetch)\n"

        source = AsyncMock()
        source.resolve = AsyncMock(return_value="password=tok\n\n")

        hosts, missing = await verify_remote_auth(run_remote, source=source)
        assert hosts == ["github.com"]
        assert missing == []

    @pytest.mark.asyncio
    async def test_no_remotes_is_noop(self):
        async def run_remote(_cmd):
            return ""

        source = AsyncMock()
        hosts, missing = await verify_remote_auth(run_remote, source=source)
        assert hosts == []
        assert missing == []
        source.resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_remote_command_failure_is_noop(self):
        async def run_remote(_cmd):
            raise RuntimeError("ssh failed")

        source = AsyncMock()
        hosts, missing = await verify_remote_auth(run_remote, source=source)
        assert hosts == []
        assert missing == []
