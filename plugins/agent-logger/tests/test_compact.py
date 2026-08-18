"""Tests for on-device cold-session compaction (sync/compact.py)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_logger.config import Config
from agent_logger.sync import compact as compact_mod
from agent_logger.sync.compact import (
    CompactResult,
    run_compact,
    select_compactable,
    session_age_days,
)

NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)


def _cfg(tmp_path: Path, **compact_opts) -> Config:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    data = {
        "sync": {
            "source": str(tmp_path / "copilot"),
            "compact": {"enabled": True, **compact_opts},
        }
    }
    return Config(data, home)


def _session(
    src: Path, sid: str, *, updated: datetime, cwd: str = "C:/repo/wt"
) -> Path:
    d = src / "session-state" / sid
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text('{"type":"session.start"}\n' * 50, encoding="utf-8")
    (d / "workspace.yaml").write_text(
        f"id: {sid}\ncwd: {cwd}\nupdated_at: {updated.isoformat()}\n",
        encoding="utf-8",
    )
    (d / "origin.json").write_text(json.dumps({"machine": "box"}), encoding="utf-8")
    return d


# --- age ------------------------------------------------------------------

def test_session_age_days_prefers_updated_at() -> None:
    ws = {"updated_at": "2026-08-07T00:00:00Z", "created_at": "2020-01-01T00:00:00Z"}
    age = session_age_days(None, ws, NOW)  # ref unused for timestamp path
    assert age is not None and abs(age - 10) < 0.01


def test_session_age_days_none_without_timestamp() -> None:
    assert session_age_days(None, {}, NOW) is None


# --- selection ------------------------------------------------------------

def test_selects_old_inactive_skips_recent(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "copilot"
    _session(src, "old1", updated=NOW - timedelta(days=40), cwd="C:/repo/gone")
    _session(src, "recent1", updated=NOW - timedelta(days=5), cwd="C:/repo/gone")
    # agent-worktrees unavailable -> fall back to on-disk existence; cwd missing
    monkeypatch.setattr(compact_mod, "tracked_worktree_paths", lambda: None)

    selected, result = select_compactable(_cfg(tmp_path), now=NOW)
    ids = {r.id for r in selected}
    assert ids == {"old1"}
    assert result.skipped_recent == 1


def test_skips_tracked_worktree(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "copilot"
    tracked_dir = tmp_path / "live-wt"
    tracked_dir.mkdir()
    _session(src, "old_tracked", updated=NOW - timedelta(days=40), cwd=str(tracked_dir))
    _session(src, "old_gone", updated=NOW - timedelta(days=40), cwd="C:/repo/gone")

    import os

    tracked = {os.path.normcase(os.path.normpath(str(tracked_dir)))}
    monkeypatch.setattr(compact_mod, "tracked_worktree_paths", lambda: tracked)

    selected, result = select_compactable(_cfg(tmp_path), now=NOW)
    assert {r.id for r in selected} == {"old_gone"}
    assert result.skipped_tracked == 1


def test_unclassified_when_no_cwd_and_require_inactive(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "copilot"
    d = src / "session-state" / "no_cwd"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (d / "workspace.yaml").write_text(
        f"id: no_cwd\nupdated_at: {(NOW - timedelta(days=40)).isoformat()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(compact_mod, "tracked_worktree_paths", lambda: set())

    selected, result = select_compactable(_cfg(tmp_path), now=NOW)
    assert selected == []
    assert result.skipped_unclassified == 1


# --- full run -------------------------------------------------------------

def test_run_compact_archives_and_reclaims(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.sessions import SessionRef

    src = tmp_path / "copilot"
    live = _session(src, "old1", updated=NOW - timedelta(days=40), cwd="C:/repo/gone")
    monkeypatch.setattr(compact_mod, "tracked_worktree_paths", lambda: None)
    monkeypatch.setattr(
        compact_mod,
        "select_compactable",
        lambda cfg, now=None: (
            [SessionRef(id="old1", kind="live", path=live)],
            CompactResult(scanned=1),
        ),
    )

    cfg = _cfg(tmp_path)
    result = run_compact(cfg, verbose=True)

    assert result.compacted == 1
    assert result.reclaimed_bytes > 0
    # live dir reclaimed
    assert not live.exists()
    # archive + sidecars in the store
    store = cfg.compact_archive_root
    assert (store / "old1.tar.gz").is_file()
    assert (store / "old1.workspace.yaml").is_file()


def test_run_compact_disabled_is_noop(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = Config({"sync": {"source": str(tmp_path / "copilot"),
                           "compact": {"enabled": False}}}, home)
    result = run_compact(cfg, verbose=True)
    assert result.compacted == 0


def test_dry_run_does_not_reclaim(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "copilot"
    live = _session(src, "old1", updated=NOW - timedelta(days=40), cwd="C:/repo/gone")
    monkeypatch.setattr(compact_mod, "tracked_worktree_paths", lambda: None)
    result = run_compact(_cfg(tmp_path), dry_run=True)
    assert result.scanned == 1
    assert live.exists()  # untouched


# --- config ---------------------------------------------------------------

def test_archive_root_defaults_under_home(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert cfg.compact_archive_root == cfg.home / "archived-sessions"


def test_archive_root_override(tmp_path: Path) -> None:
    custom = tmp_path / "custom-archive"
    cfg = _cfg(tmp_path, archive_root=str(custom))
    assert cfg.compact_archive_root == custom
