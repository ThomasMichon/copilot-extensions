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

    def test_conclude_advances_head(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = _rec(tmp_tracking_dir, sessions=[
            SessionEntry("s1", "t"), SessionEntry("s2", "t"),
        ])
        rec.head_session = "s2"
        save_record(rec)
        conclude_session(rec, "s2")
        r = load_record(rec.yaml_path)
        assert r.session_entry("s2").state == "concluded"
        # Head advanced off the concluded session to the remaining active one.
        assert r.resolved_head_session == "s1"

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
            worktree_id="wheatley-linux-20260101-000000-abcd",
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
