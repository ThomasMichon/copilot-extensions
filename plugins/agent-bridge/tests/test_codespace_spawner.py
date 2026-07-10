"""Tests for the CodeSpaceSpawner + remote endpoint descriptor (dotfiles #177).

Exercises the boundary-agnostic remote-Spawner orchestration against a fake
transport (no real ``gh``/ssh): ship-by-hash, detached launch, remote-port
read-back, ``-L`` forward stand-up, and the durable endpoint descriptor a
restarted frontend re-forwards from. The ssh/`gh` specifics live in
agent-codespaces and are covered there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_bridge.session_host import bundle as bundle_mod
from agent_bridge.session_host import protocol as proto
from agent_bridge.session_host import spawner as sp
from agent_bridge.session_host.endpoints import (
    endpoint_from_ssh_config,
    forward_from_endpoint,
    ssh_config_from_endpoint,
)
from ssh_manager import SSHConfig


class _FakeForward:
    """Stand-in for ssh_manager.LocalForward (no real ssh process)."""

    instances: list["_FakeForward"] = []

    def __init__(self, config, remote_port, *, local_port=None, **kw):
        self.config = config
        self.remote_port = remote_port
        self.local_port = local_port or 49555
        self.refreshed = 0
        self.cancelled = False
        _FakeForward.instances.append(self)

    async def establish(self):
        return self.local_port

    async def refresh(self):
        self.refreshed += 1
        return self.local_port

    async def cancel(self):
        self.cancelled = True


class _FakeTransport:
    boundary = "codespace"

    def __init__(self, state, *, exists=False):
        self._state = state
        self.exists = exists
        self.pushed: list[tuple[str, str]] = []
        self.runs: list[str] = []

    async def push_file(self, local_path, remote_path):
        self.pushed.append((local_path, remote_path))

    async def path_exists(self, remote_path):
        return self.exists

    async def run(self, command, *, timeout=60.0):
        self.runs.append(command)
        if command.startswith("cat "):
            return (0, json.dumps(self._state), "")
        return (0, "launched", "")

    def ssh_config(self):
        return SSHConfig(host_alias="cs.box", user="vscode",
                         config_file="/tmp/cs.config")

    def endpoint_extra(self):
        return {"codespace": "cs-foo", "repo": "org/repo"}


@pytest.fixture(autouse=True)
def _reset_forward():
    _FakeForward.instances.clear()
    yield
    _FakeForward.instances.clear()


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        bundle_mod, "build_session_host_bundle",
        lambda *a, **k: (Path("/tmp/session-host-abc123.pyz"), "abc123"),
    )
    monkeypatch.setattr("ssh_manager.LocalForward", _FakeForward)


# -- build_remote_launch --------------------------------------------------
def test_build_remote_launch_shape():
    cmd = sp.build_remote_launch(
        "/tmp/agent-bridge/session-host-abc.pyz",
        "/tmp/agent-bridge/host-s1.json",
        "/tmp/agent-bridge/host-s1.log",
        ["copilot", "--acp", "--stdio"],
        nonce="deadbeef",
        cwd="/workspaces/repo",
    )
    assert "setsid nohup" in cmd
    assert "--state-file" in cmd
    assert "--cwd" in cmd
    assert "</dev/null" in cmd
    assert sp._NONCE_ENV in cmd
    assert "deadbeef" in cmd
    assert "copilot" in cmd
    # child argv comes after the `--` terminator
    assert "--" in cmd


# -- spawn happy path -----------------------------------------------------
@pytest.mark.asyncio
async def test_codespace_spawner_ships_launches_forwards(monkeypatch):
    _patch_common(monkeypatch)
    state = {"pid": 111, "child_pid": 222, "port": 51000,
             "protocol_version": proto.PROTOCOL_VERSION}
    t = _FakeTransport(state)
    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot", "--acp", "--stdio"], session_id="sess1",
    )

    assert spawned.local_port == 49555
    assert spawned.host_pid == 111
    assert spawned.child_pid == 222
    assert spawned.boundary == "codespace"
    assert spawned.nonce
    assert spawned.protocol_version == proto.PROTOCOL_VERSION
    # bundle shipped once (path_exists returned False)
    assert len(t.pushed) == 1
    # a detached launch ran, carrying the nonce via env
    assert any("setsid nohup" in c for c in t.runs)
    assert any(sp._NONCE_ENV in c for c in t.runs)
    # endpoint descriptor is durable + carries the transport's extra
    ep = spawned.endpoint
    assert ep["kind"] == "codespace"
    assert ep["remote_port"] == 51000
    assert ep["local_port"] == 49555
    assert ep["codespace"] == "cs-foo"
    assert ep["ssh"]["config_file"] == "/tmp/cs.config"


@pytest.mark.asyncio
async def test_codespace_spawner_skips_ship_on_cache_hit(monkeypatch):
    _patch_common(monkeypatch)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(state, exists=True)
    await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )
    assert t.pushed == []  # already present -> no re-ship


@pytest.mark.asyncio
async def test_codespace_spawner_refresh_endpoint(monkeypatch):
    _patch_common(monkeypatch)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(state)
    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )
    await spawned.refresh_endpoint()
    assert _FakeForward.instances[-1].refreshed == 1


@pytest.mark.asyncio
async def test_codespace_spawner_launch_failure_raises(monkeypatch):
    _patch_common(monkeypatch)

    class _FailTransport(_FakeTransport):
        async def run(self, command, *, timeout=60.0):
            self.runs.append(command)
            if "setsid nohup" in command:
                return (1, "", "python3: not found")
            return (0, "", "")

    t = _FailTransport({"pid": 1, "child_pid": 2, "port": 1})
    with pytest.raises(RuntimeError, match="launch failed"):
        await sp.CodeSpaceSpawner(t, ready_timeout=2).spawn(
            ["copilot"], session_id="s",
        )


# -- endpoint descriptor codec (reattach without a live Spawner) ----------
def test_endpoint_roundtrip_rebuilds_forward():
    cfg = SSHConfig(host_alias="cs.box", user="vscode",
                    config_file="/tmp/cs.config",
                    extra_options={"StrictHostKeyChecking": "no"})
    ep = endpoint_from_ssh_config(cfg, 51000, 49555, kind="codespace",
                                  extra={"codespace": "cs-foo"})
    # survives JSON (host index round-trip)
    ep = json.loads(json.dumps(ep))
    rebuilt = ssh_config_from_endpoint(ep)
    assert rebuilt.host_alias == "cs.box"
    assert rebuilt.user == "vscode"
    assert rebuilt.config_file == "/tmp/cs.config"
    assert rebuilt.extra_options["StrictHostKeyChecking"] == "no"

    fwd = forward_from_endpoint(ep)
    assert fwd.local_port == 49555
    assert fwd._remote_port == 51000
