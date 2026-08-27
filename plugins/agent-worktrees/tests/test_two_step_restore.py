"""Tests for the two-step-restore feature (bare-resume + Reclaim + row data).

Covers the bare-resume launch plan, the ``reclaim_one`` executor, the picker
record fields, the maintenance ``reclaim`` op routing, and the remote argv --
the load-bearing pure logic behind the outage workaround.
"""

from __future__ import annotations

import argparse
import contextlib
import os

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees.picker_tui import derive, maintenance


# ── bare-resume launch plan (_resolve_resume) ──────────────────────────────
class _Rec:
    worktree_id = "wtX"
    worktree_path = "/w/wtX"
    yaml_path = "/w/wtX.yaml"
    branch = "worktree/wtX"
    resume_count = 0
    last_resumed_at = None
    sessions: list = []
    parent_session = None


def _resume_args(**kw):
    base = dict(dry_run=False, no_fast_forward=True, no_mux=False,
                no_resume=False, bare_resume=False, recovery=False,
                copilot_args=[])
    base.update(kw)
    return argparse.Namespace(**base)


def _patch_resume(monkeypatch, *, mux_live=False, live_ids=None):
    plan = {}
    monkeypatch.setattr(m, "_emit_plan", lambda p: plan.update(p))
    import types as _types
    # Foreground resume RMW-under-lock (#4547): _resolve_resume opens a
    # _RecordLock on record.yaml_path, reloads a fresh record, marks it resumed,
    # and saves it. Make that block hermetic (no real lock / filesystem): a
    # no-op lock, a load_record returning a stamped stand-in, and a no-op save.
    monkeypatch.setattr(m.tracking, "_RecordLock",
                        lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(
        m.tracking, "load_record",
        lambda p: _types.SimpleNamespace(resume_count=1,
                                         last_resumed_at="2026-01-01T00:00:00"))
    monkeypatch.setattr(m.tracking, "save_record", lambda r, path=None: None)
    monkeypatch.setattr(m.tracking, "mark_resumed", lambda r, *, save=True: None)
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(
        m, "_preflight_launch", lambda c, a, w: m.LaunchPreflight())
    monkeypatch.setattr(m, "_build_launch_cmd",
                        lambda cfg, args, wd, profile=None, **k: ["copilot"])
    monkeypatch.setattr(m, "_build_env", lambda p, s, work_dir=None: {})
    monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})
    monkeypatch.setattr(m.sessions, "find_latest_session_id_fast",
                        lambda path, sess: "sid-abc-123")
    # Head-preferred resolver falls through to the fast-latest above for the
    # plain _Rec (no resolved_head_session); pin it so the tests don't touch
    # the real filesystem.
    monkeypatch.setattr(m.sessions, "resolve_resume_target",
                        lambda rec: "sid-abc-123")
    # Execution-time liveness verdict (the "one more fresh resolve" after
    # Enter). Hermetic by default (no live mux); tests override via mux_live.
    _verdict = _types.SimpleNamespace(
        mux_live=mux_live, mux_clients=(1 if mux_live else 0),
        live_session_ids=list(live_ids or []), bare=False,
        active=bool(mux_live or live_ids), source="mux" if mux_live else "none",
    )
    monkeypatch.setattr(m.sessions, "verify_worktree_active",
                        lambda rec: _verdict)
    # Capture the cache write-back stamps (mux + bound liveness) so tests can
    # assert the fresh verdict is persisted without touching the filesystem.
    stamps: dict[str, list] = {"mux": [], "bound": []}
    monkeypatch.setattr(
        m.tracking, "stamp_mux_live",
        lambda wt, live, **kw: stamps["mux"].append((wt, live, kw)))
    monkeypatch.setattr(
        m.tracking, "stamp_bound_live",
        lambda wt, live, **kw: stamps["bound"].append((wt, live, kw)))
    plan["_stamps"] = stamps
    return plan


