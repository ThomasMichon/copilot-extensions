"""Tests for agent_logger.sync.change_tracker."""

from __future__ import annotations

import time
from pathlib import Path

from agent_logger.sync.change_tracker import (
    ChangeTracker,
    chunked,
    compute_signature,
    resolve_db_path,
    resolve_settings,
)


def _make_session(root: Path, session_id: str, content: str = "hello") -> Path:
    sess = root / "session-state" / session_id
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text(content, encoding="utf-8")
    return sess


def test_compute_signature_stable_for_unchanged_directory(tmp_path: Path) -> None:
    sess = _make_session(tmp_path, "abc-123")
    assert compute_signature(sess) == compute_signature(sess)


def test_compute_signature_changes_on_content_change(tmp_path: Path) -> None:
    sess = _make_session(tmp_path, "abc-123")
    before = compute_signature(sess)
    (sess / "events.jsonl").write_text("hello world, more content", encoding="utf-8")
    after = compute_signature(sess)
    assert before != after


def test_compute_signature_changes_on_new_file(tmp_path: Path) -> None:
    sess = _make_session(tmp_path, "abc-123")
    before = compute_signature(sess)
    (sess / "new-file.txt").write_text("new", encoding="utf-8")
    after = compute_signature(sess)
    assert before != after


def test_compute_signature_uses_relative_paths_not_absolute(tmp_path: Path) -> None:
    """The signature is keyed by each file's *relative* path within the
    session dir, so moving the same session tree elsewhere on disk (same
    content, same mtimes) still yields an identical signature."""
    import shutil

    one = _make_session(tmp_path, "abc-123")
    moved_root = tmp_path / "moved"
    moved_root.mkdir()
    two = moved_root / "abc-123"
    shutil.copytree(one, two)
    # Preserve exact mtimes so only the absolute path differs.
    for src_path in one.rglob("*"):
        if src_path.is_file():
            dst_path = two / src_path.relative_to(one)
            stat_result = src_path.stat()
            import os

            os.utime(dst_path, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns))
    assert compute_signature(one) == compute_signature(two)


def test_chunked_splits_into_bounded_batches() -> None:
    batches = list(chunked([str(i) for i in range(10)], 3))
    assert [len(b) for b in batches] == [3, 3, 3, 1]
    flattened = [item for batch in batches for item in batch]
    assert flattened == [str(i) for i in range(10)]


def test_chunked_empty_input_yields_nothing() -> None:
    assert list(chunked([], 5)) == []


def test_resolve_db_path_uses_configured_value(tmp_path: Path) -> None:
    configured = str(tmp_path / "custom.db")
    assert resolve_db_path(configured, tmp_path / "home") == Path(configured)


def test_resolve_db_path_defaults_under_home(tmp_path: Path) -> None:
    assert resolve_db_path(None, tmp_path) == tmp_path / "sync-state.db"


def test_resolve_settings_defaults() -> None:
    settings = resolve_settings({})
    assert settings == {
        "enabled": True,
        "full_sync_interval_hours": 24.0,
        "batch_size": 100,
        "db_path": None,
    }


def test_resolve_settings_honors_overrides() -> None:
    settings = resolve_settings(
        {"enabled": False, "full_sync_interval_hours": 6, "batch_size": 50, "db_path": "/x"}
    )
    assert settings == {
        "enabled": False,
        "full_sync_interval_hours": 6.0,
        "batch_size": 50,
        "db_path": "/x",
    }


def test_changed_sessions_reports_new_session_without_stored_signature(
    tmp_path: Path,
) -> None:
    source = tmp_path / "copilot"
    _make_session(source, "abc-123")
    tracker = ChangeTracker(tmp_path / "state.db")
    assert tracker.changed_sessions(source) == {"abc-123"}


def test_changed_sessions_empty_after_record(tmp_path: Path) -> None:
    source = tmp_path / "copilot"
    _make_session(source, "abc-123")
    tracker = ChangeTracker(tmp_path / "state.db")
    tracker.record(source, {"abc-123"})
    assert tracker.changed_sessions(source) == set()


