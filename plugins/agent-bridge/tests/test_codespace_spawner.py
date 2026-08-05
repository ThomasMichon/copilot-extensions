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
from agent_bridge.session_host import endpoints as endpoints_mod
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
        self.kw = kw
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

    def __init__(
        self,
        state,
        *,
        exists=False,
        reverse_forwards=None,
        probe_result=None,
        probe_error: Exception | None = None,
    ):
        self._state = state
        self.exists = exists
        self._reverse_forwards = list(reverse_forwards or [])
        self.probe_result = probe_result
        self.probe_error = probe_error
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
        if "/dev/tcp/127.0.0.1/" in command:
            if self.probe_error is not None:
                raise self.probe_error
            return self.probe_result or (1, "", "refused")
        return (0, "launched", "")

    def ssh_config(self):
        return SSHConfig(host_alias="cs.box", user="vscode",
                         config_file="/tmp/cs.config")

    def reverse_forwards(self):
        return list(self._reverse_forwards)

    def endpoint_extra(self):
        return {"codespace": "cs-foo", "repo": "org/repo"}


@pytest.fixture(autouse=True)
def _reset_forward():
    _FakeForward.instances.clear()
    _FakeRelay.instances.clear()
    _FakeRelay.start_error = None
    yield
    _FakeForward.instances.clear()
    _FakeRelay.instances.clear()
    _FakeRelay.start_error = None


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        bundle_mod, "build_session_host_bundle",
        lambda *a, **k: (Path("/tmp/session-host-abc123.pyz"), "abc123"),
    )
    monkeypatch.setattr("ssh_manager.LocalForward", _FakeForward)


class _FakeRelay:
    """Stand-in for ssh_manager.SupervisedRelayForward."""

    instances: list["_FakeRelay"] = []
    start_error: Exception | None = None

    def __init__(self, config, relay_port, *, serving_probe=None, **kw):
        self.config = config
        self.relay_port = relay_port
        self.serving_probe = serving_probe
        self.kw = kw
        self.started = 0
        self.stopped = 0
        self.is_alive = False
        _FakeRelay.instances.append(self)

    async def start(self):
        if _FakeRelay.start_error is not None:
            raise _FakeRelay.start_error
        self.started += 1
        self.is_alive = True

    async def stop(self):
        self.stopped += 1
        self.is_alive = False


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
    # reap bounds are threaded to the detached far-side host (#145)
    assert "--unexpected-reap-seconds" in cmd
    assert "--active-reap-seconds" in cmd
    # child argv comes after the `--` terminator
    assert "--" in cmd


def test_build_remote_launch_threads_reap_bounds():
    cmd = sp.build_remote_launch(
        "/tmp/agent-bridge/session-host-abc.pyz",
        "/tmp/agent-bridge/host-s1.json",
        "/tmp/agent-bridge/host-s1.log",
        ["copilot", "--acp", "--stdio"],
        unexpected_reap_seconds=45.0,
        active_reap_seconds=1800.0,
    )
    assert "--unexpected-reap-seconds 45.0" in cmd
    assert "--active-reap-seconds 1800.0" in cmd


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
async def test_codespace_spawner_splits_relay_from_local_forward(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(state, reverse_forwards=["9857:127.0.0.1:9857"])

    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )

    assert _FakeForward.instances[-1].kw.get("reverse_forwards") is None
    assert len(_FakeRelay.instances) == 1
    assert _FakeRelay.instances[0].relay_port == 9857
    assert _FakeRelay.instances[0].started == 1
    assert spawned.relay == [_FakeRelay.instances[0]]
    assert spawned.endpoint["reverse_forwards"] == ["9857:127.0.0.1:9857"]


@pytest.mark.asyncio
async def test_codespace_spawner_no_relay_when_no_reverse_forward(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(state)

    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )

    assert spawned.relay == []
    assert _FakeRelay.instances == []


@pytest.mark.asyncio
async def test_codespace_spawner_refresh_does_not_touch_relay(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(state, reverse_forwards=["9857:127.0.0.1:9857"])
    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )

    await spawned.refresh_endpoint()

    assert _FakeForward.instances[-1].refreshed == 1
    assert _FakeRelay.instances[0].started == 1
    assert _FakeRelay.instances[0].stopped == 0