class TestBareResumePlan:
    def test_normal_resume_uses_worktree_cwd_and_resume(self, monkeypatch):
        plan = _patch_resume(monkeypatch)
        cfg = argparse.Namespace(auto_fast_forward=False, repo_name="test-project")
        rc = m._resolve_resume(_Rec(), cfg, _resume_args())
        assert rc == 0
        assert plan["work_dir"] == "/w/wtX"
        assert plan["worktree_id"] == "wtX"
        assert any(str(a).startswith("--resume=") for a in plan["cmd"])

    def test_normal_resume_status_path_is_worktree(self, monkeypatch):
        plan = _patch_resume(monkeypatch)
        cfg = argparse.Namespace(auto_fast_forward=False, repo_name="test-project")
        assert m._resolve_resume(_Rec(), cfg, _resume_args()) == 0
        # status bar renders from the worktree; here it equals work_dir.
        assert plan["status_path"] == "/w/wtX"

    def test_bare_resume_status_path_is_worktree_not_home(self, monkeypatch):
        """Bare resume launches in HOME, but the mux status bar must still show
        the worktree's identity + git disposition -- so status_path stays the
        worktree even as work_dir becomes HOME."""
        plan = _patch_resume(monkeypatch)
        cfg = argparse.Namespace(auto_fast_forward=False, repo_name="test-project")
        assert m._resolve_resume(_Rec(), cfg, _resume_args(bare_resume=True)) == 0
        assert plan["work_dir"] == os.path.expanduser("~")
        assert plan["status_path"] == "/w/wtX"
        assert plan["status_path"] != plan["work_dir"]

    def test_bare_resume_uses_home_and_omits_resume(self, monkeypatch):
        plan = _patch_resume(monkeypatch)
        cfg = argparse.Namespace(auto_fast_forward=False, repo_name="test-project")
        rc = m._resolve_resume(_Rec(), cfg, _resume_args(bare_resume=True))
        assert rc == 0
        # launches in HOME, not the worktree cwd
        assert plan["work_dir"] == os.path.expanduser("~")
        # mux identity is still the worktree (correct wt-<id> session name)
        assert plan["worktree_id"] == "wtX"
        # NO --resume: the operator restores with a manual /resume
        assert not any(str(a).startswith("--resume=") for a in plan["cmd"])
        assert plan["env"][m._SESSION_BIND_WORKTREE] == "wtX"
        assert plan["env"][m._SESSION_BIND_SESSION] == "sid-abc-123"
        assert plan["env"][m._SESSION_BIND_PROJECT] == "test-project"

    def test_normal_resume_has_no_scoped_session_binding(self, monkeypatch):
        plan = _patch_resume(monkeypatch)
        cfg = argparse.Namespace(auto_fast_forward=False, repo_name="test-project")
        rc = m._resolve_resume(_Rec(), cfg, _resume_args())
        assert rc == 0
        assert m._SESSION_BIND_SESSION not in plan["env"]


