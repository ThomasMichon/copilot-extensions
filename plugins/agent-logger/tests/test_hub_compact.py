"""WS3: hub archive sync, reconciliation, and backlog compaction."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_logger.sessions import archive_session, is_archived
from agent_logger.sync.targets.filesystem import LocalTarget

NOW = datetime.now(timezone.utc)


def _target(root: Path) -> LocalTarget:
    return LocalTarget({"path": str(root)})


def _hub_session(hub: Path, machine: str, sid: str, *, updated: datetime) -> Path:
    d = hub / machine / "session-state" / sid
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text('{"type":"session.start"}\n', encoding="utf-8")
    (d / "workspace.yaml").write_text(
        f"id: {sid}\ncwd: C:/repo\nupdated_at: {updated.isoformat()}\n",
        encoding="utf-8",
    )
    (d / "origin.json").write_text(json.dumps({"machine": machine}), encoding="utf-8")
    return d


# --- push_archives (Pair B) ----------------------------------------------

def test_push_archives_copies_store_to_hub(tmp_path: Path) -> None:
    # local archive store with one archived session
    src = tmp_path / "live" / "s1"
    src.mkdir(parents=True)
    (src / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (src / "workspace.yaml").write_text("id: s1\n", encoding="utf-8")
    store = tmp_path / "archived-sessions"
    archive_session(src, store)

    hub = tmp_path / "hub"
    result = _target(hub).push_archives(store, "box")
    assert result.ok
    assert result.file_count >= 2  # tar.gz + sidecar
    assert (hub / "box" / "archived" / "s1.tar.gz").is_file()
    assert (hub / "box" / "archived" / "s1.workspace.yaml").is_file()


def test_push_archives_no_store_is_ok_noop(tmp_path: Path) -> None:
    result = _target(tmp_path / "hub").push_archives(tmp_path / "missing", "box")
    assert result.ok
    assert result.file_count == 0


# --- reconcile_hub --------------------------------------------------------

def test_reconcile_removes_uncompressed_when_archive_present(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    live = _hub_session(hub, "box", "s1", updated=NOW - timedelta(days=40))
    # produce a matching verified archive on the hub
    archive_session(live, hub / "box" / "archived")
    assert live.is_dir()

    removed = _target(hub).reconcile_hub("box")
    assert removed == 1
    assert not live.exists()


def test_reconcile_dry_run_keeps_dir(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    live = _hub_session(hub, "box", "s1", updated=NOW - timedelta(days=40))
    archive_session(live, hub / "box" / "archived")
    removed = _target(hub).reconcile_hub("box", dry_run=True)
    assert removed == 1
    assert live.is_dir()


def test_reconcile_keeps_dir_without_archive(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    live = _hub_session(hub, "box", "s1", updated=NOW - timedelta(days=40))
    removed = _target(hub).reconcile_hub("box")
    assert removed == 0
    assert live.is_dir()


# --- compact_backlog ------------------------------------------------------

def test_compact_backlog_archives_old_only(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    _hub_session(hub, "box", "old", updated=NOW - timedelta(days=40))
    _hub_session(hub, "box", "recent", updated=NOW - timedelta(days=5))

    n = _target(hub).compact_backlog("box", 30, "targz")
    assert n == 1
    assert is_archived("old", hub / "box" / "archived")
    assert not is_archived("recent", hub / "box" / "archived")


def test_compact_backlog_dry_run_writes_nothing(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    _hub_session(hub, "box", "old", updated=NOW - timedelta(days=40))
    n = _target(hub).compact_backlog("box", 30, "targz", dry_run=True)
    assert n == 1
    assert not is_archived("old", hub / "box" / "archived")


def test_compact_backlog_skips_already_archived(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    old = _hub_session(hub, "box", "old", updated=NOW - timedelta(days=40))
    archive_session(old, hub / "box" / "archived")
    n = _target(hub).compact_backlog("box", 30, "targz")
    assert n == 0


def test_compact_backlog_protects_tracked_worktree(tmp_path: Path) -> None:
    import os

    hub = tmp_path / "hub"
    # an old hub session whose worktree is still tracked must not be archived
    d = hub / "box" / "session-state" / "old"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text('{"type":"session.start"}\n', encoding="utf-8")
    (d / "workspace.yaml").write_text(
        f"id: old\ncwd: C:/repo/live-wt\nupdated_at: "
        f"{(NOW - timedelta(days=40)).isoformat()}\n",
        encoding="utf-8",
    )
    tracked = {os.path.normcase(os.path.normpath("C:/repo/live-wt"))}
    n = _target(hub).compact_backlog("box", 30, "targz", tracked_paths=tracked)
    assert n == 0
    assert not is_archived("old", hub / "box" / "archived")


def test_backlog_then_reconcile_end_to_end(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    old = _hub_session(hub, "box", "old", updated=NOW - timedelta(days=40))
    t = _target(hub)
    assert t.compact_backlog("box", 30, "targz") == 1
    assert t.reconcile_hub("box") == 1
    assert not old.exists()
    assert (hub / "box" / "archived" / "old.tar.gz").is_file()
