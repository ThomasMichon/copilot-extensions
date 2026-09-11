"""Retirement does not depend on launch admission or application listeners."""

import asyncio
import json
import shlex
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_codespaces import __main__ as cli
from agent_codespaces import execution_claims as modes
from agent_codespaces import lease, native_transport, relay_readiness


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(lease, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease, "_LOCK_FILE", tmp_path / "lease.lock")
    monkeypatch.setattr(lease, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: tmp_path / "locks")
    monkeypatch.setattr(native_transport, "InputPump", lambda: SimpleNamespace(closed=threading.Event()))
    monkeypatch.setattr(native_transport.signal, "signal", lambda *a: None)
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)


def argv(*options):
    return [
        "native-transport", "example-space", "--owner", "example-worktree",
        "--execution-id", "native-one", "--generation", "generation-one",
        "--require-relay", "--no-plugin-staging", "--resume-infrastructure", *options,
    ]


@pytest.mark.parametrize("failure", ["relay", "collision"])
def test_pre_resource_failure_can_abort_exact_generation(state, ssh_runtime, monkeypatch, failure):
    events, manager = ssh_runtime
    monkeypatch.setattr(relay_readiness, "relay_ping", lambda _: failure != "relay")
    options = ["--reverse-forward", "9857:1234"] if failure == "collision" else []
    assert cli.main(argv(*options)) == (2 if failure == "collision" else 69)
    assert modes.get("example-space")["infrastructureStopped"] is True
    assert events == ["lock", "unlock"]  # reservation lock only; no infrastructure
    manager.ensure_connected.assert_not_awaited()
    with pytest.raises(lease.ClaimConflict):
        modes.abort_unlaunched("example-space", "example-worktree", ("native-one", "wrong"))
    assert cli.main([
        "native-abort", "example-space", "--owner", "example-worktree",
        "--execution-id", "native-one", "--generation", "generation-one",
    ]) == 0
    assert modes.get("example-space") is None
    proof = modes.retirement("example-space", "example-worktree", ("native-one", "generation-one"))
    assert proof["retired"] and proof["noLaunch"]
    with pytest.raises(lease.CoordinationRejected):
        modes.reserve("example-space", "example-worktree", "native-one", "generation-one", "native")
    assert modes.reserve("example-space", "example-worktree", "next", "next", "acp")


def test_failed_reconnect_does_not_erase_prior_cleanup_uncertainty(state, ssh_runtime, monkeypatch):
    modes.reserve("example-space", "example-worktree", "native-one", "generation-one", "native")
    modes.mark("example-space", "example-worktree", ("native-one", "generation-one"), infrastructureStopped=False)
    monkeypatch.setattr(relay_readiness, "relay_ping", lambda _: False)
    assert cli.main(argv()) == 69
    assert modes.get("example-space")["infrastructureStopped"] is False
    with pytest.raises(lease.CoordinationRejected, match="uncertain"):
        modes.abort_unlaunched("example-space", "example-worktree", ("native-one", "generation-one"))


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_resource_entry_remains_uncertain_until_actual_cleanup(state, ssh_runtime, monkeypatch, cleanup_fails):
    _, manager = ssh_runtime
    monkeypatch.setattr(relay_readiness, "relay_ping", lambda _: True)
    monkeypatch.setattr(relay_readiness, "remote_relay_ready", AsyncMock(return_value=False))

    async def connect(*a):
        assert modes.get("example-space")["infrastructureStopped"] is False
        return SimpleNamespace(config=SimpleNamespace())

    manager.ensure_connected.side_effect = connect
    if cleanup_fails:
        manager.disconnect.side_effect = OSError("disconnect unconfirmed")
        with pytest.raises(OSError, match="disconnect unconfirmed"):
            cli.main(argv())
    else:
        assert cli.main(argv()) == 69
    manager.ensure_connected.assert_awaited_once()
    manager.disconnect.assert_awaited_once()
    assert modes.get("example-space")["infrastructureStopped"] is not cleanup_fails
    if cleanup_fails:
        with pytest.raises(lease.CoordinationRejected, match="uncertain"):
            modes.abort_unlaunched("example-space", "example-worktree", ("native-one", "generation-one"))
    else:
        assert modes.abort_unlaunched("example-space", "example-worktree", ("native-one", "generation-one"))


def test_retirement_mode_rejects_application_forward_options(monkeypatch):
    monkeypatch.setattr(native_transport, "command", lambda _: pytest.fail("invalid command reached provider"))
    with pytest.raises(SystemExit) as exc:
        cli.main(argv("--retirement-only", "--local-forward", "4321:4321"))
    assert exc.value.code == 2


def test_progress_contains_only_fixed_phase_status_and_identity(capsys):
    args = SimpleNamespace(execution_id="execution", generation="generation", secret="never-publish")
    native_transport.emit_progress(args, "local-config")
    assert json.loads(capsys.readouterr().out) == {
        "event": "progress", "executionId": "execution", "generation": "generation",
        "phase": "local-config", "status": "started",
    }
    with pytest.raises(ValueError):
        native_transport.emit_progress(args, "never-publish")
    assert capsys.readouterr().out == ""


