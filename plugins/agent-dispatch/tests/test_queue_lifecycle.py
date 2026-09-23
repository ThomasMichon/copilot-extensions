"""Import guard for the split-out lifecycle / transition ``TaskQueue`` mixin.

Behavioral coverage already lives in ``test_queue.py``, ``test_gc.py``,
``test_card_steer.py``, and ``test_transition_idempotent_replay.py``. This
file only guards direct importability, ``TaskQueue`` composition, queue
re-exports, and runtime-resolvable annotations for the extracted mixin.
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import CompletionOutcome, PROGRESS_SUMMARY_MAX, TaskQueue
from agent_dispatch.queue_common import CompletionOutcome as _CommonCompletionOutcome
from agent_dispatch.queue_common import PROGRESS_SUMMARY_MAX as _CommonProgressSummaryMax
from agent_dispatch.queue_lifecycle import QueueLifecycleMixin


@pytest.mark.guard
def test_task_queue_inherits_the_lifecycle_mixin():
    assert QueueLifecycleMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_queue_re_exports_match_lifecycle_dependencies():
    assert CompletionOutcome is _CommonCompletionOutcome
    assert PROGRESS_SUMMARY_MAX is _CommonProgressSummaryMax


@pytest.mark.guard
def test_lifecycle_methods_are_directly_importable():
    assert callable(QueueLifecycleMixin.approve)
    assert callable(QueueLifecycleMixin.start)
    assert callable(QueueLifecycleMixin.complete)
    assert callable(QueueLifecycleMixin.complete_with_outcome)
    assert callable(QueueLifecycleMixin._completion_event_workers)
    assert callable(QueueLifecycleMixin.suspend)
    assert callable(QueueLifecycleMixin.resume)
    assert callable(QueueLifecycleMixin.release_suspended)
    assert callable(QueueLifecycleMixin.set_hold)
    assert callable(QueueLifecycleMixin.clear_hold)
    assert callable(QueueLifecycleMixin.yield_task)
    assert callable(QueueLifecycleMixin.abandon)
    assert callable(QueueLifecycleMixin.reset)
    assert callable(QueueLifecycleMixin.heartbeat)
    assert callable(QueueLifecycleMixin.bind_owner_session)
    assert callable(QueueLifecycleMixin.set_activity)
    assert callable(QueueLifecycleMixin.record_progress)
    assert callable(QueueLifecycleMixin._transition)


@pytest.mark.guard
def test_lifecycle_annotations_resolve_via_get_type_hints():
    for name in (
        "approve",
        "start",
        "complete",
        "complete_with_outcome",
        "_completion_event_workers",
        "suspend",
        "resume",
        "release_suspended",
        "set_hold",
        "clear_hold",
        "yield_task",
        "abandon",
        "reset",
        "heartbeat",
        "bind_owner_session",
        "set_activity",
        "record_progress",
        "_transition",
    ):
        typing.get_type_hints(getattr(QueueLifecycleMixin, name))


def test_lifecycle_mixin_methods_work_end_to_end(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = q.create("lifecycle", repo="owner/repo")
    q.claim_one("worker-1", task_id=task.id, repo="owner/repo")
    q.start(task.id, "worker-1", owner_session_id="session-1")
    q.record_progress(task.id, "worker-1", phase="plan", summary="started")
    q.suspend(task.id, "worker-1", reason="waiting")
    q.resume(task.id, "worker-1")

    outcome = q.complete_with_outcome(task.id, "worker-1", result={"ok": True})

    assert isinstance(outcome, CompletionOutcome)
    assert outcome.task.status == "completed"