def test_changed_sessions_reports_modified_session(tmp_path: Path) -> None:
    source = tmp_path / "copilot"
    sess = _make_session(source, "abc-123")
    tracker = ChangeTracker(tmp_path / "state.db")
    tracker.record(source, {"abc-123"})
    (sess / "events.jsonl").write_text("changed content here", encoding="utf-8")
    assert tracker.changed_sessions(source) == {"abc-123"}


def test_changed_sessions_only_touches_other_sessions_when_scoped(tmp_path: Path) -> None:
    source = tmp_path / "copilot"
    _make_session(source, "abc-123")
    _make_session(source, "def-456")
    tracker = ChangeTracker(tmp_path / "state.db")
    tracker.record(source, {"abc-123", "def-456"})
    assert tracker.changed_sessions(source) == set()
    # Scoping to a subset never reports an untouched session outside it.
    assert tracker.changed_sessions(source, {"abc-123"}) == set()


def test_changed_sessions_missing_source_dir_is_empty(tmp_path: Path) -> None:
    tracker = ChangeTracker(tmp_path / "state.db")
    assert tracker.changed_sessions(tmp_path / "nonexistent") == set()


def test_known_session_ids_and_vanished_sessions(tmp_path: Path) -> None:
    source = tmp_path / "copilot"
    _make_session(source, "abc-123")
    _make_session(source, "def-456")
    tracker = ChangeTracker(tmp_path / "state.db")
    tracker.record(source, {"abc-123", "def-456"})
    assert tracker.known_session_ids() == {"abc-123", "def-456"}

    # Locally removed since the last sync -- vanished, but not yet forgotten.
    import shutil

    shutil.rmtree(source / "session-state" / "def-456")
    assert tracker.vanished_sessions(source) == {"def-456"}
    assert tracker.known_session_ids() == {"abc-123", "def-456"}

    tracker.forget({"def-456"})
    assert tracker.known_session_ids() == {"abc-123"}
    assert tracker.vanished_sessions(source) == set()


def test_vanished_sessions_empty_when_nothing_known(tmp_path: Path) -> None:
    tracker = ChangeTracker(tmp_path / "state.db")
    assert tracker.vanished_sessions(tmp_path / "copilot") == set()


def test_should_full_sync_true_for_fresh_db(tmp_path: Path) -> None:
    tracker = ChangeTracker(tmp_path / "state.db")
    assert tracker.should_full_sync(24) is True


def test_should_full_sync_false_right_after_marking(tmp_path: Path) -> None:
    tracker = ChangeTracker(tmp_path / "state.db")
    tracker.mark_full_sync()
    assert tracker.should_full_sync(24) is False


def test_should_full_sync_true_once_interval_elapses(tmp_path: Path, monkeypatch) -> None:
    from agent_logger.sync import change_tracker as mod

    tracker = ChangeTracker(tmp_path / "state.db")
    now = time.time()
    monkeypatch.setattr(mod.time, "time", lambda: now)
    tracker.mark_full_sync()
    assert tracker.should_full_sync(1) is False
    monkeypatch.setattr(mod.time, "time", lambda: now + 3601)
    assert tracker.should_full_sync(1) is True


def test_should_full_sync_zero_interval_disables_cadence(tmp_path: Path) -> None:
    tracker = ChangeTracker(tmp_path / "state.db")
    tracker.mark_full_sync()
    assert tracker.should_full_sync(0) is False


def test_reset_clears_signatures_and_full_sync_marker(tmp_path: Path) -> None:
    source = tmp_path / "copilot"
    _make_session(source, "abc-123")
    tracker = ChangeTracker(tmp_path / "state.db")
    tracker.record(source, {"abc-123"})
    tracker.mark_full_sync()
    assert tracker.known_session_ids() == {"abc-123"}
    assert tracker.should_full_sync(24) is False

    tracker.reset()
    assert tracker.known_session_ids() == set()
    assert tracker.should_full_sync(24) is True
