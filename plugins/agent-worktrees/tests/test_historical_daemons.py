"""Historical recognition is not authority; native retirement stays owner-scoped."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from zdd import diagnostics

from agent_worktrees import historical_daemons as owner


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "status-monitor-routing").mkdir()
    for slot in ("old", "new"):
        environment = root / "versions" / slot / ".venv"
        environment.mkdir(parents=True)
        (environment.parent / ".install-complete.json").write_text(slot)
    (root / "status-monitor-control.token").write_text("contained-test-token")
    state = SimpleNamespace(
        closed=False, root=root, refs={}, alive={11: "110", 22: "220"}, actions=[], busy=False,
        shutdown_exit=False, refusal=None, changed=False, token="contained-test-token",
    )
    state.table = {
        "active": {"bind": "127.0.0.1", "port": 4022, "pid": 22, "process_start_time": "220"},
        "previous": {"bind": "127.0.0.1", "port": 4011, "pid": 11, "process_start_time": "110"},
    }
    class Ledger:
        @staticmethod
        def read_runtime_references(_root):
            return dict(state.refs)
        @staticmethod
        def replace_runtime_references(_root, name, values, *, expected):
            assert state.refs.get(name, []) == expected
            state.actions.append(("reference", values))
            if values:
                state.refs[name] = values
            else:
                state.refs.pop(name)
    helper = SimpleNamespace(_leases=Ledger, current_version=lambda _root: "new",
                             is_complete=lambda _root, slot: slot in {"old", "new"})
    state.helper = helper
    monkeypatch.setattr(owner.retention, "_runtime", lambda: helper)
    monkeypatch.setattr(owner.retention, "pending_slot", lambda *_: False)
    monkeypatch.setattr(owner.retention, "_exit_state", lambda target: target["pid"] not in state.alive)
    monkeypatch.setattr(owner.locks, "process_start_time", lambda pid: state.alive.get(pid))
    monkeypatch.setattr(owner.locks, "read_lock", lambda _: {"pid": 22, "start_time": "220"})
    monkeypatch.setattr(owner.routing, "read_table", lambda *a, **k: dict(state.table))
    monkeypatch.setattr(owner.breadcrumb, "read_breadcrumb", lambda _: None)
    monkeypatch.setattr(owner.breadcrumb, "recovery_refusal", lambda *a: state.refusal)
    ctx = SimpleNamespace(
        config_dir=root / "status-monitor-routing", group_launchers=False, is_launcher_image=None,
        list_candidates=lambda: [diagnostics.DaemonCandidate(pid=pid, start_time=start)
                                 for pid, start in state.alive.items()],
    )
    monkeypatch.setattr(owner.daemon_health, "_context", lambda: ctx)
    class Client:
        token = state.token
        def __init__(self, _root, endpoint):
            self.pid = endpoint.pid
        def _request(self, action, payload=None, **kwargs):
            if action == "health":
                slot = "old" if self.pid == 11 else "new"
                return {"pid": self.pid, "process_start_time": state.alive.get(self.pid),
                        "runtime_slot": slot, "runtime_prefix": str(root / "versions" / slot / ".venv"),
                        "status": "draining" if state.closed and self.pid == 11 else "ready", "published": True}
            assert state.refs, "code must be retained before the first lifecycle effect"
            state.actions.append((action, payload))
            if action == "drain":
                state.closed = True
                assert payload["force"] is False
                return {"drained": not state.busy}
            if action == "shutdown":
                if state.shutdown_exit:
                    state.alive.pop(self.pid)
                if state.changed:
                    state.table["active"] = {**state.table["active"], "generation": 3}
                return {"shutdown": True}
            raise AssertionError(action)
    monkeypatch.setattr(owner, "_client", Client)
    class Pin:
        def __init__(self, pid, birth):
            self.pid = pid
            assert birth == state.alive[pid]
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def exited(self, timeout=0):
            return self.pid not in state.alive
        def terminate(self, timeout=5):
            state.actions.append(("terminate", self.pid))
            state.alive.pop(self.pid)
    monkeypatch.setattr(owner, "PinnedProcess", Pin)
    monkeypatch.setattr(owner, "birth_identity", lambda pid: state.alive[pid])
    monkeypatch.setattr(owner.control, "_acquire_cutover_lock", lambda *a, **k:
                        SimpleNamespace(release=lambda: state.actions.append(("release", None))))
    return state


def candidate(world):
    return next(item for item in owner.inspect(world.root) if item["status"] == "candidate")


def test_selected_drained_peer_uses_pins_and_releases_only_after_exit(world):
    selected = candidate(world)
    assert "token" not in selected["evidence"]
    assert owner.apply(world.root, selected)["status"] == "applied"
    assert 11 not in world.alive and 22 in world.alive and not world.refs
    assert [action[0] for action in world.actions] == [
        "reference", "drain", "shutdown", "terminate", "reference", "release",
    ]


def test_busy_accepted_work_keeps_runtime_and_never_forces(world):
    selected = candidate(world)
    world.busy = True
    assert owner.apply(world.root, selected)["status"] == "pending"
    assert world.refs and 11 in world.alive
    assert not any(action[0] in {"shutdown", "terminate"} for action in world.actions)
    world.busy = False
    assert owner.apply(world.root, selected)["status"] == "applied"


def test_unknown_rpc_outcome_retains_original_identity(world, monkeypatch):
    selected = candidate(world)
    def unavailable(*a, **k):
        raise TimeoutError("contained unknown RPC")
    monkeypatch.setattr(owner, "_client", lambda *a: SimpleNamespace(_request=unavailable))
    # Before admission there is no effect, and a fresh observation is mandatory.
    assert owner.apply(world.root, selected)["status"] == "preserved"
    assert not world.refs


def test_reference_recovery_requires_positive_original_exit(world):
    selected = candidate(world)
    world.busy = True
    assert owner.apply(world.root, selected)["status"] == "pending"
    retained = owner._retained(world.root)[0]
    assert retained["status"] == "preserved"
    assert owner.apply(world.root, retained)["status"] == "preserved"
    world.alive.pop(11)
    retained = owner._retained(world.root)[0]
    assert retained["status"] == "candidate"
    assert owner.apply(world.root, retained)["status"] == "applied"
    assert not world.refs and 22 in world.alive


@pytest.mark.parametrize("protection", ["conditional", "other-reference", "pending", "current", "changed-slot"])
def test_owner_protections_never_become_disposal_authority(world, monkeypatch, protection):
    selected = candidate(world)
    if protection == "conditional":
        world.refusal = "conditional cutover requires owner-led recovery"
    elif protection == "other-reference":
        world.refs["other-owner"] = ["old"]
    elif protection == "pending":
        monkeypatch.setattr(owner.retention, "pending_slot", lambda *_: True)
    elif protection == "current":
        world.helper.current_version = lambda _: "old"
    else:
        (world.root / "versions" / "old" / ".install-complete.json").write_text("changed")
    assert owner.apply(world.root, selected)["status"] == "preserved"
    assert not any(action[0] in {"drain", "shutdown", "terminate"} for action in world.actions)


def test_route_change_after_shutdown_cannot_force_or_release_code_hold(world):
    world.changed = True
    assert owner.apply(world.root, candidate(world))["status"] == "pending"
    assert world.refs and 11 in world.alive
    assert not any(action[0] == "terminate" for action in world.actions)


def test_current_and_unattributed_census_generations_remain_visible(world):
    world.alive[33] = "330"
    findings = owner.inspect(world.root)
    assert len(findings) == 3
    assert [item["status"] for item in findings] == ["candidate", "preserved", "preserved"]


def test_native_identity_change_refuses_saved_observation(world):
    selected = candidate(world)
    world.alive[11] = "111"
    assert owner.apply(world.root, selected)["status"] == "preserved"
    assert not world.refs


def test_inspection_does_not_create_control_token(world, monkeypatch):
    monkeypatch.undo()
    from agent_worktrees import status_monitor_cutover as control
    token = control.control_token_path(world.root)
    token.unlink()
    with pytest.raises(FileNotFoundError):
        owner._client(world.root, owner.routing.Endpoint("127.0.0.1", 4000, 11))
    assert not token.exists()


def test_actual_native_cli_capability_is_available_without_full_cluster(monkeypatch, capsys):
    import json
    from agent_worktrees import __main__ as main

    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: pytest.fail("loaded full cluster"))
    monkeypatch.setattr(main, "_load_full_command_surface", lambda: pytest.fail("loaded full surface"))
    result = main._dispatch_lazy("status-monitor-cleanup", ["status-monitor-cleanup", "capabilities", "--json"])
    assert result == 0
    assert json.loads(capsys.readouterr().out)["detector"] == owner.DETECTOR


def test_unknown_drain_rpc_after_admission_keeps_exact_code_hold(world, monkeypatch):
    selected = candidate(world)
    original = owner._client
    def client(root, endpoint):
        value = original(root, endpoint)
        request = value._request
        def dispatch(action, *args, **kwargs):
            if action == "drain":
                raise TimeoutError("contained unknown drain result")
            return request(action, *args, **kwargs)
        value._request = dispatch
        return value
    monkeypatch.setattr(owner, "_client", client)
    result = owner.apply(world.root, selected)
    assert result["status"] == "pending" and world.refs and 11 in world.alive
    assert not any(action[0] in {"shutdown", "terminate"} for action in world.actions)


def test_real_module_entry_exposes_capability_without_project_or_provision(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "status-monitor-cleanup", "capabilities", "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    contract = json.loads(result.stdout)
    assert contract["detector"] == owner.DETECTOR
    assert contract["update_prerequisite"] is False
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize("later", [{}, {"resource": ""}, {"resource": "first"}])
def test_cli_batch_rejects_all_invalid_or_duplicate_resources_before_apply(tmp_path, monkeypatch, capsys, later):
    import argparse
    from agent_worktrees import historical_daemons_cli as cli, status_monitor_runtime

    request = tmp_path / "selection.json"
    request.write_text(json.dumps([{"resource": "first"}, later]), encoding="utf-8")
    calls = []
    def apply(root, finding):
        calls.append(finding)
        return {"resource": finding["resource"], "status": "applied", "reason": "confirmed"}
    monkeypatch.setattr(owner, "apply", apply)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    args = argparse.Namespace(operation="apply", request=request, json=True)
    assert cli.cmd_status_monitor_cleanup(args) == 1
    assert not calls
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"


def test_cli_batch_retains_completed_outcomes_after_later_owner_error(tmp_path, monkeypatch, capsys):
    import argparse
    from agent_worktrees import historical_daemons_cli as cli, status_monitor_runtime

    request = tmp_path / "selection.json"
    request.write_text(json.dumps([{"resource": "first"}, {"resource": "second"}]), encoding="utf-8")
    def apply(root, finding):
        if finding["resource"] == "second":
            raise ValueError("owner observation changed")
        return {"resource": finding["resource"], "status": "applied", "reason": "confirmed"}
    monkeypatch.setattr(owner, "apply", apply)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    args = argparse.Namespace(operation="apply", request=request, json=True)
    assert cli.cmd_status_monitor_cleanup(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["settled"] is False
    assert [(item["resource"], item["status"]) for item in result["actions"]] == [
        ("first", "applied"), ("second", "failed"),
    ]
    assert result["actions"][1]["reason"] == "owner observation changed"
