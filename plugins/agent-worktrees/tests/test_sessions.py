"""Tests for agent_worktrees.sessions — session scanning and fast-path."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import make_session_dir

from agent_worktrees.sessions import (
    _normalize_path,
    backfill_sessions,
    find_latest_session_id_fast,
    list_worktree_sessions,
    mux_binding_for_session,
    mux_copilot_pane,
    mux_seed_pane,
    mux_session_index,
    mux_session_name,
    worktree_id_from_mux_session,
    recent_worktree_messages,
    scan_sessions_fast,
    validate_session_id,
)
from agent_worktrees.tracking import (
    SessionEntry,
    WorktreeRecord,
    save_record,
)

def _make_mux_record(wt_id: str, wt_path: str, sessions=None) -> WorktreeRecord:
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
        sessions=sessions,
    )


class TestMuxCopilotPane:
    def test_returns_recorded_live_head_pane(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        rec = _make_mux_record(
            "wt-pane",
            "/tmp/src/wt-pane",
            sessions=[SessionEntry("sess-head", "2026-06-01T10:00:00", pane_id="%42")],
        )
        rec.head_session = "sess-head"
        save_record(rec, tmp_tracking_dir / "wt-pane.yaml")
        monkeypatch.setattr(
            "agent_worktrees.sessions._mux_pane_alive",
            lambda pane, mux_bin: pane == "%42",
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions.mux_active_pane",
            lambda wt, mux=None: "%active",
        )

        assert mux_copilot_pane("wt-pane", mux="tmux") == "%42"

    def test_returns_recorded_live_requested_session_pane(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        rec = _make_mux_record(
            "wt-pane",
            "/tmp/src/wt-pane",
            sessions=[
                SessionEntry("sess-head", "2026-06-01T10:00:00", pane_id="%1"),
                SessionEntry("sess-other", "2026-06-01T10:01:00", pane_id="%2"),
            ],
        )
        rec.head_session = "sess-head"
        save_record(rec, tmp_tracking_dir / "wt-pane.yaml")
        monkeypatch.setattr(
            "agent_worktrees.sessions._mux_pane_alive",
            lambda pane, mux_bin: pane == "%2",
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions.mux_active_pane",
            lambda wt, mux=None: "%active",
        )

        assert mux_copilot_pane("wt-pane", session_id="sess-other", mux="tmux") == "%2"

    def test_falls_back_to_active_when_recorded_pane_dead(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        rec = _make_mux_record(
            "wt-dead",
            "/tmp/src/wt-dead",
            sessions=[SessionEntry("sess-dead", "2026-06-01T10:00:00", pane_id="%9")],
        )
        save_record(rec, tmp_tracking_dir / "wt-dead.yaml")
        monkeypatch.setattr(
            "agent_worktrees.sessions._mux_pane_alive",
            lambda pane, mux_bin: False,
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions.mux_active_pane",
            lambda wt, mux=None: "%active",
        )

        assert mux_copilot_pane("wt-dead", mux="tmux") == "%active"

    def test_falls_back_to_active_when_pane_unset(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        rec = _make_mux_record(
            "wt-unset",
            "/tmp/src/wt-unset",
            sessions=[SessionEntry("sess-unset", "2026-06-01T10:00:00")],
        )
        save_record(rec, tmp_tracking_dir / "wt-unset.yaml")
        monkeypatch.setattr(
            "agent_worktrees.sessions.mux_active_pane",
            lambda wt, mux=None: "%active",
        )

        assert mux_copilot_pane("wt-unset", mux="tmux") == "%active"


class TestMuxBindingForSession:
    def _session_lock(self, root: Path, session_id: str, pid: int) -> None:
        entry = root / session_id
        entry.mkdir(parents=True)
        (entry / f"inuse.{pid}.lock").write_text("", encoding="utf-8")

    def test_resolves_worktree_and_pane_from_lock_process_ancestry(
        self, tmp_path: Path, monkeypatch
    ):
        self._session_lock(tmp_path, "sess-a", 300)
        monkeypatch.setattr(
            "agent_worktrees.sessions._session_state_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._is_copilot_process",
            lambda pid: pid == 300,
        )
        monkeypatch.setattr(
            "agent_worktrees.reclaim.build_process_table",
            lambda: {
                100: {"ppid": 1, "name": "psmux.exe"},
                200: {"ppid": 100, "name": "pwsh.exe"},
                300: {"ppid": 200, "name": "copilot.exe"},
            },
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._windows_process_start_time",
            lambda pid: {100: 1, 200: 2, 300: 3}.get(pid),
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions.platform.system", lambda: "Windows"
        )
        monkeypatch.setattr(
            "agent_worktrees.locks.process_start_time",
            lambda pid: {
                200: "pane-created",
                300: "copilot-created",
            }.get(pid),
        )

        class _Result:
            returncode = 0
            stdout = "wt-wt-a|%7|200\npersonal|%9|400\n"

        monkeypatch.setattr(
            "subprocess.run", lambda *args, **kwargs: _Result()
        )

        assert mux_binding_for_session("sess-a", mux="psmux") == {
            "worktree_id": "wt-a",
            "session_name": "wt-wt-a",
            "pane_id": "%7",
            "pane_pid": 200,
            "pane_start_time": "pane-created",
            "copilot_pid": 300,
            "copilot_start_time": "copilot-created",
        }

    def test_resolves_expected_non_worktree_mux_session(
        self, tmp_path: Path, monkeypatch
    ):
        self._session_lock(tmp_path, "sess-a", 300)
        monkeypatch.setattr(
            "agent_worktrees.sessions._session_state_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._is_copilot_process",
            lambda pid: pid == 300,
        )
        monkeypatch.setattr(
            "agent_worktrees.reclaim.build_process_table",
            lambda: {
                100: {"ppid": 1, "name": "psmux.exe"},
                200: {"ppid": 100, "name": "pwsh.exe"},
                300: {"ppid": 200, "name": "copilot.exe"},
            },
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._windows_process_start_time",
            lambda pid: {100: 1, 200: 2, 300: 3}.get(pid),
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions.platform.system", lambda: "Windows"
        )
        monkeypatch.setattr(
            "agent_worktrees.locks.process_start_time",
            lambda pid: {
                200: "pane-created",
                300: "copilot-created",
            }.get(pid),
        )

        class _Result:
            returncode = 0
            stdout = "caller-owned|%7|200\nwt-wt-other|%8|100\n"

        monkeypatch.setattr(
            "subprocess.run", lambda *args, **kwargs: _Result()
        )

        assert mux_binding_for_session(
            "sess-a",
            mux="psmux",
            expected_session_name="caller-owned",
        ) == {
            "worktree_id": "",
            "session_name": "caller-owned",
            "pane_id": "%7",
            "pane_pid": 200,
            "pane_start_time": "pane-created",
            "copilot_pid": 300,
            "copilot_start_time": "copilot-created",
        }

    def test_ambiguous_pane_ancestry_fails_closed(
        self, tmp_path: Path, monkeypatch
    ):
        self._session_lock(tmp_path, "sess-a", 300)
        monkeypatch.setattr(
            "agent_worktrees.sessions._session_state_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._is_copilot_process", lambda _pid: True
        )
        monkeypatch.setattr(
            "agent_worktrees.reclaim.build_process_table",
            lambda: {
                100: {"ppid": 1, "name": "psmux.exe"},
                200: {"ppid": 100, "name": "pwsh.exe"},
                300: {"ppid": 200, "name": "copilot.exe"},
            },
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._windows_process_start_time",
            lambda pid: {100: 1, 200: 2, 300: 3}.get(pid),
        )
        monkeypatch.setattr(
            "agent_worktrees.locks.process_start_time",
            lambda pid: f"started-{pid}",
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions.platform.system", lambda: "Windows"
        )

        class _Result:
            returncode = 0
            stdout = "wt-wt-a|%7|200\nwt-wt-b|%8|100\n"

        monkeypatch.setattr(
            "subprocess.run", lambda *args, **kwargs: _Result()
        )

        assert mux_binding_for_session("sess-a", mux="psmux") is None

    def test_reused_windows_parent_pid_is_rejected(
        self, tmp_path: Path, monkeypatch
    ):
        self._session_lock(tmp_path, "sess-a", 300)
        monkeypatch.setattr(
            "agent_worktrees.sessions._session_state_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._is_copilot_process", lambda _pid: True
        )
        monkeypatch.setattr(
            "agent_worktrees.reclaim.build_process_table",
            lambda: {
                100: {"ppid": 1, "name": "psmux.exe"},
                200: {"ppid": 100, "name": "pwsh.exe"},
                300: {"ppid": 200, "name": "copilot.exe"},
            },
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._windows_process_start_time",
            # PID 100 was created after its alleged child 200: it was reused.
            lambda pid: {100: 4, 200: 2, 300: 3}.get(pid),
        )
        monkeypatch.setattr(
            "agent_worktrees.locks.process_start_time",
            lambda pid: f"started-{pid}",
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions.platform.system", lambda: "Windows"
        )

        class _Result:
            returncode = 0
            stdout = "wt-wt-wrong|%7|100\n"

        monkeypatch.setattr(
            "subprocess.run", lambda *args, **kwargs: _Result()
        )

        assert mux_binding_for_session("sess-a", mux="psmux") is None

    def test_missing_live_lock_does_not_query_mux(
        self, tmp_path: Path, monkeypatch
    ):
        self._session_lock(tmp_path, "sess-a", 300)
        monkeypatch.setattr(
            "agent_worktrees.sessions._session_state_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._is_copilot_process", lambda _pid: False
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *args, **kwargs: pytest.fail("mux should not be queried"),
        )

        assert mux_binding_for_session("sess-a", mux="psmux") is None

    def test_reused_lock_pid_during_validation_does_not_query_mux(
        self, tmp_path: Path, monkeypatch
    ):
        self._session_lock(tmp_path, "sess-a", 300)
        monkeypatch.setattr(
            "agent_worktrees.sessions._session_state_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "agent_worktrees.sessions._is_copilot_process", lambda _pid: True
        )
        start_times = iter(("copilot-created", "replacement-created"))
        monkeypatch.setattr(
            "agent_worktrees.locks.process_start_time",
            lambda _pid: next(start_times),
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *args, **kwargs: pytest.fail("mux should not be queried"),
        )

        assert mux_binding_for_session("sess-a", mux="psmux") is None


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

    def test_unindexed_records_are_not_swept(self, tmp_session_state_dir: Path):
        """Invariant (GH #198): sessions=None must NOT trigger a full-scan of
        session-state on a routine read -- the record is left un-enriched until
        an explicit backfill populates its registry."""
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
        assert norm not in ctx.session_count
        assert norm not in ctx.latest_summary

    def test_mixed_indexed_and_unindexed(self, tmp_session_state_dir: Path):
        """Only the registry-indexed record is enriched (random-access); the
        unindexed record is NOT swept (invariant GH #198)."""
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
        # Invariant: the unindexed record is not swept -> no enrichment.
        assert _normalize_path(wt_legacy) not in ctx.session_count

    def test_empty_sessions_list(self, tmp_session_state_dir: Path):
        """sessions=[] with nothing on disk -> empty context (via fallback)."""
        rec = self._make_record("empty-wt", "/tmp/empty", sessions=[])

        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])

        assert ctx.session_count == {}

    def test_empty_sessions_are_not_swept(
        self, tmp_session_state_dir: Path,
    ):
        """Invariant (GH #198): sessions=[] (registry active but the hook never
        recorded a session) must NOT fall back to a full session-state sweep on
        a routine read. The worktree is left un-enriched until an explicit
        backfill runs."""
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
        assert norm not in ctx.session_count
        assert norm not in ctx.latest_summary


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

    def test_fast_none_returns_none_no_sweep(self, tmp_session_state_dir: Path):
        """Invariant (GH #198): sessions=None returns None -- never a full-scan
        sweep. A launch/auto-resume lookup is not severe enough to sweep."""
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

        assert result is None

    def test_fast_empty_sessions_returns_none_no_sweep(
        self, tmp_session_state_dir: Path,
    ):
        """Invariant (GH #198): sessions=[] returns None without sweeping."""
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

        assert result is None

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
        rec = _make_record("wt-ctx2", wt_path, sessions=[
            SessionEntry("old", "2026-06-01T10:00:00"),
            SessionEntry("new", "2026-06-01T12:00:00"),
        ])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])

        norm = _normalize_path(wt_path)
        # Newest session (12:00) drives both activity and context%.
        assert "12:00:00" in ctx.last_activity[norm]
        assert ctx.context_pct[norm] == 70

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