@pytest.mark.asyncio
async def test_spawned_host_aclose_stops_relay_and_forward(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(state, reverse_forwards=["9857:127.0.0.1:9857"])
    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )

    await spawned.aclose()

    assert _FakeRelay.instances[0].stopped == 1
    assert _FakeForward.instances[-1].cancelled is True


@pytest.mark.asyncio
async def test_relay_start_failure_does_not_break_spawn(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    _FakeRelay.start_error = RuntimeError("bind failed")
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(state, reverse_forwards=["9857:127.0.0.1:9857"])

    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )

    assert spawned.local_port == 49555
    assert spawned.relay == []
    assert _FakeRelay.instances[0].stopped == 1


@pytest.mark.asyncio
async def test_relay_serving_probe_checks_far_side_port(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(
        state,
        reverse_forwards=["9857:127.0.0.1:9857"],
        probe_result=(0, "OK\n", ""),
    )
    await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )

    assert await _FakeRelay.instances[0].serving_probe() is True
    assert any("/dev/tcp/127.0.0.1/9857" in command for command in t.runs)


@pytest.mark.asyncio
async def test_relay_serving_probe_false_on_unserved_port(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(state, reverse_forwards=["9857:127.0.0.1:9857"])
    await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )

    assert await _FakeRelay.instances[0].serving_probe() is False


@pytest.mark.asyncio
async def test_relay_serving_probe_transport_error_is_healthy(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(
        state,
        reverse_forwards=["9857:127.0.0.1:9857"],
        probe_error=RuntimeError("transport down"),
    )
    await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot"], session_id="s",
    )

    assert await _FakeRelay.instances[0].serving_probe() is True


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
    ep = endpoint_from_ssh_config(
        cfg,
        51000,
        49555,
        kind="codespace",
        reverse_forwards=["9857:127.0.0.1:9857"],
        extra={"codespace": "cs-foo"},
    )
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
    assert fwd._reverse_forwards == []


def _probe_cfg() -> SSHConfig:
    return SSHConfig(
        host_alias="cs.box", user="vscode", port=22,
        identity_file="id_ed25519", config_file="/tmp/cs.config",
        extra_options={"ControlMaster": "auto", "StrictHostKeyChecking": "no"},
    )


def test_build_remote_exec_args_shape():
    from ssh_manager import build_remote_exec_args

    cfg = _probe_cfg()
    argv = build_remote_exec_args(cfg, "bash -lc 'echo hi'")
    assert argv[0] == "ssh"
    assert "-F" in argv and "-i" in argv
    assert "-T" in argv
    assert "-N" not in argv  # an exec, not a forward
    assert "ControlMaster" not in " ".join(argv)  # a probe never multiplexes
    assert argv[-1] == "bash -lc 'echo hi'"  # remote command is the last arg
    assert argv[-2] == cfg.ssh_target


class _FakeProbeProc:
    def __init__(self, returncode: int, stdout: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self):
        return (self._stdout, b"")


def _relay_endpoint() -> dict:
    return endpoint_from_ssh_config(
        _probe_cfg(), 51000, 61000, kind="codespace",
        reverse_forwards=["50629:127.0.0.1:50629"],
    )


@pytest.mark.asyncio
async def test_endpoint_serving_probe_serves(monkeypatch):
    seen = {}

    async def fake_exec(*argv, **_kw):
        seen["argv"] = argv
        return _FakeProbeProc(0, b"OK\n")

    monkeypatch.setattr(
        "agent_bridge.session_host.endpoints.asyncio.create_subprocess_exec",
        fake_exec,
    )
    probe = endpoints_mod.endpoint_serving_probe_factory(_relay_endpoint())(50629)
    assert await probe() is True
    # the probe execs a /dev/tcp accept check against the CS-side listen port
    assert any("/dev/tcp/127.0.0.1/50629" in str(a) for a in seen["argv"])


@pytest.mark.asyncio
async def test_endpoint_serving_probe_false_on_unserved_port(monkeypatch):
    async def fake_exec(*_argv, **_kw):
        return _FakeProbeProc(124, b"")  # timeout/refused -> no OK

    monkeypatch.setattr(
        "agent_bridge.session_host.endpoints.asyncio.create_subprocess_exec",
        fake_exec,
    )
    probe = endpoints_mod.endpoint_serving_probe_factory(_relay_endpoint())(50629)
    assert await probe() is False