# ── execution-time fallback ladder (fresh re-resolve after Enter) ──────────
class TestResumeFallbackLadder:
    """Open / Resume / Bare resume re-resolve the worktree's live truth after
    Enter: a live wt-<id> mux always reattaches (never fork a live worktree),
    and the head session is the shared resume target."""

    def _cfg(self):
        return argparse.Namespace(auto_fast_forward=False, repo_name="test-project")

    def test_live_mux_forces_reattach_over_bare_resume(self, monkeypatch):
        # Bare resume was chosen, but a live mux exists -> reattach it: the
        # plan must NOT go to HOME and must NOT carry a bare-resume binding
        # (which would fork a second Copilot beside the running session).
        plan = _patch_resume(monkeypatch, mux_live=True)
        rc = m._resolve_resume(_Rec(), self._cfg(), _resume_args(bare_resume=True))
        assert rc == 0
        assert plan["work_dir"] == "/w/wtX"           # worktree, not HOME
        assert plan["no_mux"] is False                # muxed reattach path
        assert m._SESSION_BIND_SESSION not in plan["env"]

    def test_live_mux_overrides_no_mux_toggle(self, monkeypatch):
        # Open/Resume with the no-mux toggle, but a live mux exists -> reattach
        # the mux rather than launch a second detached Copilot.
        plan = _patch_resume(monkeypatch, mux_live=True)
        rc = m._resolve_resume(_Rec(), self._cfg(), _resume_args(no_mux=True))
        assert rc == 0
        assert plan["no_mux"] is False

    def test_no_live_mux_preserves_bare_resume(self, monkeypatch):
        # No live mux -> Bare resume behaves as before (HOME cwd, binding set).
        plan = _patch_resume(monkeypatch, mux_live=False)
        rc = m._resolve_resume(_Rec(), self._cfg(), _resume_args(bare_resume=True))
        assert rc == 0
        assert plan["work_dir"] == os.path.expanduser("~")
        assert plan["env"][m._SESSION_BIND_SESSION] == "sid-abc-123"

    def test_no_live_mux_preserves_no_mux_toggle(self, monkeypatch):
        plan = _patch_resume(monkeypatch, mux_live=False)
        rc = m._resolve_resume(_Rec(), self._cfg(), _resume_args(no_mux=True))
        assert rc == 0
        assert plan["no_mux"] is True

    def test_json_path_skips_ladder(self, monkeypatch):
        # Programmatic --json (ACP) launches must NOT be touched by the ladder:
        # verify_worktree_active is never consulted and no_mux is preserved even
        # if a mux happened to be live.
        called = {"verify": 0}
        plan = _patch_resume(monkeypatch, mux_live=True)

        def _spy(rec):
            called["verify"] += 1
            import types as _t
            return _t.SimpleNamespace(mux_live=True, mux_clients=1,
                                      live_session_ids=[], bare=False,
                                      active=True, source="mux")
        monkeypatch.setattr(m.sessions, "verify_worktree_active", _spy)
        rc = m._resolve_resume(_Rec(), self._cfg(),
                               _resume_args(no_mux=True, json=True))
        assert rc == 0
        assert called["verify"] == 0        # ladder gated out for --json
        assert plan["no_mux"] is True       # explicit no_mux respected

    def test_head_session_is_the_resume_target(self, monkeypatch):
        # The shared resume target comes from resolve_resume_target (head-first).
        plan = _patch_resume(monkeypatch)
        monkeypatch.setattr(m.sessions, "resolve_resume_target",
                            lambda rec: "head-sid-999")
        rc = m._resolve_resume(_Rec(), self._cfg(), _resume_args())
        assert rc == 0
        assert any(a == "--resume=head-sid-999" for a in plan["cmd"])

    def test_live_verdict_written_back_to_cache(self, monkeypatch):
        # The fresh Enter-time verdict is persisted so the NEXT paint is valid:
        # a live mux + bound Copilot -> stamp both True (so next time Stop /
        # Reclaim are offered even though this launch only had "Resume").
        plan = _patch_resume(monkeypatch, mux_live=True, live_ids=["s1"])
        rc = m._resolve_resume(_Rec(), self._cfg(), _resume_args())
        assert rc == 0
        assert plan["_stamps"]["mux"] == [("wtX", True, {"refresh": True})]
        assert plan["_stamps"]["bound"] == [("wtX", True, {"refresh": True})]

    def test_not_live_verdict_written_back_as_false(self, monkeypatch):
        # A negative verdict is persisted too (records "not live"), so a stale
        # cached True hint can't strand the row as Active next time.
        plan = _patch_resume(monkeypatch, mux_live=False, live_ids=None)
        rc = m._resolve_resume(_Rec(), self._cfg(), _resume_args())
        assert rc == 0
        assert plan["_stamps"]["mux"] == [("wtX", False, {"refresh": True})]
        assert plan["_stamps"]["bound"] == [("wtX", False, {"refresh": True})]

    def test_json_path_writes_no_cache(self, monkeypatch):
        # The programmatic --json (ACP) path is gated out of the whole ladder,
        # so it neither verifies nor writes the cache.
        plan = _patch_resume(monkeypatch, mux_live=True)
        rc = m._resolve_resume(_Rec(), self._cfg(),
                               _resume_args(json=True))
        assert rc == 0
        assert plan["_stamps"]["mux"] == []
        assert plan["_stamps"]["bound"] == []


