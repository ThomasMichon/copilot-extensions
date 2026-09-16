"""Tests for the coordinator's handoff-fallback reconciliation (Phase 3 slice 2,
``efforts/active/context-handoff-overhaul``):

- :mod:`agent_dispatch.handoff_fallback_seed` -- the pure Python reimplementation
  of context-handoff's short cutover-seed contract.
- :class:`agent_dispatch.queue_handoff_fallback.HandoffFallbackMixin` -- the
  atomic single-attempt claim that fences the fallback launch against
  double-spawn.
- :func:`agent_dispatch.coordinator._find_stale_handoff_tasks` /
  :func:`_attempt_handoff_fallback` / :func:`_reconcile_handoff_fallback` --
  detection + the claim-then-spawn-via-agent-bridge flow.
"""

from __future__ import annotations

import pytest

from agent_dispatch import bridge, coordinator
from agent_dispatch.handoff_fallback_seed import (
    MAX_CUTOVER_SEED_LENGTH,
    build_cutover_seed,
    build_fallback_seed,
    lead_from,
    recovery_locator_for,
)
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


# -- handoff_fallback_seed: pure string-format parity with cutover-seed.mjs --


def test_lead_from_strips_task_prefix_and_truncates():
    assert lead_from("Task: fix the thing") == "Task: fix the thing"
    assert lead_from("continue: fix the thing") == "Task: fix the thing"
    long_title = "x" * 200
    lead = lead_from(long_title)
    assert lead.startswith("Task: ")
    assert len(lead) <= len("Task: ") + 72


def test_lead_from_falls_back_when_empty():
    assert lead_from(None) == "Task: Continue the current work"
    assert lead_from("   ") == "Task: Continue the current work"


def test_recovery_locator_for_task():
    assert recovery_locator_for("task", "8b270b8e-c165-494a") == "task:8b270b8e-c165-494a"


def test_recovery_locator_rejects_unsafe_characters():
    with pytest.raises(ValueError):
        recovery_locator_for("task", "abc | rm -rf /")


def test_recovery_locator_rejects_unknown_kind():
    with pytest.raises(ValueError):
        recovery_locator_for("worktree", "abc")


def test_build_cutover_seed_shape():
    seed = build_cutover_seed("task", "abc123", lead_from("Fix the XSS bug"))
    assert seed == (
        "Task: Fix the XSS bug | Resume: /consume-handoff to take over | "
        "Recovery: context-handoff task:abc123"
    )
    assert len(seed) <= MAX_CUTOVER_SEED_LENGTH


def test_build_cutover_seed_falls_back_on_overflow():
    # A full-length (72-char-capped) lead combined with a longer-than-usual
    # task id can overflow MAX_CUTOVER_SEED_LENGTH; the generic lead is
    # substituted rather than raising or truncating mid-word.
    long_id = "a" * 60
    seed = build_cutover_seed("task", long_id, lead_from("x" * 100))
    assert seed == (
        f"Task: Continue the current work | Resume: /consume-handoff to take "
        f"over | Recovery: context-handoff task:{long_id}"
    )
    assert len(seed) <= MAX_CUTOVER_SEED_LENGTH


def test_build_fallback_seed_matches_cutover_seed():
    assert build_fallback_seed("t1", "My Title") == build_cutover_seed(
        "task", "t1", lead_from("My Title")
    )


# -- HandoffFallbackMixin: atomic single-attempt claim -----------------------


def test_claim_handoff_fallback_first_call_wins(q):
    assert q.claim_handoff_fallback("t1") is True


def test_claim_handoff_fallback_second_call_loses(q):
    assert q.claim_handoff_fallback("t1") is True
    assert q.claim_handoff_fallback("t1") is False


def test_claim_handoff_fallback_independent_per_task(q):
    assert q.claim_handoff_fallback("t1") is True
    assert q.claim_handoff_fallback("t2") is True


def test_record_and_get_handoff_fallback_attempt(q):
    q.claim_handoff_fallback("t1", now=100.0)
    assert q.get_handoff_fallback_attempt("t1")["outcome"] == "pending"
    q.record_handoff_fallback_outcome("t1", outcome="launched", now=101.0)
    attempt = q.get_handoff_fallback_attempt("t1")
    assert attempt["outcome"] == "launched"
    assert attempt["updated_at"] == 101.0


def test_get_handoff_fallback_attempt_none_when_never_claimed(q):
    assert q.get_handoff_fallback_attempt("never-claimed") is None


# -- coordinator: stale-task detection ---------------------------------------


def _handoff_task(q, title, *, wt="wt-1", machine="m1", now, status=Status.PROPOSED):
    return q.create(
        title, status=status, target_machine=machine, target_worktree=wt,
        source="context-handoff", labels=["handoff"], now=now,
    )


def test_find_stale_handoff_tasks_respects_grace_window(q):
    old = _handoff_task(q, "old", now=100.0)
    _handoff_task(q, "fresh", now=190.0)  # too young to be stale yet
    stale = coordinator._find_stale_handoff_tasks(q, grace=60.0, machine="m1", now=200.0)
    assert [t.id for t in stale] == [old.id]


def test_find_stale_handoff_tasks_scopes_to_machine(q):
    _handoff_task(q, "other-machine", machine="m2", now=100.0)
    stale = coordinator._find_stale_handoff_tasks(q, grace=60.0, machine="m1", now=200.0)
    assert stale == []


