"""Post-action / Refresh liveness reconcile (dotfiles: "Reclaim ran but the row
stayed ACTIVE").

Three seams keep a worktree's cached liveness honest so a completed picker action
-- or an explicit Refresh -- re-renders the TRUE state instead of re-reading a
stale ``bound_live``/``mux_live`` hint (which is trusted for its full 10-min TTL):

* Fix A -- ``reclaim_one`` stamps its OWN post-reap liveness (the executor knows
  what it killed), race-safe by excluding just-terminated pids.
* Fix B -- ``_refresh_after_maint`` kicks the authoritative ``reconcile_bound_live``
  sweep as part of the automatic post-action reload.
* Fix C -- ``refresh_one`` resolves live bound/mux truth and writes it onto the
  record BEFORE the row is derived, so a manual Refresh clears a stale-ACTIVE row
  even inside the hint's TTL window.
"""

from __future__ import annotations

import types

from agent_worktrees import __main__ as cli
from agent_worktrees.picker_tui import data_local
from agent_worktrees.picker_tui.engine import PickerScreen

# ── Fix A: reclaim_one stamps its own post-reap liveness ────────────────────

class TestReclaimOneStampsLiveness:
    def _patch_common(self, monkeypatch, *, remaining, mux_present):
        """Patch reclaim_one's collaborators. ``remaining`` is what the POST-reap
        re-resolve returns (excluding killed pids); ``mux_present`` drives the
        post-reclaim mux stamp."""
        monkeypatch.setattr(cli.reclaim, "build_process_table", lambda: {})
        # First resolve = the reap target (one bare pid); second = post-reap.
        monkeypatch.setattr(
            cli.reclaim, "resolve_bound_copilots",
            _seq([
                [{"session_id": "s1", "pid": 111,
                  "worktree_id": "wtX", "homing": "bare"}],
                list(remaining),
            ]))
        monkeypatch.setattr(cli.reclaim, "resolve_bridge_bound",
                            lambda *a, **k: [])
        monkeypatch.setattr(cli.reclaim, "filter_stop_unreachable",
                            lambda found, **k: found)
        monkeypatch.setattr(cli.reclaim, "descendants_of", lambda pid, t: set())
        monkeypatch.setattr(
            cli.reclaim, "reap_bound_copilots",
            lambda targets, **k: [{"pid": t["pid"], "killed": True,
                                   "children_killed": 0} for t in targets])
        monkeypatch.setattr(cli.reclaim, "clear_lock_residue", lambda **k: [])
        monkeypatch.setattr(cli.reclaim, "clear_bridge_locks", lambda *a, **k: [])
        monkeypatch.setattr(cli.reclaim, "teardown_detached_mux",
                            lambda *a, **k: [])
        monkeypatch.setattr(cli.sessions, "has_mux_session",
                            lambda wt: mux_present)
        stamps: dict = {}
        monkeypatch.setattr(
            cli.tracking, "stamp_bound_live",
            lambda wt, live, **k: stamps.__setitem__("bound", (wt, live)))
        monkeypatch.setattr(
            cli.tracking, "stamp_mux_live",
            lambda wt, live, **k: stamps.__setitem__("mux", (wt, live)))
        return stamps

    def test_clears_bound_and_mux_when_nothing_remains(self, monkeypatch):
        # Reaped the only bound Copilot, no mux left -> stamp both False so the
        # auto-refresh renders the worktree as stopped/resumable, not ACTIVE.
        stamps = self._patch_common(monkeypatch, remaining=[], mux_present=False)
        out = cli.reclaim_one("wtX")
        assert out["ok"] is True and out["targets"] == 1
        assert stamps["bound"] == ("wtX", False)
        assert stamps["mux"] == ("wtX", False)

    def test_keeps_bound_true_when_live_mux_sibling_preserved(self, monkeypatch):
        # bare_only leaves a live, Stop-able mux-homed sibling: it still counts
        # as bound, so bound_live must NOT be cleared (Stop, not Reclaim, ends
        # it). The re-resolve returns that surviving mux binding.
        stamps = self._patch_common(
            monkeypatch,
            remaining=[{"session_id": "s2", "pid": 222,
                        "worktree_id": "wtX", "homing": "mux"}],
            mux_present=True)
        cli.reclaim_one("wtX")
        assert stamps["bound"] == ("wtX", True)
        assert stamps["mux"] == ("wtX", True)

    def test_excludes_just_killed_pid_from_remaining(self, monkeypatch):
        # Race guard: a pid we just terminated may not be OS-reaped yet, so if
        # the post-reap re-resolve still lists it, it must be excluded (pid 111
        # was the reaped target) -> bound clears to False.
        stamps = self._patch_common(
            monkeypatch,
            remaining=[{"session_id": "s1", "pid": 111,
                        "worktree_id": "wtX", "homing": "bare"}],
            mux_present=False)
        cli.reclaim_one("wtX")
        assert stamps["bound"] == ("wtX", False)


