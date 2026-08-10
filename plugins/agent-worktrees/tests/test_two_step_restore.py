"""Tests for the two-step-restore feature (bare-resume + Reclaim + row data).

Covers the bare-resume launch plan, the ``reclaim_one`` executor, the picker
record fields, the maintenance ``reclaim`` op routing, and the remote argv --
the load-bearing pure logic behind the outage workaround.
"""

from __future__ import annotations

import argparse
import contextlib
import os

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
    monkeypatch.setattr(m, "_build_launch_cmd",
                        lambda cfg, args, wd, profile=None: ["copilot"])
    monkeypatch.setattr(m, "_build_env", lambda p, s: {})
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
        captured = {}
        monkeypatch.setattr(
            m.reclaim, "reap_bound_copilots",
            lambda targets, **k: captured.update(t=[x["pid"] for x in targets])
            or [{"pid": t["pid"], "killed": True, "children_killed": 0}
                for t in targets])
        out = m.reclaim_one("wtX")
        # mux (222) filtered as positively muxed; self (me) guarded; bare (111)
        # and unknown (444) both reaped as un-muxed.
        assert captured["t"] == [111, 444]
        assert out["ok"] is True
        assert out["targets"] == 2

    def test_nothing_bound_is_ok(self, monkeypatch):
        monkeypatch.setattr(m.reclaim, "build_process_table", lambda: {})
        monkeypatch.setattr(m.reclaim, "resolve_bound_copilots", lambda **k: [])
        out = m.reclaim_one("wtX")
        assert out == {"ok": True, "worktree_id": "wtX", "targets": 0, "reaped": []}


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
            "id": "lambda-core-win-20260101-abcd", "status": "active",
            "started_at": "2026-01-01T10:00:00",
        }
        base.update(kw)
        return base

    def test_surfaces_session_id_and_lock(self):
        rec = derive.norm(
            self._raw(last_session_id="sid-xyz", session_lock_live=True),
            "lambda-core", "win")
        assert rec["last_session_id"] == "sid-xyz"
        assert rec["session_lock_live"] is True

    def test_absent_fields_default_off(self):
        rec = derive.norm(self._raw(), "lambda-core", "win")
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
            "reclaim", "wtX", "lambda-core", "win", True,
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
        verbs = self._verbs(mux_live=False, session_lock_live=True,
                            session_bare_orphan=True, last_session_id="s1")
        assert "Reclaim" in verbs
        assert "Stop" not in verbs
        assert verbs[0] == "Resume"  # stopped mux, history -> resume

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

    def test_muxed_plus_bare_offers_both(self):
        verbs = self._verbs(mux_live=True, session_lock_live=True,
                            session_bare_orphan=True, last_session_id="s1")
        assert "Stop" in verbs and "Reclaim" in verbs

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

    def test_healthy_muxed_is_no_op(self):
        # A genuine healthy muxed session (mux_live True) -> filtered out,
        # nothing dispatched, honest debug line (Stop is the verb, not Reclaim).
        stub, calls = self._stub()
        rec = {"id4": "wtX", "session_bare_orphan": False,
               "session_lock_live": True, "mux_live": True}
        type(self)._PS._start_reclaim(stub, rec)
        assert calls == []
        assert "no un-muxed bound" in stub.debug
