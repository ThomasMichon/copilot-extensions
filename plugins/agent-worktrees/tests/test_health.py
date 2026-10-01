"""Tests for agent_worktrees.health -- the doctor engine."""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import yaml

from agent_worktrees import health
from agent_worktrees.tracking import (
    HeadTransition,
    SessionEntry,
    SessionHandoff,
    WorktreeRecord,
    load_record,
    save_record,
)

# --------------------------------------------------------------------------- #
# YAML integrity
# --------------------------------------------------------------------------- #
_CORRUPT = (
    "worktree_id: wt-1\n"
    "branch: worktree/wt-1\n"
    "title: session-sync: native SSH transport via Mantis-Counter\n"
    "status: complete\n"
)
_CLEAN = "worktree_id: wt-2\nbranch: worktree/wt-2\ntitle: 'plain title'\nstatus: complete\n"


class TestYamlIntegrity:
    def test_repair_yaml_text_quotes_unquoted_colon_title(self):
        fixed = health.repair_yaml_text(_CORRUPT)
        assert fixed is not None
        data = yaml.safe_load(fixed)
        assert data["title"] == "session-sync: native SSH transport via Mantis-Counter"

    def test_repair_yaml_text_noop_on_clean(self):
        assert health.repair_yaml_text(_CLEAN) is None

    def test_integrity_detects_and_repairs(self, tmp_path: Path):
        (tmp_path / "wt-1.yaml").write_text(_CORRUPT, encoding="utf-8")
        (tmp_path / "wt-2.yaml").write_text(_CLEAN, encoding="utf-8")
        # report-only: found but not repaired
        found = health.repair_yaml_integrity(tmp_path, apply=False)
        assert len(found) == 1
        assert found[0].repairable and not found[0].repaired
        # still corrupt on disk
        try:
            yaml.safe_load((tmp_path / "wt-1.yaml").read_text(encoding="utf-8"))
            raise AssertionError("expected still-corrupt")
        except yaml.YAMLError:
            pass
        # apply: repaired and parses
        fixed = health.repair_yaml_integrity(tmp_path, apply=True)
        assert fixed[0].repaired
        assert isinstance(
            yaml.safe_load((tmp_path / "wt-1.yaml").read_text(encoding="utf-8")), dict)

    def test_integrity_clean_dir(self, tmp_path: Path):
        (tmp_path / "wt-2.yaml").write_text(_CLEAN, encoding="utf-8")
        assert health.repair_yaml_integrity(tmp_path, apply=False) == []


# --------------------------------------------------------------------------- #
# Stale status
# --------------------------------------------------------------------------- #
class TestStaleStatus:
    def test_flags_active_with_completed_at(self):
        done = "2026-05-20T00:00:00"
        recs = [
            SimpleNamespace(worktree_id="a", status="active", completed_at=done),
            SimpleNamespace(worktree_id="b", status="complete", completed_at=done),
            SimpleNamespace(worktree_id="c", status="active", completed_at=None),
            SimpleNamespace(worktree_id="d", status="finalized", completed_at=done),
        ]
        stale = health.find_stale_status(recs)
        assert [r.worktree_id for r in stale] == ["a"]