# ── Fix B: _refresh_after_maint kicks the liveness reconcile ────────────────

class TestRefreshAfterMaintReconciles:
    def _stub(self, *, live, targets_local):
        kicked = []
        local = ("m", "e")
        recs = [{"machine": "m", "env": "e", "id4": "x"}] if targets_local \
            else [{"machine": "remote", "env": "e", "id4": "y"}]
        src = types.SimpleNamespace(
            LOCAL=local,
            load=lambda: [],
            reconcile_bound_live=lambda: 0,
        )
        stub = types.SimpleNamespace(
            live=live,
            loader=(types.SimpleNamespace(
                reload=lambda m, e: None, records=lambda: []) if live else None),
            src=src,
            data=[],
            _wt_reconcile_after=None,
            _reconcile_wt_sel=lambda: None,
            _rehome_l_focus=lambda: None,
            _start_bound_live_reconcile=lambda fn: kicked.append(fn),
        )
        return stub, kicked, recs

    def test_nonlive_always_reconciles(self):
        stub, kicked, recs = self._stub(live=False, targets_local=True)
        PickerScreen._refresh_after_maint(stub, {"recs": recs})
        assert len(kicked) == 1  # the reconcile sweep was kicked

    def test_live_local_target_reconciles(self):
        stub, kicked, recs = self._stub(live=True, targets_local=True)
        PickerScreen._refresh_after_maint(stub, {"recs": recs})
        assert len(kicked) == 1

    def test_live_remote_only_does_not_reconcile_local(self):
        # A remote-only action leaves the local process scan irrelevant -- the
        # local reconcile is not kicked (the remote reconciles on its own box).
        stub, kicked, recs = self._stub(live=True, targets_local=False)
        PickerScreen._refresh_after_maint(stub, {"recs": recs})
        assert kicked == []


# ── Fix C: refresh_one corrects the record's liveness before the row build ──