# ── reclaim_one executor ───────────────────────────────────────────────────
class TestReclaimOne:
    @pytest.fixture(autouse=True)
    def _stub_bridge(self, monkeypatch):
        # reclaim_one now also consults the bridge-lock layer (#4272). Default it
        # to empty so the non-bridge tests stay hermetic (no real state-dir scan);
        # the bridge-union test overrides resolve_bridge_bound explicitly.
        monkeypatch.setattr(m.reclaim, "resolve_bridge_bound", lambda *a, **k: [])
        monkeypatch.setattr(m.reclaim, "clear_bridge_locks", lambda *a, **k: [])

    def test_unmuxed_only_and_self_guard(self, monkeypatch):
        me = os.getpid()
        rows = [
            {"session_id": "s1", "pid": 111, "worktree_id": "wtX", "homing": "bare"},
            {"session_id": "s2", "pid": 222, "worktree_id": "wtX", "homing": "mux"},
            {"session_id": "s3", "pid": me, "worktree_id": "wtX", "homing": "bare"},
            # A live bound Copilot whose homing could not be classified (pid
            # missing from a racing process-table snapshot): un-muxed, so it must
            # be reaped too -- the strand this fix closes.
            {"session_id": "s4", "pid": 444, "worktree_id": "wtX", "homing": "unknown"},
        ]
        monkeypatch.setattr(m.reclaim, "build_process_table", lambda: {})
        monkeypatch.setattr(m.reclaim, "resolve_bound_copilots",
                            lambda **k: list(rows))
        monkeypatch.setattr(m.reclaim, "descendants_of", lambda pid, t: set())
        monkeypatch.setattr(m.reclaim, "clear_lock_residue",
                            lambda **k: [])
        # The muxed row's wt-<id> session is live + reachable (Stop-able) -> it
        # is preserved by the reachability-aware filter (dotfiles #1447).
        monkeypatch.setattr(
            m.reclaim.sessions, "mux_status_many",
            lambda ids: {i: m.reclaim.sessions.MuxInfo(exists=True, clients=1)
                         for i in ids})
        captured = {}
        monkeypatch.setattr(
            m.reclaim, "reap_bound_copilots",
            lambda targets, **k: captured.update(t=[x["pid"] for x in targets])
            or [{"pid": t["pid"], "killed": True, "children_killed": 0}
                for t in targets])
        out = m.reclaim_one("wtX")
        # mux (222) filtered as a live, Stop-able mux; self (me) guarded; bare
        # (111) and unknown (444) both reaped as Stop-unreachable.
        assert captured["t"] == [111, 444]
        assert out["ok"] is True
        assert out["targets"] == 2

    def test_nothing_bound_is_ok(self, monkeypatch):
        monkeypatch.setattr(m.reclaim, "build_process_table", lambda: {})
        monkeypatch.setattr(m.reclaim, "resolve_bound_copilots", lambda **k: [])
        monkeypatch.setattr(m.reclaim, "clear_lock_residue", lambda **k: [])
        out = m.reclaim_one("wtX")
        assert out == {"ok": True, "worktree_id": "wtX", "targets": 0,
                       "reaped": [], "locks_cleared": [],
                       "bridge_locks_cleared": [], "mux_servers_torn_down": []}

    def test_bridge_bound_unions_and_reaps(self, monkeypatch):
        # #4272: a bare-resumed / bridge-owned session (cwd=home) is invisible to
        # resolve_bound_copilots; its bridge.lock target is unioned in (deduped by
        # pid) so Reclaim actually reaps it and clears the bridge.lock.
        monkeypatch.setattr(m.reclaim, "build_process_table", lambda: {})
        monkeypatch.setattr(m.reclaim, "resolve_bound_copilots", lambda **k: [])
        monkeypatch.setattr(
            m.reclaim, "resolve_bridge_bound",
            lambda wt, **k: [{"session_id": "sB", "pid": 777,
                              "worktree_id": wt, "homing": "bare",
                              "bridge_lock": "/s/sB/bridge.lock"}])
        monkeypatch.setattr(m.reclaim, "descendants_of", lambda pid, t: set())
        monkeypatch.setattr(m.reclaim, "clear_lock_residue", lambda **k: [])
        captured = {}
        monkeypatch.setattr(
            m.reclaim, "reap_bound_copilots",
            lambda targets, **k: captured.update(t=[x["pid"] for x in targets])
            or [{"pid": t["pid"], "killed": True, "children_killed": 0}
                for t in targets])
        cleared = {}
        monkeypatch.setattr(
            m.reclaim, "clear_bridge_locks",
            lambda wt, **k: cleared.update(force=set(k.get("force_pids") or []))
            or [{"session_id": "sB", "pid": 777, "path": "/s/sB/bridge.lock"}])
        out = m.reclaim_one("wtX")
        assert captured["t"] == [777]          # the bridge owner was reaped
        assert out["targets"] == 1
        assert out["ok"] is True
        assert 777 in cleared["force"]          # its bridge.lock is force-cleared
        assert out["bridge_locks_cleared"][0]["pid"] == 777

    def test_clears_lock_residue_after_reap(self, monkeypatch):
        # Reclaim must clear residual inuse.<pid>.lock "to the point where the
        # pid lock file is removed" -- the killed pids are passed as force_pids
        # (the OS may not have reaped them yet) and the cleared residue rides on
        # the result.
        monkeypatch.setattr(m.reclaim, "build_process_table", lambda: {})
        monkeypatch.setattr(
            m.reclaim, "resolve_bound_copilots",
            lambda **k: [{"session_id": "s1", "pid": 111,
                          "worktree_id": "wtX", "homing": "bare"}])
        monkeypatch.setattr(m.reclaim, "descendants_of", lambda pid, t: set())
        monkeypatch.setattr(
            m.reclaim, "reap_bound_copilots",
            lambda targets, **k: [{"pid": t["pid"], "killed": True,
                                   "children_killed": 0} for t in targets])
        seen = {}
        monkeypatch.setattr(
            m.reclaim, "clear_lock_residue",
            lambda **k: seen.update(k)
            or [{"session_id": "s1", "pid": 111, "path": "…/inuse.111.lock"}])
        out = m.reclaim_one("wtX")
        assert seen["worktree_id"] == "wtX"
        assert seen["force_pids"] == {111}
        assert out["locks_cleared"] == [
            {"session_id": "s1", "pid": 111, "path": "…/inuse.111.lock"}]

    def test_teardown_only_for_killed_targets(self, monkeypatch):
        # A mux-homed detached target whose Copilot FAILED to terminate
        # (killed=False) must NOT have its psmux server torn down -- the Copilot
        # is still live in it. Only actually-killed targets reach teardown.
        rows = [
            {"session_id": "s1", "pid": 111, "worktree_id": "wtX", "homing": "mux"},
            {"session_id": "s2", "pid": 222, "worktree_id": "wtX", "homing": "mux"},
        ]
        monkeypatch.setattr(m.reclaim, "build_process_table", lambda: {})
        monkeypatch.setattr(m.reclaim, "resolve_bound_copilots",
                            lambda **k: list(rows))
        monkeypatch.setattr(m.reclaim, "descendants_of", lambda pid, t: set())
        monkeypatch.setattr(m.reclaim, "clear_lock_residue", lambda **k: [])
        # Both detached (unreachable) so filter_stop_unreachable keeps them.
        monkeypatch.setattr(
            m.reclaim.sessions, "mux_status_many",
            lambda ids: {i: m.reclaim.sessions.MuxInfo(exists=False, clients=0)
                         for i in ids})
        # 111 killed, 222 failed to terminate.
        monkeypatch.setattr(
            m.reclaim, "reap_bound_copilots",
            lambda targets, **k: [
                {"pid": 111, "killed": True, "children_killed": 0},
                {"pid": 222, "killed": False, "children_killed": 0}])
        seen = {}
        monkeypatch.setattr(
            m.reclaim, "teardown_detached_mux",
            lambda targets, **k: seen.update(t=[x["pid"] for x in targets]) or [])
        out = m.reclaim_one("wtX")
        assert seen["t"] == [111]            # only the killed target
        assert out["ok"] is False            # 222 failed -> not all killed