# --------------------------------------------------------------------------- #
# Stale active reconciliation (real state vs. tracked ``active``)
# --------------------------------------------------------------------------- #
class TestStaleActiveReconciliation:
    def _rec(self, wid: str, *, status: str = "active", path: str = "/w/a") -> SimpleNamespace:
        return SimpleNamespace(
            worktree_id=wid, status=status, branch=f"worktree/{wid}", worktree_path=path,
        )

    def _info(self, state):
        from agent_worktrees import git_ops
        return git_ops.WorktreeStateInfo(state=state)

    def test_flags_gone_completed_and_unused(self, monkeypatch):
        from agent_worktrees import git_ops

        recs = [self._rec("gone-one"), self._rec("done-one"), self._rec("unused-one")]
        for r in recs:
            r.worktree_path = f"/w/{r.worktree_id}"
        states = {
            "/w/gone-one": git_ops.WorktreeState.GONE,
            "/w/done-one": git_ops.WorktreeState.COMPLETED,
            "/w/unused-one": git_ops.WorktreeState.UNUSED,
        }
        monkeypatch.setattr(
            health.git_ops, "classify_worktree",
            lambda path, branch, **kw: self._info(states[path]),
        )
        found = health.find_stale_active_records(recs, active_paths=set())
        assert {f.worktree_id for f in found} == {"gone-one", "done-one", "unused-one"}
        assert {f.computed_state for f in found} == {"gone", "completed", "unused"}

    def test_skips_non_active_status(self, monkeypatch):
        from agent_worktrees import git_ops

        rec = self._rec("finalized-one", status="finalized")
        monkeypatch.setattr(
            health.git_ops, "classify_worktree",
            lambda *a, **kw: self._info(git_ops.WorktreeState.GONE),
        )
        assert health.find_stale_active_records([rec], active_paths=set()) == []

    def test_skips_ambiguous_or_live_states(self, monkeypatch):
        from agent_worktrees import git_ops

        recs = [
            self._rec("dirty-one", path="/w/dirty-one"),
            self._rec("orphan-one", path="/w/orphan-one"),
            self._rec("unknown-one", path="/w/unknown-one"),
            self._rec("active-one", path="/w/active-one"),
        ]
        states = {
            "/w/dirty-one": git_ops.WorktreeState.DIRTY,
            "/w/orphan-one": git_ops.WorktreeState.ORPHAN,
            "/w/unknown-one": git_ops.WorktreeState.UNKNOWN,
            "/w/active-one": git_ops.WorktreeState.ACTIVE,
        }
        monkeypatch.setattr(
            health.git_ops, "classify_worktree",
            lambda path, branch, **kw: self._info(states[path]),
        )
        assert health.find_stale_active_records(recs, active_paths=set()) == []

    def test_skips_records_without_worktree_path(self, monkeypatch):
        rec = self._rec("no-path", path="")
        called = []
        monkeypatch.setattr(
            health.git_ops, "classify_worktree",
            lambda *a, **kw: called.append(1),
        )
        assert health.find_stale_active_records([rec], active_paths=set()) == []
        assert called == []


# --------------------------------------------------------------------------- #
# Empty session shells
# --------------------------------------------------------------------------- #
def _mk_session(root: Path, sid: str, *, user_msg: bool, age_h: float = 5.0,
                lock: bool = False) -> None:
    d = root / sid
    d.mkdir()
    line = '{"type":"user.message"}\n' if user_msg else '{"type":"assistant.message"}\n'
    (d / "events.jsonl").write_text(line, encoding="utf-8")
    if lock:
        (d / "session.lock").write_text("", encoding="utf-8")
    past = time.time() - age_h * 3600
    os.utime(d, (past, past))


class TestEmptyShells:
    def test_finds_only_empty_old_unlocked(self, tmp_path: Path):
        _mk_session(tmp_path, "empty-old", user_msg=False, age_h=10)
        _mk_session(tmp_path, "has-user", user_msg=False, age_h=10)  # override below
        # give has-user a user.message
        (tmp_path / "has-user" / "events.jsonl").write_text(
            '{"type":"user.message"}\n', encoding="utf-8")
        _mk_session(tmp_path, "empty-fresh", user_msg=False, age_h=0.1)
        _mk_session(tmp_path, "empty-locked", user_msg=False, age_h=10, lock=True)
        found = {s.session_id for s in health.find_empty_session_shells(
            tmp_path, min_age_h=2.0)}
        assert found == {"empty-old"}

    def test_excludes_given_ids(self, tmp_path: Path):
        _mk_session(tmp_path, "keep-me", user_msg=False, age_h=10)
        found = health.find_empty_session_shells(
            tmp_path, min_age_h=2.0, exclude_ids=frozenset({"keep-me"}))
        assert found == []


# --------------------------------------------------------------------------- #
# Store purge + gc
# --------------------------------------------------------------------------- #
def _mk_store(path: Path, ids: list[str]) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT)")
    con.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, session_id TEXT)")
    for sid in ids:
        con.execute("INSERT INTO sessions (id, cwd) VALUES (?,?)", (sid, "/x"))
        con.execute("INSERT INTO turns (session_id) VALUES (?)", (sid,))
    con.commit()
    con.close()


