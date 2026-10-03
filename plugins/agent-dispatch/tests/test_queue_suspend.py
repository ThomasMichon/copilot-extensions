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

import json
import typing

import pytest

from agent_dispatch.monitors import MonitorKind
from agent_dispatch.queue import Status, TaskQueue
from agent_dispatch.queue_suspend import QueueSuspendMixin
from tests._helpers import TEST_REPO


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
    [wake] = q.list_wakes(due.id)
    assert wake.message == (
        f"Task {due.id}'s cooldown elapsed while it was suspended. Re-read it "
        "and resume from the recorded state."
    )


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


def test_reconcile_reviewer_deadlines_skips_malformed_evaluator_config(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    q.register_registration(
        "evaluator",
        {"repo": TEST_REPO, "evaluator_ref": "good", "evaluator_spec": {}},
    )
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO registrations (id, kind, spec, status, created_at, updated_at)"
            " VALUES (?, ?, ?, 'active', 0, 0)",
            (
                "bad-reviewer-loop",
                "evaluator",
                json.dumps(
                    {
                        "repo": TEST_REPO,
                        "evaluator_ref": "bad",
                        "evaluator_spec": {},
                        "reviewer_loop": {"stale_after_days": "NaN"},
                    }
                ),
            ),
        )

    assert q.reconcile_reviewer_deadlines(now=1_000_000.0) == 0


