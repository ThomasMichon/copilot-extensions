"""Tests for the landed Picker cache/read seams.

Covers the explicit engine-client read paths the transplanted local source now
uses (cache-only list, classified list, and per-row refresh).
"""
from __future__ import annotations

import types

from worktree_manager.production_picker.picker_tui import data_local, derive


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
        raw: dict = {"session_lock_live": True}
        data_local._overlay_cached_state(raw, _rec())  # no cache at all
        assert raw["state"] == "active"
        assert raw["session_lock_live"] is True
        assert derive._state(raw) == "ACTIVE"

    def test_live_lock_beats_cached_terminal(self):
        raw: dict = {"session_lock_live": True}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=9, git_state="completed"))
        assert raw["state"] == "active"

    def test_no_live_signal_keeps_cached_state(self):
        raw: dict = {}
        data_local._overlay_cached_state(
            raw, _rec(session_turns=2, git_state="wip"))
        assert raw["state"] == "wip"

    def test_stale_lock_is_visible_without_forcing_active(self):
        raw: dict = {
            "id": "stale-lock",
            "session_lock_stale": True,
            "stale_lock_pids": [999],
        }
        data_local._overlay_cached_state(
            raw, _rec(session_turns=9, git_state="completed"))
        assert raw["state"] == "completed"
        assert raw["session_lock_stale"] is True
        assert raw["stale_lock_pids"] == [999]
        normalized = derive.norm(raw, "m", "win")
        # No ``closure`` descriptor in ``raw`` here, so ``_state()`` degrades
        # to MERGED rather than trusting a raw FINAL claim
        # (worktree-finality-and-obligations Phase 5 fix).
        assert normalized["state"] == "MERGED"
        assert normalized["sess"] == "LOCK"
class TestRefreshOneGuard:

    def test_missing_record_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            data_local.engine_client,
            "list_worktree_rows",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(data_local.context, "project", lambda: "example")
        assert data_local.refresh_one("no-such-wt") is None


def test_local_load_uses_cache_only_then_classified_provider_reads(monkeypatch):
    calls = []
    monkeypatch.setattr(data_local.context, "project", lambda: "example")
    monkeypatch.setattr(
        data_local.engine_group_c,
        "picker_reconcile_local",
        lambda project, **_kwargs: calls.append(("batch", project)) or types.SimpleNamespace(
            rows=[], summary={}
        ),
    )
    monkeypatch.setattr(
        data_local.engine_client,
        "list_worktree_rows",
        lambda *args, **kwargs: calls.append(("list", args, kwargs)) or [{"id": "wt-a"}],
    )

    fast = data_local.load("machine", "Win", classify=False)
    full = data_local.load("machine", "Win", classify=True)

    assert fast[0]["raw"]["id"] == "wt-a"
    assert full[0]["raw"]["id"] == "wt-a"
    assert calls == [
        ("list", ("example",), {
            "classify": False,
            "mux_details": False,
            "cache_only": True,
            "runner": None,
        }),
        ("batch", "example"),
        ("list", ("example",), {
            "classify": True,
            "mux_details": True,
            "cache_only": False,
            "runner": None,
        }),
    ]


def test_local_classify_load_invokes_group_c_batch_once(monkeypatch):
    calls = []
    runner = object()
    monkeypatch.setattr(data_local.context, "project", lambda: "example")
    monkeypatch.setattr(
        data_local.engine_client,
        "list_worktree_rows",
        lambda *_args, **kwargs: (
            calls.append(("list", kwargs.get("runner")))
            or [
            {"id": "wt-a", "state": "wip"},
            {"id": "wt-b", "state": "completed"},
            {"id": "wt-c", "state": "unknown"},
        ]),
    )

    def fake_batch(project, *, worktree_ids=None, timeout=None, runner=None):
        calls.append((project, tuple(worktree_ids or ()), runner))
        return type(
            "Batch",
            (),
            {
                "rows": [
                    {"id": "wt-a", "session_bound_live": True, "mux_session": True, "mux_attached": True, "mux_clients": 2},
                    {"id": "wt-b", "pr": None, "prs": [], "pr_count": 0},
                    {"id": "wt-c", "session_lock_stale": True, "stale_lock_pids": [321]},
                ],
                "summary": {},
            },
        )()

    monkeypatch.setattr(data_local.engine_group_c, "picker_reconcile_local", fake_batch)

    rows = data_local.load("machine", "Win", classify=True, runner=runner)

    assert calls == [("example", (), runner), ("list", runner)]
    assert len(rows) == 3
    assert rows[0]["state"] == "ACTIVE"
    assert rows[0]["mux_live"] is True
    assert rows[2]["session_lock_stale"] is True


def test_group_c_reconcile_preserves_existing_mux_fields_when_scan_unknown(monkeypatch):
    monkeypatch.setattr(data_local.context, "project", lambda: "example")
    monkeypatch.setattr(
        data_local.engine_client,
        "list_worktree_rows",
        lambda *_args, **_kwargs: [
            {
                "id": "wt-a",
                "state": "wip",
                "mux_session": True,
                "mux_attached": True,
                "mux_clients": 2,
            }
        ],
    )
    monkeypatch.setattr(
        data_local.engine_group_c,
        "picker_reconcile_local",
        lambda *_args, **_kwargs: type(
            "Batch",
            (),
            {
                "rows": [{"id": "wt-a", "session_bound_live": True}],
                "summary": {"mux_scan_ok": False},
            },
        )(),
    )

    row = data_local.load("machine", "Win", classify=True)[0]

    assert row["mux_live"] is True
    assert row["attached"] is True


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
        assert sessions.worktree_session_lock_state(
            self._rec_with_sessions("sess-B")) == (False, [999])

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


def test_live_process_signals_override_explicit_wip_or_final_state():
    for signal in (
        "session_lock_live",
        "session_bound_live",
        "session_bridge_live",
        "session_bare_orphan",
    ):
        for state in ("wip", "completed"):
            row = derive.norm(
                {"id": state, "state": state, signal: True},
                "m", "win",
            )
            assert row["state"] == "ACTIVE"
            assert row["sess"] == "PROC"



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
        from worktree_manager.production_picker.picker_tui import data_ssh as ds
        argv = ["ssh", "host", "bash -lc 'proj list --json --mux-details'"]
        added = ds._add_cache_only_arg(argv)
        assert "--cache-only" in added[2]
        # Idempotent.
        assert ds._add_cache_only_arg(added) == added
        # Round-trips back out.
        assert "--cache-only" not in ds._drop_cache_only_arg(added)[2]

    def test_add_and_drop_pwsh_encoded(self):
        import base64

        from worktree_manager.production_picker.picker_tui import data_ssh as ds
        argv = ["ssh", "host", ds._pwsh_remote("proj list --json --mux-details")]
        added = ds._add_cache_only_arg(argv)
        enc = added[2].rsplit("-EncodedCommand ", 1)[1]
        decoded = base64.b64decode(enc).decode("utf-16-le")
        assert "--cache-only" in decoded
        dropped = ds._drop_cache_only_arg(added)
        enc2 = dropped[2].rsplit("-EncodedCommand ", 1)[1]
        assert "--cache-only" not in base64.b64decode(enc2).decode("utf-16-le")

    def test_unsupported_detection(self):
        from worktree_manager.production_picker.picker_tui import data_ssh as ds
        assert ds._is_cache_only_unsupported(
            "error: unrecognized arguments: --cache-only")
        assert not ds._is_cache_only_unsupported("some other error")
