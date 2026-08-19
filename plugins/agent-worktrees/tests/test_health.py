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
from agent_worktrees.tracking import SessionEntry, WorktreeRecord

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


def _oh_rec(**over):
    base = dict(
        worktree_id="wt", status="active",
        mux_live=None, bound_live=None,
        last_resumed_at=_STALE, started_at=None, session_state_at=None,
        mux_live_at=None, bound_live_at=None,
        sessions=[_session()],
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestOrphanedHandoffs:
    def test_detects_dark_stale_unlinked_handoff(self):
        # The 453f scenario: lone handed-off tail, no successor, active, dark, stale.
        found = health.find_orphaned_handoffs([_oh_rec()], now=_NOW)
        assert len(found) == 1
        assert found[0].worktree_id == "wt" and found[0].session_id == "s1"
        assert round(found[0].age_h) == 2

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

    def test_reactivation_makes_head_resolvable(self):
        # End-to-end semantic (mirrors the orchestrator's inline --fix): a real
        # record whose head is orphaned re-derives to the re-activated session.
        entry = SessionEntry(session_id="s1", started_at="2026-01-01T00:00:00",
                             state="handed-off")
        rec = WorktreeRecord(
            worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
            repo="test-repo", machine="test", platform="wsl",
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            sessions=[entry],
        )
        assert rec.resolved_head_session is None  # orphaned
        orphans = health.find_orphaned_handoffs([rec])
        assert len(orphans) == 1 and orphans[0].session_id == "s1"
        # Apply the orchestrator's mutate.
        o = orphans[0]
        e = o.record.session_entry(o.session_id)
        e.state = "active"
        o.record.head_session = o.session_id
        assert rec.resolved_head_session == "s1"  # resumable again