def test_native_preparation_reuses_sanitized_connect_checkpoints(state, ssh_runtime, monkeypatch, capsys):
    monkeypatch.setattr(relay_readiness, "relay_ping", lambda _: True)
    monkeypatch.setattr(relay_readiness, "remote_relay_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(cli, "_provision_relay_helpers", AsyncMock(return_value=True))
    monkeypatch.setattr(native_transport, "serve", AsyncMock(return_value=0))
    assert cli.main(argv()) == 0
    progress = [json.loads(line) for line in capsys.readouterr().out.splitlines()
                if json.loads(line).get("event") == "progress"]
    phases = {(row["phase"], row["status"]) for row in progress}
    for phase in ("local-config", "owner-admission", "ssh-to-target", "target-auth-env", "target-binstub"):
        assert (phase, "started") in phases
        assert (phase, "reached") in phases
    assert all(set(row) == {"event", "executionId", "generation", "phase", "status"} for row in progress)


@pytest.mark.asyncio
async def test_required_host_resources_fail_before_native_launch_on_old_remote(capsys):
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=SimpleNamespace(
        exit_code=0, stdout=json.dumps({"capability": "codespace-native-host-v1", "supported": True}),
    )))
    args = SimpleNamespace(
        execution_id="execution", generation="generation", name="example-space",
        require_host_resources=True, retirement_only=False,
    )
    assert await native_transport.serve(args, manager, object(), "") == 69
    manager.exec_command.assert_awaited_once()
    assert json.loads(capsys.readouterr().out) == {
        "event": "failed", "code": "resource_capability_unavailable",
        "executionId": "execution", "generation": "generation",
    }


@pytest.mark.asyncio
async def test_retirement_control_skips_all_forwarding_and_rejects_activation(state, monkeypatch):
    import ssh_manager

    modes.reserve("example-space", "example-worktree", "native-one", "generation-one", "native")
    modes.mark("example-space", "example-worktree", ("native-one", "generation-one"), launchRequested=True)
    monkeypatch.setattr(ssh_manager, "LocalForward", lambda *a, **k: pytest.fail("retirement bound a listener"))
    monkeypatch.setattr("agent_codespaces.sessions.sync_codespace_sessions", lambda _: {"ok": True})
    loop = asyncio.get_running_loop()
    replies = asyncio.Queue()
    results = []

    class Output:
        def write(self, text):
            value = json.loads(text)
            if "id" in value:
                results.append(value)
                loop.call_soon_threadsafe(replies.put_nowait, value)

        def flush(self):
            pass

    monkeypatch.setattr(native_transport, "sys", SimpleNamespace(stdout=Output()))
    requests = [
        {"id": method, "method": method, "executionId": "native-one", "generation": "generation-one"}
        for method in ("launch", "activate", "message", "status", "stop")
    ]
    requests.insert(3, {"id": "stale", "method": "stop", "executionId": "native-one", "generation": "wrong"})

    class Input:
        index = 0

        async def next(self):
            if self.index:
                await asyncio.wait_for(replies.get(), 2)
            if self.index == len(requests):
                return None
            value = requests[self.index]
            self.index += 1
            return json.dumps(value).encode()

    remote_actions = []

    async def execute(name, command, **kwargs):
        words = shlex.split(shlex.split(command)[2])
        if words[1] == "native-host":
            action = words[2]
            remote_actions.append(action)
            if action == "capabilities":
                result = {"capability": "codespace-native-host-v1", "supported": True}
            elif action == "status":
                result = {"state": "unrepresented", "host": {"port": 1234}, "activated": False}
            else:
                assert action == "stop"
                assert words[3:6] == ["native-one", "--expected-generation", "generation-one"]
                assert json.loads(kwargs["input_bytes"]) == {"owner": "example-worktree", "codespace": name}
                result = {"state": "stopped", "retired": True, "exitCode": 7}
        elif words[1] == "installer-readiness":
            result = {"module": "agent-bridge/runtime", "state": "ready"}
        else:
            assert words[1:] == ["service", "start"]
            result = {}
        return SimpleNamespace(exit_code=0, stdout=json.dumps(result))

    args = SimpleNamespace(
        name="example-space", effort="example-worktree", execution_id="native-one",
        generation="generation-one", retirement_only=True,
        local_forward=[], reverse_forward=[], native_input=Input(),
    )
    assert await native_transport.serve(args, SimpleNamespace(exec_command=execute), object(), "") == 0
    assert remote_actions == ["capabilities", "status", "stop"]
    assert [value["ok"] for value in results] == [False, False, False, False, True, True]
    assert "localPort" not in results[-2]["result"]
    assert results[-1]["result"]["retired"] is True
    assert modes.get("example-space") is None
    assert args.native_retirement_recorded and args.native_retired
