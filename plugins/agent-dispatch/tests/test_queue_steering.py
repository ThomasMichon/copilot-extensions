"""Import guard for the split-out steering / wake / portability ``TaskQueue``
mixin and the queue-common support layer it depends on.

Every method here is already thoroughly exercised through
``agent_dispatch.queue.TaskQueue`` (``test_card_steer.py``,
``test_wake_outbox.py``, and related lifecycle coverage) -- this file only
guards that ``agent_dispatch.queue_steering`` remains directly importable with
its own stable public surface, is actually composed into ``TaskQueue``, that
``agent_dispatch.queue`` still re-exports the moved support objects existing
``from agent_dispatch.queue import ...`` consumers depend on, and that the
mixins' annotations resolve cleanly to ``typing.get_type_hints()``.
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import (
    DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    PROGRESS_PHASE_MAX,
    Task,
    TaskQueue,
    WakeOperation,
    _PROGRESS_PR_MAX,
)
from agent_dispatch.queue_common import (
    DEFAULT_WAKE_DELIVERY_LEASE_SECONDS as _CommonWakeLease,
)
from agent_dispatch.queue_common import PROGRESS_PHASE_MAX as _CommonProgressPhaseMax
from agent_dispatch.queue_common import Task as _CommonTask
from agent_dispatch.queue_common import WakeOperation as _CommonWakeOperation
from agent_dispatch.queue_common import _PROGRESS_PR_MAX as _CommonProgressPrMax
from agent_dispatch.queue_steering import QueueSteeringMixin


@pytest.mark.guard
def test_task_queue_inherits_the_steering_mixin():
    assert QueueSteeringMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_queue_re_exports_match_queue_common():
    assert DEFAULT_WAKE_DELIVERY_LEASE_SECONDS is _CommonWakeLease
    assert PROGRESS_PHASE_MAX is _CommonProgressPhaseMax
    assert Task is _CommonTask
    assert WakeOperation is _CommonWakeOperation
    assert _PROGRESS_PR_MAX is _CommonProgressPrMax


@pytest.mark.guard
def test_steering_methods_are_directly_importable():
    assert callable(QueueSteeringMixin._has_headless_reservation)
    assert callable(QueueSteeringMixin._has_cold_headless_reservation)
    assert callable(QueueSteeringMixin.set_card)
    assert callable(QueueSteeringMixin.submit_steer)
    assert callable(QueueSteeringMixin.save_card_draft)
    assert callable(QueueSteeringMixin.clear_card_draft)
    assert callable(QueueSteeringMixin.take_steer)
    assert callable(QueueSteeringMixin.steer_log)
    assert callable(QueueSteeringMixin.list_wakes)
    assert callable(QueueSteeringMixin._wake_is_current)
    assert callable(QueueSteeringMixin.recover_inflight_wakes)
    assert callable(QueueSteeringMixin.claim_due_wake)
    assert callable(QueueSteeringMixin.finish_wake)
    assert callable(QueueSteeringMixin.wake_metrics)
    assert callable(QueueSteeringMixin.detach)


@pytest.mark.guard
def test_mixin_method_annotations_resolve_via_get_type_hints():
    for name in (
        "set_card",
        "submit_steer",
        "save_card_draft",
        "clear_card_draft",
        "take_steer",
        "steer_log",
        "list_wakes",
        "_wake_is_current",
        "recover_inflight_wakes",
        "claim_due_wake",
        "finish_wake",
        "wake_metrics",
        "detach",
    ):
        typing.get_type_hints(getattr(QueueSteeringMixin, name))


def test_mixin_methods_work_end_to_end(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = q.create("steer me", repo="owner/repo")
    q.claim_one("worker-1", task_id=task.id, repo="owner/repo")
    q.start(task.id, "worker-1", owner_session_id="session-1")

    q.set_card(task.id, "worker-1", card={"request_input": [{"name": "decision"}]})
    updated = q.submit_steer(task.id, fields={"decision": "continue"}, wake_requested=True)

    [wake] = q.list_wakes(task.id)
    assert isinstance(updated, Task)
    assert isinstance(wake, WakeOperation)