@pytest.mark.asyncio
async def test_endpoint_serving_probe_transport_error_is_healthy(monkeypatch):
    async def fake_exec(*_argv, **_kw):
        raise RuntimeError("ssh spawn failed")

    monkeypatch.setattr(
        "agent_bridge.session_host.endpoints.asyncio.create_subprocess_exec",
        fake_exec,
    )
    probe = endpoints_mod.endpoint_serving_probe_factory(_relay_endpoint())(50629)
    # a transport failure is a health hint, never a reason to churn the relay
    assert await probe() is True


# -- dispatch-path auth-helper (re)deploy (dotfiles #733 T2) ---------------
def _install_fake_agent_codespaces(monkeypatch, recorder=None):
    """Inject a fake ``agent_codespaces.codespace_assets`` whose
    ``build_provision_command`` returns a sentinel (optionally recording calls),
    so the spawner's dispatch-path helper (re)deploy has something to import even
    though agent_codespaces is not an agent-bridge test dependency."""
    import sys
    import types

    assets = types.ModuleType("agent_codespaces.codespace_assets")

    def _build():
        if recorder is not None:
            recorder.append(1)
        return "PROVISION_HELPERS_CMD"

    assets.build_provision_command = _build
    pkg = types.ModuleType("agent_codespaces")
    monkeypatch.setitem(sys.modules, "agent_codespaces", pkg)
    monkeypatch.setitem(sys.modules, "agent_codespaces.codespace_assets", assets)


@pytest.mark.asyncio
async def test_codespace_dispatch_redeploys_auth_helpers(monkeypatch):
    """On the codespace boundary, spawn() re-asserts the ADO/git auth helpers
    (Stage-4 provision) BEFORE launching the dispatched agent, so a dispatched
    agent isn't left on a reboot-stale VS Code helper (#733 T2)."""
    _patch_common(monkeypatch)
    _install_fake_agent_codespaces(monkeypatch)
    state = {"pid": 1, "child_pid": 2, "port": 51000,
             "protocol_version": proto.PROTOCOL_VERSION}
    t = _FakeTransport(state)
    await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot", "--acp", "--stdio"], session_id="s1",
    )
    assert "PROVISION_HELPERS_CMD" in t.runs
    # ...and it ran BEFORE the detached Host launch, so the dispatched agent sees
    # the fresh helper.
    launch_idx = next(i for i, c in enumerate(t.runs) if "setsid nohup" in c)
    assert t.runs.index("PROVISION_HELPERS_CMD") < launch_idx


@pytest.mark.asyncio
async def test_non_codespace_boundary_skips_helper_redeploy(monkeypatch):
    """The mesh (non-codespace) boundary must NOT run the codespace auth-helper
    (re)deploy -- it is codespace-specific (#733 T2)."""
    _patch_common(monkeypatch)
    calls: list[int] = []
    _install_fake_agent_codespaces(monkeypatch, recorder=calls)
    state = {"pid": 1, "child_pid": 2, "port": 51000,
             "protocol_version": proto.PROTOCOL_VERSION}
    t = _FakeTransport(state)
    t.boundary = "mesh"
    await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot", "--acp", "--stdio"], session_id="s2",
    )
    assert "PROVISION_HELPERS_CMD" not in t.runs
    assert calls == []


@pytest.mark.asyncio
async def test_dispatch_helper_redeploy_missing_agent_codespaces_is_noop(monkeypatch):
    """When agent_codespaces is not importable, the dispatch-path helper
    (re)deploy is silently skipped and the launch still proceeds (best-effort,
    #733 T2)."""
    _patch_common(monkeypatch)
    import sys
    monkeypatch.setitem(sys.modules, "agent_codespaces", None)
    state = {"pid": 1, "child_pid": 2, "port": 51000,
             "protocol_version": proto.PROTOCOL_VERSION}
    t = _FakeTransport(state)
    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot", "--acp", "--stdio"], session_id="s3",
    )
    assert any("setsid nohup" in c for c in t.runs)  # launch still happened
    assert spawned.host_pid == 1
