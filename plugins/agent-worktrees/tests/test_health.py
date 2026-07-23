"""Tests for agent_worktrees.health -- the doctor engine."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from agent_worktrees import health

# --------------------------------------------------------------------------- #
# YAML integrity
# --------------------------------------------------------------------------- #
_CORRUPT = (
    "worktree_id: wt-1\n"
    "branch: worktree/wt-1\n"
    "title: session-sync: native SSH transport via Wheatley\n"
    "status: complete\n"
)
_CLEAN = "worktree_id: wt-2\nbranch: worktree/wt-2\ntitle: 'plain title'\nstatus: complete\n"


class TestYamlIntegrity:
    def test_repair_yaml_text_quotes_unquoted_colon_title(self):
        fixed = health.repair_yaml_text(_CORRUPT)
        assert fixed is not None
        data = yaml.safe_load(fixed)
        assert data["title"] == "session-sync: native SSH transport via Wheatley"

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
