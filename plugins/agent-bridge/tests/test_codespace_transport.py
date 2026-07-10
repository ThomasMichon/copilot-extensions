"""Tests for the codespace-boundary transport (gh cp + ssh_manager exec)."""

from __future__ import annotations

import pytest

from agent_bridge.session_host.codespace_transport import CodeSpaceTransport
from ssh_manager import SSHConfig


class _FakeResult:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _FakeManager:
    def __init__(self, results=None):
        self.connected = []
        self.commands = []
        self._results = results or {}

    async def ensure_connected(self, name, source, forwards):
        self.connected.append(name)

    async def exec_command(self, name, command, timeout=60.0):
        self.commands.append(command)
        for needle, res in self._results.items():
            if needle in command:
                return res
        return _FakeResult(0, "", "")


class _FakeSource:
    def get_ssh_config(self):
        return SSHConfig(host_alias="cs.box", user="vscode",
                         config_file="/tmp/cs.config")


def _transport(**kw):
    return CodeSpaceTransport(
        "cs-foo", "org/repo", manager=_FakeManager(kw.pop("results", None)),
        source=_FakeSource(), **kw,
    )


@pytest.mark.asyncio
async def test_run_maps_result():
    t = _transport(results={"echo hi": _FakeResult(0, "hi", "")})
    rc, out, err = await t.run("echo hi")
    assert (rc, out) == (0, "hi")
    assert t._manager.connected == ["cs-foo"]  # connected once


@pytest.mark.asyncio
async def test_path_exists_true_false():
    t_yes = _transport(results={"test -f": _FakeResult(0, "__EXISTS__\n", "")})
    assert await t_yes.path_exists("/tmp/x") is True
    t_no = _transport(results={"test -f": _FakeResult(0, "", "")})
    assert await t_no.path_exists("/tmp/x") is False


@pytest.mark.asyncio
async def test_push_file_mkdirs_and_cps(monkeypatch):
    t = _transport()
    calls = {}

    class _P:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def fake_spawn(*args, **kw):
        calls["args"] = list(args)
        return _P()

    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    await t.push_file("C:/local/session-host.pyz", "/tmp/agent-bridge/sh.pyz")
    # mkdir -p ran for the parent dir
    assert any("mkdir -p" in c for c in t._manager.commands)
    # gh codespace cp invoked with -e (expand) + remote: prefix
    assert calls["args"][:3] == ["gh", "codespace", "cp"]
    assert "-e" in calls["args"]
    assert calls["args"][-1] == "remote:/tmp/agent-bridge/sh.pyz"


@pytest.mark.asyncio
async def test_push_file_raises_on_gh_failure(monkeypatch):
    t = _transport()

    class _P:
        returncode = 1

        async def communicate(self):
            return (b"", b"no space left")

    async def fake_spawn(*args, **kw):
        return _P()

    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    with pytest.raises(RuntimeError, match="gh codespace cp failed"):
        await t.push_file("x", "/tmp/y")


def test_reverse_forwards_and_extra():
    t = _transport(relay_port=51234)
    assert t.reverse_forwards() == ["51234:127.0.0.1:51234"]
    assert t.endpoint_extra() == {"codespace": "cs-foo", "repo": "org/repo"}
    t2 = _transport()
    assert t2.reverse_forwards() == []
    assert t2.ssh_config().host_alias == "cs.box"


def test_parse_codespace_target_recognizes_shape():
    from agent_bridge.session_host.codespace_transport import parse_codespace_target

    cmd = [
        "python", "-m", "agent_codespaces", "ssh", "--stdio",
        "plugin-propagation-test-v467", "--repo", "odsp-microsoft/odsp-web-codespaces",
        "--remote-cmd", "cd /workspaces/odsp-web && copilot --acp --stdio",
    ]
    parsed = parse_codespace_target(cmd)
    assert parsed["name"] == "plugin-propagation-test-v467"
    assert parsed["repo"] == "odsp-microsoft/odsp-web-codespaces"
    assert parsed["acp_command"].startswith("cd /workspaces/odsp-web")


def test_parse_codespace_target_rejects_non_codespace():
    from agent_bridge.session_host.codespace_transport import parse_codespace_target

    assert parse_codespace_target([]) is None
    assert parse_codespace_target(["copilot", "--acp", "--stdio"]) is None
    # agent_codespaces but not an stdio launch (e.g. a diagnostic remote-cmd)
    assert parse_codespace_target(
        ["python", "-m", "agent_codespaces", "ssh", "cs-x", "--remote-cmd", "ls"]
    ) is None