class TestRefreshOneAuthoritativeLiveness:
    def _run(self, tmp_path, monkeypatch, *, bound, mux_exists,
             stale_bound_live):
        """Drive refresh_one with a real on-disk record carrying a stale-fresh
        ``bound_live`` hint, mocking the live scans + heavy downstream so we can
        assert the record's in-memory liveness was corrected BEFORE the row was
        built (which is what makes a Refresh clear a stale-ACTIVE row)."""
        from agent_worktrees import tracking
        wt_dir = tmp_path / "wt"
        (wt_dir / ".git").mkdir(parents=True)
        rec = tracking.WorktreeRecord(
            worktree_id="wtX", branch="worktree/wtX",
            worktree_path=str(wt_dir), repo="o/r", machine="m",
            platform="wsl", started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0,
            title=None, status="active", completed_at=None,
            sessions=[], prs=[], kind="session",
            bound_live=stale_bound_live,
            bound_live_at="2026-06-01T10:00:00",
        )
        tracking.save_record(rec, tmp_path / "wtX.yaml")
        monkeypatch.setattr(data_local.cfg, "tracking_dir", lambda: tmp_path)
        monkeypatch.setattr(
            data_local.cfg, "load_config",
            lambda: types.SimpleNamespace(default_repo=types.SimpleNamespace(
                remote="origin", default_branch="master")))
        monkeypatch.setattr(data_local.sessions, "scan_sessions_fast",
                            lambda recs: types.SimpleNamespace(
                                latest_summary={}, active_sessions={}))
        monkeypatch.setattr(data_local.reclaim, "bare_orphan_worktree_ids",
                            lambda: set())
        monkeypatch.setattr(data_local.reclaim, "live_bridge_worktrees",
                            lambda: set())
        monkeypatch.setattr(
            data_local.sessions, "mux_status_many",
            lambda ids: {"wtX": data_local.sessions.MuxInfo(
                exists=mux_exists, clients=1 if mux_exists else 0)})
        monkeypatch.setattr(data_local.reclaim, "resolve_bound_copilots",
                            lambda **k: list(bound))
        monkeypatch.setattr(data_local.reclaim, "resolve_bridge_bound",
                            lambda *a, **k: [], raising=False)
        stamps: dict = {}
        monkeypatch.setattr(
            data_local.tracking, "stamp_bound_live",
            lambda wt, live, **k: stamps.__setitem__("bound", live))
        monkeypatch.setattr(
            data_local.tracking, "stamp_mux_live",
            lambda wt, live, **k: stamps.__setitem__("mux", live))
        # Capture the record's liveness AT ROW-BUILD time (proves the correction
        # happens BEFORE the derivation reads it). Stub the heavy downstream.
        seen: dict = {}
        monkeypatch.setattr(cli, "_build_active_paths", lambda *a, **k: set())
        monkeypatch.setattr(cli, "_classify_one_record", lambda *a, **k: None)

        def _fake_to_dict(rec_arg, **k):
            seen["bound_live"] = rec_arg.bound_live
            seen["mux_live"] = rec_arg.mux_live
            return {"id": "wtX", "status": "active",
                    "started_at": "2026-06-01T10:00:00"}
        monkeypatch.setattr(cli, "_worktree_to_dict", _fake_to_dict)
        monkeypatch.setattr(data_local, "_stamp_from_raw",
                            lambda *a, **k: None)
        data_local.refresh_one("wtX", "m", "e")
        return stamps, seen

    def test_stale_true_cleared_when_nothing_bound(self, tmp_path, monkeypatch):
        # The operator's case: cache says bound_live=True (still fresh), but the
        # live resolve finds nothing bound and no mux -> refresh corrects the
        # record to False (persisted AND in-memory) so the row is not ACTIVE.
        stamps, seen = self._run(
            tmp_path, monkeypatch, bound=[], mux_exists=False,
            stale_bound_live=True)
        assert stamps["bound"] is False
        assert stamps["mux"] is False
        assert seen["bound_live"] is False   # corrected before the row build
        assert seen["mux_live"] is False

    def test_live_bound_marked_true(self, tmp_path, monkeypatch):
        stamps, seen = self._run(
            tmp_path, monkeypatch,
            bound=[{"session_id": "s", "pid": 9, "worktree_id": "wtX",
                    "homing": "bare"}],
            mux_exists=False, stale_bound_live=None)
        assert stamps["bound"] is True
        assert seen["bound_live"] is True

    def test_live_mux_marked_true(self, tmp_path, monkeypatch):
        stamps, seen = self._run(
            tmp_path, monkeypatch, bound=[], mux_exists=True,
            stale_bound_live=None)
        assert stamps["mux"] is True
        assert seen["mux_live"] is True


def _seq(returns):
    """A side_effect that returns each item in ``returns`` on successive calls."""
    it = iter(returns)

    def _next(*a, **k):
        return next(it)
    return _next
