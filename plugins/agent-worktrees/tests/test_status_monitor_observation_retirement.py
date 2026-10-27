"""Observation cleanup remains owned through the monitor's final route release."""

from types import SimpleNamespace

import pytest

from agent_worktrees import status_monitor_retirement as retirement


@pytest.mark.parametrize("original_failure", [False, True])
def test_retirement_settles_observations_after_workers_and_before_authority_release(
    monkeypatch, original_failure,
):
    events = []
    settled = False
    original = RuntimeError("initial monitor error") if original_failure else None
    interrupted = KeyboardInterrupt("cleanup interrupted")
    attempts = 0

    def record(name):
        return lambda *a, **k: events.append(name)

    def finish():
        nonlocal attempts, settled
        assert "workers-settled" in events
        attempts += 1
        events.append("cleanup")
        if attempts == 1:
            raise interrupted
        settled = True

    def release(*args):
        assert settled, "route release discarded retained observation custody"
        events.append("route-release")

    runtime = SimpleNamespace(
        shutdown=record("shutdown"),
        feed=SimpleNamespace(wait_closed=record("feed-closed")),
        shutdown_and_wait=record("workers-settled"),
    )
    monkeypatch.setattr(retirement.resident_push, "reset", record("reset"))
    monkeypatch.setattr(retirement.status_monitor_cutover, "clear_route_if_owner", release)
    monkeypatch.setattr(retirement.locks, "read_lock", lambda *a: None)
    monkeypatch.setattr(retirement.time, "sleep", record("retry-wait"))
    expected = original or interrupted
    with pytest.raises(type(expected)) as caught:
        retirement.retire_monitor(
            close_admission=record("closed"), repositories_runtime=runtime,
            worktrees_runtime=runtime, close_request_surfaces=record("surfaces-closed"),
            control_server=SimpleNamespace(close=record("control-closed")),
            receipts=SimpleNamespace(wait_idle=record("receipts-idle")),
            worktree_status_runtime=runtime, managed_mux_runtime=runtime,
            lock="unused.lock", original_error=original, finish_observation_custody=finish,
        )
    assert caught.value is expected
    assert attempts == 2 and settled
    assert events.index("workers-settled") < events.index("cleanup")
    assert events[-1] == "route-release"
