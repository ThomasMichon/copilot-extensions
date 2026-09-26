"""Import guard + behavioral coverage for the suspend/resume/cooldown mixin.

Split out of :mod:`agent_dispatch.queue_lifecycle` for module-size discipline
(see that module's history) as :mod:`agent_dispatch.queue_suspend`. This
file both guards direct importability/composition (mirroring
``test_queue_lifecycle.py``'s pattern) and covers Phase 3 of
``efforts/active/agent-dispatch-monitor-and-confirmed-state/README.md``: the
default cooldown monitor a bare ``suspend()`` applies, and
``reconcile_cooldowns()``'s auto-resume pass.
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.monitors import MonitorKind
from agent_dispatch.queue import Status, TaskQueue
from agent_dispatch.queue_suspend import QueueSuspendMixin


@pytest.mark.guard
def test_task_queue_inherits_the_suspend_mixin():
    assert QueueSuspendMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_suspend_methods_are_directly_importable():
    assert callable(QueueSuspendMixin.suspend)
    assert callable(QueueSuspendMixin.resume)
    assert callable(QueueSuspendMixin.reconcile_cooldowns)


@pytest.mark.guard
def test_suspend_annotations_resolve_via_get_type_hints():
    for name in ("suspend", "resume", "reconcile_cooldowns"):
        typing.get_type_hints(getattr(QueueSuspendMixin, name))


def _ready_started_task(q, *, worker_id="worker-1", repo="owner/repo"):
    task = q.create("cooldown", repo=repo)
    q.claim_one(worker_id, task_id=task.id, repo=repo)
    q.start(task.id, worker_id, owner_session_id="session-1")
    return task


def test_bare_suspend_applies_a_default_cooldown_monitor(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = _ready_started_task(q)
    now = 1_000.0
    suspended = q.suspend(task.id, "worker-1", reason="waiting", now=now)
    assert suspended.monitor_kind == MonitorKind.COOLDOWN.value
    assert suspended.monitor_not_before > now


def test_suspend_accepts_an_explicit_cooldown_override(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = _ready_started_task(q)
    now = 1_000.0
    suspended = q.suspend(
        task.id, "worker-1", reason="waiting", cooldown_seconds=30.0, now=now
    )
    assert suspended.monitor_not_before == now + 30.0


def test_suspend_with_cooldown_seconds_none_clears_the_monitor(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = _ready_started_task(q)
    suspended = q.suspend(task.id, "worker-1", reason="waiting", cooldown_seconds=None)
    assert suspended.monitor_kind is None
    assert suspended.monitor_not_before is None


def test_resume_clears_the_monitor(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = _ready_started_task(q)
    q.suspend(task.id, "worker-1", reason="waiting")
    resumed = q.resume(task.id, "worker-1")
    assert resumed.monitor_kind is None
    assert resumed.monitor_not_before is None
    assert resumed.status == Status.STARTED


def test_reconcile_cooldowns_resumes_only_due_tasks(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    due = _ready_started_task(q, worker_id="worker-1")
    not_due = _ready_started_task(q, worker_id="worker-2")
    now = 1_000.0
    q.suspend(due.id, "worker-1", reason="waiting", cooldown_seconds=10.0, now=now)
    q.suspend(not_due.id, "worker-2", reason="waiting", cooldown_seconds=1_000.0, now=now)

    resumed = q.reconcile_cooldowns(now=now + 20.0)

    assert resumed == 1
    assert q.get(due.id).status == Status.STARTED
    assert q.get(not_due.id).status == Status.SUSPENDED


def test_reconcile_cooldowns_ignores_a_task_with_no_monitor(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = _ready_started_task(q)
    q.suspend(task.id, "worker-1", reason="waiting", cooldown_seconds=None)

    resumed = q.reconcile_cooldowns(now=1_000_000.0)

    assert resumed == 0
    assert q.get(task.id).status == Status.SUSPENDED


def test_reconcile_cooldowns_skips_a_task_already_resumed_manually(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = _ready_started_task(q)
    now = 1_000.0
    q.suspend(task.id, "worker-1", reason="waiting", cooldown_seconds=10.0, now=now)
    q.resume(task.id, "worker-1", now=now + 5.0)

    resumed = q.reconcile_cooldowns(now=now + 20.0)

    assert resumed == 0
    assert q.get(task.id).status == Status.STARTED


def test_reconcile_cooldowns_ignores_a_task_no_longer_suspended(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = _ready_started_task(q)
    now = 1_000.0
    q.suspend(task.id, "worker-1", reason="waiting", cooldown_seconds=10.0, now=now)
    q.abandon(task.id, worker_id="worker-1", permitted=True, reason="giving up", now=now + 15.0)

    resumed = q.reconcile_cooldowns(now=now + 20.0)

    assert resumed == 0
    assert q.get(task.id).status == Status.ABANDONED
