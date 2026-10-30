"""Compact resume acknowledgements must be followed by exact full facts."""

import subprocess

import pytest

from agent_dispatch import bridge, ordinary_bridge
from tests.test_ordinary_assignment import _start, assigned as assigned
from tests._hosting_owner import admission_owner as admission_owner

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


@pytest.mark.parametrize("change", [
    None, {"acp_session_id": "replaced", "durable_session_id": "replaced"},
    {"target_dir": "/different-workspace"}, {"worktree_id": "different-worktree"},
    {"session_id": "different-bridge"}, {"project": "different-project"},
    {"status": "running"}, {"read_only": True}, {"at_rest": None},
])
def test_ordinary_assignment_retained_resume_reads_full_identity(assigned, monkeypatch, change):
    queue, task, _, _, response, calls = assigned
    _start(assigned)
    calls.clear()
    original = ordinary_bridge.subprocess.run

    def run(argv, **kwargs):
        if argv[:3] == ["bridge", "--json", "status"] and "--full" not in argv:
            calls.append("compact-status")
            return subprocess.CompletedProcess(argv, 0, '{"status":"stopped"}', "")
        if argv[:3] == ["bridge", "--json", "resume"]:
            assert argv == [
                "bridge", "--json", "resume", "bridge-a", "--require-agent-hosting", "--strict",
            ]
            calls.append("resume")
            return subprocess.CompletedProcess(
                argv, 0, '{"session_id":"bridge-a","status":"idle","verb":"resumed"}', "",
            )
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    if change is not None:
        response.update(change)
        with pytest.raises(bridge.BridgeCarriedSessionBusy):
            _start(assigned)
        assert "send" not in calls
        assert queue.get_reservation(task["spawn_reservation_key"]).agent_info["acp_session_id"] == "native-a"
    else:
        _start(assigned)
        assert calls.index("resume") < calls.index("status") < calls.index("send")
    assert "create" not in calls


@pytest.mark.parametrize("output,code", [
    ('{"session_id":"bridge-a","status":"idle"}', 0), ("{}", 0), ("not JSON", 0),
    ("", 1),
])
def test_ordinary_assignment_missing_full_info_never_sends(assigned, monkeypatch, output, code):
    _, _, _, _, _, calls = assigned
    original = ordinary_bridge.subprocess.run

    def run(argv, **kwargs):
        if argv[:3] == ["bridge", "--json", "status"]:
            calls.append("status")
            return subprocess.CompletedProcess(argv, code, output, "")
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        _start(assigned)
    assert calls == ["record", "resume", "status"]
