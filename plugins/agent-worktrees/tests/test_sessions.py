"""Tests for agent_worktrees.sessions — session scanning and fast-path."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from conftest import make_session_dir

from agent_worktrees.sessions import (
    _normalize_path,
    backfill_sessions,
    find_latest_session_id,
    find_latest_session_id_fast,
    scan_sessions,
    scan_sessions_fast,
    validate_session_id,
)
from agent_worktrees.tracking import (
    SessionEntry,
    WorktreeRecord,
)

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
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions(["/tmp/wt"])
        assert ctx.active_sessions == {}
        assert ctx.session_count == {}

    def test_no_worktree_paths(self, tmp_session_state_dir: Path):
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert ctx.turn_count[norm] == 2  # two user.message lines

    def test_detects_live_sessions(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-live"
        make_session_dir(
            tmp_session_state_dir, "sess-live", wt_path,
            lock_pid=os.getpid(),  # current process
        )

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert ctx.session_count[norm] == 1

    def test_skips_bad_workspace_yaml(self, tmp_session_state_dir: Path):
        """Malformed workspace.yaml should be skipped gracefully."""
        sdir = tmp_session_state_dir / "bad-sess"
        sdir.mkdir()
        (sdir / "workspace.yaml").write_text("not: [valid: yaml: {{{")

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast(records)

        assert ctx.session_count[_normalize_path(wt_fast)] == 1
        assert ctx.session_count[_normalize_path(wt_legacy)] == 1

    def test_empty_sessions_list(self, tmp_session_state_dir: Path):
        """sessions=[] with nothing on disk -> empty context (via fallback)."""
        rec = self._make_record("empty-wt", "/tmp/empty", sessions=[])

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])

        assert ctx.session_count == {}

    def test_empty_sessions_falls_back_to_full_scan(
        self, tmp_session_state_dir: Path,
    ):
        """sessions=[] (registry active but hook never recorded the session)
        must fall back to a full cwd-based scan so the worktree still gets its
        summary + turn count -- otherwise the status bar loses its title and
        reads a bare UNUSED state.  Mirrors find_latest_session_id_fast."""
        wt_path = "/tmp/wt-empty-recovered"
        make_session_dir(
            tmp_session_state_dir, "unregistered-sess", wt_path,
            summary="Recovered session",
        )
        rec = self._make_record("empty-recovered-wt", wt_path, sessions=[])

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])

        norm = _normalize_path(wt_path)
        assert ctx.session_count[norm] == 1
        assert "Recovered session" in ctx.latest_summary[norm]


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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            result = find_latest_session_id(wt_path)

        assert result == "new-sess"

    def test_skips_stale_stubs(self, tmp_session_state_dir: Path):
        """Sessions with only workspace.yaml (no events/db) should be skipped."""
        wt_path = "/tmp/wt-stubs"
        make_session_dir(
            tmp_session_state_dir, "stub-sess", wt_path,
            has_events_file=False,
        )

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            result = find_latest_session_id(wt_path)

        assert result is None

    def test_no_matching_sessions(self, tmp_session_state_dir: Path):
        make_session_dir(
            tmp_session_state_dir, "other-sess", "/tmp/other",
        )

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
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

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            result = find_latest_session_id_fast(wt_path, sessions)

        assert result == "new-sess"

    def test_fast_skips_stale_stubs(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-fast-stubs"
        make_session_dir(
            tmp_session_state_dir, "stub-sess", wt_path,
            has_events_file=False,
        )

        sessions = [SessionEntry("stub-sess", "2026-06-01T10:00:00")]

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            result = find_latest_session_id_fast(wt_path, sessions)

        assert result is None

    def test_fast_falls_back_for_none(self, tmp_session_state_dir: Path):
        """sessions=None should delegate to full scan."""
        wt_path = "/tmp/wt-fallback"
        make_session_dir(
            tmp_session_state_dir, "fallback-sess", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
        )

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            result = find_latest_session_id_fast(wt_path, None)

        assert result == "fallback-sess"

    def test_fast_empty_sessions_falls_back(self, tmp_session_state_dir: Path):
        """sessions=[] should fall back to full scan (hook may not have fired)."""
        wt_path = "/tmp/wt-empty-fallback"
        make_session_dir(
            tmp_session_state_dir, "discovered-sess", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
        )

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            result = find_latest_session_id_fast(wt_path, [])

        assert result == "discovered-sess"

    def test_fast_skips_missing_dirs(self, tmp_session_state_dir: Path):
        sessions = [SessionEntry("gone-sess", "2026-06-01T10:00:00")]

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            result = find_latest_session_id_fast("/tmp/wt", sessions)

        assert result is None


# ---------------------------------------------------------------------------
# Detached parent-continuation sessions (subconscious / rem-agent runs)
# ---------------------------------------------------------------------------

def _mark_detached(session_dir: Path) -> None:
    """Write the ``.detached`` marker Copilot CLI uses for detached children."""
    (session_dir / ".detached").write_text("")


def _make_record(wt_id: str, wt_path: str, sessions=None) -> WorktreeRecord:
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


class TestDetachedSessionsExcluded:
    """Detached parent-continuation sessions must not be attributed to a
    worktree.

    The Copilot CLI's subconscious / rem-agent consolidation runs are
    spawned detached from a parent session and inherit that parent's cwd --
    which, for an old session, is an already-finalized worktree path. Such
    sessions carry a ``.detached`` marker file and must be skipped so they
    don't re-activate finalized worktrees or pollute their summaries.
    """

    def test_scan_sessions_skips_detached_live_session(
        self, tmp_session_state_dir: Path
    ):
        """A detached session with a live lock must NOT mark the worktree active."""
        wt_path = "/tmp/wt-detached-live"
        sdir = make_session_dir(
            tmp_session_state_dir, "detached-sess", wt_path,
            summary="Apply context_board add/prune updates",
            lock_pid=os.getpid(),
        )
        _mark_detached(sdir)

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            with patch(
                "agent_worktrees.sessions._is_copilot_process", return_value=True
            ):
                ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert norm not in ctx.active_sessions
        assert norm not in ctx.session_count
        # The consolidation prompt must not become the worktree's summary.
        assert norm not in ctx.latest_summary

    def test_scan_sessions_keeps_normal_live_session(
        self, tmp_session_state_dir: Path
    ):
        """Control: a non-detached live session in the same worktree counts."""
        wt_path = "/tmp/wt-mixed-live"
        detached = make_session_dir(
            tmp_session_state_dir, "detached-sess", wt_path,
            summary="Apply context_board add/prune updates",
            lock_pid=os.getpid(),
        )
        _mark_detached(detached)
        make_session_dir(
            tmp_session_state_dir, "real-sess", wt_path,
            summary="Real interactive work",
            lock_pid=os.getpid(),
        )

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            with patch(
                "agent_worktrees.sessions._is_copilot_process", return_value=True
            ):
                ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert ctx.active_sessions.get(norm) == ["real-sess"]
        assert ctx.session_count[norm] == 1
        assert "Real interactive work" in ctx.latest_summary[norm]

    def test_find_latest_skips_detached(self, tmp_session_state_dir: Path):
        """A newer detached session must not be chosen as the resume target."""
        wt_path = "/tmp/wt-latest-detached"
        make_session_dir(
            tmp_session_state_dir, "real-sess", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
        )
        detached = make_session_dir(
            tmp_session_state_dir, "detached-sess", wt_path,
            updated_at="2026-06-01T12:00:00.000Z",
        )
        _mark_detached(detached)

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            result = find_latest_session_id(wt_path)

        assert result == "real-sess"

    def test_scan_fast_skips_detached(self, tmp_session_state_dir: Path):
        """Registry fast-path enrichment must skip detached sessions."""
        wt_path = "/tmp/wt-fast-detached"
        sdir = make_session_dir(
            tmp_session_state_dir, "detached-sess", wt_path,
            summary="Apply context_board add/prune updates",
            lock_pid=os.getpid(),
        )
        _mark_detached(sdir)

        rec = _make_record("fast-detached-wt", wt_path, sessions=[
            SessionEntry("detached-sess", "2026-06-01T10:00:00"),
        ])

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            with patch(
                "agent_worktrees.sessions._is_copilot_process", return_value=True
            ):
                ctx = scan_sessions_fast([rec])

        norm = _normalize_path(wt_path)
        assert norm not in ctx.active_sessions
        assert norm not in ctx.session_count

    def test_backfill_skips_detached(self, tmp_session_state_dir: Path):
        """Backfill must not register a detached session against a worktree."""
        wt_path = "/tmp/wt-backfill-detached"
        make_session_dir(
            tmp_session_state_dir, "real-sess", wt_path,
        )
        detached = make_session_dir(
            tmp_session_state_dir, "detached-sess", wt_path,
        )
        _mark_detached(detached)

        rec = _make_record("backfill-wt", wt_path, sessions=[])

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            discovered = backfill_sessions([rec])

        assert discovered.get("backfill-wt") == ["real-sess"]


# ---------------------------------------------------------------------------
# Mux probe robustness (has_mux_session / _list_mux_sessions /
# kill_mux_session must degrade gracefully when the spawn itself fails,
# e.g. Windows Application Control policy: OSError WinError 4551)
# ---------------------------------------------------------------------------

class TestMuxSpawnFailureDegrades:
    """A blocked or missing multiplexer must not crash the caller.

    Regression: subprocess.run raised OSError (WinError 4551, Application
    Control policy blocked psmux) which escaped the narrow
    except (FileNotFoundError, subprocess.TimeoutExpired) and crashed the
    binstub during `resolve`.
    """

    _BLOCKED = OSError(4551, "An Application Control policy has blocked this file")

    def test_has_mux_session_survives_oserror(self):
        from agent_worktrees.sessions import has_mux_session

        with patch("subprocess.run", side_effect=self._BLOCKED):
            assert has_mux_session("anything") is False

    def test_list_mux_sessions_survives_oserror(self):
        from agent_worktrees.sessions import _list_mux_sessions

        with patch("subprocess.run", side_effect=self._BLOCKED):
            assert _list_mux_sessions() is None

    def test_kill_mux_session_survives_oserror(self):
        from agent_worktrees.sessions import kill_tmux_session

        with patch("subprocess.run", side_effect=self._BLOCKED):
            assert kill_tmux_session("anything") is False

    def test_has_mux_session_still_handles_missing_binary(self):
        from agent_worktrees.sessions import has_mux_session

        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert has_mux_session("anything") is False


# ---------------------------------------------------------------------------
# Context % + last-activity enrichment
# ---------------------------------------------------------------------------

class TestContextEnrichment:
    """last_activity and context_pct derived from session-state."""

    def test_scan_sessions_populates_activity_and_context(
        self, tmp_session_state_dir: Path
    ):
        wt_path = "/tmp/wt-ctx"
        make_session_dir(
            tmp_session_state_dir, "sess-ctx", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
            context_pct=42,
        )
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert ctx.context_pct[norm] == 42
        # YAML parses the timestamp to a datetime; str() form is preserved.
        assert "2026-06-01" in ctx.last_activity[norm]
        assert "10:00:00" in ctx.last_activity[norm]

    def test_newest_session_wins_for_context(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-ctx2"
        make_session_dir(
            tmp_session_state_dir, "old", wt_path,
            updated_at="2026-06-01T10:00:00.000Z", context_pct=30,
        )
        make_session_dir(
            tmp_session_state_dir, "new", wt_path,
            updated_at="2026-06-01T12:00:00.000Z", context_pct=70,
        )
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        # Newest session (12:00) drives both activity and context%.
        assert "12:00:00" in ctx.last_activity[norm]
        assert ctx.context_pct[norm] == 70

    def test_missing_context_json_omits_pct(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-noctx"
        make_session_dir(tmp_session_state_dir, "sess-noctx", wt_path)
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions([wt_path])

        norm = _normalize_path(wt_path)
        assert norm not in ctx.context_pct
        # last_activity is still populated from workspace.yaml updated_at.
        assert norm in ctx.last_activity

    def test_fast_path_populates_context(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-fast-ctx"
        make_session_dir(
            tmp_session_state_dir, "fast-ctx", wt_path,
            updated_at="2026-06-02T09:00:00.000Z", context_pct=55,
        )
        rec = _make_record(
            "wt-fast-ctx", wt_path,
            sessions=[SessionEntry(session_id="fast-ctx", started_at="2026-06-02T09:00:00")],
        )
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])

        norm = _normalize_path(wt_path)
        assert ctx.context_pct[norm] == 55
        assert "2026-06-02" in ctx.last_activity[norm]
        assert "09:00:00" in ctx.last_activity[norm]


# ---------------------------------------------------------------------------
# validate_session_id (parent-session resume fallback, #1029)
# ---------------------------------------------------------------------------

class TestValidateSessionId:
    def test_returns_id_for_valid_session(self, tmp_session_state_dir: Path):
        make_session_dir(tmp_session_state_dir, "good-sess", "/tmp/wt",
                         summary="work")
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            assert validate_session_id("good-sess") == "good-sess"

    def test_none_for_missing_dir(self, tmp_session_state_dir: Path):
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            assert validate_session_id("nope") is None

    def test_none_for_stub_without_conversation(self, tmp_session_state_dir: Path):
        # A dir with no session.db / events.jsonl is a stale stub, not resumable.
        (tmp_session_state_dir / "stub").mkdir()
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            assert validate_session_id("stub") is None

    def test_none_for_empty_input(self):
        assert validate_session_id(None) is None
        assert validate_session_id("") is None
