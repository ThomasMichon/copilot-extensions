"""Tests for the #892 Increment 3 namespace-resolver process boundary.

``CliNamespaceResolver`` drives a namespace provider (e.g. agent-codespaces) over
a subprocess seam (`<binstub> namespace-list/-resolve/-target-repo/-ensure-ready`)
instead of importing its resolver in the bridge venv, falling back to an
in-process resolver on any *subprocess* failure while mapping a provider's
legitimate not-found (exit 3) / bad-state (exit 4) back to KeyError / ValueError.
These tests mock ``shutil.which`` + ``subprocess.run`` (the shim uses the
module-level names in ``agent_registry``).
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_bridge.agent_registry import (
    CliNamespaceResolver,
    NamespaceAgentInfo,
    NamespaceResolver,
)
from agent_bridge.transport import SpawnTarget


class _Fallback(NamespaceResolver):
    """A recording in-process fallback resolver."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def prefix(self) -> str:
        return "codespace"

    async def list(self):
        self.calls.append("list")
        return [NamespaceAgentInfo(name="fallback-cs")]

    async def resolve(self, name, *, extra_plugins=(), repo=None, repo_remote=None):
        self.calls.append("resolve")
        return SpawnTarget(type="command", spawn_command=["fb"], user="fbuser")

    async def ensure_ready(self, name):
        self.calls.append("ensure_ready")

    async def target_repo(self, name):
        self.calls.append("target_repo")
        return "fb/repo"


def _cp(rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out, err)


def _which(_name):
    return "/usr/bin/agent-codespaces"


# --- list ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_uses_cli():
    fb = _Fallback()
    payload = json.dumps([
        {"name": "cs-a", "display_name": "A", "state": "available"},
    ])
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(0, payload)):
        agents = await CliNamespaceResolver("codespace", "agent-codespaces", fb).list()
    assert [a.name for a in agents] == ["cs-a"]
    assert "list" not in fb.calls  # CLI path, no fallback


@pytest.mark.asyncio
async def test_list_falls_back_on_unparseable():
    fb = _Fallback()
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(0, "not json")):
        agents = await CliNamespaceResolver("codespace", "agent-codespaces", fb).list()
    assert [a.name for a in agents] == ["fallback-cs"]
    assert fb.calls == ["list"]


@pytest.mark.asyncio
async def test_list_falls_back_when_no_binstub():
    fb = _Fallback()
    with patch("shutil.which", return_value=None):
        agents = await CliNamespaceResolver("codespace", "agent-codespaces", fb).list()
    assert [a.name for a in agents] == ["fallback-cs"]


# --- resolve -------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_builds_spawn_target_and_argv():
    fb = _Fallback()
    seen = {}

    def _run(argv, **_kw):
        seen["argv"] = argv
        return _cp(0, json.dumps({"type": "command", "spawn_command": ["ssh", "x"], "user": "me"}))

    with patch("shutil.which", _which), patch("subprocess.run", side_effect=_run):
        t = await CliNamespaceResolver("codespace", "agent-codespaces", fb).resolve(
            "cs-a", extra_plugins=[SimpleNamespace(source="/p/one")], repo="o/r",
            repo_remote="https://x/r.git",
        )
    assert isinstance(t, SpawnTarget)
    assert t.spawn_command == ["ssh", "x"] and t.user == "me"
    assert seen["argv"][:2] == ["/usr/bin/agent-codespaces", "namespace-resolve"]
    assert "--repo" in seen["argv"] and "o/r" in seen["argv"]
    assert "--repo-remote" in seen["argv"] and "https://x/r.git" in seen["argv"]
    assert "--stage-plugin" in seen["argv"] and "/p/one" in seen["argv"]
    assert "resolve" not in fb.calls


@pytest.mark.asyncio
async def test_resolve_carries_venue_metadata():
    """workspace_folder + security_profile from namespace-resolve land on
    SpawnTarget.venue (container fleets surface them for cwd + trust gating)."""
    fb = _Fallback()

    def _run(argv, **_kw):
        return _cp(0, json.dumps({
            "type": "command",
            "spawn_command": ["c", "exec", "--stdio", "odsp-web-1"],
            "user": "vscode",
            "workspace_folder": "/workspaces/odsp-web",
            "security_profile": "trusted",
        }))

    with patch("shutil.which", _which), patch("subprocess.run", side_effect=_run):
        t = await CliNamespaceResolver("container", "agent-containers", fb).resolve(
            "odsp-web-1",
        )
    assert t.venue == {
        "workspace_folder": "/workspaces/odsp-web",
        "security_profile": "trusted",
    }


@pytest.mark.asyncio
async def test_resolve_carries_structured_codespace_metadata():
    fb = _Fallback()
    metadata = {
        "name": "cs-a",
        "repo": "org/repo",
        "acp_command": "cd /workspaces/repo && copilot --acp --stdio",
        "workspace_folder": "/workspaces/repo",
    }

    def _run(argv, **_kw):
        return _cp(0, json.dumps({
            "type": "command",
            "spawn_command": ["agent-codespaces", "ssh", "cs-a", "--stdio"],
            "user": "vscode",
            "codespace": metadata,
        }))

    with patch("shutil.which", _which), patch("subprocess.run", side_effect=_run):
        target = await CliNamespaceResolver(
            "codespace", "agent-codespaces", fb
        ).resolve("cs-a")

    assert target.codespace == metadata


@pytest.mark.asyncio
async def test_resolve_venue_none_when_absent():
    """A spec without workspace_folder/security_profile leaves venue None."""
    fb = _Fallback()

    def _run(argv, **_kw):
        return _cp(0, json.dumps(
            {"type": "command", "spawn_command": ["ssh", "x"], "user": "me"}))

    with patch("shutil.which", _which), patch("subprocess.run", side_effect=_run):
        t = await CliNamespaceResolver("codespace", "agent-codespaces", fb).resolve("cs-a")
    assert t.venue is None


