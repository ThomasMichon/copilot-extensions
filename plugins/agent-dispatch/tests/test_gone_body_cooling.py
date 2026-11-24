"""A delivered owner wake holds a lane only while its body may still exist."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_dispatch import tracking
from agent_dispatch.queue_records import SpawnState, TaskError
from agent_dispatch.queue_relevance import RelevanceScope
from agent_dispatch.supervisor import Supervisor
from tests import test_supervisor as _supervisor_tests
from tests._helpers import TEST_REPO

SCOPE = RelevanceScope(TEST_REPO, "emitter", "review-source")
_fixtures: Any = _supervisor_tests


def _report_closed(queue: Any, task_id: str) -> None:
    snapshot = queue.relevance_snapshot(task_id)
    queue.reconcile_task_relevance(
        task_id, request_id="closed-source", scope=SCOPE,
        expected_generation=snapshot["generation"], expected_revision=snapshot["revision"],
        verdict="obsolete", reason="Backing object is closed",
        observation_ref="provider-object:closed:revision-2", reporter="source-evaluator",
    )


def _suspended_closeout_body(tmp_path: Path) -> tuple[Any, Any, Any]:
    queue = _fixtures.OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create(
        "Dormant review", labels=["review"], source=SCOPE.source, origin_ref=SCOPE.origin_ref,
    )
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(
        reservation.key, session_handle="local-body:bridge-a",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.claim_one("host/worker", task_id=task.id)
    queue.start(task.id, "host/worker", owner_session_id="original-native")
    queue.suspend(task.id, "host/worker", reason="Waiting on the source")
    _report_closed(queue, task.id)
    return queue, task, queue.latest_reservation(task.id)


def _claim_cooling(queue: Any, reservation: Any, **kwargs: Any) -> Any:
    return queue.claim_spawn_cooling(
        reservation.key, startup_revision=reservation.startup_revision,
        session_handle=reservation.session_handle, state=reservation.state, **kwargs,
    )


def _deliver_closeout_wake(queue: Any, task_id: str) -> None:
    wake = queue.claim_due_wake()
    assert wake is not None and wake.task_id == task_id
    queue.finish_wake(wake.id, wake.delivery_token, delivered=True)
    assert queue.list_wakes(task_id)[0].status == "delivered"


def test_delivered_closeout_wake_excludes_cooling_only_while_its_body_may_exist(
    tmp_path: Path,
) -> None:
    queue, task, reservation = _suspended_closeout_body(tmp_path)
    with pytest.raises(TaskError, match="wake excludes physical cooling"):
        _claim_cooling(queue, reservation, body_gone=True)
    _deliver_closeout_wake(queue, task.id)
    with pytest.raises(TaskError, match="current owner wake excludes physical cooling"):
        _claim_cooling(queue, reservation)
    assert _claim_cooling(queue, reservation, body_gone=True).cooling_token


def test_gone_body_with_delivered_closeout_wake_is_cooled_without_stop(tmp_path: Path) -> None:
    queue, task, reservation = _suspended_closeout_body(tmp_path)
    _deliver_closeout_wake(queue, task.id)
    stopped: list[str] = []
    verdict = {"value": tracking.LIVE}

    def stop(session_id: str) -> bool:
        stopped.append(session_id)
        return True

    supervisor = Supervisor(
        _fixtures.QueueBackedClient(queue),
        spawn_fn=lambda _task: pytest.fail("no replacement"),
        repo=TEST_REPO,
        labels=["review"],
        local_cold_fn=stop,
        local_body_verdict_fn=lambda _session_id: verdict["value"],
    )

    assert supervisor.cool_dormant_bodies() == 0
    assert queue.get_reservation(reservation.key).state == SpawnState.SPAWNED
    verdict["value"] = tracking.GONE
    assert supervisor.cool_dormant_bodies() == 1
    assert stopped == []
    assert queue.get_reservation(reservation.key).state == SpawnState.COLD
