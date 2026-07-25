"""Tests for liveness garbage collection -- the lease -> GC recovery model.

Covers the identity-keyed tri-state resolver (`tracking.liveness_verdict`), the
fenced `TaskQueue.reconcile_liveness` GC pass (owner-session identity, not
worktree occupancy), the dead-letter cap, and the `backlog_health` buildup
signal broken out by liveness.
"""

from __future__ import annotations

import subprocess

import pytest

from agent_dispatch import tracking
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


class _Proc:
    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


def _fake_run(returncode=0, stdout="", raises=None):
    def run(*_a, **_k):
        if raises is not None:
            raise raises
        return _Proc(returncode, stdout)
    return run


def _bridge_ok(monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: "/usr/bin/agent-bridge")


def _claim_and_start(q, task_id, *, wt, session, now):
    """Claim + start a task so it carries a captured owner_session_id."""
    q.claim_one(f"m/{wt}", machine="m", worktree=wt, task_id=task_id, now=now)
    q.start(task_id, f"m/{wt}", owner_session_id=session, now=now + 1)


# -- tri-state, identity-keyed liveness_verdict ------------------------------


def test_verdict_live_when_current_session_matches_owner(monkeypatch):
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(tracking.subprocess, "run", _fake_run(0, '{"session_id": "S1"}'))
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.LIVE


def test_verdict_gone_when_worktree_reused_by_different_session(monkeypatch):
    # A DIFFERENT session now occupies the worktree -> our owner is gone (the
    # reused-worktree false-negative the design closes).
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(tracking.subprocess, "run", _fake_run(0, '{"session_id": "S2"}'))
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.GONE


def test_verdict_gone_when_worktree_empty(monkeypatch):
    # `{}` (CLI 404): no session occupies the worktree at all -> owner gone.
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(tracking.subprocess, "run", _fake_run(0, "{}"))
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.GONE


def test_verdict_unknown_when_owner_identity_not_captured(monkeypatch):
    # Even with a live session present, no captured owner identity means we can't
    # attribute it -> unknown (the claim-before-registration false-positive guard).
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(tracking.subprocess, "run", _fake_run(0, '{"session_id": "S1"}'))
    assert tracking.liveness_verdict("wt", owner_session_id=None) == tracking.UNKNOWN
    monkeypatch.setattr(tracking.subprocess, "run", _fake_run(0, "{}"))
    assert tracking.liveness_verdict("wt", owner_session_id=None) == tracking.UNKNOWN


@pytest.mark.parametrize(
    "run",
    [
        _fake_run(1, "{}"),                 # non-zero exit -> bridge errored
        _fake_run(0, ""),                    # exit 0 but silent -> ambiguous
        _fake_run(0, "not json"),            # unparseable
        _fake_run(0, "[]"),                  # valid JSON but not an object
        _fake_run(raises=OSError("boom")),   # cannot spawn
        _fake_run(raises=subprocess.TimeoutExpired("cmd", 3)),
    ],
)
def test_verdict_unknown_on_any_resolver_failure(monkeypatch, run):
    _bridge_ok(monkeypatch)
    monkeypatch.setattr(tracking.subprocess, "run", run)
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.UNKNOWN


def test_verdict_unknown_when_bridge_absent(monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: None)
    assert tracking.liveness_verdict("wt", owner_session_id="S1") == tracking.UNKNOWN


def test_verdict_unknown_on_empty_worktree_handle():
    assert tracking.liveness_verdict("", owner_session_id="S1") == tracking.UNKNOWN
    assert tracking.liveness_verdict(None, owner_session_id="S1") == tracking.UNKNOWN


# -- reconcile_liveness: fenced, identity-keyed GC ---------------------------


def test_reconcile_requeues_only_gone_by_identity(q):
    a = q.create("a", now=1000.0)
    b = q.create("b", now=1001.0)
    _claim_and_start(q, a.id, wt="wtA", session="SA", now=1002.0)
    _claim_and_start(q, b.id, wt="wtB", session="SB", now=1003.0)

    def resolver(wt, mc, sid):
        return {"wtA": "gone", "wtB": "live"}[wt]

    counts = q.reconcile_liveness(resolver, now=5000.0)
    assert counts["checked"] == 2
    assert counts["gone"] == 1 and counts["live"] == 1
    assert counts["requeued"] == 1 and counts["dead_lettered"] == 0
    assert q.get(a.id).status == Status.QUEUED
    assert q.get(a.id).owner is None and q.get(a.id).owner_session_id is None
    assert q.get(b.id).status == Status.STARTED  # a live owner is never disturbed


