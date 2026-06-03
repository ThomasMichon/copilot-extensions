"""Tests for agent_worktrees.sessions — session scanning and fast-path."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_worktrees.sessions import (
    SessionContext,
    _normalize_path,
    find_latest_session_id,
    find_latest_session_id_fast,
    scan_sessions,
    scan_sessions_fast,
)
from agent_worktrees.tracking import (
    SessionEntry,
    WorktreeRecord,
    save_record,
)

from conftest import make_session_dir


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_strips_trailing_slash(self):
        assert _normalize_path("/home/user/src/") == "/home/user/src"

    def test_strips_trailing_backslash(self):
        assert _normalize_path("C:\\Users\\test\\") == "C:\\Users\\test"

    def test_no_trailing_sep(self):
        assert _normalize_path("/home/user/src") == "/home/user/src"


# ---------------------------------------------------------------------------
# scan_sessions (legacy full scan)
# ---------------------------------------------------------------------------

class TestScanSessions:
    """Test the full-scan scan_sessions."""

    def test_empty_session_dir(self, tmp_session_state_dir: Path):
        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions(["/tmp/wt"])
        assert ctx.active_sessions == {}
        assert ctx.session_count == {}

    def test_no_worktree_paths(self, tmp_session_state_dir: Path):
        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions([])
        assert ctx.session_count == {}

    def test_matches_sessions_by_cwd(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/test-worktree"
        make_session_dir(
            tmp_session_state_dir, "sess-001", wt_path,
            summary="First session",
        )
        make_session_dir(
            tmp_session_state_dir, "sess-002", wt_path,
            summary="Second session",
            updated_at="2026-06-01T12:00:00.000Z",
        )
        # Unrelated session
        make_session_dir(
            tmp_session_state_dir, "sess-other", "/tmp/other-worktree",
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert ctx.session_count[norm] == 2
        assert "Second session" in ctx.latest_summary[norm]

    def test_counts_user_turns(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-turns"
        make_session_dir(
            tmp_session_state_dir, "sess-turns", wt_path,
            events_lines=[
                '{"type":"user.message","content":"hello"}',
                '{"type":"assistant.message","content":"hi"}',
                '{"type":"user.message","content":"do something"}',
            ],
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert ctx.turn_count[norm] == 2  # two user.message lines

    def test_detects_live_sessions(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-live"
        make_session_dir(
            tmp_session_state_dir, "sess-live", wt_path,
            lock_pid=os.getpid(),  # current process
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            # Mock _is_copilot_process to return True for our PID
            with patch("agent_worktrees.sessions._is_copilot_process", return_value=True):
                ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert norm in ctx.active_sessions
        assert "sess-live" in ctx.active_sessions[norm]

    def test_ignores_dead_lock_files(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-dead"
        make_session_dir(
            tmp_session_state_dir, "sess-dead", wt_path,
            lock_pid=99999999,  # unlikely to be running
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            with patch("agent_worktrees.sessions._is_copilot_process", return_value=False):
                ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert norm not in ctx.active_sessions

    def test_subdirectory_matching(self, tmp_session_state_dir: Path):
        """Session cwd inside a worktree root should match."""
        wt_path = "/tmp/wt-parent"
        make_session_dir(
            tmp_session_state_dir, "sess-sub", wt_path + "/subdir",
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert ctx.session_count[norm] == 1

    def test_skips_bad_workspace_yaml(self, tmp_session_state_dir: Path):
        """Malformed workspace.yaml should be skipped gracefully."""
        sdir = tmp_session_state_dir / "bad-sess"
        sdir.mkdir()
        (sdir / "workspace.yaml").write_text("not: [valid: yaml: {{{")

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions(["/tmp/wt"])

        assert ctx.session_count == {}


# ---------------------------------------------------------------------------
# scan_sessions_fast
# ---------------------------------------------------------------------------

class TestScanSessionsFast:
    """Test registry-accelerated scanning."""

    def _make_record(self, wt_id: str, wt_path: str, sessions=None) -> WorktreeRecord:
        return WorktreeRecord(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=wt_path,
            repo="test",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            handoff_prompt=None,
            sessions=sessions,
        )

    def test_fast_path_reads_known_sessions(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-fast"
        make_session_dir(
            tmp_session_state_dir, "known-sess", wt_path,
            summary="Fast session",
            events_lines=['{"type":"user.message","content":"hi"}'],
        )

        rec = self._make_record("fast-wt", wt_path, sessions=[
            SessionEntry("known-sess", "2026-06-01T10:00:00"),
        ])

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])

        norm = _normalize_path(wt_path)
        assert ctx.session_count[norm] == 1
        assert ctx.turn_count[norm] == 1
        assert "Fast session" in ctx.latest_summary[norm]

    def test_fast_path_skips_missing_session_dirs(self, tmp_session_state_dir: Path):
        """Session ID in registry but dir doesn't exist — skip gracefully."""
        rec = self._make_record("orphan-wt", "/tmp/orphan", sessions=[
            SessionEntry("nonexistent-sess", "2026-06-01T10:00:00"),
        ])

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])

        assert ctx.session_count == {}

    def test_fallback_for_unindexed_records(self, tmp_session_state_dir: Path):
        """Records with sessions=None should fall back to full scan."""
        wt_path = "/tmp/wt-unindexed"
        make_session_dir(
            tmp_session_state_dir, "legacy-sess", wt_path,
            summary="Legacy session",
        )

        rec = self._make_record("unindexed-wt", wt_path, sessions=None)

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])

        norm = _normalize_path(wt_path)
        assert ctx.session_count[norm] == 1
        assert "Legacy session" in ctx.latest_summary[norm]

    def test_mixed_indexed_and_unindexed(self, tmp_session_state_dir: Path):
        """Mix of indexed and unindexed records should merge results."""
        wt_fast = "/tmp/wt-fast-mix"
        wt_legacy = "/tmp/wt-legacy-mix"

        make_session_dir(tmp_session_state_dir, "fast-sess", wt_fast, summary="Fast")
        make_session_dir(tmp_session_state_dir, "legacy-sess", wt_legacy, summary="Legacy")

        records = [
            self._make_record("fast-wt", wt_fast, sessions=[
                SessionEntry("fast-sess", "2026-06-01T10:00:00"),
            ]),
            self._make_record("legacy-wt", wt_legacy, sessions=None),
        ]

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast(records)

        assert ctx.session_count[_normalize_path(wt_fast)] == 1
        assert ctx.session_count[_normalize_path(wt_legacy)] == 1

    def test_empty_sessions_list(self, tmp_session_state_dir: Path):
        """sessions=[] (indexed, none registered) should produce empty context."""
        rec = self._make_record("empty-wt", "/tmp/empty", sessions=[])

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            ctx = scan_sessions_fast([rec])

        assert ctx.session_count == {}


