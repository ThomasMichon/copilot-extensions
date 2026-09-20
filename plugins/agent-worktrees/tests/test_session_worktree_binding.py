"""Tests for gitea aperture-labs#7230 -- session/worktree association must
always be durably recorded, and a session that yields to a handoff must open
the head position for the *next* session, not just its own formally-linked
successor.

Two independent halves:
  1. `handoff_diagnostics.stamp_session_state_worktree_binding` -- the
     session-state-folder half of "every session that starts in a worktree
     is recorded" (the worktree's own tracking YAML already accumulates
     every session in `record.sessions`; this is the durable session-state
     mirror of that same fact).
  2. `tracking`'s new "yielded" session state + the relaxed `register_session`
     head-claim gating that lets an ordinary next session supersede a
     yielded (not formally concluded) predecessor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import handoff_diagnostics, sessions, tracking
from agent_worktrees.tracking import (
    SessionLifecycleError,
    WorktreeRecord,
    load_record,
    save_record,
)


def _save_record(tracking_dir: Path, wt_id: str, wt_path: str, machine: str = "test") -> None:
    rec = WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=wt_path,
        repo="test-repo",
        machine=machine,
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
    )
    save_record(rec, tracking_dir / f"{wt_id}.yaml")


# -- stamp_session_state_worktree_binding ------------------------------------


class TestStampSessionStateWorktreeBinding:
    def test_writes_a_durable_binding_record(self, tmp_path, monkeypatch):
        session_dir = sessions._session_state_dir() / "sid-1"
        session_dir.mkdir(parents=True, exist_ok=True)

        result = handoff_diagnostics.stamp_session_state_worktree_binding(
            "sid-1", "wt-a", worktree_dir="/tmp/src/wt-a", machine="book2",
        )

        path = handoff_diagnostics.session_state_worktree_binding_path("sid-1")
        assert path.exists()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["sessionId"] == "sid-1"
        assert on_disk["worktreeId"] == "wt-a"
        assert on_disk["worktreeDir"] == "/tmp/src/wt-a"
        assert on_disk["machine"] == "book2"
        assert on_disk["registeredAt"]
        assert result == on_disk

    def test_never_creates_the_session_state_dir(self, tmp_path):
        # An active session already owns its session-state dir (mirrors
        # context-handoff's writeSessionStateHandoff convention) -- a session
        # id this process has never heard of is a silent no-op, not an error.
        result = handoff_diagnostics.stamp_session_state_worktree_binding(
            "never-registered", "wt-a", worktree_dir="/tmp/src/wt-a",
        )
        assert result is None
        path = handoff_diagnostics.session_state_worktree_binding_path(
            "never-registered"
        )
        assert not path.exists()

    def test_no_op_without_a_session_id_or_worktree_id(self):
        assert handoff_diagnostics.stamp_session_state_worktree_binding(
            None, "wt-a",
        ) is None
        assert handoff_diagnostics.stamp_session_state_worktree_binding(
            "sid-1", None,
        ) is None

    def test_degrades_safe_on_write_failure(self, monkeypatch):
        session_dir = sessions._session_state_dir() / "sid-2"
        session_dir.mkdir(parents=True, exist_ok=True)

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        result = handoff_diagnostics.stamp_session_state_worktree_binding(
            "sid-2", "wt-a", worktree_dir="/tmp/src/wt-a",
        )
        assert result is None


# -- yielded state + relaxed head-claim gating -------------------------------


class TestYieldedPredecessorAutoClaim:
    def test_opening_a_handoff_yields_the_predecessor(self, tmp_tracking_dir, monkeypatch_config):
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")

        tracking.open_handoff(rec, "old", "task-123")

        assert rec.session_entry("old").state == "yielded"
        # A yielded (not concluded) predecessor no longer resolves as head --
        # this is what opens the position for the next session to claim.
        assert rec.resolved_head_session is None

    def test_ordinary_next_session_auto_claims_head_after_yield(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")

        # An entirely ORDINARY new session -- no handoff_token, no
        # candidate_token, exactly what a manually-opened pane or a plain
        # /consume-handoff resume looks like.
        tracking.register_session("wt-1", "new")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session == "new"
        # The stale pending handoff is superseded, not left dangling.
        assert rec.handoffs[0].state == "cancelled"

    def test_yielded_predecessor_does_not_conclude(self, tmp_tracking_dir, monkeypatch_config):
        # "yielded" is deliberately distinct from "handed-off"/"concluded" --
        # the predecessor may still be alive/resumable.
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("old").state not in ("handed-off", "concluded")

    def test_a_genuinely_concluded_predecessors_pending_handoff_still_gates(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        # A pending handoff whose predecessor already explicitly concluded
        # (conclude_session, not merely open_handoff) represents a specific,
        # in-flight formal cutover -- an unrelated ordinary session must
        # still NOT silently hijack it (unchanged from before this fix).
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.conclude_session(
            rec, "old", state="handed-off", handoff_token="expected",
        )

        tracking.register_session("wt-1", "new")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session is None
        assert rec.handoffs[0].state == "pending"

    def test_explicit_bind_still_supersedes_a_concluded_predecessors_handoff(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.conclude_session(
            rec, "old", state="handed-off", handoff_token="expected",
        )
        tracking.register_session("wt-1", "new")

        tracking.register_session("wt-1", "new", source="bind")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session == "new"
        assert rec.handoffs[0].state == "cancelled"

    def test_stale_cancelled_token_degrades_instead_of_raising(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        # A live-cutover successor whose token was already superseded by an
        # unrelated ordinary session (a genuine race this fix newly permits)
        # must never hard-fail the sessionStart hook -- consuming a specific
        # handoff's charter is a separate concern from this session's own
        # registration.
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        # An unrelated ordinary session wins the race first.
        tracking.register_session("wt-1", "interloper")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.handoffs[0].state == "cancelled"

        # The "real" successor arrives moments later still carrying the
        # now-stale token -- must not raise.
        linked = tracking.register_session(
            "wt-1", "real-successor", handoff_token="task-123",
        )
        assert linked is None
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("real-successor") is not None

    def test_candidate_mismatch_still_raises_for_a_genuine_conflict(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        # A real candidate-mismatch conflict (two sessions both trying to
        # claim the SAME still-pending token) must still be rejected -- the
        # degrade-on-stale-token path above must not swallow genuine misuse.
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        tracking.register_session(
            "wt-1", "candidate", candidate_token="task-123",
        )
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.associate_handoff_candidate(rec, "task-123", "candidate")

        with pytest.raises(SessionLifecycleError, match="associated with candidate"):
            tracking.register_session(
                "wt-1", "other", source="bind", handoff_token="task-123",
            )

    def test_candidate_token_registration_never_auto_claims_either(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        # A session carrying a candidate_token is (likely) the intended
        # successor, but merely registering with that token must not itself
        # claim head -- only a deliberate later bind/link may.
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")

        tracking.register_session(
            "wt-1", "candidate", candidate_token="task-123",
        )

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session is None
        assert rec.handoffs[0].state == "pending"