class TestStorePurge:
    def test_purge_removes_rows(self, tmp_path: Path):
        db = tmp_path / "session-store.db"
        _mk_store(db, ["s1", "s2", "keep"])
        removed = health.purge_store_rows(db, ["s1", "s2"])
        assert removed == 4  # 2 sessions + 2 turns
        con = sqlite3.connect(str(db))
        assert [r[0] for r in con.execute("SELECT id FROM sessions")] == ["keep"]
        con.close()

    def test_purge_absent_db_is_noop(self, tmp_path: Path):
        assert health.purge_store_rows(tmp_path / "nope.db", ["s1"]) == 0

    def test_gc_report_only_removes_nothing(self, tmp_path: Path):
        ss = tmp_path / "session-state"
        ss.mkdir()
        _mk_session(ss, "empty-old", user_msg=False, age_h=10)
        db = tmp_path / "session-store.db"
        _mk_store(db, ["empty-old"])
        shells = health.find_empty_session_shells(ss, min_age_h=2.0)
        res = health.gc_empty_shells(ss, db, shells, apply=False)
        assert res["count"] == 1 and res["removed_dirs"] == 0
        assert (ss / "empty-old").is_dir()  # untouched

    def test_gc_apply_deletes_dirs_and_rows(self, tmp_path: Path):
        ss = tmp_path / "session-state"
        ss.mkdir()
        _mk_session(ss, "empty-old", user_msg=False, age_h=10)
        db = tmp_path / "session-store.db"
        _mk_store(db, ["empty-old"])
        shells = health.find_empty_session_shells(ss, min_age_h=2.0)
        res = health.gc_empty_shells(ss, db, shells, apply=True)
        assert res["removed_dirs"] == 1 and res["removed_rows"] == 2
        assert not (ss / "empty-old").exists()


# --------------------------------------------------------------------------- #
# Alignment audit + helpers
# --------------------------------------------------------------------------- #
class TestAlignment:
    def test_flags_foreign_parent_cwd(self, tmp_path: Path):
        # parent session with a DIFFERENT cwd than the worktree's own path
        parent = tmp_path / "parent-sess"
        parent.mkdir()
        (parent / "workspace.yaml").write_text("cwd: D:\\wt\\da0c\n", encoding="utf-8")
        recs = [
            SimpleNamespace(worktree_id="d922", sessions=[], parent_session="parent-sess",
                            worktree_path="D:\\wt\\d922"),
            # own session -> excluded
            SimpleNamespace(worktree_id="da0c", sessions=[SimpleNamespace(session_id="x")],
                            parent_session="parent-sess", worktree_path="D:\\wt\\da0c"),
            # no parent -> excluded
            SimpleNamespace(worktree_id="solo", sessions=[], parent_session=None,
                            worktree_path="D:\\wt\\solo"),
        ]
        out = health.audit_alignment(recs, tmp_path)
        assert [m["worktree_id"] for m in out] == ["d922"]

    def test_matching_cwd_not_flagged(self, tmp_path: Path):
        parent = tmp_path / "p2"
        parent.mkdir()
        (parent / "workspace.yaml").write_text("cwd: D:\\wt\\same\n", encoding="utf-8")
        recs = [SimpleNamespace(worktree_id="same", sessions=[], parent_session="p2",
                                worktree_path="D:\\wt\\same")]
        assert health.audit_alignment(recs, tmp_path) == []


class TestHelpers:
    def test_registered_session_ids(self):
        recs = [
            SimpleNamespace(sessions=[SimpleNamespace(session_id="a"),
                                      SimpleNamespace(session_id="b")]),
            SimpleNamespace(sessions=None),
            SimpleNamespace(sessions=[]),
        ]
        assert health.registered_session_ids(recs) == {"a", "b"}

    def test_default_store_db(self, tmp_path: Path):
        ss = tmp_path / "session-state"
        assert health.default_store_db(ss) == tmp_path / "session-store.db"


# --------------------------------------------------------------------------- #
# Orphaned handoffs (head lost to a failed cutover)
# --------------------------------------------------------------------------- #
_NOW = 1_800_000_000.0
_STALE = datetime.fromtimestamp(_NOW - 7200).isoformat()   # 2h ago
_FRESH = datetime.fromtimestamp(_NOW - 60).isoformat()     # 1m ago


def _session(sid="s1", state="handed-off", successor=None):
    return SimpleNamespace(session_id=sid, state=state, successor=successor,
                           started_at=None, ended_at=None)


