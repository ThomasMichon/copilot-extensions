from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from zdd import breadcrumb, process_exit, routing

from agent_worktrees import runtime_monitor_grace as grace
from agent_worktrees import runtime_monitor_retirements as retirements
from agent_worktrees import status_monitor_cutover as cutover
from agent_worktrees import status_monitor_recovery_contract as recovery
from agent_worktrees.status_monitor_handover import ACTION, CAPABILITY, MonitorHandover


@pytest.fixture
def retirement(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    old = routing.Endpoint("127.0.0.1", 41001, 101, generation=1, process_start_time="1001")
    new = routing.Endpoint("127.0.0.1", 41002, os.getpid(), generation=2, process_start_time="2002")
    predecessor = {"pid": old.pid, "start": old.process_start_time, "slot": None, "release_claim": None}
    retirements.reserve(root, predecessor, committed=True)
    directory = root / "status-monitor-routing"
    directory.mkdir()
    route_path = routing.routing_table_path(directory)
    route_path.write_text(json.dumps({"active": new.to_dict(), "previous": old.to_dict()}))
    identities = {old.pid: old.process_start_time, new.pid: new.process_start_time}
    exited = set()
    monkeypatch.setattr(retirements.locks, "process_start_time", identities.get)

    def observe(pid, start):
        if pid in exited:
            return True
        if pid not in identities:
            raise PermissionError("native identity unavailable")
        return identities[pid] != start

    monkeypatch.setattr(process_exit, "observe_original_exit", observe)
    calls = []

    def terminate(pid, start, *, exit_timeout):
        assert (pid, start, exit_timeout) == (old.pid, old.process_start_time, 5.0)
        calls.append((pid, start))
        exited.add(pid)
        return {"exit_confirmed": True, "identity_verified": True, "killed": True}

    monkeypatch.setattr(grace.diagnostics, "terminate_pid_if_identity", terminate)
    monkeypatch.setattr(recovery, "successor_healthy", lambda *a, **k: True)
    return SimpleNamespace(
        root=root, directory=directory, route_path=route_path, old=old, new=new,
        predecessor=predecessor, identities=identities, exited=exited, calls=calls,
    )


def test_ready_successor_arms_once_without_changing_legacy_custody(retirement):
    r = retirement
    original = retirements._records(r.root)
    grace.arm(r.root, ready=lambda: False, clock=lambda: 1000.0)
    assert not grace._path(r.root, r.predecessor).exists()
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1100.0)
    assert grace._read(grace._path(r.root, r.predecessor))["ready_at"] == 1000.0
    assert retirements._records(r.root) == original
    assert r.calls == []


def test_deadline_survives_owner_restart_and_forces_only_after_grace(retirement):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    grace.reconcile(r.root, clock=lambda: 1299.999)
    assert r.calls == []
    grace.reconcile(r.root, clock=lambda: 1300.0)
    assert r.calls == [(r.old.pid, r.old.process_start_time)]
    assert not grace._path(r.root, r.predecessor).exists()
    assert routing.read_table(r.directory)["active"] == r.new.to_dict()
    assert "previous" not in routing.read_table(r.directory)
    assert not retirements.reconcile(r.root)


def test_resident_read_admission_never_performs_expiry(retirement, monkeypatch):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1.0)
    monkeypatch.setattr(grace, "reconcile", lambda root: pytest.fail("read admission forced retirement"))
    resident = retirements.ResidentRetirements(r.root)
    resident.refresh(cleanup=False)
    assert resident.pending and r.calls == []


@pytest.mark.parametrize("state", ["prepared", "cancelled"])
def test_uncommitted_custody_cannot_arm_a_deadline(retirement, state):
    r = retirement
    path, record = retirements._records(r.root)[0]
    retirements._write_record(path, {**record, "state": state})
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    assert not grace._path(r.root, r.predecessor).exists()


@pytest.mark.parametrize("clock", [float("nan"), float("inf"), -1, 0, True])
def test_invalid_deadline_is_refused_before_publication(retirement, clock):
    r = retirement
    with pytest.raises(ValueError, match="grace record"):
        grace.arm(r.root, ready=lambda: True, clock=lambda: clock)
    assert not grace._path(r.root, r.predecessor).exists()