# ── _resume_decision carries the bare-resume option ────────────────────────
class TestResumeDecisionOption:
    def _decide(self, **kw):
        import types
        from agent_worktrees.picker_tui.engine import PickerScreen
        stub = types.SimpleNamespace(src=types.SimpleNamespace(LOCAL=("m", "e")))
        rec = {"raw": {"id": "wtX"}, "id4": "wtX", "machine": "m",
               "env": "e", "title": "t"}
        return PickerScreen._resume_decision(stub, rec, **kw)

    def test_plain_resume_has_no_options(self):
        d = self._decide()
        assert "options" not in d and d["action"] == "resume"

    def test_bare_resume_sets_option(self):
        d = self._decide(bare_resume=True)
        assert d["options"]["bare_resume"] is True

    def test_no_mux_and_bare_compose(self):
        d = self._decide(no_mux=True, bare_resume=True)
        assert d["options"] == {"no_mux": True, "bare_resume": True}


# ── picker record fields (derive.norm passthrough) ─────────────────────────
class TestDeriveFields:
    def _raw(self, **kw):
        base = {
            "id": "anomalous-potato-win-20260101-abcd", "status": "active",
            "started_at": "2026-01-01T10:00:00",
        }
        base.update(kw)
        return base

    def test_surfaces_session_id_and_lock(self):
        rec = derive.norm(
            self._raw(last_session_id="sid-xyz", session_lock_live=True),
            "anomalous-potato", "win")
        assert rec["last_session_id"] == "sid-xyz"
        assert rec["session_lock_live"] is True

    def test_absent_fields_default_off(self):
        rec = derive.norm(self._raw(), "anomalous-potato", "win")
        assert rec["last_session_id"] is None
        assert rec["session_lock_live"] is False


# ── maintenance reclaim op routing ─────────────────────────────────────────
class TestMaintenanceReclaimOp:
    def test_result_ok_reads_ok(self):
        assert maintenance._result_ok("reclaim", {"ok": True}) is True
        assert maintenance._result_ok("reclaim", {"ok": False}) is False

    def test_local_task_calls_reclaim_one(self, monkeypatch):
        monkeypatch.setattr(m, "reclaim_one",
                            lambda wt, **k: {"ok": True, "worktree_id": wt})
        task = maintenance._make_task(
            "reclaim", "wtX", "anomalous-potato", "win", True,
            include_unused=False, include_conversations=False)
        assert task() == {"ok": True, "worktree_id": "wtX"}