def _handoff(predecessor="s1", opened_at=None, candidate_at=None):
    return SimpleNamespace(predecessor=predecessor, opened_at=opened_at,
                           candidate_at=candidate_at)


def _oh_rec(**over):
    base = dict(
        worktree_id="wt", status="active",
        mux_live=None, bound_live=None,
        last_resumed_at=_STALE, started_at=None, session_state_at=None,
        mux_live_at=None, bound_live_at=None,
        sessions=[_session()],
        handoffs=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestOrphanedHandoffs:
    def test_detects_dark_stale_unlinked_handoff(self, monkeypatch):
        # The 453f scenario: lone handed-off tail, no successor, active, dark, stale.
        monkeypatch.setattr(health.cfg, "active_project", lambda: "proj-a")
        monkeypatch.setattr(
            health.handoff_trace,
            "read_trace",
            lambda project, worktree_id: [
                {"stage": 8, "stage_name": "handoff_successor_spawn_started"}
            ],
        )
        found = health.find_orphaned_handoffs([_oh_rec()], now=_NOW)
        assert len(found) == 1
        assert found[0].worktree_id == "wt" and found[0].session_id == "s1"
        assert round(found[0].age_h) == 2
        assert found[0].last_stage == 8
        assert found[0].last_stage_name == "handoff_successor_spawn_started"

    def test_skips_fresh_cutover(self):
        # A successor may still be registering -- must NOT be touched.
        rec = _oh_rec(last_resumed_at=_FRESH)
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_when_mux_live(self):
        rec = _oh_rec(mux_live=True)
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_when_bound_live(self):
        rec = _oh_rec(bound_live=True)
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_deliberate_concluded_tail(self):
        rec = _oh_rec(sessions=[_session(state="concluded")])
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_detects_dark_stale_unlinked_yielded_tail(self, monkeypatch):
        """aperture-labs#7824 regression: a lone "yielded" tail (opened a
        handoff intent, never linked to a successor) is the same "orphaned,
        nobody ever took head" shape as an unlinked "handed-off" tail, and
        must be detected the same way."""
        monkeypatch.setattr(health.cfg, "active_project", lambda: "proj-a")
        monkeypatch.setattr(
            health.handoff_trace, "read_trace", lambda project, worktree_id: [],
        )
        rec = _oh_rec(sessions=[_session(state="yielded")])
        found = health.find_orphaned_handoffs([rec], now=_NOW)
        assert len(found) == 1
        assert found[0].session_id == "s1"

    def test_skips_when_yielded_tail_has_linked_successor(self):
        rec = _oh_rec(sessions=[_session("old", "yielded", successor="new")])
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_freshly_yielded_handoff_despite_stale_record_timestamps(self):
        """`open_handoff()` only ever runs on an *active* session, so a
        handoff it opened moments ago is itself fresh activity -- a
        long-dormant `last_resumed_at` must never outrank it. Without
        folding `SessionHandoff.opened_at` into the activity calculation, a
        record with stale legacy timestamps but a handoff opened seconds ago
        would be misjudged stale and `doctor --fix` would undo an in-flight
        handoff."""
        rec = _oh_rec(
            sessions=[_session(state="yielded")],
            handoffs=[_handoff(predecessor="s1", opened_at=_FRESH)],
        )
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_stale_handoff_with_fresh_candidate_pickup(self):
        """`associate_handoff_candidate()` leaves a handoff "pending" while a
        successor is mid-pickup, and that successor may not yet make
        `mux_live`/`bound_live` true -- a recent candidate association is
        itself fresh activity just like a recent `opened_at`, so an
        old-`opened_at`/fresh-`candidate_at` handoff must not be misjudged
        stale during that pickup window."""
        rec = _oh_rec(
            sessions=[_session(state="yielded")],
            handoffs=[_handoff(predecessor="s1", opened_at=_STALE, candidate_at=_FRESH)],
        )
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_completed_succession(self):
        # Predecessor handed-off + a live successor => head resolves, not orphaned.
        rec = _oh_rec(sessions=[
            _session("old", "handed-off", successor="new"),
            _session("new", "active"),
        ])
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_when_tail_has_linked_successor(self):
        rec = _oh_rec(sessions=[_session("old", "handed-off", successor="new")])
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_terminal_status(self):
        for st in ("finalized", "complete", "pushed", "orphaned"):
            rec = _oh_rec(status=st)
            assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_skips_no_sessions(self):
        assert health.find_orphaned_handoffs([_oh_rec(sessions=[])], now=_NOW) == []

    def test_unprovable_staleness_is_skipped(self):
        # No parseable timestamp anywhere -> cannot prove staleness -> skip.
        rec = _oh_rec(last_resumed_at=None)
        assert health.find_orphaned_handoffs([rec], now=_NOW) == []

    def test_reactivation_makes_head_resolvable(self, tmp_tracking_dir, monkeypatch_config):
        # End-to-end semantic: a real record whose head is orphaned re-derives
        # to the re-activated session via the orchestrator's own repair verb.
        entry = SessionEntry(session_id="s1", started_at="2026-01-01T00:00:00",
                             state="handed-off")
        rec = WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
            repo="test-repo", machine="test", platform="wsl",
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[entry],
        )
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session is None  # orphaned
        orphans = health.find_orphaned_handoffs([rec])
        assert len(orphans) == 1 and orphans[0].session_id == "s1"
        assert health.reactivate_orphaned_handoff(orphans[0]) is True
        assert orphans[0].reactivated is True
        fresh = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert fresh.resolved_head_session == "s1"  # resumable again

    def test_yielded_tail_reactivation_makes_head_resolvable(
        self, tmp_tracking_dir, monkeypatch_config
    ):
        """Same end-to-end semantic as `test_reactivation_makes_head_resolvable`
        above, for a "yielded" (not "handed-off") orphaned tail -- the
        orchestrator's repair must reactivate either state the same way
        (aperture-labs#7824)."""
        entry = SessionEntry(session_id="s1", started_at="2026-01-01T00:00:00",
                             state="yielded")
        rec = WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
            repo="test-repo", machine="test", platform="wsl",
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[entry],
        )
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session is None  # orphaned
        orphans = health.find_orphaned_handoffs([rec])
        assert len(orphans) == 1 and orphans[0].session_id == "s1"
        assert health.reactivate_orphaned_handoff(orphans[0]) is True
        fresh = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert fresh.resolved_head_session == "s1"  # resumable again

    def test_yielded_tail_reactivation_cancels_its_stale_pending_handoff(
        self, tmp_tracking_dir, monkeypatch_config
    ):
        """The pending handoff that produced a yielded orphan's state must
        not survive its repair -- an active head that still reports a
        pending handoff blocks terminal cleanup and could let a later token
        consumer link a handoff the repair already superseded."""
        entry = SessionEntry(session_id="s1", started_at="2026-01-01T00:00:00",
                             state="yielded")
        rec = WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
            repo="test-repo", machine="test", platform="wsl",
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[entry],
            handoffs=[
                SessionHandoff(ordinal=1, token="tok", predecessor="s1",
                               state="pending", opened_at="2026-01-01T00:00:00"),
            ],
        )
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")
        orphans = health.find_orphaned_handoffs([rec])
        assert len(orphans) == 1
        assert health.reactivate_orphaned_handoff(orphans[0]) is True
        fresh = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert fresh.resolved_head_session == "s1"
        assert fresh.handoffs[0].state == "cancelled"

    def test_reactivate_orphaned_handoff_bumps_revision_even_when_head_unchanged(
        self, tmp_tracking_dir, monkeypatch_config
    ):
        """aperture-labs#7824 review follow-through: `set_head_session` is a
        no-op when the ledger already names the orphan candidate (true here,
        since it IS the orphan candidate), so without an explicit revision
        bump a stale equal-revision writer could later silently undo this
        repair."""
        entry = SessionEntry(session_id="s1", started_at="2026-01-01T00:00:00",
                             state="yielded")
        rec = WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
            repo="test-repo", machine="test", platform="wsl",
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            lifecycle_revision=5,
            sessions=[entry],
            head_transitions=[
                HeadTransition(revision=5, session_id="s1", reason="initial",
                               at="2026-01-01T00:00:00"),
            ],
        )
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")
        orphans = health.find_orphaned_handoffs([rec])
        assert len(orphans) == 1
        assert health.reactivate_orphaned_handoff(orphans[0]) is True
        fresh = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert fresh.lifecycle_revision > 5

    def test_reactivate_orphaned_handoff_skips_when_superseded(
        self, tmp_tracking_dir, monkeypatch_config
    ):
        """`find_orphaned_handoffs` runs outside any lock, so the snapshot it
        returns can go stale before the repair acts on it -- e.g. a live
        session genuinely claims this handoff (linking a real successor) in
        the interim. The repair must re-validate under its own lock and
        no-op rather than clobber that live claim."""
        entry = SessionEntry(session_id="s1", started_at="2026-01-01T00:00:00",
                             state="yielded")
        rec = WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
            repo="test-repo", machine="test", platform="wsl",
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[entry],
        )
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")
        orphans = health.find_orphaned_handoffs([rec])
        assert len(orphans) == 1
        # A live session claims the worktree on disk after the scan.
        on_disk = load_record(tmp_tracking_dir / "wt-1.yaml")
        on_disk.session_entry("s1").state = "active"
        save_record(on_disk, tmp_tracking_dir / "wt-1.yaml")
        assert health.reactivate_orphaned_handoff(orphans[0]) is False
        assert orphans[0].reactivated is False
        fresh = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert fresh.session_entry("s1").state == "active"  # untouched, not clobbered

    def test_detects_yielded_tail_despite_an_older_active_session(self):
        """aperture-labs#7824 regression: an older session left
        "active" must not save a worktree from detection when the ledger's
        authoritative head is a LATER, now-yielded session -- the real
        `resolved_head_session` ledger replay (not a naive "any session is
        head-eligible" scan) is what must gate this, since the naive scan
        would wrongly treat the older active session as still current."""
        old = SessionEntry(session_id="old", started_at="2026-01-01T00:00:00",
                           state="active")
        new = SessionEntry(session_id="new", started_at="2026-01-01T00:01:00",
                           state="yielded")
        rec = WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
            repo="test-repo", machine="test", platform="wsl",
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[old, new],
            head_transitions=[
                HeadTransition(revision=1, session_id="new", reason="initial",
                               at="2026-01-01T00:01:00"),
            ],
        )
        assert rec.resolved_head_session is None  # the ledger says "new", ineligible
        orphans = health.find_orphaned_handoffs([rec])
        assert len(orphans) == 1 and orphans[0].session_id == "new"

    def test_detects_ledger_named_orphan_that_is_not_the_list_tail(self):
        """aperture-labs#7824 regression: `old` registers, `new`
        registers/concludes, `old` is explicitly re-adopted (the ledger's
        latest transition names it), then `old` yields. The list TAIL is
        still terminal `new`, but the ledger's own named, now-ineligible
        session is the earlier `old` entry -- that is the real orphan, and
        detection must not miss it just because it isn't last in the list."""
        old = SessionEntry(session_id="old", started_at="2026-01-01T00:00:00",
                           state="yielded")
        new = SessionEntry(session_id="new", started_at="2026-01-01T00:01:00",
                           state="concluded")
        rec = WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
            repo="test-repo", machine="test", platform="wsl",
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[old, new],
            head_transitions=[
                HeadTransition(revision=1, session_id="new", reason="initial",
                               at="2026-01-01T00:01:00"),
                HeadTransition(revision=2, session_id="old", reason="adopted",
                               at="2026-01-01T00:02:00"),
            ],
        )
        assert rec.resolved_head_session is None  # the ledger says "old", ineligible
        orphans = health.find_orphaned_handoffs([rec])
        assert len(orphans) == 1 and orphans[0].session_id == "old"


def test_finds_stale_head_cache_from_transition_replay():
    record = WorktreeRecord(
        worktree_id="wt-head",
        branch="worktree/wt-head",
        worktree_path="/tmp/wt-head",
        repo="repo",
        machine="machine",
        platform="wsl",
        started_at="t",
        last_resumed_at="t",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[SessionEntry("old", "t"), SessionEntry("new", "t")],
        head_session="old",
        lifecycle_revision=2,
        head_revision=1,
        head_transitions=[
            HeadTransition(1, "old", "initial", "t"),
            HeadTransition(2, "new", "adopted", "t"),
        ],
    )

    assert health.find_stale_head_caches([record]) == [record]
