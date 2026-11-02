from __future__ import annotations

import pytest

from agent_worktrees import self_retire
from agent_worktrees.status_monitor_handover import MonitorHandover


def test_passive_route_visibility_is_not_promotion_authority():
    state = MonitorHandover(passive=True)
    state.generation = 2
    assert not state.promotion_requested and not state.read_admitted()
    state.reopen()
    assert state.read_admitted()


def test_ready_handoff_latches_even_if_the_successor_dies(monkeypatch, tmp_path):
    readiness = iter([False, True])
    monkeypatch.setattr(self_retire, "is_superseded", lambda *a, **k: next(readiness))
    state = MonitorHandover(passive=False)
    state.generation = 1
    assert state.read_admitted() and not state.observe(tmp_path, 101)
    assert state.observe(tmp_path, 101) and not state.read_admitted()
    assert state.observe(tmp_path, 101)
    state.reopen()
    assert state.read_admitted()


@pytest.mark.parametrize("error_type", [ValueError, OSError])
def test_discovery_contention_reports_retry_without_unwinding_the_resident(
    tmp_path, monkeypatch, caplog, error_type,
):
    from agent_worktrees import runtime_monitor_identity as identity

    def unavailable(*args, **kwargs):
        raise error_type("temporary discovery contention")

    monkeypatch.setattr(identity.routing, "routing_lock", unavailable)
    assert not identity.publish_lock(tmp_path / "status-monitor.lock", extra={})
    assert "publication deferred" in caplog.text


def test_strict_discovery_refusal_is_retryable_not_resident_exit(tmp_path, monkeypatch, caplog):
    from agent_worktrees import runtime_monitor_identity as identity
    from zdd import routing

    def refused(*args, **kwargs):
        raise routing.ActivePublicationRefused("route read unavailable")

    monkeypatch.setattr(identity.routing, "routing_lock", refused)
    assert not identity.publish_lock(tmp_path / "status-monitor.lock", extra={})
    assert "publication deferred" in caplog.text


def test_installation_cutover_lease_blocks_a_racing_idle_exit(tmp_path):
    from agent_worktrees import status_monitor_cutover as cutover

    state = MonitorHandover(passive=False)
    held = cutover._acquire_cutover_lock(tmp_path, timeout_s=0)
    try:
        assert state.idle_guard(tmp_path) is None
    finally:
        held.release()
    idle = state.idle_guard(tmp_path)
    assert idle is not None
    idle.release()


@pytest.mark.parametrize("alive,recorded,current,released", [
    (True, "1001", "1002", True),
    (True, "1001", "1001", False),
    (True, "1001", None, False),
    (True, None, "1002", False),
    (False, "1001", None, True),
])
def test_cold_lease_release_requires_dead_successor_identity(
    tmp_path, monkeypatch, alive, recorded, current, released,
):
    from agent_worktrees import status_monitor_cutover as cutover
    from zdd import diagnostics, routing

    endpoint = routing.Endpoint(
        generation=2, bind="127.0.0.1", port=12345, pid=202,
        process_start_time=recorded,
    )
    monkeypatch.setattr(routing, "read_table", lambda directory: {"active": endpoint.to_dict()})
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda pid: alive)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: current)
    state = MonitorHandover(passive=False)
    held = cutover._acquire_cutover_lock(tmp_path, timeout_s=0)
    remaining = held
    try:
        remaining = state.release_cold_lease(held, tmp_path, 101)
        assert (remaining is None) is released
        contender = state.idle_guard(tmp_path)
        assert (contender is not None) is released
        if contender is not None:
            contender.release()
        assert not state.retiring
    finally:
        if remaining is not None:
            remaining.release()