# ---------------------------------------------------------------------------
# recent_worktree_messages (read-side companion to the disposition summary)
# ---------------------------------------------------------------------------

def _conv_event(kind: str, content: str, ts: str) -> str:
    """A real-shaped user/assistant message event line (text under data.content)."""
    import json
    return json.dumps({"type": kind, "data": {"content": content}, "timestamp": ts})


class TestRecentWorktreeMessages:
    """The last-N conversation-turn derivation behind the Picker viewer."""

    def test_returns_last_n_newest_last(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-recent"
        make_session_dir(
            tmp_session_state_dir, "sess-recent", wt_path,
            events_lines=[
                _conv_event("user.message", "first ask", "2026-06-01T10:00:00Z"),
                _conv_event("assistant.message", "first answer", "2026-06-01T10:00:01Z"),
                _conv_event("user.message", "second ask", "2026-06-01T10:00:02Z"),
                _conv_event("assistant.message", "second answer", "2026-06-01T10:00:03Z"),
                _conv_event("user.message", "third ask", "2026-06-01T10:00:04Z"),
            ],
        )
        rec = _make_record("wt-recent", wt_path,
                           sessions=[SessionEntry("sess-recent", "2026-06-01T10:00:00")])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            out = recent_worktree_messages(rec, limit=3)
        assert out["session_id"] == "sess-recent"
        assert out["count"] == 3
        # Newest last, oldest of the tail first.
        assert [m["text"] for m in out["messages"]] == [
            "second ask", "second answer", "third ask"]
        assert [m["role"] for m in out["messages"]] == [
            "user", "assistant", "user"]

    def test_skips_tool_only_assistant_turns(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-toolonly"
        make_session_dir(
            tmp_session_state_dir, "sess-tool", wt_path,
            events_lines=[
                _conv_event("user.message", "do the thing", "2026-06-01T10:00:00Z"),
                # Tool-only assistant turn -- empty content, must be skipped.
                _conv_event("assistant.message", "", "2026-06-01T10:00:01Z"),
                _conv_event("assistant.message", "done", "2026-06-01T10:00:02Z"),
            ],
        )
        rec = _make_record("wt-toolonly", wt_path,
                           sessions=[SessionEntry("sess-tool", "2026-06-01T10:00:00")])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            out = recent_worktree_messages(rec, limit=5)
        assert [m["text"] for m in out["messages"]] == ["do the thing", "done"]

    def test_picks_newest_session(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-multi"
        make_session_dir(
            tmp_session_state_dir, "sess-old", wt_path,
            updated_at="2026-06-01T10:00:00.000Z",
            events_lines=[_conv_event("user.message", "old work", "2026-06-01T10:00:00Z")],
        )
        make_session_dir(
            tmp_session_state_dir, "sess-new", wt_path,
            updated_at="2026-06-02T10:00:00.000Z",
            events_lines=[_conv_event("user.message", "new work", "2026-06-02T10:00:00Z")],
        )
        rec = _make_record("wt-multi", wt_path, sessions=[
            SessionEntry("sess-old", "2026-06-01T10:00:00"),
            SessionEntry("sess-new", "2026-06-02T10:00:00"),
        ])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            out = recent_worktree_messages(rec, limit=3)
        assert out["session_id"] == "sess-new"
        assert [m["text"] for m in out["messages"]] == ["new work"]

    def test_empty_when_no_session(self, tmp_session_state_dir: Path):
        rec = _make_record("wt-none", "/tmp/wt-none", sessions=[])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            out = recent_worktree_messages(rec, limit=3)
        assert out == {"session_id": None, "messages": [], "count": 0}


# ---------------------------------------------------------------------------
# mux_seed_pane — hardened readiness + echo-verify (issue: replay debounce)
# ---------------------------------------------------------------------------

class _SeedDriver:
    """Fake ``subprocess.run`` for capture-pane / send-keys during seeding.

    Before the literal ``-l`` type, capture-pane serves ``ready_caps`` (the
    readiness poll); after it, ``echo_caps`` (the echo-verify poll). Exhausted
    lists yield ``""``. Every send-keys is recorded.
    """

    def __init__(self, ready_caps, echo_caps):
        self.ready_caps = list(ready_caps)
        self.echo_caps = list(echo_caps)
        self.typed = False
        self.sends = []  # each == the args after ``-t <pane>``

    def run(self, argv, **kw):
        from types import SimpleNamespace

        verb = argv[1]
        if verb == "capture-pane":
            src = self.echo_caps if self.typed else self.ready_caps
            out = src.pop(0) if src else ""
            return SimpleNamespace(stdout=out, returncode=0)
        if verb == "send-keys":
            self.sends.append(argv[4:])  # drop [bin, send-keys, -t, pane]
            if "-l" in argv:
                self.typed = True
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    def enter_sent(self):
        return any(s == ["Enter"] for s in self.sends)


class _Clock:
    def __init__(self, step=10.0):
        self.t = 0.0
        self.step = step

    def __call__(self):
        v = self.t
        self.t += self.step
        return v


def _run_seed(driver, seed="Continue: build multi-account effort"):
    with patch("subprocess.run", side_effect=driver.run), \
         patch("time.sleep"), \
         patch("time.monotonic", side_effect=_Clock()), \
         patch("agent_worktrees.sessions._mux_bin", return_value="tmux"):
        return mux_seed_pane(
            "%9", seed, ready_timeout=100.0, poll_interval=0.0, settle=0.0,
        )


def test_seed_pane_not_ready_never_submits():
    # Copilot never shows a cue -> we must NOT type or press Enter (no blind
    # submit into a half-loaded TUI / fallback shell).
    driver = _SeedDriver(ready_caps=[], echo_caps=[])
    result = _run_seed(driver)
    assert result["ready"] is False
    assert result["sent"] is False
    assert result["submitted"] is False
    assert result["reason"] == "not-ready-timeout"
    assert driver.sends == []  # nothing typed, Enter never pressed


def test_seed_pane_requires_two_consecutive_cues():
    # A single transient caret frame (then gone) is NOT enough; a flapping cue
    # keeps stability from reaching 2, so seeding still degrades to not-ready.
    driver = _SeedDriver(ready_caps=["❯", "", "❯", "", "❯", ""], echo_caps=[])
    result = _run_seed(driver)
    assert result["ready"] is False
    assert driver.sends == []


def test_seed_pane_happy_path_submits():
    # Stable caret (2 in a row) -> type -> the echo shows the seed head -> Enter.
    seed = "Continue: build multi-account effort"
    driver = _SeedDriver(
        ready_caps=["❯", "❯"],
        echo_caps=[f"❯ {seed}"],
    )
    result = _run_seed(driver, seed=seed)
    assert result["ready"] is True
    assert result["sent"] is True
    assert result["submitted"] is True
    assert result["ok"] is True
    assert driver.enter_sent() is True


def test_seed_pane_footer_cue_also_ready():
    # The "esc … interrupt" footer is a valid Copilot cue (no caret needed).
    seed = "Continue: do the thing"
    driver = _SeedDriver(
        ready_caps=["press esc to interrupt", "press esc to interrupt"],
        echo_caps=[seed],
    )
    result = _run_seed(driver, seed=seed)
    assert result["ready"] is True
    assert result["submitted"] is True


def test_seed_pane_not_echoed_skips_enter():
    # Ready + typed, but the seed never echoes back -> do NOT press Enter, so a
    # partially-eaten seed is never submitted as a bogus turn.
    seed = "Continue: build multi-account effort"
    driver = _SeedDriver(ready_caps=["❯", "❯"], echo_caps=[])
    result = _run_seed(driver, seed=seed)
    assert result["ready"] is True
    assert result["sent"] is True
    assert result["submitted"] is False
    assert result["reason"] == "seed-not-echoed"
    assert driver.enter_sent() is False


class TestListWorktreeSessionsLifecycle:
    """``list_worktree_sessions`` stamps the ASSERTED lifecycle (``state`` +
    ``is_head``) onto each entry so a consumer (agent-bridge -> Neuron Forge)
    can resolve the head-first current session and badge the rest "no longer
    current" (agent-fabric single-current-session-per-worktree, Phase 4).
    """

    def test_stamps_state_and_is_head(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-life"
        for sid, ts in (("s1", "2026-06-01T10:00:00Z"),
                        ("s2", "2026-06-01T10:00:01Z"),
                        ("s3", "2026-06-01T10:00:02Z")):
            make_session_dir(
                tmp_session_state_dir, sid, wt_path, updated_at=ts,
                events_lines=[_conv_event("user.message", "hi", ts)],
            )
        rec = _make_record("wt-life", wt_path, sessions=[
            SessionEntry("s1", "t", state="handed-off", successor="s2"),
            SessionEntry("s2", "t", predecessor="s1"),
            SessionEntry("s3", "t"),
        ])
        rec.head_session = "s2"
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            out = list_worktree_sessions(rec)
        by_id = {s["id"]: s for s in out}
        assert by_id["s1"]["state"] == "handed-off"
        assert by_id["s1"]["is_head"] is False
        assert by_id["s2"]["state"] == "active"
        assert by_id["s2"]["is_head"] is True  # the asserted head
        assert by_id["s3"]["state"] == "active"
        assert by_id["s3"]["is_head"] is False

    def test_legacy_record_derives_newest_head(self, tmp_session_state_dir: Path):
        # No head_session stamped, no per-session state -> derived head is the
        # newest non-concluded session; every entry defaults to ``active``.
        wt_path = "/tmp/wt-legacy"
        for sid, ts in (("a", "2026-06-01T10:00:00Z"),
                        ("b", "2026-06-01T10:00:05Z")):
            make_session_dir(
                tmp_session_state_dir, sid, wt_path, updated_at=ts,
                events_lines=[_conv_event("user.message", "hi", ts)],
            )
        rec = _make_record("wt-legacy", wt_path, sessions=[
            SessionEntry("a", "t"), SessionEntry("b", "t"),
        ])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            out = list_worktree_sessions(rec)
        by_id = {s["id"]: s for s in out}
        assert by_id["b"]["is_head"] is True   # newest survivor
        assert by_id["a"]["is_head"] is False
        assert all(s["state"] == "active" for s in out)

    def test_all_concluded_has_no_head(self, tmp_session_state_dir: Path):
        wt_path = "/tmp/wt-done"
        for sid in ("x", "y"):
            make_session_dir(
                tmp_session_state_dir, sid, wt_path,
                events_lines=[_conv_event("user.message", "hi",
                                          "2026-06-01T10:00:00Z")],
            )
        rec = _make_record("wt-done", wt_path, sessions=[
            SessionEntry("x", "t", state="concluded"),
            SessionEntry("y", "t", state="handed-off"),
        ])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            out = list_worktree_sessions(rec)
        assert all(s["is_head"] is False for s in out)


# ---------------------------------------------------------------------------
# session_has_conversation_data + resolve_resume_target
# (execution-time resume fallback ladder -- Open/Resume/Bare resume agree on
# one target, head-preferred, stub-rejecting)
# ---------------------------------------------------------------------------

from agent_worktrees.sessions import (  # noqa: E402
    resolve_resume_target,
    session_has_conversation_data,
)


class TestSessionHasConversationData:
    def test_true_with_events_jsonl(self, tmp_session_state_dir: Path):
        make_session_dir(tmp_session_state_dir, "s-ok", "/tmp/w",
                         events_lines=[_conv_event("user.message", "hi",
                                                   "2026-06-01T10:00:00Z")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            assert session_has_conversation_data("s-ok") is True

    def test_false_for_stub_dir_without_conversation_data(
            self, tmp_session_state_dir: Path):
        # workspace.yaml only -- the stale stub Copilot rejects with
        # "No session matched".
        make_session_dir(tmp_session_state_dir, "s-stub", "/tmp/w",
                         has_events_file=False)
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            assert session_has_conversation_data("s-stub") is False

    def test_false_for_missing_dir_and_empty_id(
            self, tmp_session_state_dir: Path):
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            assert session_has_conversation_data("nope") is False
            assert session_has_conversation_data("") is False
            assert session_has_conversation_data(None) is False


class TestResolveResumeTarget:
    def test_prefers_asserted_head_over_newer_latest(
            self, tmp_session_state_dir: Path):
        wt = "/tmp/wt-head"
        make_session_dir(tmp_session_state_dir, "old", wt,
                         updated_at="2026-06-01T10:00:00Z",
                         events_lines=[_conv_event("user.message", "a",
                                                   "2026-06-01T10:00:00Z")])
        # 'new' is filesystem-latest, but 'old' is the asserted head.
        make_session_dir(tmp_session_state_dir, "new", wt,
                         updated_at="2026-06-01T12:00:00Z",
                         events_lines=[_conv_event("user.message", "b",
                                                   "2026-06-01T12:00:00Z")])
        rec = _make_record("wt-head", wt,
                           sessions=[SessionEntry("old", "t"),
                                     SessionEntry("new", "t")])
        rec.head_session = "old"
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            assert resolve_resume_target(rec) == "old"

    def test_falls_back_to_latest_when_head_is_a_stub(
            self, tmp_session_state_dir: Path):
        wt = "/tmp/wt-stubhead"
        # Head 'h' is a stub (no conversation data) -> rejected; fall through
        # to the filesystem-latest valid session 'v'.
        make_session_dir(tmp_session_state_dir, "h", wt, has_events_file=False)
        make_session_dir(tmp_session_state_dir, "v", wt,
                         updated_at="2026-06-01T11:00:00Z",
                         events_lines=[_conv_event("user.message", "b",
                                                   "2026-06-01T11:00:00Z")])
        rec = _make_record("wt-stubhead", wt,
                           sessions=[SessionEntry("v", "t"),
                                     SessionEntry("h", "t")])
        rec.head_session = "h"
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            assert resolve_resume_target(rec) == "v"

    def test_none_when_nothing_resumable(self, tmp_session_state_dir: Path):
        wt = "/tmp/wt-empty"
        make_session_dir(tmp_session_state_dir, "stub", wt,
                         has_events_file=False)
        rec = _make_record("wt-empty", wt, sessions=[SessionEntry("stub", "t")])
        with patch("agent_worktrees.sessions._session_state_dir",
                   return_value=tmp_session_state_dir):
            assert resolve_resume_target(rec) is None
# last_session_id (folded into the single scan pass -- GH #198)
# ---------------------------------------------------------------------------

class TestLastSessionId:
    """SessionContext.last_session_id carries the resume-target id (newest
    session with conversation data), folded into the single registry-driven
    scan pass so the list render never re-scans all of session-state per
    worktree (GH #198; docs/patterns/session-state-access.md).
    """

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
            sessions=sessions,
        )

    def test_records_newest_via_registry(self, tmp_session_state_dir: Path):
        wt = "/tmp/wt-lsid"
        make_session_dir(
            tmp_session_state_dir, "old", wt,
            updated_at="2026-06-01T10:00:00.000Z",
        )
        make_session_dir(
            tmp_session_state_dir, "new", wt,
            updated_at="2026-06-01T12:00:00.000Z",
        )
        rec = self._make_record("lsid", wt, sessions=[
            SessionEntry("old", "2026-06-01T10:00:00"),
            SessionEntry("new", "2026-06-01T12:00:00"),
        ])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])
        norm = _normalize_path(wt)
        assert ctx.last_session_id[norm] == "new"

    def test_newer_stub_does_not_override_older_valid(self, tmp_session_state_dir: Path):
        """A newer stale stub (workspace.yaml only, no events/db) must NOT
        become the resume target; the older session carrying conversation data
        wins. Guards the ordering fix: last_session_id is tracked independently
        of last_activity so the activity gate can't hide the older valid session
        behind the newer stub.
        """
        wt = "/tmp/wt-lsid-stub"
        make_session_dir(
            tmp_session_state_dir, "real-old", wt,
            updated_at="2026-06-01T10:00:00.000Z",
        )
        make_session_dir(
            tmp_session_state_dir, "stub-new", wt,
            updated_at="2026-06-01T12:00:00.000Z",
            has_events_file=False,
        )
        rec = self._make_record("lsid-stub", wt, sessions=[
            SessionEntry("real-old", "2026-06-01T10:00:00"),
            SessionEntry("stub-new", "2026-06-01T12:00:00"),
        ])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])
        norm = _normalize_path(wt)
        assert ctx.last_session_id[norm] == "real-old"

    def test_fast_path_records_newest(self, tmp_session_state_dir: Path):
        wt = "/tmp/wt-lsid-fast"
        make_session_dir(
            tmp_session_state_dir, "s-old", wt,
            updated_at="2026-06-01T10:00:00.000Z",
        )
        make_session_dir(
            tmp_session_state_dir, "s-new", wt,
            updated_at="2026-06-01T12:00:00.000Z",
        )
        rec = self._make_record("lsid-fast", wt, sessions=[
            SessionEntry("s-old", "2026-06-01T10:00:00"),
            SessionEntry("s-new", "2026-06-01T12:00:00"),
        ])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])
            parity = find_latest_session_id_fast(wt, rec.sessions)
        norm = _normalize_path(wt)
        assert ctx.last_session_id[norm] == "s-new"
        assert ctx.last_session_id[norm] == parity

    def test_no_qualifying_session(self, tmp_session_state_dir: Path):
        wt = "/tmp/wt-lsid-none"
        make_session_dir(
            tmp_session_state_dir, "stub-only", wt,
            has_events_file=False,
        )
        rec = self._make_record("lsid-none", wt, sessions=[
            SessionEntry("stub-only", "2026-06-01T10:00:00"),
        ])
        with patch(
            "agent_worktrees.sessions._session_state_dir",
            return_value=tmp_session_state_dir,
        ):
            ctx = scan_sessions_fast([rec])
        assert _normalize_path(wt) not in ctx.last_session_id


