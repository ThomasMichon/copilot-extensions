"""No-prompt create exposes readiness without admitting a model turn."""

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from agent_bridge import __main__ as core
from agent_bridge.client import BridgeClientError
from agent_bridge.session_targeting_cli import _cmd_create

pytestmark = pytest.mark.contract("agent_bridge.create.readiness")


@pytest.mark.parametrize("status,turns,ready", [("idle", 0, True), ("starting", 0, False), ("running", 1, False)])
def test_no_prompt_create_reports_observed_ready_identity(monkeypatch, status, turns, ready):
    output = []

    class Client:
        def get_session(self, target):
            if target == "example@local":
                raise BridgeClientError(404, "not found")
            assert target == "bridge-a"
            return {"status": status, "turn_count": turns,
                    "acp_session_id": "native-a", "target_dir": "/example/worktree"}

    monkeypatch.setattr(core, "_get_client", Client)
    monkeypatch.setattr(core, "_caller_id_for", lambda args: "dispatch")
    monkeypatch.setattr("agent_bridge.resume_handoff_cli.reject_singleton_create", lambda **kw: None)
    monkeypatch.setattr(core, "_resolve_target", lambda *args, **kw: "bridge-a")
    monkeypatch.setattr(core, "_connection_identity", lambda *args: {
        "repo": "example", "worktree_id": "worktree-a", "session_id": "bridge-a",
    })
    monkeypatch.setattr(core, "_submit_and_stream", lambda *a, **kw: pytest.fail("no model prompt allowed"))
    monkeypatch.setattr(core, "_json_out", output.append)
    args = argparse.Namespace(target="example@local", prompt=None, prompt_file=None,
                              json=True, charter="reviewer")
    _cmd_create(args)
    assert output == [{
        "session_id": "bridge-a", "ready": ready,
        "status": "awaiting_first_prompt" if ready else status,
        "connection": {"repo": "example", "worktree_id": "worktree-a",
                       "session_id": "bridge-a", "native_session_id": "native-a",
                       "target_dir": "/example/worktree"},
    }]


def test_dispatch_ordinary_calls_parse_with_real_bridge_parser(monkeypatch):
    dispatch_src = Path(__file__).resolve().parents[2] / "agent-dispatch" / "src"
    monkeypatch.syspath_prepend(str(dispatch_src))
    from agent_dispatch import bridge, ordinary_bridge

    parser = core.build_parser()
    created = {
        "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
        "connection": {"repo": "example", "worktree_id": "worktree-a",
                       "native_session_id": "native-a"},
    }
    reservation = {"state": "reserving", "agent_info": None, "conversation_retired": False}
    calls = []

    class Client:
        def get_reservation(self, key):
            return reservation

        def record_created_agent(self, key, **facts):
            reservation["agent_info"] = facts["agent_info"]

    def run(argv, **kwargs):
        args = parser.parse_args(argv[1:])
        assert args.json is True
        calls.append(args.command)
        if args.command == "create":
            assert args.target == "example@local" and args.charter == "reviewer"
            assert args.prompt is None and args.prompt_file is None
            result = created
        elif args.command == "send":
            assert args.target == "bridge-a" and args.no_wait and args.prompt_file == "-"
            assert args.expected_native_session_id == "native-a"
            assert kwargs["input"].startswith("assigned task")
            result = {"session_id": "bridge-a", "outcome": "delivered", "native_session_id": "native-a"}
        else:
            assert args.session_id == "bridge-a"
            if args.command == "resume":
                assert args.strict
            result = {"session_id": "bridge-a", "status": "stopped"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(ordinary_bridge, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge, "registered_agent_project", lambda *args, **kw: None)
    task = {"spawn_reservation_key": "reservation-a", "generation": 0}
    kwargs = {"client": Client(), "agent": "local", "project": "example",
              "charter": "reviewer", "prompt": "assigned task"}
    handle = ordinary_bridge.start(task, **kwargs)
    ordinary_bridge.start(task, **kwargs)
    ordinary_bridge.observe(handle)
    assert calls == ["create", "send", "status", "resume", "send", "wait"]
