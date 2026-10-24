"""No-prompt create exposes readiness without admitting a model turn."""

import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

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


def test_dispatch_ordinary_calls_parse_with_real_bridge_parser(monkeypatch, tmp_path):
    dispatch_src = Path(__file__).resolve().parents[2] / "agent-dispatch" / "src"
    monkeypatch.syspath_prepend(str(dispatch_src))
    from agent_dispatch import bridge, hosting_policy, ordinary_bridge

    workspace = tmp_path / "execution-harness"
    (workspace / ".git").mkdir(parents=True)
    policy_calls = []

    def metadata_owner(argv, **kwargs):
        assert argv == ["metadata-owner", "repos", "find", "example", "--registered-only", "--json"]
        policy_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"path": str(workspace)}), "")

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_INSTALL_DIR", raising=False)
    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", lambda: ["metadata-owner"])
    monkeypatch.setattr(hosting_policy, "subprocess", SimpleNamespace(
        run=metadata_owner, SubprocessError=subprocess.SubprocessError,
    ))

    parser = core.build_parser()
    created = {
        "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
        "connection": {"repo": "example", "worktree_id": "worktree-a",
                       "native_session_id": "native-a", "target_dir": "/example/worktree"},
        "creation": {"worktree": {"creation_nonce": "nonce-a"}},
    }
    reservation = {
        "key": "reservation-a", "state": "reserving", "agent_info": None,
        "conversation_retired": False, "creation_key": "logical-work", "allocation_project": None,
        "allocation_revision": 1,
    }
    calls = []

    class Client:
        def get_reservation(self, key):
            return reservation

        def record_spawn_allocation(self, key, project):
            reservation["allocation_project"] = project
            return reservation

        def record_created_agent(self, key, **facts):
            reservation["agent_info"] = {
                **facts["agent_info"], "startup_invocation": facts["startup_invocation"],
            }

    def run(argv, **kwargs):
        args = parser.parse_args(argv[1:])
        assert args.json is True
        calls.append(args.command)
        if args.command == "create":
            assert args.target == "example@local" and args.charter == "reviewer"
            assert args.creation_key == "logical-work"
            assert args.prompt is None and args.prompt_file is None
            result = created
        elif args.command == "send":
            assert args.target == "bridge-a" and args.no_wait and args.native_invocation_file == "-"
            assert args.expected_native_session_id == "native-a"
            invocation = json.loads(kwargs["input"])
            assert invocation["prompt"].startswith("assigned task")
            result = {
                "version": 1, "session_id": "bridge-a", "outcome": "delivered",
                "native_session_id": "native-a", "invocation_key": invocation["invocation_key"],
                "turn_index": 0, "queued": False, "admission": "accepted",
            }
        else:
            assert args.session_id == "bridge-a"
            if args.command == "resume":
                assert args.strict
                result = {"session_id": "bridge-a", "status": "idle", "verb": "resumed"}
            elif args.command == "status" and args.full:
                result = {
                    "session_id": "bridge-a", "acp_session_id": "native-a",
                    "durable_session_id": "native-a", "worktree_id": "worktree-a",
                    "project": "example", "target_dir": "/example/worktree",
                    "status": "idle", "read_only": False, "at_rest": True, "liveness": None,
                }
            else:
                result = {"session_id": "bridge-a", "status": "stopped"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(ordinary_bridge, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge, "registered_agent_project", lambda *args, **kw: None)
    task = {"id": "task-a", "spawn_reservation_key": "reservation-a", "generation": 0}
    kwargs = {"client": Client(), "agent": "local", "project": "example",
              "charter": "reviewer", "prompt": "assigned task"}
    handle = ordinary_bridge.start(task, **kwargs)
    ordinary_bridge.start(task, **kwargs)
    ordinary_bridge.observe(handle)
    assert calls == ["create", "send", "send", "wait"]
    assert len(policy_calls) == 1
