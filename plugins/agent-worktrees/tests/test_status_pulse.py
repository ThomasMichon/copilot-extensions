"""worktree-status-core: the live activity pulse (derived from the agent's
``assistant.intent`` stream via the ``substatus.json`` sidecar).

Covers both layers:
  * ``sessions`` -- reading the sidecar into ``SessionContext.live_intent``
    (newest-session-wins, stale-drop), mirroring the context% precedent.
  * ``picker_tui.derive`` -- freshness classification ('fresh'/'stale'/None)
    and its exposure on the normalized record.

The pulse is a *derived* register and must never be conflated with the
agent-asserted ``follow_up`` disposition.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from unittest.mock import patch

from agent_worktrees.picker_tui import derive
from agent_worktrees.sessions import (
    _normalize_path,
    scan_sessions,
    scan_sessions_fast,
)

from conftest import make_session_dir
from agent_worktrees.tracking import WorktreeRecord, SessionEntry


def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat()


def _raw(**kw):
    base = dict(id="lambda-core-win-20260625-0000-abcd", machine="lambda-core",
                title="Feeder cam", status="active", state="active")
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# derive layer: freshness classification
# ---------------------------------------------------------------------------

class TestPulseFreshness:
    def test_fresh_recent_intent(self):
        now_iso = _iso(derive.NOW - _dt.timedelta(seconds=10))
        n = derive.norm(
            _raw(live_intent="Wiring the pulse extension", live_intent_at=now_iso),
            "lambda-core", "win")
        assert n["live_pulse"] == "fresh"
        assert n["live_intent"] == "Wiring the pulse extension"

    def test_fresh_with_tz_aware_z_timestamp(self):
        # The live-pulse extension stamps `new Date().toISOString()` -- a UTC
        # `...Z` (tz-aware) value. It must compare cleanly against NOW (naive
        # local) and classify as fresh, not silently drop to None.
        z_iso = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=10)
        ).isoformat().replace("+00:00", "Z")
        n = derive.norm(
            _raw(live_intent="from a real session", live_intent_at=z_iso),
            "lambda-core", "win")
        assert n["live_pulse"] == "fresh"

    def test_stale_when_aged(self):
        old_iso = _iso(derive.NOW - _dt.timedelta(seconds=derive._PULSE_FRESH_SECS + 60))
        n = derive.norm(
            _raw(live_intent="older intent", live_intent_at=old_iso),
            "lambda-core", "win")
        assert n["live_pulse"] == "stale"

    def test_idle_is_never_fresh(self):
        now_iso = _iso(derive.NOW - _dt.timedelta(seconds=5))
        n = derive.norm(
            _raw(live_intent="just finished", live_intent_at=now_iso,
                 live_intent_idle=True),
            "lambda-core", "win")
        assert n["live_pulse"] == "stale"

    def test_expired_drops_the_line(self):
        expired_iso = _iso(derive.NOW - _dt.timedelta(seconds=derive._PULSE_STALE_SECS + 60))
        n = derive.norm(
            _raw(live_intent="ancient", live_intent_at=expired_iso),
            "lambda-core", "win")
        assert n["live_pulse"] is None

    def test_no_pulse_when_absent(self):
        n = derive.norm(_raw(), "lambda-core", "win")
        assert n["live_pulse"] is None
        assert n["live_intent"] == ""

    def test_unparseable_timestamp_is_safe(self):
        n = derive.norm(
            _raw(live_intent="x", live_intent_at="not-a-date"),
            "lambda-core", "win")
        assert n["live_pulse"] is None

    def test_pulse_never_sets_follow_up(self):
        # The derived pulse must not flip the agent-asserted disposition.
        now_iso = _iso(derive.NOW - _dt.timedelta(seconds=5))
        n = derive.norm(
            _raw(live_intent="busy", live_intent_at=now_iso),
            "lambda-core", "win")
        assert n["follow_up"] is False
        assert not n["title"].startswith("\u271a")


# ---------------------------------------------------------------------------
# sessions layer: reading the sidecar into SessionContext
# ---------------------------------------------------------------------------

def _make_record(wt_id, wt_path, sessions=None):
    return WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=wt_path,
        repo="aperture-labs",
        machine="lambda-core",
        platform="windows",
        started_at="",
        last_resumed_at="",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=sessions or [],
    )


class TestPulseSessionScan:
    def test_scan_populates_live_intent(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-pulse"
        make_session_dir(
            tmp_session_state_dir, "sess-pulse", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
            substatus={"sessionId": "sess-pulse", "intent": "Doing the thing",
                       "updatedAt": "2026-06-01T10:00:00.000Z", "idle": False},
        )
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions([wt_path])
        norm = _normalize_path(wt_path)
        assert ctx.live_intent[norm] == "Doing the thing"
        assert ctx.live_intent_at[norm] == "2026-06-01T10:00:00.000Z"
        assert ctx.live_intent_idle[norm] is False

    def test_newest_session_wins(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-pulse2"
        make_session_dir(
            tmp_session_state_dir, "old", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
            substatus={"intent": "old intent", "updatedAt": "old", "idle": True},
        )
        make_session_dir(
            tmp_session_state_dir, "new", wt_path,
            updated_at="2026-06-01T12:00:00.000Z",
            substatus={"intent": "new intent", "updatedAt": "new", "idle": False},
        )
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions([wt_path])
        norm = _normalize_path(wt_path)
        assert ctx.live_intent[norm] == "new intent"

    def test_newer_session_without_sidecar_clears_stale(
        self, tmp_session_state_dir: Path
    ):
        wt_path = "/tmp/wt-pulse3"
        make_session_dir(
            tmp_session_state_dir, "old", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
            substatus={"intent": "old intent", "updatedAt": "old", "idle": True},
        )
        make_session_dir(
            tmp_session_state_dir, "new", wt_path,
            updated_at="2026-06-01T12:00:00.000Z",
        )
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions([wt_path])
        norm = _normalize_path(wt_path)
        # The newest session has no pulse -> the older one must not linger.
        assert norm not in ctx.live_intent

    def test_missing_sidecar_omits_intent(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-nopulse"
        make_session_dir(tmp_session_state_dir, "sess-nopulse", wt_path)
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions([wt_path])
        assert _normalize_path(wt_path) not in ctx.live_intent

    def test_blank_intent_ignored(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-blankpulse"
        make_session_dir(
            tmp_session_state_dir, "blank", wt_path,
            substatus={"intent": "   ", "updatedAt": "x"},
        )
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions([wt_path])
        assert _normalize_path(wt_path) not in ctx.live_intent

    def test_fast_path_populates_intent(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-fast-pulse"
        make_session_dir(
            tmp_session_state_dir, "fast-pulse", wt_path,
            updated_at="2026-06-02T09:00:00.000Z",
            substatus={"intent": "fast pulse", "updatedAt": "2026-06-02T09:00:00.000Z"},
        )
        rec = _make_record(
            "wt-fast-pulse", wt_path,
            sessions=[SessionEntry(session_id="fast-pulse",
                                   started_at="2026-06-02T09:00:00")],
        )
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        assert ctx.live_intent[_normalize_path(wt_path)] == "fast pulse"