# ---------------------------------------------------------------------------
# Regression guard: session-state sweep confined to backfill
# (docs/patterns/session-state-access.md)
# ---------------------------------------------------------------------------

def test_session_state_sweep_confined_to_backfill():
    """Within the session-discovery module, iteration over the session-state
    ROOT (``iterdir``/``scandir``/``listdir``) may live ONLY in the sanctioned
    ``backfill_sessions`` sweep. Any other function enumerating the root would
    reintroduce the O(worktrees x sessions) hot-path sweep this pattern forbids
    (GH #198; docs/patterns/session-state-access.md).
    """
    import ast

    import agent_worktrees.sessions as sessions_mod

    src = Path(sessions_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    sweep_attrs = {"iterdir", "scandir", "listdir"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == "backfill_sessions":
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in sweep_attrs):
                offenders.append(f"{node.name}:{sub.func.attr}")
    assert not offenders, (
        "session-state sweep found outside backfill_sessions -- resolve by "
        f"exact session id instead: {sorted(set(offenders))}"
    )

    # The second sanctioned choke point is the resident reconciler's bounded
    # cursor. Pin its single state-root scandir so another unbounded cursor
    # cannot appear elsewhere in that module unnoticed.
    import agent_worktrees.session_catalog as catalog_mod

    catalog_src = Path(catalog_mod.__file__).read_text(encoding="utf-8")
    assert catalog_src.count("os.scandir(session_dir)") == 1


class TestMuxSessionName:
    """`.` is the window/pane separator in a tmux/psmux target spec.

    A dotted worktree id produced a session that could be created but never
    addressed again, so the launcher failed every has-session/attach after
    creating it.
    """

    def test_dots_are_replaced(self):
        assert (
            mux_session_name("host.local-linux-20260828-113232-c3c7")
            == "wt-host_local-linux-20260828-113232-c3c7"
        )

    def test_every_dot_is_replaced(self):
        assert mux_session_name("a.b.c") == "wt-a_b_c"

    def test_dot_free_ids_are_untouched(self):
        # Must stay a no-op or it orphans already-running sessions.
        assert (
            mux_session_name("host-linux-20260828-113232-c3c7")
            == "wt-host-linux-20260828-113232-c3c7"
        )

    def test_empty_id_falls_back_to_base(self):
        assert mux_session_name("") == "wt-base"

    def test_result_is_addressable_as_a_target(self):
        # tmux splits a target on ':' (window) then '.' (pane); the session
        # portion must survive that split intact.
        name = mux_session_name("host.local-linux-20260828-113232-c3c7")
        assert name.split(":")[0].split(".")[0] == name


class TestWorktreeIdFromMuxSession:
    """The `.` -> `_` mapping is lossy, so the inverse needs the known ids.

    A miss here is not cosmetic: `reap-sessions` treats an unresolved id as
    "untracked", which makes a live tracked session eligible for reaping.
    """

    def test_recovers_a_dotted_id_from_known_ids(self):
        wt_id = "host.local-linux-20260828-113232-c3c7"
        assert (
            worktree_id_from_mux_session(mux_session_name(wt_id), [wt_id]) == wt_id
        )

    def test_dot_free_id_round_trips_without_known_ids(self):
        wt_id = "host-linux-20260828-113232-c3c7"
        assert worktree_id_from_mux_session(mux_session_name(wt_id)) == wt_id

    def test_unknown_session_falls_back_to_the_stripped_name(self):
        assert worktree_id_from_mux_session("wt-someone-else") == "someone-else"

    def test_non_wt_session_is_rejected(self):
        assert worktree_id_from_mux_session("scratch") == ""

    def test_round_trip_holds_for_every_known_id(self):
        ids = [
            "host.local-linux-1-a",
            "host-linux-2-b",
            "a.b.c-linux-3-c",
        ]
        for wt_id in ids:
            assert (
                worktree_id_from_mux_session(mux_session_name(wt_id), ids) == wt_id
            )


class TestMuxSessionIndex:
    """The reap sweep resolves many sessions, so it builds the map once."""

    def test_index_maps_session_name_to_id(self):
        ids = ["host.local-linux-1-a", "host-linux-2-b"]
        assert mux_session_index(ids) == {
            "wt-host_local-linux-1-a": "host.local-linux-1-a",
            "wt-host-linux-2-b": "host-linux-2-b",
        }

    def test_index_and_known_ids_agree(self):
        ids = ["host.local-linux-1-a", "host-linux-2-b"]
        index = mux_session_index(ids)
        for wt_id in ids:
            name = mux_session_name(wt_id)
            assert worktree_id_from_mux_session(name, index=index) == wt_id
            assert worktree_id_from_mux_session(name, ids) == wt_id

    def test_index_miss_still_falls_back(self):
        index = mux_session_index(["host-linux-1-a"])
        assert worktree_id_from_mux_session("wt-other", index=index) == "other"
