"""Tests for the two-step-restore feature (bare-resume + Reclaim + row data).

Covers the bare-resume launch plan, the ``reclaim_one`` executor, the picker
record fields, the maintenance ``reclaim`` op routing, and the remote argv --
the load-bearing pure logic behind the outage workaround.
"""

from __future__ import annotations

import argparse
import os

from agent_worktrees import __main__ as m
from agent_worktrees.picker_tui import derive, maintenance


# ── bare-resume launch plan (_resolve_resume) ──────────────────────────────
class _Rec:
    worktree_id = "wtX"
    worktree_path = "/w/wtX"
    branch = "worktree/wtX"
    resume_count = 0
    sessions: list = []
    parent_session = None


def _resume_args(**kw):
    base = dict(dry_run=False, no_fast_forward=True, no_mux=False,
                no_resume=False, bare_resume=False, recovery=False,
                copilot_args=[])
    base.update(kw)
    return argparse.Namespace(**base)


def _patch_resume(monkeypatch):
    plan = {}
    monkeypatch.setattr(m, "_emit_plan", lambda p: plan.update(p))
    monkeypatch.setattr(m.tracking, "mark_resumed", lambda r: None)
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(m, "_build_launch_cmd",
                        lambda cfg, args, wd, profile=None: ["copilot"])
    monkeypatch.setattr(m, "_build_env", lambda p, s: {})
    monkeypatch.setattr(m, "_repo_session_env", lambda c, w: {})
    monkeypatch.setattr(m.sessions, "find_latest_session_id_fast",
                        lambda path, sess: "sid-abc-123")
    return plan


class TestBareResumePlan:
    def test_normal_resume_uses_worktree_cwd_and_resume(self, monkeypatch):
        plan = _patch_resume(monkeypatch)
        cfg = argparse.Namespace(auto_fast_forward=False)
        rc = m._resolve_resume(_Rec(), cfg, _resume_args())
        assert rc == 0
        assert plan["work_dir"] == "/w/wtX"
        assert plan["worktree_id"] == "wtX"
        assert any(str(a).startswith("--resume=") for a in plan["cmd"])

    def test_bare_resume_uses_home_and_omits_resume(self, monkeypatch):
        plan = _patch_resume(monkeypatch)
        cfg = argparse.Namespace(auto_fast_forward=False)
        rc = m._resolve_resume(_Rec(), cfg, _resume_args(bare_resume=True))
        assert rc == 0
        # launches in HOME, not the worktree cwd
        assert plan["work_dir"] == os.path.expanduser("~")
        # mux identity is still the worktree (correct wt-<id> session name)
        assert plan["worktree_id"] == "wtX"
        # NO --resume: the operator restores with a manual /resume
        assert not any(str(a).startswith("--resume=") for a in plan["cmd"])


# ── reclaim_one executor ───────────────────────────────────────────────────
class TestReclaimOne:
    def test_bare_only_and_self_guard(self, monkeypatch):
        me = os.getpid()
        rows = [
            {"session_id": "s1", "pid": 111, "worktree_id": "wtX", "homing": "bare"},
            {"session_id": "s2", "pid": 222, "worktree_id": "wtX", "homing": "mux"},
            {"session_id": "s3", "pid": me, "worktree_id": "wtX", "homing": "bare"},
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
        # mux (222) filtered by bare_only; self (me) guarded; only 111 reaped
        assert captured["t"] == [111]
        assert out["ok"] is True
        assert out["targets"] == 1

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