def test_reconcile_unknown_never_requeues(q):
    t = q.create("x", now=1000.0)
    _claim_and_start(q, t.id, wt="wt", session="S1", now=1001.0)
    counts = q.reconcile_liveness(lambda wt, mc, sid: "unknown", now=1_000_000.0)
    assert counts["requeued"] == 0
    assert q.get(t.id).status == Status.STARTED


def test_reconcile_persists_last_liveness_for_metric(q):
    t = q.create("x", now=1000.0)
    _claim_and_start(q, t.id, wt="wt", session="S1", now=1001.0)
    q.reconcile_liveness(lambda wt, mc, sid: "live", now=1010.0)
    assert q.get(t.id).last_liveness == "live"


def test_reconcile_fence_no_ops_when_owner_reclaimed_midpass(q):
    """If the task is re-claimed (new generation) between the probe and the write,
    the fenced requeue must no-op -- no clobber of the new owner."""
    t = q.create("x", now=1000.0)
    _claim_and_start(q, t.id, wt="wt", session="S1", now=1001.0)
    gen_before = q.get(t.id).generation

    # A resolver that, after being asked, requeues + reclaims the task under a new
    # generation -- simulating the probe->write race window.
    def racing_resolver(wt, mc, sid):
        q.reconcile_liveness(lambda *a: "gone", now=1002.0)  # requeue under S1...
        q.claim_one("m/wt2", machine="m", worktree="wt2", task_id=t.id, now=1003.0)  # ...reclaimed
        return "gone"

    counts = q.reconcile_liveness(racing_resolver, now=1004.0)
    assert counts["requeued"] == 0  # outer write fenced out by the generation change
    assert q.get(t.id).generation != gen_before


def test_reconcile_dead_letters_past_attempt_cap(q):
    t = q.create("poison", now=1000.0)
    for i in range(10):
        st = q.get(t.id)
        if st.status == Status.DEAD_LETTER:
            break
        if st.status in (Status.QUEUED, Status.PROPOSED):
            _claim_and_start(q, t.id, wt="wt", session="S1", now=1100.0 + i)
        q.reconcile_liveness(lambda wt, mc, sid: "gone", max_attempts=3, now=1200.0 + i)
    assert q.get(t.id).status == Status.DEAD_LETTER


def test_reconcile_ignores_unheld_tasks(q):
    q.create("queued-only", now=1000.0)  # never claimed
    counts = q.reconcile_liveness(lambda wt, mc, sid: "gone", now=2000.0)
    assert counts["checked"] == 0 and counts["requeued"] == 0


def test_reconcile_default_resolver_safe_without_bridge(q, monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: None)
    t = q.create("x", now=1000.0)
    _claim_and_start(q, t.id, wt="wt", session="S1", now=1001.0)
    assert q.reconcile_liveness(now=9999.0)["requeued"] == 0
    assert q.get(t.id).status == Status.STARTED


def test_recover_expired_leases_shim_delegates(q, monkeypatch):
    monkeypatch.setattr(tracking, "liveness_verdict", lambda *a, **k: "gone")
    t = q.create("x", now=1000.0)
    _claim_and_start(q, t.id, wt="wt", session="S1", now=1001.0)
    assert q.recover_expired_leases() == 1  # back-compat int return
    assert q.get(t.id).status == Status.QUEUED


# -- backlog_health (buildup, broken out by liveness) ------------------------


def test_backlog_health_empty(q):
    h = q.backlog_health(now=1000.0)
    assert h["queued"] == 0 and h["held"] == 0 and h["dead_letter"] == 0
    assert h["oldest_queued_age"] is None and h["oldest_held_live_age"] is None


def test_backlog_health_breaks_out_held_by_liveness(q):
    q.create("old", now=1000.0)  # queued
    live = q.create("live-held", now=1002.0)
    _claim_and_start(q, live.id, wt="wtL", session="SL", now=1003.0)  # last_seen at 1004
    q.reconcile_liveness(lambda wt, mc, sid: "live", now=1010.0)

    h = q.backlog_health(now=1100.0)
    assert h["queued"] == 1
    assert h["held"] == 1 and h["held_live"] == 1
    assert h["oldest_queued_age"] == pytest.approx(100.0)  # 1100 - 1000
    # stuck-but-alive signal: age since the live-held task last progressed (1004)
    assert h["oldest_held_live_age"] == pytest.approx(96.0)
