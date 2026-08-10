"""Tests for ``tracking.seal_worktree_identity`` -- the deterministic identity
seal ``finalize`` runs as a backstop for the best-effort session hooks.

A worktree whose ``register-session`` / ``deregister-session`` hooks were
bypassed (a dispatched or crashed session, a bare-resume cwd) can reach finalize
with an empty ``sessions`` registry *and* a ``null`` title, so the Picker renders
it "(untitled)" with no session linkage. The seal fills both gaps from
session-state before the worktree is pruned. It is gap-filling (never overwrites
an asserted title or an existing registry) and mutates the record in place so a
later ``update_status(record, "finalized")`` preserves it.
"""

from __future__ import annotations

from pathlib import Path

from conftest import make_session_dir

from agent_worktrees import sessions, tracking


def _wire_dirs(monkeypatch, tracking_dir: Path, state_dir: Path) -> None:
    monkeypatch.setattr(tracking.cfg, "tracking_dir", lambda: tracking_dir)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state_dir)


def _save(rec: tracking.WorktreeRecord, tracking_dir: Path) -> None:
    tracking.save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")


def _record(wt_id: str, wt_path: str, *, title=None, sessions=None, status="active"):
    return tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=wt_path,
        repo="test",
        machine="test",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=title,
        status=status,
        completed_at=None,
        sessions=sessions,
    )


def test_seals_title_and_registry_for_bare_record(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """The observed bug: empty registry + null title -> both filled from state."""
    wt_path = "/w/bare"
    make_session_dir(
        tmp_session_state_dir, "sess-1", wt_path, summary="Analyze CPU Alert",
    )
    rec = _record("wt-bare", wt_path, title=None, sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    result = tracking.seal_worktree_identity(rec)

    assert result == {"sessions": 1, "titled": True}
    after = tracking.load_record(tmp_tracking_dir / "wt-bare.yaml")
    assert after.title == "Analyze CPU Alert"
    assert [s.session_id for s in (after.sessions or [])] == ["sess-1"]


def test_mutates_record_in_place_so_later_save_preserves_seal(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """The seal must mutate the passed object, so finalize's later
    ``update_status(record, ...)`` save keeps the sealed title/sessions."""
    wt_path = "/w/inplace"
    make_session_dir(tmp_session_state_dir, "s-inplace", wt_path, summary="Real Work")
    rec = _record("wt-inplace", wt_path, title=None, sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    tracking.seal_worktree_identity(rec)
    # The in-memory object carries the seal...
    assert rec.title == "Real Work"
    assert [s.session_id for s in (rec.sessions or [])] == ["s-inplace"]

    # ...so a subsequent status save (stale-object path finalize actually uses)
    # does NOT clobber it.
    tracking.update_status(rec, "finalized")
    after = tracking.load_record(tmp_tracking_dir / "wt-inplace.yaml")
    assert after.status == "finalized"
    assert after.title == "Real Work"
    assert [s.session_id for s in (after.sessions or [])] == ["s-inplace"]


def test_preserves_asserted_title_fills_only_registry(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    wt_path = "/w/curated"
    make_session_dir(tmp_session_state_dir, "s-cur", wt_path, summary="Live Summary")
    rec = _record("wt-cur", wt_path, title="Curated Title", sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    result = tracking.seal_worktree_identity(rec)

    assert result == {"sessions": 1, "titled": False}
    after = tracking.load_record(tmp_tracking_dir / "wt-cur.yaml")
    assert after.title == "Curated Title"
    assert [s.session_id for s in (after.sessions or [])] == ["s-cur"]


def test_preserves_existing_registry_fills_only_title(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    wt_path = "/w/hasreg"
    make_session_dir(tmp_session_state_dir, "kept", wt_path, summary="Derived Title")
    rec = _record(
        "wt-hasreg", wt_path, title=None,
        sessions=[tracking.SessionEntry("kept", "2026-06-01T10:00:00")],
    )
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    result = tracking.seal_worktree_identity(rec)

    assert result == {"sessions": 0, "titled": True}
    after = tracking.load_record(tmp_tracking_dir / "wt-hasreg.yaml")
    assert after.title == "Derived Title"
    assert [s.session_id for s in (after.sessions or [])] == ["kept"]


def test_idempotent_second_call_noops(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    wt_path = "/w/idem"
    make_session_dir(tmp_session_state_dir, "s-idem", wt_path, summary="Once")
    rec = _record("wt-idem", wt_path, title=None, sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    assert tracking.seal_worktree_identity(rec) == {"sessions": 1, "titled": True}
    # Fully sealed now -> nothing left to do.
    assert tracking.seal_worktree_identity(rec) == {"sessions": 0, "titled": False}


def test_no_session_state_leaves_record_untitled(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """No session-state to draw from -> the seal is a silent no-op, not a crash."""
    wt_path = "/w/empty"
    rec = _record("wt-empty", wt_path, title=None, sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    assert tracking.seal_worktree_identity(rec) == {"sessions": 0, "titled": False}
    after = tracking.load_record(tmp_tracking_dir / "wt-empty.yaml")
    assert not (after.title and after.title != "null")


def test_detached_session_supplies_neither_title_nor_registry(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """A detached subconscious session must not seal a worktree's identity."""
    wt_path = "/w/det"
    sdir = make_session_dir(
        tmp_session_state_dir, "det-sess", wt_path,
        summary="Apply context_board add/prune updates for this session",
    )
    (sdir / ".detached").write_text("")
    rec = _record("wt-det", wt_path, title=None, sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    assert tracking.seal_worktree_identity(rec) == {"sessions": 0, "titled": False}
    after = tracking.load_record(tmp_tracking_dir / "wt-det.yaml")
    assert not (after.title and after.title != "null")
    assert not after.sessions


def test_none_record_is_safe_noop(monkeypatch, tmp_tracking_dir, tmp_session_state_dir):
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)
    assert tracking.seal_worktree_identity(None) == {"sessions": 0, "titled": False}
