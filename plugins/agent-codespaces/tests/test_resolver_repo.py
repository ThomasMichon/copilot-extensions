"""Codespace resolver: <repo>@<codespace> repo matching (venue-unify)."""

from __future__ import annotations

from agent_codespaces.resolver import _norm_repo, _repo_matches_codespace


class TestNormRepo:
    def test_strips_owner_and_codespaces_suffix(self):
        assert _norm_repo("example-org/example-web-codespaces") == "example-web"
        assert _norm_repo("example-web") == "example-web"
        assert _norm_repo("example-user/dotfiles") == "dotfiles"

    def test_case_insensitive(self):
        assert _norm_repo("Example-Web") == "example-web"


class TestRepoMatchesCodespace:
    def test_logical_repo_matches_codespaces_host(self):
        # example-web addresses an example-web-codespaces CodeSpace.
        assert _repo_matches_codespace(
            "example-web", "example-org/example-web-codespaces"
        )

    def test_exact_host_repo_matches(self):
        assert _repo_matches_codespace(
            "example-web-codespaces", "example-org/example-web-codespaces"
        )

    def test_different_repo_does_not_match(self):
        assert not _repo_matches_codespace(
            "dotfiles", "example-org/example-web-codespaces"
        )

    def test_empty_cs_repository(self):
        assert not _repo_matches_codespace("example-web", None)
        assert not _repo_matches_codespace("example-web", "")


import sys
import types

import pytest

from agent_codespaces.config import CodespacesConfig, RepoConfig
from agent_codespaces.lifecycle import CodespaceInfo
from agent_codespaces.resolver import CodespaceResolver

_COPILOT = "copilot --acp --stdio --allow-all-tools"
_CS_REPO = "example-org/example-web-codespaces"


@pytest.fixture(autouse=True)
def _stub_agent_bridge_transport(monkeypatch):
    """Stub ``agent_bridge.transport.SpawnTarget`` so ``resolve()`` runs without
    the (heavier) agent-bridge dependency chain installed in this venv."""
    from dataclasses import dataclass, field

    @dataclass
    class SpawnTarget:
        type: str = ""
        spawn_command: list = field(default_factory=list)
        user: str | None = None

    pkg = types.ModuleType("agent_bridge")
    transport = types.ModuleType("agent_bridge.transport")
    transport.SpawnTarget = SpawnTarget
    pkg.transport = transport
    monkeypatch.setitem(sys.modules, "agent_bridge", pkg)
    monkeypatch.setitem(sys.modules, "agent_bridge.transport", transport)


def _cs(name="cs-1", repo=_CS_REPO, state="Available"):
    return CodespaceInfo(
        name=name, display_name=name, repository=repo,
        branch="main", state=state, machine="m",
    )


def _remote_cmd(spawn_command):
    """Extract the --remote-cmd payload from a spawn command list."""
    return spawn_command[spawn_command.index("--remote-cmd") + 1]


@pytest.fixture
def _patched(monkeypatch):
    def _apply(config):
        monkeypatch.setattr(
            "agent_codespaces.resolver.list_codespaces", lambda: [_cs()],
        )
        monkeypatch.setattr(
            "agent_codespaces.resolver.load_merged_config", lambda: config,
        )
    return _apply


class TestResolveCrossRepo:
    """#174: resolve(<name>, repo=..., repo_remote=...) builds the launch cmd."""

    @pytest.mark.asyncio
    async def test_other_repo_clone_if_missing(self, _patched):
        _patched(CodespacesConfig())
        remote = "https://your-org.visualstudio.com/your-org/_git/example-marketplace"
        target = await CodespaceResolver().resolve(
            "cs-1", repo="example-marketplace", repo_remote=remote,
        )
        cmd = _remote_cmd(target.spawn_command)
        assert cmd == (
            f"[ -d /workspaces/example-marketplace/.git ] || "
            f"git clone {remote} /workspaces/example-marketplace; "
            f"cd /workspaces/example-marketplace && {_COPILOT}"
        )

    @pytest.mark.asyncio
    async def test_own_product_no_clone(self, _patched):
        config = CodespacesConfig(repos={_CS_REPO: RepoConfig(workspace_repo="example-web")})
        _patched(config)
        target = await CodespaceResolver().resolve(
            "cs-1", repo="example-web",
            repo_remote="https://github.com/example-org/example-web",
        )
        cmd = _remote_cmd(target.spawn_command)
        assert cmd == f"cd /workspaces/example-web && {_COPILOT}"
        assert "git clone" not in cmd

    @pytest.mark.asyncio
    async def test_dotfiles_no_clone(self, _patched):
        _patched(CodespacesConfig(dotfiles_repo="example-user/dotfiles"))
        target = await CodespaceResolver().resolve(
            "cs-1", repo="dotfiles",
            repo_remote="https://github.com/example-user/dotfiles",
        )
        cmd = _remote_cmd(target.spawn_command)
        assert cmd == (
            "cd /workspaces/.codespaces/.persistedshare/dotfiles "
            f"&& {_COPILOT}"
        )
        assert "git clone" not in cmd

    @pytest.mark.asyncio
    async def test_bare_request_unchanged(self, _patched):
        config = CodespacesConfig(repos={_CS_REPO: RepoConfig(workspace_repo="example-web")})
        _patched(config)
        target = await CodespaceResolver().resolve("cs-1")
        cmd = _remote_cmd(target.spawn_command)
        assert cmd == f"cd /workspaces/example-web && {_COPILOT}"

    @pytest.mark.asyncio
    async def test_cross_repo_no_longer_rejected(self, _patched):
        """The dev52 hard reject is gone: a non-host repo resolves, not raises."""
        _patched(CodespacesConfig())
        target = await CodespaceResolver().resolve(
            "cs-1", repo="some-other-repo",
            repo_remote="https://example.com/x/some-other-repo",
        )
        assert "/workspaces/some-other-repo" in _remote_cmd(target.spawn_command)
