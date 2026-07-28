"""Tests for the read-only idle/busy restart-readiness probe (#533 Part B)."""

from __future__ import annotations

from agent_bridge.runtime_restart import busy_sessions, daemon_busy_sessions


def test_busy_sessions_flags_running_and_background_tasks():
    sessions = [
        {"id": "a", "status": "idle"},
        {"id": "b", "status": "running"},
        {"id": "c", "status": "stopped"},
        {"session_id": "d", "status": "idle", "has_active_background_tasks": True},
        {"id": "e", "status": "RUNNING"},  # case-insensitive
    ]
    assert set(busy_sessions(sessions)) == {"b", "d", "e"}


def test_busy_sessions_empty_when_idle_or_none():
    assert busy_sessions([{"id": "a", "status": "idle"}, {"id": "b", "status": "ended"}]) == []
    assert busy_sessions([]) == []
    assert busy_sessions(None) == []  # type: ignore[arg-type]


class _Client:
    def __init__(self, sessions=None, boom=False):
        self._sessions = sessions or []
        self._boom = boom

    def list_sessions(self, **_kw):
        if self._boom:
            raise RuntimeError("daemon unreachable")
        return self._sessions


def test_daemon_busy_sessions_reads_client():
    assert daemon_busy_sessions(_Client([{"id": "x", "status": "running"}])) == ["x"]
    assert daemon_busy_sessions(_Client([{"id": "x", "status": "idle"}])) == []


def test_daemon_busy_unreachable_is_safe_idle():
    # An unreachable/erroring daemon is not serving a live dispatch, so a restart
    # is safe -> report idle ([]), never block the swap on a probe failure.
    assert daemon_busy_sessions(_Client(boom=True)) == []


def test_service_is_busy_exit_codes(monkeypatch):
    """`service is-busy` exits BUSY_EXIT_CODE (3, not argparse's 2) when busy, 0 idle."""
    import pytest

    from agent_bridge import __main__ as m
    from agent_bridge.runtime_restart import BUSY_EXIT_CODE

    args = m.build_parser().parse_args(["service", "is-busy"])
    monkeypatch.setattr(m, "_get_client", lambda: _Client([{"id": "x", "status": "running"}]))
    with pytest.raises(SystemExit) as ei:
        args.func(args)
    assert ei.value.code == BUSY_EXIT_CODE == 3

    args2 = m.build_parser().parse_args(["service", "is-busy"])
    monkeypatch.setattr(m, "_get_client", lambda: _Client([{"id": "x", "status": "idle"}]))
    with pytest.raises(SystemExit) as ei2:
        args2.func(args2)
    assert ei2.value.code == 0
