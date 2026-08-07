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
async def test_no_fallback_raises_when_cli_absent():
    r = CliNamespaceResolver("codespace", "agent-codespaces", fallback=None)
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError):
            await r.list()