@pytest.mark.parametrize("changed", ["owner", "birth", "not-ready", "witness", "guard"])
def test_changed_authority_never_forces_predecessor(retirement, monkeypatch, changed):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    held = None
    if changed == "owner":
        other = routing.Endpoint("127.0.0.1", 41003, 303, generation=3, process_start_time="3003")
        r.route_path.write_text(json.dumps({"active": other.to_dict(), "previous": r.old.to_dict()}))
    elif changed == "birth":
        r.identities[r.new.pid] = "9999"
    elif changed == "not-ready":
        monkeypatch.setattr(recovery, "successor_healthy", lambda *a, **k: False)
    elif changed == "witness":
        breadcrumb.write_breadcrumb(r.directory, state="started", new_pid=404, new_port=41004)
    elif changed == "guard":
        held = cutover._acquire_cutover_lock(r.directory, timeout_s=0)
    try:
        grace.reconcile(r.root, clock=lambda: 1300.0)
        assert r.calls == []
        assert grace._path(r.root, r.predecessor).exists()
    finally:
        if held is not None:
            held.release()


def test_route_change_during_readiness_refuses_force(retirement, monkeypatch):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)

    def changed(*a, **kw):
        r.route_path.write_text(json.dumps({"active": r.old.to_dict()}))
        return True

    monkeypatch.setattr(recovery, "successor_healthy", changed)
    grace.reconcile(r.root, clock=lambda: 1300.0)
    assert r.calls == []


def test_reused_old_pid_settles_original_exit_without_a_signal(retirement):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    r.identities[r.old.pid] = "9999"
    grace.reconcile(r.root, clock=lambda: 1300.0)
    assert r.calls == []
    assert not grace._path(r.root, r.predecessor).exists()


def test_predecessor_exit_before_expiry_releases_fallback_before_custody(retirement):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    r.exited.add(r.old.pid)
    grace.reconcile(r.root, clock=lambda: 1001.0)
    assert r.calls == []
    assert routing.read_table(r.directory) == {"active": r.new.to_dict()}
    assert not grace._path(r.root, r.predecessor).exists()
    assert not retirements.reconcile(r.root)


@pytest.mark.parametrize("forced", [False, True])
def test_failed_fallback_release_preserves_deadline_and_custody_for_retry(retirement, monkeypatch, forced):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1.0)
    if not forced:
        r.exited.add(r.old.pid)
    release = routing.release_previous_guarded

    def failed(*a, **kw):
        raise OSError("fixture route release failed")

    monkeypatch.setattr(routing, "release_previous_guarded", failed)
    resident = retirements.ResidentRetirements(r.root)
    resident.refresh()
    assert resident.pending and "route release failed" in resident.error
    assert grace._path(r.root, r.predecessor).exists() and retirements._records(r.root)
    assert routing.read_table(r.directory)["previous"] == r.old.to_dict()
    assert len(r.calls) == int(forced)
    monkeypatch.setattr(routing, "release_previous_guarded", release)
    resident.refresh()
    assert not resident.pending and resident.error is None
    assert not grace._path(r.root, r.predecessor).exists()
    assert not retirements._records(r.root)
    assert routing.read_table(r.directory) == {"active": r.new.to_dict()}
    assert len(r.calls) == int(forced)


@pytest.mark.parametrize("changed", ["active", "previous", "absent"])
def test_exit_cleanup_respects_guarded_route_comparison(retirement, changed):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    r.exited.add(r.old.pid)
    other = routing.Endpoint("127.0.0.1", 41003, 303, generation=3, process_start_time="3003")
    table = {"active": r.new.to_dict(), "previous": r.old.to_dict()}
    if changed == "absent":
        del table["previous"]
    else:
        table[changed] = other.to_dict()
    r.route_path.write_text(json.dumps(table))
    if changed == "active":
        with pytest.raises(RuntimeError, match="fallback release remains pending"):
            grace.reconcile(r.root, clock=lambda: 1001.0)
        assert grace._path(r.root, r.predecessor).exists() and retirements._records(r.root)
    else:
        grace.reconcile(r.root, clock=lambda: 1001.0)
        assert not grace._path(r.root, r.predecessor).exists()
    assert routing.read_table(r.directory) == table and r.calls == []


def test_unknown_old_identity_preserves_deadline_and_custody(retirement):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    del r.identities[r.old.pid]
    with pytest.raises(RuntimeError, match="identity is unavailable"):
        grace.reconcile(r.root, clock=lambda: 1300.0)
    assert r.calls == [] and retirements._records(r.root)
    assert grace._path(r.root, r.predecessor).exists()


