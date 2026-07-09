"""Session liveness: last_output_at / heartbeat / stall detection (#145).

The turn-boundary ``updated_at`` cannot tell a hard-working long turn from a
silent mid-turn stall (both leave it frozen). ``last_output_at`` (per ACP frame)
+ a periodic transport heartbeat + ``liveness_state`` fix that.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import (
    Session,
    SessionManager,
    _STALL_AFTER_S,
)
from agent_bridge.transport import SpawnTarget


def _session(status=SessionStatus.RUNNING, running=True):
    s = Session("s1", "calm-lake", SpawnTarget(type="local", cwd="/wt"))
    s.status = status
    s.client = SimpleNamespace(is_running=running) if running is not None else None
    return s


class TestCaptureProgressStampsOutput:
    def test_any_event_stamps_last_output_at(self):
        s = _session()
        assert s.last_output_at is None
        SessionManager._capture_progress(s, "tool_call_update", {})
        assert s.last_output_at is not None
        assert s.last_output_at <= time.time() + 1


class TestLivenessState:
    def test_non_running_returns_none(self):
        assert _session(status=SessionStatus.IDLE).liveness_state() is None
        assert _session(status=SessionStatus.STOPPED).liveness_state() is None

    def test_disconnected_when_client_not_running(self):
        s = _session(running=False)  # client is None
        assert s.liveness_state() == "disconnected"
        s2 = _session()
        s2.client = SimpleNamespace(is_running=False)
        assert s2.liveness_state() == "disconnected"

    def test_active_when_output_recent(self):
        s = _session()
        s.last_output_at = time.time()
        assert s.liveness_state() == "active"

    def test_active_when_no_output_yet(self):
        # Turn just started, no frame yet -- don't false-alarm.
        s = _session()
        assert s.last_output_at is None
        assert s.liveness_state() == "active"

    def test_stalled_when_output_stale_but_channel_alive(self):
        s = _session()
        s.last_output_at = time.time() - (_STALL_AFTER_S + 60)
        assert s.liveness_state() == "stalled"

    def test_threshold_is_respected(self):
        s = _session()
        now = time.time()
        s.last_output_at = now - (_STALL_AFTER_S - 5)
        assert s.liveness_state(now=now) == "active"
        s.last_output_at = now - (_STALL_AFTER_S + 5)
        assert s.liveness_state(now=now) == "stalled"


class TestNoteHeartbeats:
    def test_beats_running_live_sessions_only(self):
        mgr = SessionManager.__new__(SessionManager)  # avoid full init/db
        running = _session()
        idle = _session(status=SessionStatus.IDLE)
        dead = _session()
        dead.client = SimpleNamespace(is_running=False)
        mgr._sessions = {"r": running, "i": idle, "d": dead}

        beat = mgr.note_heartbeats()

        assert beat == 1
        assert running.last_heartbeat_at is not None
        assert idle.last_heartbeat_at is None
        assert dead.last_heartbeat_at is None
