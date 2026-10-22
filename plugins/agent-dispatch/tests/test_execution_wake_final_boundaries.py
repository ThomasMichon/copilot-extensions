"""Wake and reservation final admission share the same unresolved participants."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

import pytest

from agent_dispatch import coordinator_execution_sender as sender
from agent_dispatch.events import EventBus
from agent_dispatch.queue_execution_custody import ExecutionSendIdentity
from agent_dispatch.queue_execution_scopes import ExecutionScopeBinding
from agent_dispatch.queue_execution_wakes import wake_table
from agent_dispatch.queue_records import TaskError
from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
from agent_dispatch.wake import drain_wake_outbox
from tests.test_execution_wake_sends import (
    Cohort, INCARNATION, REPO, SOURCE, _claim, _next_shared_wake, _row,
    cohort as cohort,
)


def _reserved_sender(
    cohort: Cohort,
) -> tuple[ExecutionSendIdentity, tuple[ExecutionScopeBinding, str]]:
    queue, wake, binding = cohort
    task = queue.create("reserved", repo=REPO, exclusive_key="blocked-lane")
    reservation, created = queue.reserve_spawn(task.id)
    assert created
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET source = ? WHERE id = ?", (SOURCE, task.id))
        conn.execute("UPDATE tasks SET exclusive_key = 'blocked-lane' WHERE id = ?", (wake.task_id,))
        conn.commit()
    provenance = sender.CoordinatorExecutionProvenance(
        queue, queue.get(wake.task_id), INCARNATION
    ).prepare()
    assert provenance is not None
    identity = ExecutionSendIdentity(
        binding.scope, "reservation-send", provenance[1], reservation.key,
        queue.get(task.id).generation, "launch",
    )
    return identity, provenance


def test_reservation_acquired_before_wake_must_recheck_final_custody(cohort: Cohort) -> None:
    queue, pending, binding = cohort
    identity, provenance = _reserved_sender(cohort)
    wake = _claim(queue, pending)
    assert wake is not None
    admission = queue.admit_execution_wake(wake, provenance=provenance)
    queue.settle_execution_wake(wake, admission, outcome="unknown", detail="synthetic lost reply")
    with pytest.raises(TaskError, match="unresolved execution wake"):
        queue.admit_execution_send(
            identity, source=SOURCE, expected_producer_generation=binding.producer_generation,
        )
    queue.settle_execution_wake(wake, admission, outcome="sent", detail="synthetic acceptance")
    assert queue.admit_execution_send(
        identity, source=SOURCE, expected_producer_generation=binding.producer_generation,
    ).replayed is False


def test_wake_lease_expiring_while_acquiring_write_lock_refuses_send(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    assert wake is not None
    provenance = sender.CoordinatorExecutionProvenance(
        queue, queue.get(wake.task_id), INCARNATION
    ).prepare()
    clock = [wake.delivery_expires_at - 1]
    connect = queue._connect

    class LockDelay:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def __getattr__(self, name: str) -> Any:
            return getattr(self.connection, name)

        def execute(self, sql: str, *args: Any) -> Any:
            result = self.connection.execute(sql, *args)
            if sql == "BEGIN IMMEDIATE":
                clock[0] = wake.delivery_expires_at + 1
            return result

    @contextmanager
    def delayed_connect() -> Iterator[LockDelay]:
        with connect() as connection:
            yield LockDelay(connection)

    monkeypatch.setattr(queue, "_connect", delayed_connect)
    monkeypatch.setattr(queue, "_now", lambda now: clock[0] if now is None else now)
    with pytest.raises(TaskError, match="unexpired delivery token"):
        queue.admit_execution_wake(wake, provenance=provenance)
    assert _row(queue, wake)["execution_send"] is None


def test_existing_reservation_send_replay_remains_observation_during_wake_ambiguity(
    cohort: Cohort,
) -> None:
    queue, pending, binding = cohort
    identity, provenance = _reserved_sender(cohort)
    queue.admit_execution_send(
        identity, source=SOURCE, expected_producer_generation=binding.producer_generation,
    )
    queue.settle_execution_send(identity, outcome="sent", detail="synthetic acceptance")
    wake = _claim(queue, pending)
    assert wake is not None
    queue.admit_execution_wake(wake, provenance=provenance)
    assert queue.admit_execution_send(
        identity, source=SOURCE, expected_producer_generation=binding.producer_generation,
    ).replayed is True
    assert _row(queue, wake)["execution_send_state"] == "pending"


@pytest.mark.parametrize("outcome", ["pending", "unknown"])
def test_reservation_blocked_oldest_wake_does_not_starve_other_deliveries(
    cohort: Cohort, outcome: str,
) -> None:
    queue, pending, binding = cohort
    identity, _provenance = _reserved_sender(cohort)
    queue.admit_execution_send(
        identity, source=SOURCE, expected_producer_generation=binding.producer_generation,
    )
    if outcome == "unknown":
        queue.settle_execution_send(identity, outcome="unknown", detail="synthetic lost reply")
    other = _next_shared_wake(
        queue, "owner" if wake_table(pending) == "wake_outbox" else "waiter",
        exclusive_key=None,
    )
    calls = []

    def deliver(*args: Any) -> bool:
        calls.append(args)
        return True

    async def scenario() -> None:
        drain = (
            drain_wake_outbox(
                queue, EventBus(), interval=0.01, deliver=deliver,
                execution_incarnation=INCARNATION,
            )
            if wake_table(pending) == "wake_outbox"
            else drain_run_waiter_wakes(
                queue, interval=0.01, deliver=deliver, execution_incarnation=INCARNATION,
            )
        )
        loop = asyncio.create_task(drain)
        try:
            for _ in range(100):
                if _row(queue, other)["status"] == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("reservation-blocked wake starved unrelated delivery")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(calls) == 1
    assert _row(queue, pending)["status"] == "pending"
    assert _row(queue, pending)["attempts"] == 0
