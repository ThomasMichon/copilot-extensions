"""Isolated cutover validation must remain isolated even across PID reuse."""

import pytest

from agent_dispatch.reap import CoordProc, reap_superseded_coordinators

from . import windows_cutover_identity_fixture as fixture

pytestmark = pytest.mark.guard


def test_fixture_reap_excludes_reused_unknown_and_unowned_pids(monkeypatch):
    owned = {1: "100", 2: "200", 3: "300", 4: None}
    live = {1: "100", 2: "reused-by-host", 3: "300", 4: "400", 5: "500"}
    signalled = []
    monkeypatch.setattr(fixture, "process_start_time", live.get)
    monkeypatch.setattr(fixture, "terminate_pid_if_identity", lambda pid, token: (
        signalled.append((pid, token)) or {"killed": True}
    ))
    result = fixture.reap_fixture_processes(
        owned, keep_pids={1},
        list_procs=lambda: [CoordProc(pid, "python -m agent_dispatch serve") for pid in live],
        reap_fn=reap_superseded_coordinators,
    )
    assert result.reaped == [3]
    assert signalled == [(3, "300")]


def test_fixture_termination_preserves_original_token_after_selection(monkeypatch):
    owned = {1: "100", 2: "200"}
    live = dict(owned)
    requests = []
    monkeypatch.setattr(fixture, "process_start_time", live.get)

    def native_guard(pid, token):
        requests.append((pid, token))
        return {"killed": live.get(pid) == token}

    def after_selection(**kwargs):
        selected = kwargs["list_procs"]()
        live[2] = owned[2] = "reused-by-host"
        return reap_superseded_coordinators(
            keep_pids=kwargs["keep_pids"], list_procs=lambda: selected,
            terminate=kwargs["terminate"],
        )

    monkeypatch.setattr(fixture, "terminate_pid_if_identity", native_guard)
    result = fixture.reap_fixture_processes(
        owned, keep_pids={1},
        list_procs=lambda: [CoordProc(pid, "python -m agent_dispatch serve") for pid in live],
        reap_fn=after_selection,
    )
    assert not result.reaped
    assert requests == [(2, "200")]
    assert result.errors == ["terminate pid=2 failed"]
