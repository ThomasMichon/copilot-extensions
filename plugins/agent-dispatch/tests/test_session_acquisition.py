"""Real dispatch request construction and truthful CLI/ACP cleanup handles."""

from __future__ import annotations

import json
import subprocess
import argparse
import contextlib
import io

import pytest

from agent_dispatch import bridge, session_acquisition, spawn_factories
from agent_dispatch.create_cli import _do_spawn

pytestmark = pytest.mark.contract("agent_dispatch.session_acquisition")


def task(**changes):
    return {
        "id": "task-a", "generation": 2, "repo": "example",
        "spawn_reservation_key": "reservation-a", "spawn_worktree": "worktree-a",
        "spawn_worktree_ownership": "reused", "spawn_conversation_retired": False,
        "spawn_session_handle": "local-body:bridge-a", **changes,
    }


@pytest.fixture
def transport(monkeypatch):
    calls = []
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])

    def run(argv, **kwargs):
        payload = json.loads(kwargs["input"])
        calls.append((argv, payload))
        native = "native-cli" if payload["mode"] == "cli" else "native-acp"
        response = {
            **{key: payload[key] for key in ("mode", "task_id", "task_generation", "worktree_id", "delivery_id")},
            "session_id": native if payload["mode"] == "cli" else "bridge-a",
            "native_session_id": native, "acquired": "resumed",
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("mode", ["cli", "acp"])
def test_actual_request_carries_owner_facts_and_keeps_distinct_identities(transport, mode):
    sample = task(spawn_session_handle="native-cli" if mode == "cli" else "local-body:bridge-a")
    result = session_acquisition.acquire(sample, mode=mode, prompt="A & B", project="example")
    argv, payload = transport[0]
    assert argv == ["bridge", "acquire", "--request-file", "-"]
    assert payload["task_generation"] == 2
    assert payload["reservation_key"] == "reservation-a"
    assert payload["conversation_retired"] is False
    assert payload["prior_session_id"] == ("native-cli" if mode == "cli" else "bridge-a")
    assert payload["prompt"] == "A & B"
    handle = session_acquisition.handle(result)
    assert handle["session"] == ("native-cli" if mode == "cli" else "local-body:bridge-a")
    assert handle["native_session_id"] != "bridge-a"
    assert not any("embody" in args or "create" in args or "send" in args for args, _ in transport)


@pytest.mark.parametrize("mode", ["cli"])
def test_factories_use_bridge_acquisition_and_stable_delivery(transport, mode):
    factory = spawn_factories.make_embody_spawn if mode == "cli" else spawn_factories.make_headless_spawn
    sample = task(spawn_session_handle=None)
    spawn = factory()
    ok, handle = spawn(sample)
    assert ok and handle["mode"] == mode
    ok, _ = spawn(sample)
    assert ok
    assert transport[0][1] == transport[1][1]
    assert spawn.allocation_interface == mode


def test_absent_bridge_does_not_launch_directly(monkeypatch):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: None)
    with pytest.raises(bridge.BridgeUnavailable, match="acquisition capability"):
        session_acquisition.acquire(task(), mode="cli", prompt="work")


def test_missing_retirement_marker_is_conveyed_as_unknown(transport):
    sample = task()
    sample.pop("spawn_conversation_retired")
    session_acquisition.acquire(sample, mode="acp", prompt="work")
    assert transport[0][1]["conversation_retired"] is None


def test_retired_carried_handle_is_not_revived(transport):
    session_acquisition.acquire(task(spawn_conversation_retired=True), mode="acp", prompt="work")
    assert transport[0][1]["prior_session_id"] is None
    assert transport[0][1]["conversation_retired"] is True


@pytest.mark.parametrize("backend,mode", [("embody", "cli")])
def test_one_shot_uses_the_same_constructed_boundary(transport, backend, mode):
    args = argparse.Namespace(
        spawn_backend=backend, spawn_agent="task-worker", run_async=True, verify_timeout=7,
    )
    result, via, handle = _do_spawn(args, task(spawn_session_handle=None))
    assert result.returncode == 0 and via == "agent-bridge"
    assert handle["mode"] == mode
    assert transport[0][1]["mode"] == mode
    assert transport[0][1]["verify_timeout"] == 7


@pytest.mark.parametrize("failure", ["exit", "timeout", "oserror"])
def test_observation_failure_preserves_successful_acquisition_and_cleanup_handle(
    transport, monkeypatch, capsys, failure,
):
    original = session_acquisition.subprocess.run
    def run(argv, **kwargs):
        if "acquire" in argv:
            return original(argv, **kwargs)
        assert argv == ["bridge", "wait", "bridge-a", "--json"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        if failure == "oserror":
            raise OSError("wait transport unavailable")
        return subprocess.CompletedProcess(argv, 1, "", "wait unavailable")
    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    result = session_acquisition.acquire(task(), mode="acp", prompt="work", wait=True)
    handle = session_acquisition.handle(result)
    assert result.returncode == 0
    assert handle["session"] == "local-body:bridge-a"
    assert handle["worktree"] == "worktree-a"
    assert handle["native_session_id"] == "native-acp"
    assert handle["observation_error"]
    assert "observation failed" in result.stderr
    assert len(transport) == 1


@pytest.mark.parametrize("typed", [False, True])
def test_pre_request_protocol_refusal_is_not_an_ambiguous_launch(monkeypatch, typed):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    detail = {
        "code": "unsupported_acquisition_protocol",
        "message": "daemon does not support strict session acquisition",
        "effect": "not_started",
    } if typed else "daemon does not support strict session acquisition"
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, json.dumps({
            "ok": False, "status": 426, "detail": detail,
        }), "")
    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    result = session_acquisition.acquire(task(), mode="acp", prompt="work")
    assert result.returncode == 1
    assert "does not support strict session acquisition" in result.stderr
    assert calls == [["bridge", "acquire", "--request-file", "-"]]


