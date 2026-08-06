"""Tests for the picker cache-first-paint render logic (dotfiles#948).

Covers ``data_local._overlay_cached_state`` (how a cache-only first-paint row
reads turns/state from the session-render cache, and renders Unknown when the
cache was never populated) and the ``refresh_one`` missing-record guard.
"""
from __future__ import annotations

import types

from agent_worktrees.picker_tui import data_local, derive


def _rec(**kw):
    base = dict(session_turns=None, git_state=None, session_summary=None,
                sessions=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestOverlayCachedState:
    def test_never_populated_is_unknown(self):
        raw: dict = {}
        data_local._overlay_cached_state(raw, _rec())
        assert raw["state"] == "unknown"
        # Unknown must render as "?" through the display mapping.
        assert derive._state(raw) == "?"

    def test_cached_turns_and_state_render(self):
        raw = {"status": "active"}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=12, git_state="wip"))
        assert raw["turn_count"] == 12
        assert raw["state"] == "wip"
        assert derive._state(raw) == "WIP"

    def test_zero_turns_is_populated_not_unknown(self):
        # 0 turns with a cached state is UNUSED, NOT Unknown.
        raw: dict = {}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=0, git_state="unused"))
        assert raw["state"] == "unused"
        assert derive._state(raw) == "UNUSED"

    def test_cached_summary_fills_untitled(self):
        raw = {"title": "null"}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=3, git_state="wip",
                      session_summary="Fix the widget"))
        assert raw["title"] == "Fix the widget"

    def test_fresh_bound_hint_beats_stale_terminal(self):
        # A live bound Copilot (cache-only #1416 hint) wins over a now-stale
        # cached terminal state -> ACTIVE.
        raw = {"session_bound_live": True}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=5, git_state="completed"))
        assert raw["state"] == "active"
        assert derive._state(raw) == "ACTIVE"

    def test_bound_live_always_means_active(self):
        # A live bound Copilot is ACTIVE regardless of the cached state -- WIP is
        # a lower (session-ended) state, so liveness overrides it (dotfiles#948
        # follow-up: ACTIVE must be surfaced in the first paint).
        raw = {"session_bound_live": True}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=5, git_state="wip"))
        assert raw["state"] == "active"

    def test_live_lock_forces_active_even_when_unknown(self, monkeypatch):
        # The cheap lock-file scan is the primary first-paint ACTIVE signal: a
        # running Copilot in a never-cached worktree must render ACTIVE, not "?".
        monkeypatch.setattr(
            data_local.sessions, "worktree_has_live_session",
            lambda rec: True)
        raw: dict = {}
        data_local._overlay_cached_state(raw, _rec())  # no cache at all
        assert raw["state"] == "active"
        assert raw["session_lock_live"] is True
        assert derive._state(raw) == "ACTIVE"

    def test_live_lock_beats_cached_terminal(self, monkeypatch):
        monkeypatch.setattr(
            data_local.sessions, "worktree_has_live_session",
            lambda rec: True)
        raw: dict = {}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=9, git_state="completed"))
        assert raw["state"] == "active"

    def test_no_live_signal_keeps_cached_state(self, monkeypatch):
        monkeypatch.setattr(
            data_local.sessions, "worktree_has_live_session",
            lambda rec: False)
        raw: dict = {}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=2, git_state="wip"))
        assert raw["state"] == "wip"


class TestRefreshOneGuard:
    def test_missing_record_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_local.cfg, "tracking_dir", lambda: tmp_path)
        assert data_local.refresh_one("no-such-wt") is None


class TestWorktreeHasLiveSession:
    """``sessions.worktree_has_live_session`` -- the cheap, registry-targeted
    lock-file ACTIVE probe used by the cache-only first paint."""

    def _rec_with_sessions(self, *session_ids):
        sess = [types.SimpleNamespace(session_id=s) for s in session_ids]
        return types.SimpleNamespace(sessions=sess)

    def test_no_sessions_is_false(self, tmp_path, monkeypatch):
        from agent_worktrees import sessions
        monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
        assert sessions.worktree_has_live_session(
            types.SimpleNamespace(sessions=None)) is False
        assert sessions.worktree_has_live_session(
            types.SimpleNamespace(sessions=[])) is False

    def test_live_lock_detected(self, tmp_path, monkeypatch):
        from agent_worktrees import sessions
        monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
        monkeypatch.setattr(sessions, "_is_copilot_process", lambda pid: pid == 4242)
        sdir = tmp_path / "sess-A"
        sdir.mkdir()
        (sdir / "inuse.4242.lock").write_text("")
        assert sessions.worktree_has_live_session(
            self._rec_with_sessions("sess-A")) is True

    def test_dead_lock_not_detected(self, tmp_path, monkeypatch):
        from agent_worktrees import sessions
        monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
        monkeypatch.setattr(sessions, "_is_copilot_process", lambda pid: False)
        sdir = tmp_path / "sess-B"
        sdir.mkdir()
        (sdir / "inuse.999.lock").write_text("")
        assert sessions.worktree_has_live_session(
            self._rec_with_sessions("sess-B")) is False

    def test_detached_session_skipped(self, tmp_path, monkeypatch):
        from agent_worktrees import sessions
        monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
        monkeypatch.setattr(sessions, "_is_copilot_process", lambda pid: True)
        sdir = tmp_path / "sess-C"
        sdir.mkdir()
        (sdir / "inuse.4242.lock").write_text("")
        (sdir / sessions._DETACHED_MARKER).write_text("")
        assert sessions.worktree_has_live_session(
            self._rec_with_sessions("sess-C")) is False

    def test_no_lock_file_is_false(self, tmp_path, monkeypatch):
        from agent_worktrees import sessions
        monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
        sdir = tmp_path / "sess-D"
        sdir.mkdir()
        assert sessions.worktree_has_live_session(
            self._rec_with_sessions("sess-D")) is False