def test_find_stale_handoff_tasks_machine_agnostic_task_matches_any(q):
    # An unpinned target_machine matches any caller (machine_matches convention).
    task = q.create(
        "unpinned", status=Status.PROPOSED, target_worktree="wt-1",
        source="context-handoff", labels=["handoff"], now=100.0,
    )
    stale = coordinator._find_stale_handoff_tasks(q, grace=60.0, machine="m1", now=200.0)
    assert [t.id for t in stale] == [task.id]


def test_find_stale_handoff_tasks_ignores_non_handoff_labels(q):
    q.create(
        "ordinary", status=Status.PROPOSED, target_machine="m1",
        target_worktree="wt-1", now=100.0,
    )
    stale = coordinator._find_stale_handoff_tasks(q, grace=60.0, machine="m1", now=200.0)
    assert stale == []


def test_find_stale_handoff_tasks_ignores_claimed_tasks(q):
    # A CLAIMED/STARTED/COMPLETED handoff task is no longer "unclaimed"; only
    # proposed/queued are reconciliation candidates.
    task = q.create(
        "claimed", status=Status.QUEUED, target_machine="m1",
        target_worktree="wt-1", source="context-handoff", labels=["handoff"],
        now=100.0,
    )
    assert q.claim_one("some-worker", machine="m1", worktree="wt-1", task_id=task.id) is not None
    stale = coordinator._find_stale_handoff_tasks(q, grace=60.0, machine="m1", now=200.0)
    assert stale == []


# -- coordinator: claim-then-spawn --------------------------------------------


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_attempt_handoff_fallback_launches_via_reclaim(q):
    task = _handoff_task(q, "Fix the thing", now=100.0)
    calls = {}

    def fake_spawn(task_id, **kwargs):
        calls["task_id"] = task_id
        calls["kwargs"] = kwargs
        return _Proc(returncode=0)

    outcome = coordinator._attempt_handoff_fallback(q, task, spawn_fn=fake_spawn)
    assert outcome == "launched"
    assert calls["task_id"] == task.id
    assert calls["kwargs"]["reclaim"] is True
    assert calls["kwargs"]["worktree_id"] == "wt-1"
    assert calls["kwargs"]["wait"] is False
    assert "Resume: /consume-handoff to take over" in calls["kwargs"]["prompt"]
    assert q.get_handoff_fallback_attempt(task.id)["outcome"] == "launched"


def test_attempt_handoff_fallback_respects_claim_fence(q):
    task = _handoff_task(q, "Fix the thing", now=100.0)
    calls = []

    def fake_spawn(task_id, **kwargs):
        calls.append(task_id)
        return _Proc(returncode=0)

    first = coordinator._attempt_handoff_fallback(q, task, spawn_fn=fake_spawn)
    second = coordinator._attempt_handoff_fallback(q, task, spawn_fn=fake_spawn)
    assert first == "launched"
    assert second == "skipped"
    assert calls == [task.id]  # never spawned twice


def test_attempt_handoff_fallback_records_failed_outcome(q):
    task = _handoff_task(q, "Fix the thing", now=100.0)

    def fake_spawn(task_id, **kwargs):
        return _Proc(returncode=1, stderr="boom")

    outcome = coordinator._attempt_handoff_fallback(q, task, spawn_fn=fake_spawn)
    assert outcome == "failed"
    attempt = q.get_handoff_fallback_attempt(task.id)
    assert attempt["outcome"] == "failed"
    assert attempt["detail"] == "boom"


def test_attempt_handoff_fallback_records_unavailable_when_no_bridge(q, monkeypatch):
    task = _handoff_task(q, "Fix the thing", now=100.0)
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: None)

    outcome = coordinator._attempt_handoff_fallback(q, task)  # real bridge.spawn_worker
    assert outcome == "unavailable"
    assert q.get_handoff_fallback_attempt(task.id)["outcome"] == "unavailable"


def test_attempt_handoff_fallback_survives_unexpected_exception(q):
    task = _handoff_task(q, "Fix the thing", now=100.0)

    def raising_spawn(task_id, **kwargs):
        raise RuntimeError("kaboom")

    outcome = coordinator._attempt_handoff_fallback(q, task, spawn_fn=raising_spawn)
    assert outcome == "error"
    attempt = q.get_handoff_fallback_attempt(task.id)
    assert attempt["outcome"] == "error"
    assert "kaboom" in attempt["detail"]


# -- coordinator: full reconcile pass -----------------------------------------


def test_reconcile_handoff_fallback_degrades_safe_without_machine(q, monkeypatch):
    from agent_dispatch import identity

    monkeypatch.setattr(identity, "resolve_machine", lambda: None)
    _handoff_task(q, "old", now=100.0)
    counts = coordinator._reconcile_handoff_fallback(q, grace=60.0)
    assert counts == {
        "checked": 0, "launched": 0, "skipped": 0,
        "unavailable": 0, "failed": 0, "error": 0,
    }


def test_reconcile_handoff_fallback_launches_only_stale_tasks(q, monkeypatch):
    import time as time_mod

    from agent_dispatch import identity

    monkeypatch.setattr(identity, "resolve_machine", lambda: "m1")
    old = _handoff_task(q, "old", now=100.0)  # far in the past -> stale
    _handoff_task(q, "fresh", now=time_mod.time())  # just created -> not stale

    launched_ids = []

    def fake_spawn(task_id, **kwargs):
        launched_ids.append(task_id)
        return _Proc(returncode=0)

    monkeypatch.setattr(bridge, "spawn_worker", fake_spawn)
    counts = coordinator._reconcile_handoff_fallback(q, grace=60.0)
    assert counts["checked"] == 1
    assert counts["launched"] == 1
    assert launched_ids == [old.id]