def test_unqualified_protocol_string_failure_remains_ambiguous(monkeypatch):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(session_acquisition.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 1, json.dumps({
            "status": 500, "detail": "daemon does not support strict session acquisition",
        }), "unknown failure"))
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        session_acquisition.acquire(task(), mode="acp", prompt="work")


def test_cli_factory_unsupported_options_fail_without_deferred_retry(monkeypatch):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    calls = []
    def run(argv, **kwargs):
        payload = json.loads(kwargs["input"])
        calls.append(payload)
        assert payload["charter"] == "configured-charter"
        return subprocess.CompletedProcess(argv, 1, json.dumps({
            "status": 409, "detail": {
                "code": "cli_options_unsupported",
                "message": "CLI acquisition cannot yet prove execution overrides",
                "effect": "not_started",
            },
        }), "")
    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    ok, failure = spawn_factories.make_embody_spawn(charter="configured-charter")(task())
    assert not ok
    assert not failure.get("deferred")
    assert "execution overrides" in failure["error"]
    assert len(calls) == 1


def test_one_shot_cli_charter_is_not_silently_discarded(monkeypatch):
    captured = {}

    def acquire(task, **options):
        captured.update(options)
        return subprocess.CompletedProcess([], 1, "", "CLI charter capability unavailable")

    monkeypatch.setattr(session_acquisition, "acquire", acquire)
    args = argparse.Namespace(spawn_backend="embody", spawn_agent="local",
                              spawn_charter="reviewer", run_async=True)
    result, _, handle = _do_spawn(args, task())
    assert captured["charter"] == "reviewer"
    assert result.returncode == 1 and handle["not_started"]


def test_old_bridge_cli_argparse_refusal_is_proven_pre_effect(monkeypatch):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    parser = argparse.ArgumentParser(prog="agent-bridge")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("create")
    commands.add_parser("send")
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), pytest.raises(SystemExit) as error:
            parser.parse_args(argv[1:])
        return subprocess.CompletedProcess(argv, error.value.code, "", stderr.getvalue())
    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    ok, failure = spawn_factories.make_embody_spawn()(task())
    assert not ok and not failure.get("deferred")
    assert failure["error"] == "bridge executable does not support session acquisition"
    assert calls == [["bridge", "acquire", "--request-file", "-"]]


@pytest.mark.parametrize("exit_code,stdout,error", [
    (1, "", "agent-bridge: error: argument command: invalid choice: 'acquire' (choose from 'create')"),
    (2, '{"effect":"unknown"}', "agent-bridge: error: argument command: invalid choice: 'acquire' (choose from 'create')"),
    (2, "", "another-tool: error: argument command: invalid choice: 'acquire' (choose from 'create')"),
    (2, "", "agent-bridge: error: argument --request-file: invalid choice: '-' (choose from 'file')"),
])
def test_argparse_lookalikes_do_not_claim_absent_launch(monkeypatch, exit_code, stdout, error):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(session_acquisition.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, exit_code, stdout, "usage: agent-bridge ...\n" + error))
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        session_acquisition.acquire(task(), mode="cli", prompt="work")


def test_postlaunch_dispatch_fence_refusal_defers_instead_of_authorizing_cleanup(monkeypatch):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(session_acquisition.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 1, json.dumps({
            "detail": {"code": "dispatch_fence_changed", "message": "task changed"},
        }), "task changed"))
    args = argparse.Namespace(spawn_backend="embody", spawn_agent="worker", run_async=True)
    result, _, handle = _do_spawn(args, task(spawn_worktree_ownership="created"))
    assert result.returncode == 1
    assert handle["deferred"] is True
    assert handle["worktree"] == "worktree-a"


@pytest.mark.parametrize("error,expected", [
    (FileNotFoundError("missing"), bridge.BridgeUnavailable),
    (subprocess.TimeoutExpired(["bridge"], 1), bridge.BridgeCarriedSessionBusy),
])
def test_transport_failures_are_typed_at_acquisition_boundary(monkeypatch, error, expected):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    def run(*args, **kwargs):
        raise error
    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    with pytest.raises(expected):
        session_acquisition.acquire(task(), mode="acp", prompt="work")


@pytest.mark.parametrize("code", ["dispatch_capability_missing", "dispatch_authority_unavailable", "worktree_unknown", "head_unknown"])
def test_unattested_refusal_code_never_authorizes_cleanup(monkeypatch, code):
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(session_acquisition.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 1, json.dumps({"detail": {"code": code}}), "refused"))
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        session_acquisition.acquire(task(), mode="acp", prompt="work")