class TestIncrementalTurnCount:
    """``sessions._count_user_turns`` -- size-keyed incremental turn count."""

    def _write_events(self, entry, lines):
        (entry / "events.jsonl").write_text(
            "".join(l + "\n" for l in lines), encoding="utf-8")

    def test_counts_user_messages(self, tmp_path):
        from agent_worktrees import sessions
        self._write_events(tmp_path, [
            '{"type":"user.message"}',
            '{"type":"assistant.message"}',
            '{"type":"user.message"}',
        ])
        assert sessions._count_user_turns(tmp_path) == 2
        # A sidecar is written keyed by size.
        assert (tmp_path / sessions._TURNS_SIDECAR).exists()

    def test_incremental_append_only_reads_tail(self, tmp_path):
        from agent_worktrees import sessions
        self._write_events(tmp_path, ['{"type":"user.message"}'])
        assert sessions._count_user_turns(tmp_path) == 1
        # Append two more user turns; the incremental count picks them up.
        with open(tmp_path / "events.jsonl", "a", encoding="utf-8") as f:
            f.write('{"type":"assistant.message"}\n')
            f.write('{"type":"user.message"}\n')
            f.write('{"type":"user.message"}\n')
        assert sessions._count_user_turns(tmp_path) == 3

    def test_unchanged_file_hits_sidecar(self, tmp_path, monkeypatch):
        from agent_worktrees import sessions
        self._write_events(tmp_path, ['{"type":"user.message"}'] * 4)
        assert sessions._count_user_turns(tmp_path) == 4
        # A second call must NOT re-read events.jsonl (open would raise).
        import builtins
        real_open = builtins.open

        def _boom(path, *a, **k):
            if str(path).endswith("events.jsonl"):
                raise AssertionError("events.jsonl re-read despite sidecar hit")
            return real_open(path, *a, **k)

        monkeypatch.setattr(builtins, "open", _boom)
        assert sessions._count_user_turns(tmp_path) == 4

    def test_missing_events_is_zero(self, tmp_path):
        from agent_worktrees import sessions
        assert sessions._count_user_turns(tmp_path) == 0


class TestCacheOnlySshArgs:
    """The remote fast-phase --cache-only argv plumbing (dotfiles#948)."""

    def test_add_and_drop_bash(self):
        from agent_worktrees.picker_tui import data_ssh as ds
        argv = ["ssh", "host", "bash -lc 'proj list --json --mux-details'"]
        added = ds._add_cache_only_arg(argv)
        assert "--cache-only" in added[2]
        # Idempotent.
        assert ds._add_cache_only_arg(added) == added
        # Round-trips back out.
        assert "--cache-only" not in ds._drop_cache_only_arg(added)[2]

    def test_add_and_drop_pwsh_encoded(self):
        import base64

        from agent_worktrees.picker_tui import data_ssh as ds
        argv = ["ssh", "host", ds._pwsh_remote("proj list --json --mux-details")]
        added = ds._add_cache_only_arg(argv)
        enc = added[2].rsplit("-EncodedCommand ", 1)[1]
        decoded = base64.b64decode(enc).decode("utf-16-le")
        assert "--cache-only" in decoded
        dropped = ds._drop_cache_only_arg(added)
        enc2 = dropped[2].rsplit("-EncodedCommand ", 1)[1]
        assert "--cache-only" not in base64.b64decode(enc2).decode("utf-16-le")

    def test_unsupported_detection(self):
        from agent_worktrees.picker_tui import data_ssh as ds
        assert ds._is_cache_only_unsupported(
            "error: unrecognized arguments: --cache-only")
        assert not ds._is_cache_only_unsupported("some other error")
