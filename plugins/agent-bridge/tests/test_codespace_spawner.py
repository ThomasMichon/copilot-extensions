"""Tests for the CodeSpaceSpawner + remote endpoint descriptor (dotfiles #177).

Exercises the boundary-agnostic remote-Spawner orchestration against a fake
transport (no real ``gh``/ssh): ship-by-hash, detached launch, remote-port
read-back, ``-L`` forward stand-up, and the durable endpoint descriptor a
restarted frontend re-forwards from. The ssh/`gh` specifics live in
agent-codespaces and are covered there.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from ssh_manager import SSHConfig

from agent_bridge.session_host import bundle as bundle_mod
from agent_bridge.session_host import endpoints as endpoints_mod
from agent_bridge.session_host import protocol as proto
from agent_bridge.session_host import spawner as sp
from agent_bridge.session_host.endpoints import (
    endpoint_from_ssh_config,
    forward_from_endpoint,
    ssh_config_from_endpoint,
)


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
        preflight_result=None,
        liveness="1:1",
    ):
        self._state = state
        self.exists = exists
        self._reverse_forwards = list(reverse_forwards or [])
        self.probe_result = probe_result
        self.probe_error = probe_error
        self.preflight_result = preflight_result
        self.liveness = liveness
        self.pushed: list[tuple[str, str]] = []
        self.runs: list[str] = []
        self.launched_session_id = ""

    async def push_file(self, local_path, remote_path):
        self.pushed.append((local_path, remote_path))

    async def path_exists(self, remote_path):
        return self.exists

    async def run(self, command, *, timeout=60.0):
        self.runs.append(command)
        if command.startswith("python3 -S "):
            return self.preflight_result or (0, "", "")
        if command.startswith("cat ") or command.startswith("if test -f "):
            state = dict(self._state)
            state.setdefault("session_id", self.launched_session_id or "sess1")
            state.setdefault("nonce", "test-nonce")
            return (0, json.dumps(state), "")
        if command.startswith("b=$(cat "):
            return (0, self.liveness, "")
        if "/dev/tcp/127.0.0.1/" in command:
            if self.probe_error is not None:
                raise self.probe_error
            return self.probe_result or (1, "", "refused")
        if "kill -TERM --" in command:
            self.liveness = "0:0"
            return (0, "", "")
        match = re.search(r"--session-id ([A-Za-z0-9_.-]+)", command)
        if match:
            self.launched_session_id = match.group(1)
        return (0, "launched", "")

    async def home_dir(self):
        return "/home/vscode"

    async def is_running(self):
        return True

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
    monkeypatch.setattr(sp, "new_nonce", lambda: "test-nonce")
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
        session_id="s1",
        host_version="0.4.0-dev1",
        reverse_forwards=["9857:127.0.0.1:61234"],
    )
    assert "setsid nohup" in cmd
    assert "--state-file" in cmd
    assert "--cwd" in cmd
    assert "</dev/null" in cmd
    assert sp._NONCE_ENV in cmd
    assert "deadbeef" in cmd
    assert "--session-id s1" in cmd
    assert "--host-version 0.4.0-dev1" in cmd
    assert "--reverse-forward" in cmd
    assert "9857:127.0.0.1:61234" in cmd
    assert "chmod 700" in cmd
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
    # The staged archive is import-checked without site-packages before launch.
    assert any(c.startswith("python3 -S ") and "--help" in c for c in t.runs)
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
    assert spawned.state_file == (
        "/home/vscode/.agent-bridge/session-hosts/host-sess1.json"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_confirmed", [True, False])
async def test_post_launch_failure_requires_confirmed_remote_cleanup(
    monkeypatch,
    cleanup_confirmed,
):
    _patch_common(monkeypatch)

    class PollFailureTransport(_FakeTransport):
        async def run(self, command, *, timeout=60.0):
            if command.startswith("cat "):
                raise ConnectionError("transport dropped after launch")
            if command.startswith("python3 -c "):
                self.runs.append(command)
                if cleanup_confirmed:
                    return 0, "__REAPED__", ""
                return 1, "", "cleanup unavailable"
            return await super().run(command, timeout=timeout)

    transport = PollFailureTransport({
        "pid": 111,
        "child_pid": 222,
        "port": 51000,
    })
    spawner = sp.CodeSpaceSpawner(transport, ready_timeout=0.1)

    expected = (
        ConnectionError
        if cleanup_confirmed
        else sp.RemoteSpawnCleanupPendingError
    )
    with pytest.raises(expected):
        await spawner.spawn(
            ["copilot", "--acp", "--stdio"],
            session_id="sess1",
        )
    assert any(command.startswith("python3 -c ") for command in transport.runs)
    assert any("os.unlink(path)" in command for command in transport.runs)


@pytest.mark.asyncio
async def test_codespace_spawner_recovers_remote_authority_record(monkeypatch):
    _patch_common(monkeypatch)
    state = {
        "version": 2,
        "session_id": "sess1",
        "pid": 111,
        "host_pid": 111,
        "child_pid": 222,
        "port": 51000,
        "protocol_version": proto.PROTOCOL_VERSION,
        "host_version": "0.4.0-dev1",
        "nonce": "secure-nonce",
        "created_at": 123.0,
        "state": "running",
        "child_executable": "bash",
        "cwd": "/workspaces/repo",
        "reverse_forwards": ["9857:127.0.0.1:61234"],
        "boot_id": "boot-one",
        "host_start_ticks": "100",
        "child_start_ticks": "101",
    }
    t = _FakeTransport(state)

    rec = await sp.CodeSpaceSpawner(t).recover_record("sess1")

    assert rec is not None
    assert rec.session_id == "sess1"
    assert rec.host_pid == 111
    assert rec.child_pid == 222
    assert rec.port == 0
    assert rec.nonce == "secure-nonce"
    assert rec.endpoint["remote_port"] == 51000
    assert rec.endpoint["local_port"] == 0
    assert rec.endpoint["reverse_forwards"] == ["9857:127.0.0.1:61234"]
    assert rec.extra["recovered_from_remote"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("liveness", ["0:1", "1:0"])
async def test_remote_authority_reaps_asymmetric_survivor(
    monkeypatch,
    liveness,
):
    _patch_common(monkeypatch)
    state = {
        "version": 2,
        "session_id": "sess1",
        "pid": 111,
        "child_pid": 222,
        "port": 51000,
        "nonce": "secure-nonce",
        "state": "running",
        "boot_id": "boot-one",
        "host_start_ticks": "100",
        "child_start_ticks": "101",
    }
    t = _FakeTransport(state, liveness=liveness)

    with pytest.raises(sp.RemoteHostDeadError):
        await sp.CodeSpaceSpawner(t).recover_record("sess1")
    assert any("kill -TERM -- -111" in command for command in t.runs)


@pytest.mark.asyncio
async def test_remote_authority_preserves_host_for_terminal_replay(monkeypatch):
    _patch_common(monkeypatch)
    state = {
        "version": 2,
        "session_id": "sess1",
        "pid": 111,
        "child_pid": 222,
        "port": 51000,
        "nonce": "secure-nonce",
        "state": "child_exited",
        "child_exit_code": 7,
        "boot_id": "boot-one",
        "host_start_ticks": "100",
        "child_start_ticks": "101",
    }
    transport = _FakeTransport(state, liveness="1:0")

    rec = await sp.CodeSpaceSpawner(transport).recover_record("sess1")

    assert rec is not None
    assert rec.session_id == "sess1"
    assert rec.host_pid == 111
    assert rec.child_pid == 222
    assert not any("kill -TERM -- -111" in command for command in transport.runs)


@pytest.mark.asyncio
async def test_poll_state_ignores_stale_replacement_record():
    class StaleThenLive(_FakeTransport):
        def __init__(self):
            super().__init__({})
            self.polls = 0

        async def run(self, command, *, timeout=60.0):
            if command.startswith("cat "):
                self.polls += 1
                nonce = "old" if self.polls == 1 else "new"
                return (0, json.dumps({
                    "session_id": "sess1",
                    "nonce": nonce,
                    "pid": 111,
                    "child_pid": 222,
                    "port": 51000,
                }), "")
            return await super().run(command, timeout=timeout)

    transport = StaleThenLive()
    spawner = sp.CodeSpaceSpawner(transport, ready_timeout=2.0)

    state = await spawner._poll_state(
        "/home/vscode/.agent-bridge/session-hosts/host-sess1.json",
        "/tmp/host.log",
        session_id="sess1",
        nonce="new",
    )

    assert state["nonce"] == "new"
    assert transport.polls == 2


@pytest.mark.asyncio
async def test_remote_authority_malformed_liveness_is_inconclusive(monkeypatch):
    _patch_common(monkeypatch)
    state = {
        "version": 2,
        "session_id": "sess1",
        "pid": 111,
        "child_pid": 222,
        "port": 51000,
        "nonce": "secure-nonce",
        "state": "running",
        "boot_id": "boot-one",
        "host_start_ticks": "100",
        "child_start_ticks": "101",
    }
    t = _FakeTransport(state, liveness="warning\n1:1")

    with pytest.raises(ConnectionError, match="inconclusive"):
        await sp.CodeSpaceSpawner(t).recover_record("sess1")

    assert not any("kill -TERM" in command for command in t.runs)


@pytest.mark.asyncio
async def test_remote_authority_read_failure_is_inconclusive(monkeypatch):
    _patch_common(monkeypatch)

    class FailedReadTransport(_FakeTransport):
        async def run(self, command, *, timeout=60.0):
            if command.startswith("if test -f "):
                return (255, "", "ssh transport failed")
            return await super().run(command, timeout=timeout)

    with pytest.raises(ConnectionError, match="read failed"):
        await sp.CodeSpaceSpawner(FailedReadTransport({})).recover_record("sess1")


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
async def test_codespace_spawner_fails_fast_on_bundle_preflight(monkeypatch):
    _patch_common(monkeypatch)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    t = _FakeTransport(
        state,
        preflight_result=(1, "", "ModuleNotFoundError: missing_dependency"),
    )

    with pytest.raises(RuntimeError, match="bundle preflight failed") as exc_info:
        await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
            ["copilot"], session_id="s",
        )

    assert "missing_dependency" in str(exc_info.value)
    assert not any("setsid nohup" in command for command in t.runs)


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
@pytest.mark.parametrize("failure", ["start", "serving"])
async def test_required_relay_failure_aborts_before_acp_ready(
    monkeypatch,
    failure,
):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    if failure == "start":
        _FakeRelay.start_error = RuntimeError("reverse-forward failed")

    class CleanupTransport(_FakeTransport):
        async def run(self, command, *, timeout=60.0):
            if command.startswith("python3 -c "):
                self.runs.append(command)
                return 0, "__REAPED__", ""
            return await super().run(command, timeout=timeout)

    state = {"pid": 1, "child_pid": 2, "port": 51000}
    transport = CleanupTransport(
        state,
        reverse_forwards=["9857:127.0.0.1:61234"],
        probe_result=(
            (0, "OK\n", "")
            if failure == "start"
            else (1, "", "connection refused")
        ),
    )

    with pytest.raises(
        endpoints_mod.CredentialRelayReadinessError,
        match="credential relay",
    ):
        await sp.CodeSpaceSpawner(
            transport,
            ready_timeout=5,
            require_relay_ready=True,
            relay_ready_timeout=0.01,
        ).spawn(["copilot"], session_id="s")

    assert _FakeRelay.instances[0].stopped == 1
    assert _FakeForward.instances[-1].cancelled is True
    assert any(command.startswith("python3 -c ") for command in transport.runs)


@pytest.mark.asyncio
async def test_required_relay_disabled_path_remains_auth_light(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", _FakeRelay)
    state = {"pid": 1, "child_pid": 2, "port": 51000}
    transport = _FakeTransport(state)

    spawned = await sp.CodeSpaceSpawner(
        transport,
        ready_timeout=5,
        require_relay_ready=True,
    ).spawn(["copilot"], session_id="s")

    assert spawned.local_port == 49555
    assert spawned.relay == []
    assert _FakeRelay.instances == []


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


def test_build_remote_exec_args_carries_required_reverse_forward():
    from ssh_manager import build_remote_exec_args

    cfg = _probe_cfg()
    spec = "127.0.0.1:9857:127.0.0.1:61234"
    cfg = replace(
        cfg,
        extra_options={
            **cfg.extra_options,
            "ExitOnForwardFailure": "no",
        },
    )
    argv = build_remote_exec_args(
        cfg,
        "copilot --acp --stdio",
        reverse_forwards=[spec],
    )

    assert ["-o", "ExitOnForwardFailure=yes"] == argv[
        argv.index("ExitOnForwardFailure=yes") - 1:
        argv.index("ExitOnForwardFailure=yes") + 1
    ]
    assert argv[argv.index("-R") + 1] == spec
    assert "ExitOnForwardFailure=no" not in argv
    assert argv[-2] == cfg.ssh_target
    assert argv[-1] == "copilot --acp --stdio"


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
def _install_fake_provision_binstub(monkeypatch, recorder=None):
    """Resolve the dispatch-path provision command over the PROCESS BOUNDARY
    (#1643): make the ``agent-codespaces`` binstub present and its
    ``provision-command`` CLI print a sentinel command. There is **no** in-process
    ``agent_codespaces`` import fallback anymore -- the daemon runs from its own
    isolated venv and never imports a provider -- so the seam is exercised purely
    via ``shutil.which`` + ``subprocess.run``."""
    import shutil
    import subprocess

    _real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda name, *a, **k: (
            "/bin/agent-codespaces" if name == "agent-codespaces"
            else _real_which(name, *a, **k)
        ),
    )

    _real_run = subprocess.run

    def _fake_run(argv, *a, **k):
        if (
            isinstance(argv, (list, tuple)) and len(argv) >= 2
            and argv[1] == "provision-command"
        ):
            if recorder is not None:
                recorder.append(1)
            return subprocess.CompletedProcess(
                list(argv), 0, "PROVISION_HELPERS_CMD", "",
            )
        return _real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", _fake_run)


@pytest.mark.asyncio
async def test_codespace_dispatch_redeploys_auth_helpers(monkeypatch):
    """On the codespace boundary, spawn() re-asserts the ADO/git auth helpers
    (Stage-4 provision) BEFORE launching the dispatched agent, so a dispatched
    agent isn't left on a reboot-stale VS Code helper (#733 T2)."""
    _patch_common(monkeypatch)
    _install_fake_provision_binstub(monkeypatch)
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
    _install_fake_provision_binstub(monkeypatch, recorder=calls)
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
    """When the ``agent-codespaces`` binstub is absent, the dispatch-path helper
    (re)deploy is silently skipped and the launch still proceeds (best-effort,
    #733 T2 / #1643 -- no in-process import fallback, so a missing binstub is the
    degrade condition)."""
    _patch_common(monkeypatch)
    import shutil
    _real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda name, *a, **k: (
            None if name == "agent-codespaces" else _real_which(name, *a, **k)
        ),
    )
    state = {"pid": 1, "child_pid": 2, "port": 51000,
             "protocol_version": proto.PROTOCOL_VERSION}
    t = _FakeTransport(state)
    spawned = await sp.CodeSpaceSpawner(t, ready_timeout=5).spawn(
        ["copilot", "--acp", "--stdio"], session_id="s3",
    )
    assert any("setsid nohup" in c for c in t.runs)  # launch still happened
    assert spawned.host_pid == 1