@pytest.mark.asyncio
async def test_resolve_not_found_maps_to_keyerror():
    fb = _Fallback()
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(3, "", "no such cs")):
        with pytest.raises(KeyError):
            await CliNamespaceResolver("codespace", "agent-codespaces", fb).resolve("nope")
    assert "resolve" not in fb.calls  # authoritative outcome, not a fallback


@pytest.mark.asyncio
async def test_resolve_bad_state_maps_to_valueerror():
    fb = _Fallback()
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(4, "", "is Failed")):
        with pytest.raises(ValueError):
            await CliNamespaceResolver("codespace", "agent-codespaces", fb).resolve("cs-a")


@pytest.mark.asyncio
async def test_resolve_falls_back_on_other_nonzero():
    fb = _Fallback()
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(1, "", "crash")):
        t = await CliNamespaceResolver("codespace", "agent-codespaces", fb).resolve("cs-a")
    assert t.spawn_command == ["fb"]
    assert fb.calls == ["resolve"]


# --- ensure_ready / target_repo ------------------------------------------

@pytest.mark.asyncio
async def test_ensure_ready_ok_and_not_ready():
    fb = _Fallback()
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(0)):
        await CliNamespaceResolver("codespace", "agent-codespaces", fb).ensure_ready("cs-a")
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(1, "", "not reachable")):
        with pytest.raises(RuntimeError):
            await CliNamespaceResolver("codespace", "agent-codespaces", fb).ensure_ready("cs-a")


@pytest.mark.asyncio
async def test_target_repo_cli_and_fallback():
    fb = _Fallback()
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(0, "owner/name\n")):
        assert await CliNamespaceResolver("codespace", "agent-codespaces", fb).target_repo("cs") == "owner/name"
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(0, "  \n")):
        assert await CliNamespaceResolver("codespace", "agent-codespaces", fb).target_repo("cs") is None


@pytest.mark.asyncio
async def test_no_fallback_list_degrades_to_empty_when_cli_absent():
    # A missing provider binstub with no in-process fallback (e.g. the
    # ``codespace:`` namespace inside the elevated sub-daemon, which cannot see
    # the agent-codespaces binstub) must NOT raise from list() -- it means "no
    # dynamic agents from this provider". Degrade to empty so agent enumeration
    # stays clean (previously this produced a scary RuntimeError traceback on
    # every list).
    r = CliNamespaceResolver("codespace", "agent-codespaces", fallback=None)
    with patch("shutil.which", return_value=None):
        assert await r.list() == []


@pytest.mark.asyncio
async def test_no_fallback_resolve_still_raises_when_cli_absent():
    # resolve/ensure_ready stay strict: you cannot spawn what you cannot
    # resolve, so the degraded-list path must not soften these.
    r = CliNamespaceResolver("codespace", "agent-codespaces", fallback=None)
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError):
            await r.resolve("cs-a")
        with pytest.raises(RuntimeError):
            await r.ensure_ready("cs-a")


# --- restricted (container) variant + signature-aware fallback (#892 Inc 3b) ---

import inspect  # noqa: E402

from agent_bridge.agent_registry import RestrictedCliNamespaceResolver  # noqa: E402


class _NarrowFallback(NamespaceResolver):
    """A fallback whose resolve(name) takes NO cross-repo/plugin kwargs."""

    def __init__(self):
        self.calls = []

    @property
    def prefix(self):
        return "container"

    async def list(self):
        self.calls.append("list")
        return [NamespaceAgentInfo(name="fb-ctr")]

    async def resolve(self, name):
        self.calls.append("resolve")
        return SpawnTarget(type="command", spawn_command=["fb"], user=None)


def test_restricted_resolve_signature_hides_cross_repo():
    # agent-bridge introspects resolver.resolve to decide cross-repo support.
    restricted = inspect.signature(RestrictedCliNamespaceResolver.resolve)
    assert "repo" not in restricted.parameters
    assert "extra_plugins" not in restricted.parameters
    full = inspect.signature(CliNamespaceResolver.resolve)
    assert "repo" in full.parameters and "extra_plugins" in full.parameters


@pytest.mark.asyncio
async def test_restricted_resolve_uses_cli_name_only():
    fb = _NarrowFallback()
    seen = {}

    def _run(argv, **_kw):
        seen["argv"] = argv
        return _cp(0, json.dumps({"type": "command", "spawn_command": ["docker", "x"], "user": "u"}))

    with patch("shutil.which", _which), patch("subprocess.run", side_effect=_run):
        t = await RestrictedCliNamespaceResolver("container", "agent-containers", fb).resolve("ctr-1")
    assert t.spawn_command == ["docker", "x"]
    # name-only argv -- no cross-repo/plugin flags leak to a container provider.
    assert seen["argv"] == ["/usr/bin/agent-codespaces", "namespace-resolve", "ctr-1"]


@pytest.mark.asyncio
async def test_signature_aware_fallback_drops_unsupported_kwargs():
    # The core 3b fix: falling back to a NARROW resolver must not TypeError on
    # repo/extra_plugins -- they are dropped to match the fallback's signature.
    fb = _NarrowFallback()
    with patch("shutil.which", _which), patch("subprocess.run", return_value=_cp(1, "", "crash")):
        t = await CliNamespaceResolver("container", "agent-containers", fb)._resolve_impl(
            "ctr-1", repo="o/r", repo_remote="https://x", extra_plugins=[SimpleNamespace(source="/p")],
        )
    assert t.spawn_command == ["fb"]
    assert fb.calls == ["resolve"]