# ---------------------------------------------------------------------------
# find_latest_session_id
# ---------------------------------------------------------------------------

class TestFindLatestSessionId:
    """Test legacy full-scan latest session finder."""

    def test_finds_most_recent(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-latest"
        make_session_dir(
            tmp_session_state_dir, "old-sess", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
        )
        make_session_dir(
            tmp_session_state_dir, "new-sess", wt_path,
            updated_at="2026-06-01T12:00:00.000Z",
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            result = find_latest_session_id(wt_path)

        assert result == "new-sess"

    def test_skips_stale_stubs(self, tmp_session_state_dir: Path):
        """Sessions with only workspace.yaml (no events/db) should be skipped."""
        wt_path = "/tmp/wt-stubs"
        make_session_dir(
            tmp_session_state_dir, "stub-sess", wt_path,
            has_events_file=False,
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            result = find_latest_session_id(wt_path)

        assert result is None

    def test_no_matching_sessions(self, tmp_session_state_dir: Path):
        make_session_dir(
            tmp_session_state_dir, "other-sess", "/tmp/other",
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            result = find_latest_session_id("/tmp/wt-none")

        assert result is None


# ---------------------------------------------------------------------------
# find_latest_session_id_fast
# ---------------------------------------------------------------------------

class TestFindLatestSessionIdFast:
    """Test registry-accelerated latest session finder."""

    def test_fast_finds_most_recent(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-fast-latest"
        make_session_dir(
            tmp_session_state_dir, "old-sess", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
        )
        make_session_dir(
            tmp_session_state_dir, "new-sess", wt_path,
            updated_at="2026-06-01T12:00:00.000Z",
        )

        sessions = [
            SessionEntry("old-sess", "2026-06-01T10:00:00"),
            SessionEntry("new-sess", "2026-06-01T12:00:00"),
        ]

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            result = find_latest_session_id_fast(wt_path, sessions)

        assert result == "new-sess"

    def test_fast_skips_stale_stubs(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-fast-stubs"
        make_session_dir(
            tmp_session_state_dir, "stub-sess", wt_path,
            has_events_file=False,
        )

        sessions = [SessionEntry("stub-sess", "2026-06-01T10:00:00")]

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            result = find_latest_session_id_fast(wt_path, sessions)

        assert result is None

    def test_fast_falls_back_for_none(self, tmp_session_state_dir: Path):
        """sessions=None should delegate to full scan."""
        wt_path = "/tmp/wt-fallback"
        make_session_dir(
            tmp_session_state_dir, "fallback-sess", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
        )

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            result = find_latest_session_id_fast(wt_path, None)

        assert result == "fallback-sess"

    def test_fast_empty_sessions(self, tmp_session_state_dir: Path):
        result = find_latest_session_id_fast("/tmp/wt", [])
        assert result is None

    def test_fast_skips_missing_dirs(self, tmp_session_state_dir: Path):
        sessions = [SessionEntry("gone-sess", "2026-06-01T10:00:00")]

        with patch("agent_worktrees.sessions._session_state_dir", return_value=tmp_session_state_dir):
            result = find_latest_session_id_fast("/tmp/wt", sessions)

        assert result is None