# ── Actions-menu verb gating: Stop vs Reclaim light up when (and only when)
#    they apply (#4058 Slice 4) ──────────────────────────────────────────────
class TestSessionActionVerbs:
    """``PickerScreen._session_action_verbs`` -- the pure, record-driven gate
    for the per-row Actions menu. The invariant: a worktree holding a live bound
    lock always exposes a lifecycle verb -- Stop when muxed, else Reclaim (see
    ``_reclaimable``), so a bound-but-unclassifiable Copilot is never stranded
    ACTIVE with no verb."""

    from agent_worktrees.picker_tui.engine import PickerScreen as _PS

    def _verbs(self, **rec):
        return type(self)._PS._session_action_verbs(rec)

    def test_healthy_muxed_offers_stop_not_reclaim(self):
        # A live mux + live lock, no bare orphan -> Stop (graceful) is the verb;
        # Reclaim would no-op on the muxed session, so it must NOT appear.
        verbs = self._verbs(mux_live=True, session_lock_live=True,
                            session_bare_orphan=False, last_session_id="s1")
        assert "Stop" in verbs
        assert "Reclaim" not in verbs
        assert verbs[0] == "Open"  # live mux -> attach

    def test_bare_orphan_offers_reclaim_not_stop(self):
        # A bare (un-muxed) bound Copilot -> Reclaim; no mux for Stop to reach.
        # Resume/Bare-resume are SUPPRESSED (Resume XOR Reclaim): clear the
        # bound process first, then resume.
        verbs = self._verbs(mux_live=False, session_lock_live=True,
                            session_bare_orphan=True, last_session_id="s1")
        assert "Reclaim" in verbs
        assert "Stop" not in verbs
        assert "Resume" not in verbs and "Bare resume" not in verbs
        assert verbs[0] == "Reclaim"

    def test_bare_orphan_reclaims_even_when_lock_scan_missed_it(self):
        # #662/#1416 blind spot: the cwd-keyed lock-scan never registered the
        # bare session (session_lock_live False), but the machine-wide bare
        # scan found it -> Reclaim must still be offered.
        verbs = self._verbs(mux_live=False, session_lock_live=False,
                            session_bare_orphan=True, last_session_id="s1")
        assert "Reclaim" in verbs

    def test_unmuxed_live_lock_offers_reclaim_even_without_bare_flag(self):
        # The strand this fix closes: a live inuse lock binds a Copilot, there is
        # NO mux for Stop, and the bare scan did not flag it (homing could not be
        # classified "bare" -> "unknown"). Reclaim MUST still light up so the row
        # is never ACTIVE-with-no-verb.
        verbs = self._verbs(mux_live=False, session_lock_live=True,
                            session_bare_orphan=False, last_session_id="s1")
        assert "Reclaim" in verbs
        assert "Stop" not in verbs

    def test_unmuxed_cached_bound_live_offers_reclaim(self):
        # Even from the cached off-hot-path hint alone (session_bound_live, no
        # fresh lock verdict yet), an un-muxed bound worktree offers Reclaim.
        verbs = self._verbs(mux_live=False, session_bound_live=True,
                            session_bare_orphan=False, last_session_id="s1")
        assert "Reclaim" in verbs

    def test_unmuxed_bridge_live_offers_reclaim(self):
        # #4272 strand: a live BRIDGE-owned Copilot (bridge.lock, cwd=home) makes
        # the row ACTIVE, but there is NO mux for Stop, no bare-scan flag, and no
        # row-visible inuse lock (session_lock_live/bound_live both False). Before
        # the fix this fell through to Resume/Bare-resume -- which then FAIL
        # because the bridge Copilot already holds the session -- stranding the
        # row ACTIVE with no way to clear it. Reclaim MUST light up.
        verbs = self._verbs(mux_live=False, session_bridge_live=True,
                            session_lock_live=False, session_bound_live=False,
                            session_bare_orphan=False, last_session_id="s1")
        assert "Reclaim" in verbs
        assert "Stop" not in verbs
        assert "Resume" not in verbs and "Bare resume" not in verbs
        assert verbs[0] == "Reclaim"

    def test_muxed_plus_bare_is_warning_stop_and_repair(self):
        # The #662 double-binding (a healthy mux AND a separate stray bare
        # orphan) is an INCONSISTENT/WARNING state: Stop reaches the mux, Repair
        # reaps the stray orphan while preserving the mux. Reclaim/Open are NOT
        # offered here (Reclaim would be unsafe beside a live mux).
        verbs = self._verbs(mux_live=True, session_lock_live=True,
                            session_bare_orphan=True, last_session_id="s1")
        assert "Stop" in verbs and "Repair" in verbs
        assert "Reclaim" not in verbs
        assert "Open" not in verbs

    def test_stale_lock_no_mux_offers_reclaim(self):
        # Stale-lock residue (an inuse.<pid>.lock whose pid is dead) with no mux
        # and no live lock -> Reclaim (file-only cleanup), never stranded and
        # never falsely ACTIVE. Resume is suppressed until the residue is cleared.
        verbs = self._verbs(mux_live=False, session_lock_stale=True,
                            last_session_id="s1")
        assert "Reclaim" in verbs
        assert "Resume" not in verbs and "Stop" not in verbs

    def test_resumable_offers_resume_and_bare_resume(self):
        # No mux, no bound proc/lock/residue, has a head session, dir present ->
        # the resumable group: Resume + Bare resume (Reclaim absent).
        verbs = self._verbs(mux_live=False, last_session_id="s1")
        assert verbs[0] == "Resume"
        assert "Bare resume" in verbs
        assert "Reclaim" not in verbs and "Stop" not in verbs

    def test_gone_offers_cleanup_only(self):
        # A gone worktree (dir missing) with no live mux/lock -> no launch verbs,
        # just Cleanup (+ always Refresh) to prune the leftover record.
        verbs = self._verbs(cleanup_bucket="gone")
        assert "Open" not in verbs and "Resume" not in verbs
        assert "Reclaim" not in verbs and "Stop" not in verbs
        assert "Cleanup" in verbs and "Refresh" in verbs

    def test_sessionless_offers_neither(self):
        verbs = self._verbs(sessionless=True)
        # Refresh is always offered (picker-cache-first-paint, dotfiles#948);
        # the lifecycle set for a sessionless worktree is still just Open.
        assert [v for v in verbs if v != "Refresh"] == ["Open"]
        assert "Stop" not in verbs and "Reclaim" not in verbs

    def test_refresh_always_offered(self):
        # picker-cache-first-paint (dotfiles#948): every worktree carries a
        # Refresh verb (the only way to populate an Unknown row on demand),
        # regardless of its lifecycle state.
        assert "Refresh" in self._verbs(sessionless=True)
        assert "Refresh" in self._verbs(
            mux_live=True, session_lock_live=True, last_session_id="s1")
        assert "Refresh" in self._verbs(
            session_bare_orphan=True, last_session_id="s1")


