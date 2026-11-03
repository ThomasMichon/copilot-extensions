from __future__ import annotations

import copy
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from agent_procutil import process_retirement as native
from zdd import routing

from agent_worktrees import runtime_monitor_identity as identity
from agent_worktrees import runtime_monitor_retirements as retirements
from agent_worktrees.status_monitor_recovery_contract import CAPABILITY as RECOVERY


@pytest.fixture
def legacy(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    prefix = root / "versions" / "serving"
    prefix.mkdir(parents=True)
    (root / "status-monitor-control.token").write_text("fixture-token", encoding="utf-8")
    endpoint = routing.Endpoint("127.0.0.1", 61001, 202, "old", 7, "2002")
    state = {
        "table": {"active": endpoint.to_dict()}, "start": "2002", "events": [],
        "health": {"pid": 202, "status": "ready", "runtime_prefix": str(prefix),
                   "runtime_slot": "serving", "runtime_generation": "a" * 32, "capabilities": []},
    }
    class Guard:
        held = True
        lock_path = root / "status-monitor-routing" / "cutover.lock"
    guard = Guard()
    monkeypatch.setattr(identity.cutover, "SingleInstance", Guard)
    monkeypatch.setattr(routing, "routing_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(routing, "read_table", lambda *a, **k: copy.deepcopy(state["table"]))
    monkeypatch.setattr(identity.diagnostics, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(retirements, "_exit_state", lambda record: False)
    monkeypatch.setattr(identity.locks, "process_start_time", lambda pid: state["start"])
    monkeypatch.setattr(identity.locks, "read_lock", lambda path: None)
    monkeypatch.setattr(identity.locks, "lock_is_live", lambda record: False)
    def client(url, **options):
        assert url == endpoint.base_url and options["token"] == "fixture-token"
        return SimpleNamespace(health=lambda: dict(state["health"]))
    monkeypatch.setattr(identity.cutover, "ControlClient", client)
    monkeypatch.setattr(native, "birth_identity", lambda pid: "native-birth")
    class Pin:
        def __init__(self, pid, birth):
            assert (pid, birth) == (202, "native-birth")
            state["events"].append("pin")
        def __enter__(self):
            if state.get("change_on_pin") == "birth":
                state["start"] = "reused"
            elif state.get("change_on_pin") == "route":
                state["table"]["active"]["pid"] = 303
            return self
        def __exit__(self, *_):
            state["events"].append("close")
        def terminate(self, *, timeout):
            assert timeout == 5.0
            state["events"].append("terminate")
            if state.get("termination_failure"):
                raise native.RetirementRefused("fixture exit unconfirmed")
        def exited(self):
            return not state.get("exit_unconfirmed")
    monkeypatch.setattr(native, "PinnedProcess", Pin)
    return root, (202, "2002", "serving"), guard, state


def test_unsupported_capability_keeps_authenticated_native_owner(legacy):
    root, expected, _guard, state = legacy
    assert identity.routed_identity(root, require_async_retirement=True) == expected
    with pytest.raises(identity.AsyncRetirementUnsupported) as raised:
        identity.routed_identity(root, require_async_retirement=True, require_capabilities=True)
    assert raised.value.identity == expected
    assert state["events"] == []


def test_legacy_retirement_terminates_only_the_pinned_owned_process(legacy):
    root, expected, guard, state = legacy
    assert identity.retire_legacy(root, expected, guard=guard) == {
        "pid": 202, "exit_confirmed": True, "forced": True,
    }
    assert state["events"] == ["pin", "terminate", "close"]


@pytest.mark.parametrize("change", ["owner", "capability", "birth", "route", "guard"])
def test_legacy_retirement_refuses_changed_owner_or_native_identity(legacy, change):
    root, expected, guard, state = legacy
    if change == "owner":
        state["health"]["pid"] = 303
    elif change == "capability":
        state["health"]["capabilities"] = [retirements.CAPABILITY, RECOVERY]
    elif change == "guard":
        guard.held = False
    else:
        state["change_on_pin"] = change
    with pytest.raises(ValueError):
        identity.retire_legacy(root, expected, guard=guard)
    assert "terminate" not in state["events"]


@pytest.mark.parametrize("failure", ["termination_failure", "exit_unconfirmed"])
def test_legacy_retirement_never_credits_an_unconfirmed_exit(legacy, failure):
    root, expected, guard, state = legacy
    state[failure] = True
    with pytest.raises(RuntimeError, match="unconfirmed"):
        identity.retire_legacy(root, expected, guard=guard)
    assert state["events"] == ["pin", "terminate", "close"]


@pytest.mark.parametrize("boundary", ["exited", "live", "other-owner"])
def test_cold_start_ignores_only_the_native_confirmed_exact_old_discovery(
    legacy, monkeypatch, boundary,
):
    from agent_worktrees import status_monitor_runtime as runtime
    from zdd import process_exit

    root, expected, guard, _state = legacy
    cutover = identity.cutover
    monkeypatch.setattr(runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_monitor_lock_path", lambda: root / "status-monitor.lock")
    monkeypatch.setattr(runtime, "_aw_runtime_home", lambda: root)
    monkeypatch.setattr(cutover, "routing_dir", lambda *a: root / "status-monitor-routing")
    monkeypatch.setattr(cutover, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(identity.locks, "read_lock", lambda path: {
        "pid": 303 if boundary == "other-owner" else expected[0], "start_time": expected[1],
    })
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda *args: (
        args == expected[:2] and boundary != "live"
    ))
    monkeypatch.setattr(cutover, "routed_monitor_identity", lambda *a, **k: None)
    def selected(*args, **kwargs):
        assert kwargs["observed"] is None and kwargs["incumbent_expected"] is False
        raise RuntimeError("reached fresh successor preparation")
    monkeypatch.setattr(identity, "async_predecessor", selected)
    if boundary == "exited":
        expected_error = RuntimeError
        message = "fresh successor preparation"
    else:
        expected_error = ValueError
        message = "unconfirmed|different monitor"
    with pytest.raises(expected_error, match=message):
        cutover.activate_after_update(
            monitor_was_live=False, cutover_guard=guard, spawned_handles=[],
            asynchronous_retirement=True, retired_predecessor=expected[:2],
        )


@pytest.mark.parametrize("native_exit", [False, None])
def test_candidate_serving_uses_authenticated_route_not_outgoing_discovery(legacy, monkeypatch, native_exit):
    root, _expected, _guard, state = legacy
    state["health"]["published"] = True
    monkeypatch.setattr(retirements, "_exit_state", lambda record: native_exit)
    assert identity.candidate_serving(root, root / "versions" / "serving") is True


@pytest.mark.parametrize("change", ["prefix", "pid", "birth", "generation", "publication"])
def test_candidate_serving_rejects_unbound_or_unready_route(legacy, monkeypatch, change):
    from agent_worktrees import runtime_identity

    root, _expected, _guard, state = legacy
    state["health"]["published"] = True
    monkeypatch.setattr(runtime_identity, "slot_generation", lambda slot: "a" * 32)
    if change == "prefix":
        foreign = root.parent / "foreign"
        foreign.mkdir()
        state["health"]["runtime_prefix"] = str(foreign)
    elif change == "pid":
        state["health"]["pid"] = 303
    elif change == "birth":
        state["start"] = "reused"
    elif change == "generation":
        state["health"]["runtime_generation"] = "b" * 32
    else:
        state["health"]["published"] = False
    assert identity.candidate_serving(root, root / "versions" / "serving") is False


@pytest.mark.parametrize("prefix", [None, "versions/serving"])
@pytest.mark.parametrize("capabilities", [[], [retirements.CAPABILITY]])
def test_legacy_retirement_requires_an_absolute_authenticated_prefix(legacy, prefix, capabilities):
    root, expected, guard, state = legacy
    state["health"]["runtime_prefix"] = prefix
    state["health"]["capabilities"] = capabilities
    with pytest.raises(ValueError, match="runtime prefix is unavailable"):
        identity.retire_legacy(root, expected, guard=guard)
    assert state["events"] == []


def test_failed_startup_settles_without_querying_a_native_exited_route(legacy, monkeypatch):
    from agent_worktrees import runtime_monitor_settlement as settlement

    root, expected, _guard, state = legacy
    monkeypatch.setattr(retirements, "_exit_state", lambda record: record == {
        "pid": expected[0], "start": expected[1],
    })
    monkeypatch.setattr(identity.cutover, "ControlClient", lambda *a, **k: pytest.fail("queried exited listener"))
    monkeypatch.setattr(settlement.time, "sleep", lambda *_: pytest.fail("dead startup did not settle"))
    assert identity.locks.process_start_time(expected[0]) == expected[1]
    assert settlement.settle_monitor(
        root, root / "versions" / "serving",
        {"action": "cutover", "result": {"committed": False}}, expected[:2],
        [SimpleNamespace(poll=lambda: 1)], candidate_serving=identity.candidate_serving,
        refused=RuntimeError,
    ) is False
    assert state["events"] == []
