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

    def test_never_expires_shows_last_intent(self):
        # copilot-extensions#228: a very old intent no longer drops -- a worktree
        # where work happened keeps showing its last reported intent (greyed as
        # 'stale'), so the picker always answers "what was this doing?".
        ancient_iso = _iso(derive.NOW - _dt.timedelta(days=3))
        n = derive.norm(
            _raw(live_intent="ancient", live_intent_at=ancient_iso),
            "lambda-core", "win")
        assert n["live_pulse"] == "stale"
        assert n["live_intent"] == "ancient"

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
# derive layer: the graded rest (live_rest) -> pulse mapping (#228 slice 3)
# ---------------------------------------------------------------------------

class TestPulseRestGrading:
    def test_awaiting_operator_pulse(self):
        # An old timestamp AND awaiting-operator: the rest wins -> 'awaiting'
        # (the standout "needs me" level), never dropped on age.
        old_iso = _iso(derive.NOW - _dt.timedelta(hours=6))
        n = derive.norm(
            _raw(live_intent="Waiting on you", live_intent_at=old_iso,
                 live_rest="awaiting-operator"),
            "lambda-core", "win")
        assert n["live_pulse"] == "awaiting"
        assert n["awaiting_operator"] is True
        assert n["live_rest"] == "awaiting-operator"

    def test_awaiting_operator_title_marker(self):
        # placement=both: awaiting-operator also rides a scannable ⏳ title
        # marker, kept just inside the outermost orphan ⚠.
        n = derive.norm(
            _raw(live_intent="x", live_rest="awaiting-operator", title="Needs me"),
            "lambda-core", "win")
        assert "\u23f3" in n["title"]
        # Orphan stays outermost when both are present.
        both = derive.norm(
            _raw(live_intent="x", live_rest="awaiting-operator",
                 session_bare_orphan=True, title="Wedged"),
            "lambda-core", "win")
        assert both["title"].startswith("\u26a0")
        assert "\u23f3" in both["title"]

    def test_no_awaiting_marker_when_not_parked(self):
        n = derive.norm(_raw(live_intent="x", live_rest="busy"), "m", "e")
        assert "\u23f3" not in n["title"]
        assert n["awaiting_operator"] is False

    def test_busy_rest_is_fresh(self):
        # A stale-aged timestamp but a live 'busy' rest -> the crisp rest wins.
        old_iso = _iso(derive.NOW - _dt.timedelta(seconds=derive._PULSE_FRESH_SECS + 60))
        n = derive.norm(
            _raw(live_intent="churning", live_intent_at=old_iso, live_rest="busy"),
            "lambda-core", "win")
        assert n["live_pulse"] == "fresh"

    def test_idle_rest_is_stale(self):
        now_iso = _iso(derive.NOW - _dt.timedelta(seconds=5))
        n = derive.norm(
            _raw(live_intent="done", live_intent_at=now_iso, live_rest="idle"),
            "lambda-core", "win")
        assert n["live_pulse"] == "stale"

    def test_rest_only_without_intent_has_no_line(self):
        # The coarse backbone can carry live_rest with NO intent text (extension
        # not loaded); the sub-line is intent-driven, so there is nothing to show.
        n = derive.norm(_raw(live_rest="idle"), "lambda-core", "win")
        assert n["live_pulse"] is None
        assert n["live_intent"] == ""
        assert "\u23f3" not in n["title"]


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
        rec = _make_record("wt-pulse", wt_path,
                           sessions=[SessionEntry("sess-pulse", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        norm = _normalize_path(wt_path)
        assert ctx.live_intent[norm] == "Doing the thing"
        assert ctx.live_intent_at[norm] == "2026-06-01T10:00:00.000Z"
        assert ctx.live_intent_idle[norm] is False

    def test_scan_populates_rest_awaiting_operator(
        self, tmp_session_state_dir: Path
    ):
        """#228: the graded rest state surfaces awaiting-operator ("needs me")."""
        wt_path = "/tmp/wt-rest-await"
        make_session_dir(
            tmp_session_state_dir, "sess-await", wt_path,
            substatus={"intent": "Waiting on you", "updatedAt": "t",
                       "idle": False, "rest": "awaiting-operator",
                       "restAt": "2026-06-01T10:05:00.000Z"},
        )
        rec = _make_record("wt-rest-await", wt_path,
                           sessions=[SessionEntry("sess-await", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        norm = _normalize_path(wt_path)
        assert ctx.live_rest[norm] == "awaiting-operator"
        assert ctx.live_rest_at[norm] == "2026-06-01T10:05:00.000Z"

    def test_scan_populates_rest_idle(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-rest-idle"
        make_session_dir(
            tmp_session_state_dir, "sess-idle", wt_path,
            substatus={"intent": "Done", "updatedAt": "t", "idle": True,
                       "rest": "idle", "restAt": "2026-06-01T10:06:00.000Z"},
        )
        rec = _make_record("wt-rest-idle", wt_path,
                           sessions=[SessionEntry("sess-idle", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        norm = _normalize_path(wt_path)
        assert ctx.live_rest[norm] == "idle"

    def test_legacy_sidecar_derives_rest_from_idle(
        self, tmp_session_state_dir: Path
    ):
        """A legacy sidecar (no ``rest`` field) derives the coarse rest from
        ``idle``: idle=True -> "idle"; idle=False -> no rest surfaced."""
        wt_idle = "/tmp/wt-legacy-idle"
        make_session_dir(
            tmp_session_state_dir, "leg-idle", wt_idle,
            substatus={"intent": "x", "updatedAt": "t", "idle": True},
        )
        wt_busy = "/tmp/wt-legacy-busy"
        make_session_dir(
            tmp_session_state_dir, "leg-busy", wt_busy,
            substatus={"intent": "x", "updatedAt": "t", "idle": False},
        )
        recs = [
            _make_record("wt-legacy-idle", wt_idle,
                         sessions=[SessionEntry("leg-idle", "t")]),
            _make_record("wt-legacy-busy", wt_busy,
                         sessions=[SessionEntry("leg-busy", "t")]),
        ]
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast(recs)
        assert ctx.live_rest[_normalize_path(wt_idle)] == "idle"
        assert _normalize_path(wt_busy) not in ctx.live_rest

    def test_newer_session_without_sidecar_clears_rest(
        self, tmp_session_state_dir: Path
    ):
        wt_path = "/tmp/wt-rest-clear"
        make_session_dir(
            tmp_session_state_dir, "old", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
            substatus={"intent": "old", "updatedAt": "old", "idle": True,
                       "rest": "idle", "restAt": "old"},
        )
        make_session_dir(
            tmp_session_state_dir, "new", wt_path,
            updated_at="2026-06-01T12:00:00.000Z",
        )
        rec = _make_record("wt-rest-clear", wt_path, sessions=[
            SessionEntry("old", "t"), SessionEntry("new", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        assert _normalize_path(wt_path) not in ctx.live_rest

    # -- Slice 2 (#228): extension-free backbone at-rest inference from a
    # bounded events.jsonl tail (turn boundaries persist; session.idle does not).

    def test_backbone_infers_idle_from_turn_end(
        self, tmp_session_state_dir: Path
    ):
        """No sidecar; the last TURN BOUNDARY in events.jsonl is an
        assistant.turn_end (the trailing hook.end is NOT a boundary) -> coarse
        'idle' from the backbone (no live_rest_at, which is sidecar-only)."""
        wt_path = "/tmp/wt-bb-idle"
        make_session_dir(
            tmp_session_state_dir, "sess-bb-idle", wt_path,
            events_lines=['{"type": "assistant.turn_start"}',
                          '{"type": "tool.execution_complete"}',
                          '{"type": "assistant.turn_end"}',
                          '{"type": "hook.end"}'],
        )
        rec = _make_record("wt-bb-idle", wt_path,
                           sessions=[SessionEntry("sess-bb-idle", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        norm = _normalize_path(wt_path)
        assert ctx.live_rest[norm] == "idle"
        assert norm not in ctx.live_rest_at   # backbone carries no timestamp

    def test_backbone_infers_busy_from_turn_start(
        self, tmp_session_state_dir: Path
    ):
        """The last turn boundary is a turn_start (a turn is in flight) -> 'busy'."""
        wt_path = "/tmp/wt-bb-busy"
        make_session_dir(
            tmp_session_state_dir, "sess-bb-busy", wt_path,
            events_lines=['{"type": "assistant.turn_start"}',
                          '{"type": "assistant.turn_end"}',
                          '{"type": "assistant.turn_start"}',
                          '{"type": "tool.execution_start"}'],
        )
        rec = _make_record("wt-bb-busy", wt_path,
                           sessions=[SessionEntry("sess-bb-busy", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        assert ctx.live_rest[_normalize_path(wt_path)] == "busy"

    def test_backbone_none_without_turn_boundary(
        self, tmp_session_state_dir: Path
    ):
        """Events with no turn boundary in the tail -> unknown (never guessed)."""
        wt_path = "/tmp/wt-bb-none"
        make_session_dir(
            tmp_session_state_dir, "sess-bb-none", wt_path,
            events_lines=['{"type": "user.message"}',
                          '{"type": "tool.execution_start"}'],
        )
        rec = _make_record("wt-bb-none", wt_path,
                           sessions=[SessionEntry("sess-bb-none", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        assert _normalize_path(wt_path) not in ctx.live_rest

    def test_sidecar_rest_wins_over_backbone(
        self, tmp_session_state_dir: Path
    ):
        """The crisp sidecar rest (awaiting-operator) overrides the coarse
        events-derived backbone (which would say 'idle' from turn_end)."""
        wt_path = "/tmp/wt-bb-override"
        make_session_dir(
            tmp_session_state_dir, "sess-bb-ov", wt_path,
            events_lines=['{"type": "assistant.turn_start"}',
                          '{"type": "assistant.turn_end"}'],
            substatus={"intent": "Need input", "updatedAt": "t", "idle": False,
                       "rest": "awaiting-operator", "restAt": "2026-06-01T10:00:00Z"},
        )
        rec = _make_record("wt-bb-override", wt_path,
                           sessions=[SessionEntry("sess-bb-ov", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        norm = _normalize_path(wt_path)
        assert ctx.live_rest[norm] == "awaiting-operator"
        assert ctx.live_rest_at[norm] == "2026-06-01T10:00:00Z"

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
        rec = _make_record("wt-pulse2", wt_path, sessions=[
            SessionEntry("old", "t"), SessionEntry("new", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
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
        rec = _make_record("wt-pulse3", wt_path, sessions=[
            SessionEntry("old", "t"), SessionEntry("new", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        norm = _normalize_path(wt_path)
        # The newest session has no pulse -> the older one must not linger.
        assert norm not in ctx.live_intent

    def test_missing_sidecar_omits_intent(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-nopulse"
        make_session_dir(tmp_session_state_dir, "sess-nopulse", wt_path)
        rec = _make_record("wt-nopulse", wt_path,
                           sessions=[SessionEntry("sess-nopulse", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
        assert _normalize_path(wt_path) not in ctx.live_intent

    def test_blank_intent_ignored(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-blankpulse"
        make_session_dir(
            tmp_session_state_dir, "blank", wt_path,
            substatus={"intent": "   ", "updatedAt": "x"},
        )
        rec = _make_record("wt-blankpulse", wt_path,
                           sessions=[SessionEntry("blank", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])
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