class TestStartReclaimFilter:
    """``_start_reclaim`` filters targets on :meth:`_reclaimable` -- the same
    predicate the un-muxed executor acts on -- so it never dispatches a no-op
    reclaim (and the debug line is honest when nothing qualifies)."""

    from agent_worktrees.picker_tui.engine import PickerScreen as _PS

    def _stub(self):
        import types
        calls = []
        stub = types.SimpleNamespace(
            debug="",
            _run_op_progress=lambda *a, **k: calls.append((a, k)),
        )
        return stub, calls

    def test_bare_orphan_dispatches(self):
        stub, calls = self._stub()
        rec = {"id4": "wtX", "session_bare_orphan": True,
               "session_lock_live": True}
        type(self)._PS._start_reclaim(stub, rec)
        assert len(calls) == 1
        assert calls[0][0][:3] == ("Reclaim", "reclaim", [rec])

    def test_unmuxed_live_lock_dispatches(self):
        # Live lock, no mux, not flagged bare -> the stranded case now dispatches.
        stub, calls = self._stub()
        rec = {"id4": "wtX", "session_bare_orphan": False,
               "session_lock_live": True, "mux_live": False}
        type(self)._PS._start_reclaim(stub, rec)
        assert len(calls) == 1
        assert calls[0][0][:3] == ("Reclaim", "reclaim", [rec])

    def test_bridge_live_dispatches(self):
        # #4272: an ACTIVE-via-bridge worktree (bridge.lock live, no mux, no
        # inuse-lock signal) is reclaimable and must dispatch, so it is never
        # stranded ACTIVE with no way to act.
        stub, calls = self._stub()
        rec = {"id4": "wtX", "session_bare_orphan": False,
               "session_lock_live": False, "session_bound_live": False,
               "session_bridge_live": True, "mux_live": False}
        type(self)._PS._start_reclaim(stub, rec)
        assert len(calls) == 1
        assert calls[0][0][:3] == ("Reclaim", "reclaim", [rec])

    def test_healthy_muxed_is_no_op(self):
        # A genuine healthy muxed session (mux_live True) -> filtered out,
        # nothing dispatched, honest debug line (Stop is the verb, not Reclaim).
        stub, calls = self._stub()
        rec = {"id4": "wtX", "session_bare_orphan": False,
               "session_lock_live": True, "mux_live": True}
        type(self)._PS._start_reclaim(stub, rec)
        assert calls == []
        assert "no Stop-unreachable bound" in stub.debug