def test_iter_suspended_reviewer_candidates_paginates_past_the_first_batch(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    q.register_registration(
        "evaluator",
        {
            "repo": TEST_REPO,
            "evaluator_ref": "review-loop",
            "evaluator_spec": {},
            "reviewer_loop": {"stale_after_days": 7},
        },
    )
    now = 1_000_000.0
    stale_payload = json.dumps({"reviewer_loop": {"last_commit_at": now - (8 * 86400.0)}})
    for index in range(3):
        task = q.create(
            f"review {index}",
            repo=TEST_REPO,
            require_verification=True,
            evaluator_ref="review-loop",
            payload_inline=stale_payload,
        )
        worker = f"worker-{index}"
        q.claim_one(worker, task_id=task.id, repo=TEST_REPO)
        q.start(task.id, worker, owner_session_id=f"session-{index}")
        q.suspend(task.id, worker, reason="waiting", cooldown_seconds=None, now=now)

    tasks = list(q._iter_suspended_reviewer_candidates(batch_size=2))

    assert len(tasks) == 3
    assert {task.title for task in tasks} == {"review 0", "review 1", "review 2"}


def test_reconcile_reviewer_deadlines_uses_current_machine_scope_and_all_repos(
    tmp_path, monkeypatch
):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("AGENT_DISPATCH_ENV", "default")
    monkeypatch.setattr(
        "agent_dispatch.remote_dispatch.local_machine", lambda: "host-a"
    )
    q.register_registration(
        "evaluator",
        {
            "all_repos": True,
            "evaluator_ref": "review-loop",
            "evaluator_spec": {},
            "reviewer_loop": {"stale_after_days": 7},
        },
        machine="host-a",
        env="default",
    )
    q.register_registration(
        "evaluator",
        {
            "repo": TEST_REPO,
            "evaluator_ref": "review-loop",
            "evaluator_spec": {},
            "reviewer_loop": {"stale_after_days": 30},
        },
        machine="host-b",
        env="default",
    )
    now = 1_000_000.0
    task = q.create(
        "review scoped",
        repo=TEST_REPO,
        require_verification=True,
        evaluator_ref="review-loop",
        payload_inline=json.dumps({"reviewer_loop": {"last_commit_at": now - (8 * 86400.0)}}),
    )
    q.claim_one("worker-1", task_id=task.id, repo=TEST_REPO)
    q.start(task.id, "worker-1", owner_session_id="session-1")
    q.suspend(task.id, "worker-1", reason="waiting", cooldown_seconds=None, now=now)

    resumed = q.reconcile_reviewer_deadlines(now=now)

    assert resumed == 1


def test_reconcile_reviewer_deadlines_reads_blob_spilled_payload(tmp_path, monkeypatch):
    q = TaskQueue(str(tmp_path / "q.sqlite3"), blob_threshold=16)
    monkeypatch.setenv("AGENT_DISPATCH_ENV", "default")
    monkeypatch.setattr(
        "agent_dispatch.remote_dispatch.local_machine", lambda: "host-a"
    )
    q.register_registration(
        "evaluator",
        {
            "repo": TEST_REPO,
            "evaluator_ref": "review-loop",
            "evaluator_spec": {},
            "reviewer_loop": {"stale_after_days": 7},
        },
        machine="host-a",
        env="default",
    )
    now = 1_000_000.0
    payload = json.dumps(
        {
            "reviewer_loop": {"last_commit_at": now - (8 * 86400.0)},
            "padding": "x" * 200,
        }
    )
    task = q.create(
        "review blob",
        repo=TEST_REPO,
        require_verification=True,
        evaluator_ref="review-loop",
        payload_inline=payload,
    )
    assert task.payload_inline is None
    assert task.payload_ref is not None
    q.claim_one("worker-1", task_id=task.id, repo=TEST_REPO)
    q.start(task.id, "worker-1", owner_session_id="session-1")
    q.suspend(task.id, "worker-1", reason="waiting", cooldown_seconds=None, now=now)

    resumed = q.reconcile_reviewer_deadlines(now=now)

    assert resumed == 1


def test_reconcile_reviewer_deadlines_skips_bad_payload_and_keeps_processing(
    tmp_path, monkeypatch
):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("AGENT_DISPATCH_ENV", "default")
    monkeypatch.setattr(
        "agent_dispatch.remote_dispatch.local_machine", lambda: "host-a"
    )
    q.register_registration(
        "evaluator",
        {
            "repo": TEST_REPO,
            "evaluator_ref": "review-loop",
            "evaluator_spec": {},
            "reviewer_loop": {"stale_after_days": 7},
        },
        machine="host-a",
        env="default",
    )
    now = 1_000_000.0
    payload = json.dumps({"reviewer_loop": {"last_commit_at": now - (8 * 86400.0)}})
    bad = q.create(
        "bad payload",
        repo=TEST_REPO,
        require_verification=True,
        evaluator_ref="review-loop",
        payload_inline=payload,
    )
    good = q.create(
        "good payload",
        repo=TEST_REPO,
        require_verification=True,
        evaluator_ref="review-loop",
        payload_inline=payload,
    )
    for index, task in enumerate((bad, good), start=1):
        worker = f"worker-{index}"
        q.claim_one(worker, task_id=task.id, repo=TEST_REPO)
        q.start(task.id, worker, owner_session_id=f"session-{index}")
        q.suspend(task.id, worker, reason="waiting", cooldown_seconds=None, now=now)

    original = q.read_payload

    def flaky_read_payload(task_or_id):
        task = q.get(task_or_id) if isinstance(task_or_id, str) else task_or_id
        assert task is not None
        if task.id == bad.id:
            raise KeyError("missing blob")
        return original(task)

    monkeypatch.setattr(q, "read_payload", flaky_read_payload)

    assert q.reconcile_reviewer_deadlines(now=now) == 1
    assert q.list_wakes(good.id)
    assert q.list_run_waiter_wakes(bad.id) == []

    later = q.create(
        "later payload",
        repo=TEST_REPO,
        require_verification=True,
        evaluator_ref="review-loop",
        payload_inline=payload,
    )
    q.claim_one("worker-3", task_id=later.id, repo=TEST_REPO)
    q.start(later.id, "worker-3", owner_session_id="session-3")
    q.suspend(later.id, "worker-3", reason="waiting", cooldown_seconds=None, now=now)

    assert q.reconcile_reviewer_deadlines(now=now) == 1
    assert q.list_wakes(later.id)
