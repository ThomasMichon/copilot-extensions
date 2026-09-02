"""Tests for the session-lifecycle ground-layer model (agent-fabric vision
`single-current-session-per-worktree`): the per-worktree head pointer, the
asserted per-session state (active/handed-off/concluded), the two-way
predecessor<->successor chain, and their YAML round-trip + backward compat.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent_worktrees import tracking
from agent_worktrees.tracking import (
    HeadTransition,
    SessionEntry,
    SessionLifecycleError,
    WorktreeRecord,
    conclude_session,
    link_succession,
    load_record,
    save_record,
    set_head_session,
)


def _rec(tmp_tracking_dir: Path, sessions=None) -> WorktreeRecord:
    rec = WorktreeRecord(
        worktree_id="wt-1",
        branch="worktree/wt-1",
        worktree_path="/tmp/wt-1",
        repo="test-repo",
        machine="test",
        platform="wsl",
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=sessions if sessions is not None else [],
    )
    save_record(rec, tmp_tracking_dir / "wt-1.yaml")
    return rec


class TestBackwardCompat:
    def test_clean_record_emits_no_lifecycle_keys(self, tmp_tracking_dir: Path):
        _rec(tmp_tracking_dir)
        txt = (tmp_tracking_dir / "wt-1.yaml").read_text()
        assert "head_session" not in txt
        assert "state:" not in txt
        assert "successor" not in txt
        assert "predecessor" not in txt

    def test_active_sessions_stay_byte_identical(self, tmp_tracking_dir: Path):
        # A session in the default 'active' state with no links emits only the
        # legacy keys (no state/successor/predecessor churn).
        _rec(tmp_tracking_dir, sessions=[
            SessionEntry(session_id="s1", started_at="2026-01-01T00:00:00", pid=7),
        ])
        txt = (tmp_tracking_dir / "wt-1.yaml").read_text()
        assert "session_id: s1" in txt
        assert "state:" not in txt

    def test_legacy_yaml_without_lifecycle_loads(self, tmp_tracking_dir: Path):
        p = tmp_tracking_dir / "wt-1.yaml"
        p.write_text(
            "worktree_id: wt-1\nbranch: worktree/wt-1\nworktree_path: /tmp/wt-1\n"
            "repo: r\nmachine: m\nplatform: wsl\n"
            "started_at: 2026-01-01T00:00:00\nlast_resumed_at: 2026-01-01T00:00:00\n"
            "resume_count: 0\ntitle: null\nstatus: active\ncompleted_at: null\n"
            "sessions:\n- session_id: s1\n  started_at: 2026-01-01T00:00:00\n"
        )
        rec = load_record(p)
        assert rec.head_session is None
        entry = rec.session_entry("s1")
        assert entry is not None and entry.state == "active"
        assert entry.successor is None and entry.predecessor is None
        # Derived head = the sole active session.
        assert rec.resolved_head_session == "s1"

    def test_unknown_state_degrades_to_active(self, tmp_tracking_dir: Path):
        p = tmp_tracking_dir / "wt-1.yaml"
        p.write_text(
            "worktree_id: wt-1\nbranch: worktree/wt-1\nworktree_path: /tmp/wt-1\n"
            "repo: r\nmachine: m\nplatform: wsl\n"
            "started_at: 2026-01-01T00:00:00\nlast_resumed_at: 2026-01-01T00:00:00\n"
            "resume_count: 0\ntitle: null\nstatus: active\ncompleted_at: null\n"
            "sessions:\n- session_id: s1\n  started_at: 2026-01-01T00:00:00\n"
            "  state: bogus\n"
        )
        rec = load_record(p)
        assert rec.session_entry("s1").state == "active"

    def test_null_activation_start_is_skipped(self, tmp_tracking_dir: Path):
        p = tmp_tracking_dir / "wt-1.yaml"
        p.write_text(
            "worktree_id: wt-1\nbranch: worktree/wt-1\n"
            "worktree_path: /tmp/wt-1\nrepo: r\nmachine: m\nplatform: wsl\n"
            "started_at: 2026-01-01T00:00:00\n"
            "last_resumed_at: 2026-01-01T00:00:00\nresume_count: 0\n"
            "title: null\nstatus: active\ncompleted_at: null\n"
            "sessions:\n- session_id: s1\n  started_at: 2026-01-01T00:00:00\n"
            "  activations:\n  - ordinal: 1\n    started_at: null\n"
            "    start_recorded_at: null\n"
        )
        entry = load_record(p).session_entry("s1")
        assert entry.activations == []


class TestResolvedHead:
    def test_no_sessions_is_none(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir)
        assert rec.resolved_head_session is None

    def test_stored_head_wins_when_active(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("s1", "t"), SessionEntry("s2", "t"),
        ])
        rec.head_session = "s1"
        assert rec.resolved_head_session == "s1"

    def test_derives_newest_active_when_head_absent(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("s1", "t"), SessionEntry("s2", "t"),
        ])
        assert rec.resolved_head_session == "s2"

    def test_stale_concluded_head_does_not_win(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("s1", "t"),
            SessionEntry("s2", "t", state="concluded"),
        ])
        # Head points at a concluded session -> resolved advances to the active one.
        rec.head_session = "s2"
        assert rec.resolved_head_session == "s1"

    def test_all_concluded_is_none(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("s1", "t", state="concluded"),
            SessionEntry("s2", "t", state="handed-off"),
        ])
        assert rec.resolved_head_session is None


class TestTransitions:
    def test_set_head_requires_tracked_session(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = _rec(tmp_tracking_dir, sessions=[SessionEntry("s1", "t")])
        set_head_session(rec, "s1")
        assert load_record(rec.yaml_path).head_session == "s1"
        with pytest.raises(SessionLifecycleError):
            set_head_session(rec, "ghost")

    def test_conclude_clears_head_without_guessing_replacement(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("s1", "t"), SessionEntry("s2", "t"),
        ])
        rec.head_session = "s2"
        save_record(rec)
        conclude_session(rec, "s2")
        r = load_record(rec.yaml_path)
        assert r.session_entry("s2").state == "concluded"
        assert r.resolved_head_session is None
        assert r.replayed_head_transition.reason == "concluded"

    def test_conclude_last_clears_head(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = _rec(tmp_tracking_dir, sessions=[SessionEntry("s1", "t")])
        rec.head_session = "s1"
        save_record(rec)
        conclude_session(rec, "s1")
        r = load_record(rec.yaml_path)
        assert r.resolved_head_session is None

    def test_conclude_rejects_bad_state(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir, sessions=[SessionEntry("s1", "t")])
        with pytest.raises(SessionLifecycleError):
            conclude_session(rec, "s1", state="active")  # type: ignore[arg-type]

    def test_conclude_unknown_session_raises(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir, sessions=[SessionEntry("s1", "t")])
        with pytest.raises(SessionLifecycleError):
            conclude_session(rec, "ghost")

    def test_link_succession_writes_two_way_chain(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("old", "t"), SessionEntry("new", "t"),
        ])
        rec.head_session = "old"
        save_record(rec)
        link_succession(rec, "old", "new")
        r = load_record(rec.yaml_path)
        old, new = r.session_entry("old"), r.session_entry("new")
        assert old.state == "handed-off" and old.successor == "new"
        assert new.predecessor == "old" and new.state == "active"
        assert r.head_session == "new"
        assert r.resolved_head_session == "new"

    def test_link_succession_unknown_raises(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir, sessions=[SessionEntry("old", "t")])
        with pytest.raises(SessionLifecycleError):
            link_succession(rec, "old", "ghost")
        with pytest.raises(SessionLifecycleError):
            link_succession(rec, "ghost", "old")

    def test_save_false_batches(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("s1", "t"), SessionEntry("s2", "t"),
        ])
        conclude_session(rec, "s1", save=False)
        # Not persisted yet.
        assert load_record(rec.yaml_path).session_entry("s1").state == "active"
        save_record(rec)
        assert load_record(rec.yaml_path).session_entry("s1").state == "concluded"


class TestRegisterSessionHeadInit:
    def test_first_session_initializes_head(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "sess-A", pid=100)
        assert load_record(tmp_tracking_dir / "wt-1.yaml").head_session == "sess-A"

    def test_second_session_does_not_move_head(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "sess-A")
        tracking.register_session("wt-1", "sess-B")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        # Head stays on the incumbent; a parallel session doesn't steal it.
        assert rec.head_session == "sess-A"
        assert {s.session_id for s in rec.sessions} == {"sess-A", "sess-B"}

    def test_new_session_after_conclusion_becomes_head(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "sess-A")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(rec, "sess-A")
        # After the only session concluded, a fresh one becomes the head.
        tracking.register_session("wt-1", "sess-C")
        assert load_record(tmp_tracking_dir / "wt-1.yaml").head_session == "sess-C"

    def test_resume_existing_session_keeps_head(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "sess-A")
        # Re-register (resume) the same session: dedupe path, head unchanged.
        tracking.register_session("wt-1", "sess-A", pid=999)
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.head_session == "sess-A"
        assert len(rec.sessions) == 1
        assert rec.session_entry("sess-A").pid == 999


class TestExactHandoffLedger:
    def test_started_candidate_does_not_take_over_until_acknowledged(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        tracking.register_session("wt-1", "new")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.associate_handoff_candidate(rec, "task-123", "new")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        handoff = rec.handoffs[0]
        assert handoff.candidate == "new"
        assert handoff.state == "pending"
        assert rec.resolved_head_session == "old"
        assert rec.session_entry("old").state == "active"

        tracking.register_session(
            "wt-1", "new", source="bind", handoff_token="task-123"
        )
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.handoffs[0].state == "linked"
        assert rec.resolved_head_session == "new"
        assert rec.session_entry("old").state == "handed-off"

    def test_candidate_token_rejects_a_different_successor(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        tracking.register_session("wt-1", "candidate")
        tracking.register_session("wt-1", "other")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.associate_handoff_candidate(
            rec, "task-123", "candidate"
        )
        with pytest.raises(SessionLifecycleError, match="associated with candidate"):
            tracking.register_session(
                "wt-1", "other", source="bind", handoff_token="task-123"
            )

    def test_exact_handoff_token_completes_link(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(
            rec, "old", state="handed-off", handoff_token="task-123"
        )
        tracking.register_session(
            "wt-1", "new", handoff_token="task-123"
        )
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        old, new = rec.session_entry("old"), rec.session_entry("new")
        assert old.state == "handed-off" and old.successor == "new"
        assert new.predecessor == "old"
        assert rec.resolved_head_session == "new"
        assert rec.handoffs[0].ordinal == 1
        assert rec.handoffs[0].state == "linked"

    def test_unclaimed_pending_handoff_does_not_adopt_arbitrary_session(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(
            rec, "old", state="handed-off", handoff_token="expected"
        )
        tracking.register_session("wt-1", "new")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("old").successor is None
        assert rec.session_entry("new").predecessor is None
        assert rec.resolved_head_session is None
        assert rec.handoffs[0].state == "pending"

    def test_handed_off_without_token_does_not_create_unclaimable_handoff(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(rec, "old", state="handed-off")
        tracking.register_session("wt-1", "new")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.handoffs == []
        assert rec.resolved_head_session == "new"

    def test_explicit_bind_supersedes_stale_pending_handoff(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(
            rec, "old", state="handed-off", handoff_token="stale"
        )
        tracking.register_session("wt-1", "new")
        tracking.register_session("wt-1", "new", source="bind")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session == "new"
        assert rec.handoffs[0].state == "cancelled"
        assert rec.replayed_head_transition.reason == "rebind"

    def test_existing_active_session_can_rebind_after_head_concludes(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        tracking.register_session("wt-1", "new")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(rec, "old", state="handed-off")
        tracking.register_session("wt-1", "new")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session == "new"
        assert rec.replayed_head_transition.reason == "rebind"

    def test_token_selects_one_of_multiple_pending_handoffs(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir, sessions=[
            SessionEntry("h1", "t"),
            SessionEntry("h2", "t"),
        ])
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "h1", "token-1", save=False)
        tracking.open_handoff(rec, "h2", "token-2")
        tracking.register_session(
            "wt-1", "new", handoff_token="token-1"
        )
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("h1").successor == "new"
        assert rec.session_entry("new").predecessor == "h1"
        assert rec.session_entry("h2").successor is None
        assert [handoff.state for handoff in rec.handoffs] == [
            "linked", "pending"
        ]

    def test_linked_token_is_idempotent_for_same_successor(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "token")
        tracking.register_session("wt-1", "new", handoff_token="token")
        tracking.register_session("wt-1", "new", handoff_token="token")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("old").successor == "new"
        assert len(rec.head_transitions) == 2

    def test_unknown_token_still_persists_successor_association(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        with pytest.raises(SessionLifecycleError):
            tracking.register_session(
                "wt-1", "new", handoff_token="missing"
            )
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("new") is not None
        assert rec.resolved_head_session == "old"

    def test_late_token_cannot_overwrite_explicit_conclusion(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "token")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(rec, "old", state="concluded")
        with pytest.raises(SessionLifecycleError):
            tracking.register_session(
                "wt-1", "new", handoff_token="token"
            )
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("old").state == "concluded"
        assert rec.session_entry("old").successor is None

    def test_token_cannot_overwrite_conflicting_successor_predecessor(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir, sessions=[
            SessionEntry("old", "t", successor="other"),
            SessionEntry("new", "t", predecessor="different"),
        ])
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "token")
        with pytest.raises(SessionLifecycleError):
            tracking.link_handoff(rec, "token", "new")
        assert rec.session_entry("old").successor == "other"
        assert rec.session_entry("new").predecessor == "different"

    def test_new_handoff_cancels_prior_pending_for_same_predecessor(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "first")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "second")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert [handoff.state for handoff in rec.handoffs] == [
            "cancelled", "pending"
        ]
        assert [handoff.ordinal for handoff in rec.handoffs] == [1, 2]


class TestActivationLedger:
    def test_resume_appends_interval_without_overwriting_first_start(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session(
            "wt-1", "s", started_at="2026-01-01T10:00:00",
            recorded_at="2026-01-01T10:00:01",
        )
        tracking.deregister_session(
            "wt-1", "s", ended_at="2026-01-01T11:00:00",
            recorded_at="2026-01-01T11:00:01",
        )
        tracking.register_session(
            "wt-1", "s", started_at="2026-01-02T10:00:00",
            recorded_at="2026-01-02T10:00:01", source="hook:resume",
        )
        entry = load_record(tmp_tracking_dir / "wt-1.yaml").session_entry("s")
        assert entry.started_at == "2026-01-01T10:00:00"
        assert entry.ended_at is None
        assert [activation.ordinal for activation in entry.activations] == [1, 2]
        assert entry.activations[0].ended_at == "2026-01-01T11:00:00"
        assert entry.activations[1].start_source == "hook:resume"

    def test_duplicate_start_and_end_deliveries_are_idempotent(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "s", started_at="start")
        tracking.register_session("wt-1", "s", started_at="start")
        tracking.deregister_session("wt-1", "s", ended_at="end")
        tracking.deregister_session("wt-1", "s", ended_at="duplicate-end")
        entry = load_record(tmp_tracking_dir / "wt-1.yaml").session_entry("s")
        assert len(entry.activations) == 1
        assert entry.activations[0].started_at == "start"
        assert entry.activations[0].ended_at == "end"

    def test_new_start_after_missed_end_closes_prior_interval(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "s", started_at="first")
        tracking.register_session("wt-1", "s", started_at="second")
        entry = load_record(tmp_tracking_dir / "wt-1.yaml").session_entry("s")
        assert len(entry.activations) == 2
        assert entry.activations[0].ended_at == "second"
        assert entry.activations[0].end_source == "inferred:next-start"
        assert entry.activations[1].started_at == "second"


class TestHeadTransitionReplay:
    def test_transition_ledger_wins_over_stale_cache(self, tmp_tracking_dir: Path):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("old", "t"), SessionEntry("current", "t"),
        ])
        rec.head_session = "old"
        rec.head_revision = 1
        rec.lifecycle_revision = 2
        rec.head_transitions = [
            HeadTransition(1, "old", "initial", "t"),
            HeadTransition(2, "current", "adopted", "t"),
        ]
        assert rec.resolved_head_session == "current"
        assert tracking.repair_head_cache(rec) is True
        assert rec.head_session == "current"
        assert rec.head_revision == 2

    def test_stale_non_lifecycle_writer_cannot_roll_back_ledger(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "old")
        stale = load_record(tmp_tracking_dir / "wt-1.yaml")
        current = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(current, "old", "token")
        stale_revision = stale.lifecycle_revision
        stale.summary = "unrelated status write"
        save_record(stale)
        after = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert after.summary == "unrelated status write"
        assert after.handoffs[0].token == "token"
        assert after.lifecycle_revision > stale_revision


class TestHeadSessionCommand:
    """The ``head-session`` CLI read -- the ground-layer derive point that
    agent-bridge's create guard consumes (agent-fabric `derive-dont-duplicate`).
    """

    @staticmethod
    def _run(monkeypatch, worktree_id: str) -> dict:
        from agent_worktrees import __main__ as m

        captured: dict = {}
        monkeypatch.setattr(m, "_json_output", lambda data: captured.update(data))
        args = argparse.Namespace(worktree_id=worktree_id, json=True)
        rc = m.cmd_head_session(args)
        captured["_rc"] = rc
        return captured

    def test_active_head_reported(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "sess-A")
        out = self._run(monkeypatch, "wt-1")
        assert out["_rc"] == 0
        assert out["tracked"] is True
        assert out["head_session"] == "sess-A"
        assert out["active"] is True
        assert out["state"] == "active"

    def test_concluded_head_is_inactive(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "sess-A")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(rec, "sess-A")
        out = self._run(monkeypatch, "wt-1")
        # A worktree whose only session was concluded has no active head, so a
        # fresh create is permitted (guard must not fire).
        assert out["tracked"] is True
        assert out["head_session"] is None
        assert out["active"] is False
        assert out["state"] is None

    def test_handed_off_head_advances_to_successor(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "sess-A")
        tracking.register_session("wt-1", "sess-B")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        # sess-A is head; hand it off into sess-B.
        set_head_session(rec, "sess-A")
        link_succession(rec, "sess-A", "sess-B")
        out = self._run(monkeypatch, "wt-1")
        assert out["head_session"] == "sess-B"
        assert out["active"] is True
        assert out["state"] == "active"

    def test_pending_handoff_is_occupied_but_not_an_active_head(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _rec(tmp_tracking_dir)
        tracking.register_session("wt-1", "sess-A")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        conclude_session(
            rec, "sess-A", state="handed-off", handoff_token="token"
        )
        out = self._run(monkeypatch, "wt-1")
        assert out["head_session"] is None
        assert out["active"] is False
        assert out["occupied"] is True
        assert out["pending_handoffs"][0]["token"] == "token"

    def test_untracked_worktree_fails_open(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        # An unknown worktree is not an error: exit 0, tracked False, no head --
        # a fail-open signal so the create guard permits the session.
        out = self._run(monkeypatch, "nonesuch")
        assert out["_rc"] == 0
        assert out["tracked"] is False
        assert out["head_session"] is None
        assert out["active"] is False


class TestFindTrackingFileAcrossProjects:
    """``_find_tracking_file`` resolves a worktree id **without** an active
    project -- the project-agnostic lookup the agent-bridge daemon needs (its
    CWD is unrelated to the worktree it guards). It searches every project's
    tracking dir, prefers an exact id, and fails open on ambiguity/traversal.
    """

    def test_exact_match_found_via_registry_dir(
        self, tmp_path: Path, monkeypatch
    ):
        from agent_worktrees import __main__ as m

        proj = tmp_path / ".proj-a" / "worktrees"
        proj.mkdir(parents=True)
        _rec(proj)  # writes proj/wt-1.yaml
        # No active-project fast path; resolution must come from the registry dir.
        monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [proj])
        assert m._find_tracking_file("wt-1") == proj / "wt-1.yaml"

    def test_unique_suffix_match(self, tmp_path: Path, monkeypatch):
        from agent_worktrees import __main__ as m

        proj = tmp_path / ".proj-a" / "worktrees"
        proj.mkdir(parents=True)
        rec = WorktreeRecord(
            worktree_id="mantis-counter-linux-20260101-000000-abcd",
            branch="b", worktree_path="/tmp/x", repo="r", machine="test",
            platform="wsl", started_at="t", last_resumed_at="t", resume_count=0,
            title=None, status="active", completed_at=None, sessions=[],
        )
        save_record(rec, proj / f"{rec.worktree_id}.yaml")
        monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [proj])
        assert m._find_tracking_file("abcd") == proj / f"{rec.worktree_id}.yaml"

    def test_ambiguous_suffix_fails_open(self, tmp_path: Path, monkeypatch):
        from agent_worktrees import __main__ as m

        a = tmp_path / ".proj-a" / "worktrees"
        b = tmp_path / ".proj-b" / "worktrees"
        for d, wid in ((a, "aaa-dup"), (b, "bbb-dup")):
            d.mkdir(parents=True)
            rec = WorktreeRecord(
                worktree_id=wid, branch="b", worktree_path="/tmp/x", repo="r",
                machine="test", platform="wsl", started_at="t",
                last_resumed_at="t", resume_count=0, title=None,
                status="active", completed_at=None, sessions=[],
            )
            save_record(rec, d / f"{wid}.yaml")
        monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [a, b])
        # Two records end in "dup" -> ambiguous -> None (never guess).
        assert m._find_tracking_file("dup") is None

    def test_path_traversal_rejected(self, monkeypatch):
        from agent_worktrees import __main__ as m

        # A traversal/glob id never touches the filesystem.
        assert m._find_tracking_file("../etc/passwd") is None


class TestConcludeAndLinkCommands:
    """The mutating ground-layer CLI verbs context-handoff shells to at cutover.
    Both resolve the worktree project-agnostically and persist to the RESOLVED
    path (they run with no active project, so a bare save would misfire).
    """

    @staticmethod
    def _run(monkeypatch, tracking_dir: Path, fn_name: str, **ns) -> dict:
        from agent_worktrees import __main__ as m

        captured: dict = {}
        monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [tracking_dir])
        monkeypatch.setattr(m, "_json_output", lambda data: captured.update(data))
        rc = getattr(m, fn_name)(argparse.Namespace(json=True, **ns))
        captured["_rc"] = rc
        return captured

    def test_conclude_session_hands_off_and_clears_head(
        self, tmp_tracking_dir: Path, monkeypatch
    ):
        _rec(tmp_tracking_dir, sessions=[SessionEntry("solo", "t")])
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        rec.head_session = "solo"
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")
        out = self._run(
            monkeypatch, tmp_tracking_dir, "cmd_conclude_session",
            worktree_id="wt-1", session_id="solo", state="handed-off",
        )
        assert out["_rc"] == 0
        assert out["state"] == "handed-off"
        assert out["head_session"] is None
        # Persisted to the resolved path.
        r = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert r.session_entry("solo").state == "handed-off"

    def test_conclude_unknown_session_errors(
        self, tmp_tracking_dir: Path, monkeypatch
    ):
        _rec(tmp_tracking_dir, sessions=[SessionEntry("solo", "t")])
        out = self._run(
            monkeypatch, tmp_tracking_dir, "cmd_conclude_session",
            worktree_id="wt-1", session_id="ghost", state="handed-off",
        )
        assert out["_rc"] != 0

    def test_conclude_unknown_worktree_errors(
        self, tmp_tracking_dir: Path, monkeypatch
    ):
        out = self._run(
            monkeypatch, tmp_tracking_dir, "cmd_conclude_session",
            worktree_id="nope", session_id="x", state="handed-off",
        )
        assert out["_rc"] != 0

    def test_link_succession_writes_two_way_and_moves_head(
        self, tmp_tracking_dir: Path, monkeypatch
    ):
        _rec(tmp_tracking_dir, sessions=[
            SessionEntry("old", "t"), SessionEntry("new", "t"),
        ])
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        rec.head_session = "old"
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")
        out = self._run(
            monkeypatch, tmp_tracking_dir, "cmd_link_succession",
            worktree_id="wt-1", predecessor="old", successor="new",
            predecessor_state="handed-off",
        )
        assert out["_rc"] == 0
        assert out["head_session"] == "new"
        r = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert r.session_entry("old").successor == "new"
        assert r.session_entry("old").state == "handed-off"
        assert r.session_entry("new").predecessor == "old"
        assert r.head_session == "new"

    def test_link_succession_unknown_session_errors(
        self, tmp_tracking_dir: Path, monkeypatch
    ):
        _rec(tmp_tracking_dir, sessions=[SessionEntry("old", "t")])
        out = self._run(
            monkeypatch, tmp_tracking_dir, "cmd_link_succession",
            worktree_id="wt-1", predecessor="old", successor="ghost",
            predecessor_state="handed-off",
        )
        assert out["_rc"] != 0


class TestListSessionsEnvelopeHead:
    """``list-sessions --worktree`` puts the asserted head on the envelope so a
    consumer (agent-bridge -> Neuron Forge) resolves the current session without
    re-deriving it (agent-fabric single-current-session-per-worktree, Phase 4).
    """

    def test_scoped_envelope_carries_head(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        from agent_worktrees import __main__ as m
        from agent_worktrees import sessions as S

        _rec(tmp_tracking_dir, sessions=[
            SessionEntry("s1", "t"), SessionEntry("s2", "t"),
        ])
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        set_head_session(rec, "s1")
        # Stub the per-session meta scan (no real session-state on disk here);
        # the envelope head is derived from the record, independently of it.
        monkeypatch.setattr(
            S, "list_worktree_sessions",
            lambda record: [{"id": s.session_id} for s in record.sessions],
        )
        captured: dict = {}
        monkeypatch.setattr(m, "_json_output", lambda data: captured.update(data))
        rc = m.cmd_list_sessions(argparse.Namespace(worktree_id="wt-1", json=True))
        assert rc == 0
        assert captured["head_session"] == "s1"
        assert {s["id"] for s in captured["sessions"]} == {"s1", "s2"}

    def test_unscoped_envelope_head_is_none(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        from agent_worktrees import __main__ as m
        from agent_worktrees import sessions as S

        _rec(tmp_tracking_dir, sessions=[SessionEntry("s1", "t")])
        monkeypatch.setattr(S, "list_worktree_sessions", lambda record: [])
        captured: dict = {}
        monkeypatch.setattr(m, "_json_output", lambda data: captured.update(data))
        rc = m.cmd_list_sessions(argparse.Namespace(worktree_id=None, json=True))
        assert rc == 0
        # Per-session is_head covers the all-worktrees case; the envelope head is
        # only meaningful when scoped to one worktree.
        assert captured["head_session"] is None

    def test_all_projects_carries_resolved_provenance(
        self, tmp_path: Path, monkeypatch
    ):
        from agent_worktrees import __main__ as m
        from agent_worktrees import sessions as S

        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_a.mkdir()
        project_b.mkdir()
        first = WorktreeRecord(
            worktree_id="wt-a",
            branch="worktree/wt-a",
            worktree_path="/tmp/wt-a",
            repo="a",
            machine="test",
            platform="wsl",
            started_at="t",
            last_resumed_at="t",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[SessionEntry("session-a", "t")],
            kind="bridge",
            origin="user",
        )
        second = WorktreeRecord(
            worktree_id="wt-b",
            branch="worktree/wt-b",
            worktree_path="/tmp/wt-b",
            repo="b",
            machine="test",
            platform="wsl",
            started_at="t",
            last_resumed_at="t",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[SessionEntry("session-b", "t")],
            kind="system",
        )
        save_record(first, project_a / "wt-a.yaml")
        save_record(second, project_b / "wt-b.yaml")
        monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [project_a, project_b])
        monkeypatch.setattr(
            S,
            "list_worktree_sessions",
            lambda record: [{"id": entry.session_id} for entry in record.sessions],
        )
        captured: dict = {}
        monkeypatch.setattr(m, "_json_output", lambda data: captured.update(data))

        rc = m.cmd_list_sessions(argparse.Namespace(
            worktree_id=None,
            all_projects=True,
            json=True,
        ))

        assert rc == 0
        assert captured["sessions"] == [
            {
                "id": "session-a",
                "worktree_id": "wt-a",
                "interface": "acp",
                "origin": "user",
            },
            {
                "id": "session-b",
                "worktree_id": "wt-b",
                "interface": "cli",
                "origin": "system",
            },
        ]

    def test_conflicting_duplicate_session_provenance_is_unknown(
        self, tmp_path: Path, monkeypatch
    ):
        from agent_worktrees import __main__ as m
        from agent_worktrees import sessions as S

        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_a.mkdir()
        project_b.mkdir()
        for project, worktree_id, kind in (
            (project_a, "wt-a", "session"),
            (project_b, "wt-b", "system"),
        ):
            record = WorktreeRecord(
                worktree_id=worktree_id,
                branch=f"worktree/{worktree_id}",
                worktree_path=f"/tmp/{worktree_id}",
                repo="example",
                machine="test",
                platform="wsl",
                started_at="t",
                last_resumed_at="t",
                resume_count=0,
                title=None,
                status="active",
                completed_at=None,
                sessions=[SessionEntry("shared-session", "t")],
                kind=kind,
            )
            save_record(record, project / f"{worktree_id}.yaml")
        monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [project_a, project_b])
        monkeypatch.setattr(
            S,
            "list_worktree_sessions",
            lambda record: [{"id": entry.session_id} for entry in record.sessions],
        )
        captured: dict = {}
        monkeypatch.setattr(m, "_json_output", lambda data: captured.update(data))

        rc = m.cmd_list_sessions(argparse.Namespace(
            worktree_id=None,
            all_projects=True,
            json=True,
        ))

        assert rc == 0
        assert len(captured["sessions"]) == 1
        assert captured["sessions"][0]["interface"] == "unknown"
        assert captured["sessions"][0]["origin"] == "unknown"
        assert captured["sessions"][0]["provenance_conflict"] is True