class TestStartRepairFilter:
    """``_start_repair`` filters on :meth:`_warning` (mux + stray bare orphan)
    and drives the bare-only ``reclaim`` op -- reaping the stray orphan while
    the positively mux-homed session is left running (PRESERVED)."""

    from agent_worktrees.picker_tui.engine import PickerScreen as _PS

    def _stub(self):
        import types
        calls = []
        stub = types.SimpleNamespace(
            debug="",
            _run_op_progress=lambda *a, **k: calls.append((a, k)),
        )
        return stub, calls

    def test_warning_dispatches_reclaim_op(self):
        stub, calls = self._stub()
        rec = {"id4": "wtX", "mux_live": True, "session_bare_orphan": True}
        type(self)._PS._start_repair(stub, rec)
        assert len(calls) == 1
        # Repair reuses the bare-only ``reclaim`` op (reap the stray orphan,
        # preserve the mux) -- the verb label differs, the op does not.
        assert calls[0][0][:3] == ("Repair", "reclaim", [rec])

    def test_non_warning_is_no_op(self):
        # A cleanly muxed session (no stray orphan) is not a warning -> nothing
        # to repair, honest debug line.
        stub, calls = self._stub()
        rec = {"id4": "wtX", "mux_live": True, "session_bare_orphan": False}
        type(self)._PS._start_repair(stub, rec)
        assert calls == []
        assert "no stray orphan" in stub.debug


class TestClearLockResidue:
    """``reclaim.clear_lock_residue`` -- removes inuse.<pid>.lock residue for a
    worktree's session dirs, keeping a live muxed sibling's lock intact."""

    def _session_dir(self, tmp_path, monkeypatch, sid, *, cwd, pids):
        import agent_worktrees.reclaim as rc
        from agent_worktrees import sessions
        state = tmp_path / "session-state"
        sdir = state / sid
        sdir.mkdir(parents=True, exist_ok=True)
        for pid in pids:
            (sdir / f"inuse.{pid}.lock").write_text("x", encoding="utf-8")
        monkeypatch.setattr(sessions, "_session_state_dir", lambda: state)
        # Attribute the session dir to worktree "wtX" via its recorded cwd.
        monkeypatch.setattr(rc, "_session_cwd", lambda entry: cwd)
        monkeypatch.setattr(rc, "_resolve_worktree_id_for_cwd",
                            lambda cwd: "wtX" if cwd else None)
        return rc, sdir

    def test_removes_stale_and_forced_keeps_live(self, tmp_path, monkeypatch):
        from agent_worktrees import sessions
        rc, sdir = self._session_dir(
            tmp_path, monkeypatch, "s1", cwd="/w/wtX", pids=[111, 222, 333])
        # 111 = a pid we just killed (force-remove); 222 = a live muxed sibling
        # (keep); 333 = stale/dead (remove).
        monkeypatch.setattr(
            sessions, "_is_process_alive", lambda pid: pid in (111, 222))
        monkeypatch.setattr(
            sessions, "_is_copilot_process", lambda pid: pid in (111, 222))
        removed = rc.clear_lock_residue(worktree_id="wtX", force_pids={111})
        removed_pids = sorted(r["pid"] for r in removed)
        assert removed_pids == [111, 333]
        # The live muxed sibling's lock is preserved.
        assert (sdir / "inuse.222.lock").exists()
        assert not (sdir / "inuse.111.lock").exists()
        assert not (sdir / "inuse.333.lock").exists()

    def test_skips_unrelated_worktree(self, tmp_path, monkeypatch):
        from agent_worktrees import sessions
        rc, sdir = self._session_dir(
            tmp_path, monkeypatch, "s1", cwd="/w/wtX", pids=[111])
        monkeypatch.setattr(sessions, "_is_process_alive", lambda pid: False)
        monkeypatch.setattr(sessions, "_is_copilot_process", lambda pid: False)
        # Ask for a DIFFERENT worktree -> no match, nothing removed.
        removed = rc.clear_lock_residue(worktree_id="other")
        assert removed == []
        assert (sdir / "inuse.111.lock").exists()
