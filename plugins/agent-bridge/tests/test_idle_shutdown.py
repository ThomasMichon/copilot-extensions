"""Unit tests for the idle-shutdown active-session counter."""

from __future__ import annotations

from dataclasses import dataclass

from agent_bridge.app import _count_active_sessions
from agent_bridge.models import SessionStatus


@dataclass
class _FakeSession:
    status: object


class _FakeMgr:
    def __init__(self, statuses):
        self._s = [_FakeSession(s) for s in statuses]

    def list_sessions(self):
        return self._s


def test_counts_only_active_statuses_enum():
    mgr = _FakeMgr([
        SessionStatus.RUNNING,
        SessionStatus.IDLE,
        SessionStatus.CREATED,
        SessionStatus.STARTING,
        SessionStatus.STOPPED,
        SessionStatus.ENDED,
        SessionStatus.FAILED,
        SessionStatus.STOPPING,
    ])
    assert _count_active_sessions(mgr) == 4


def test_counts_active_statuses_strings():
    mgr = _FakeMgr(["running", "stopped", "idle", "ended"])
    assert _count_active_sessions(mgr) == 2


def test_zero_when_all_terminal():
    mgr = _FakeMgr([SessionStatus.STOPPED, SessionStatus.ENDED])
    assert _count_active_sessions(mgr) == 0


def test_empty_is_zero():
    assert _count_active_sessions(_FakeMgr([])) == 0