def test_unconfirmed_force_releases_only_matching_fallback_and_keeps_custody(retirement, monkeypatch):
    r = retirement
    grace.arm(r.root, ready=lambda: True, clock=lambda: 1000.0)
    monkeypatch.setattr(
        grace.diagnostics, "terminate_pid_if_identity",
        lambda *a, **k: {"exit_confirmed": False},
    )
    with pytest.raises(RuntimeError, match="remains unconfirmed"):
        grace.reconcile(r.root, clock=lambda: 1300.0)
    assert routing.read_table(r.directory)["active"] == r.new.to_dict()
    assert "previous" not in routing.read_table(r.directory)
    assert retirements._records(r.root) and grace._path(r.root, r.predecessor).exists()


def test_notice_is_observed_but_does_not_grant_readiness_or_close_admission(retirement):
    r = retirement
    state = MonitorHandover(passive=False)
    state.generation = 1
    assert state.accept(r.directory, r.old.pid, {
        "old": r.old.to_dict(), "successor": r.new.to_dict(),
    }) == {"observed": True}
    assert state.announcement == r.new
    assert state.read_admitted() and not state.retiring


def test_incoming_notice_is_one_bounded_new_to_old_attempt(retirement, monkeypatch):
    r = retirement
    breadcrumb.write_breadcrumb(
        r.directory, state="committed", old=r.old.to_dict(), new_pid=r.new.pid,
        new_port=r.new.port, new_process_start_time=r.new.process_start_time,
        self_retirement=True,
    )
    requests = []

    class Client:
        def __init__(self, url, **kwargs):
            assert url == r.old.base_url
            assert kwargs["timeout"] == cutover._READ_TIMEOUT_S

        def _request(self, action, payload=None, **kwargs):
            requests.append((action, payload, kwargs))
            if action == "health":
                return {"capabilities": [CAPABILITY]}
            raise TimeoutError("old wire contract is unavailable")

    monkeypatch.setattr(cutover, "ControlClient", Client)
    monkeypatch.setattr(cutover, "load_or_create_control_token", lambda root: "fixture-control")
    from agent_worktrees import status_monitor_handover as handover

    times = iter([1.0, 2.0])
    monkeypatch.setattr(handover, "time", SimpleNamespace(monotonic=lambda: next(times)))
    state = MonitorHandover(passive=True)
    state.announce(r.directory, r.new.pid)
    state.announce(r.directory, r.new.pid)
    assert requests == [
        ("health", None, {"max_response_bytes": 4096, "total_timeout": cutover._READ_TIMEOUT_S}),
        (ACTION, {"old": r.old.to_dict(), "successor": r.new.to_dict()},
         {"max_response_bytes": 4096, "total_timeout": cutover._READ_TIMEOUT_S - 1.0}),
    ]
    assert not state.retiring and not state.promotion_requested


@pytest.mark.parametrize("field", ["old", "successor", "extra"])
def test_foreign_or_malformed_notice_is_not_admitted(retirement, field):
    r = retirement
    payload = {"old": r.old.to_dict(), "successor": r.new.to_dict()}
    if field == "extra":
        payload[field] = True
    else:
        payload[field] = {"pid": 999}
    state = MonitorHandover(passive=False)
    assert state.accept(r.directory, r.old.pid, payload) == {"observed": False}
    assert state.announcement is None and not state.retiring


@pytest.mark.parametrize("capabilities", [None, CAPABILITY, {}, []])
def test_legacy_or_malformed_support_skips_only_the_optional_notice(retirement, monkeypatch, capabilities):
    r = retirement
    breadcrumb.write_breadcrumb(
        r.directory, state="committed", old=r.old.to_dict(), new_pid=r.new.pid,
        new_port=r.new.port, new_process_start_time=r.new.process_start_time,
        self_retirement=True,
    )
    requests = []

    class Client:
        def __init__(self, *a, **kw):
            pass

        def _request(self, action, **kwargs):
            assert action == "health"
            requests.append(action)
            return {"capabilities": capabilities}

    monkeypatch.setattr(cutover, "ControlClient", Client)
    monkeypatch.setattr(cutover, "load_or_create_control_token", lambda root: "fixture-control")
    state = MonitorHandover(passive=True)
    state.announce(r.directory, r.new.pid)
    state.announce(r.directory, r.new.pid)
    assert requests == ["health"] and state.announced == r.new
    assert not state.retiring
