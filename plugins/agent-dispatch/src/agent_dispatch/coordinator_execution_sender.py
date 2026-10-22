"""Custody at coordinator-owned transport boundaries in already bound scopes."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from . import coordinator_execution_runtime as runtime
from .execution_scope_binding import resolve_execution_installation
from .loop_governance import LoopGovernance
from .queue import SpawnReservation, Task, TaskQueue
from .queue_execution_custody import ExecutionSendIdentity, SendKind, execution_scope_qualified
from .queue_execution_scopes import ExecutionScopeBinding
from .queue_records import TaskError


@dataclass
class ExecutionSendAttempt:
    accepted: bool = False

    def sent(self) -> None:
        self.accepted = True


class CoordinatorExecutionProvenance:
    def __init__(
        self, queue: TaskQueue, task: Task, incarnation: str
    ) -> None:
        self.queue = queue
        self.task = task
        self.incarnation = incarnation
        self.governance = LoopGovernance()
        self.producer_generation: int | None = None

    def prepare(self) -> tuple[ExecutionScopeBinding, str] | None:
        # Historical qualification cannot disappear with a missing binding row.
        with self.queue._connect() as conn:
            qualified = execution_scope_qualified(conn, self.task.repo, self.task.source)
        if not qualified:
            return None
        if not self.incarnation:
            raise TaskError("qualified sender requires its coordinator application incarnation")
        installation = resolve_execution_installation(self.governance)
        process = runtime._own_process(self.incarnation)
        producer = self.queue.producer_scope_status(self.task.repo, self.task.source)
        if self.producer_generation is None:
            self.producer_generation = producer.current_generation
        elif producer.current_generation != self.producer_generation:
            raise TaskError("coordinator producer generation changed before send admission")
        binding = self.queue.bind_execution_scope(
            installation,
            repo=self.task.repo,
            source=self.task.source,
            expected_producer_generation=self.producer_generation,
        )
        enrolled = self.queue.enroll_coordinator_execution_runtime(binding, installation, process)
        runtime_id = enrolled["runtime_id"]
        if not isinstance(runtime_id, str):
            raise TaskError("coordinator enrollment omitted runtime identity")
        if (
            resolve_execution_installation(self.governance) != installation
            or runtime._own_process(self.incarnation) != process
        ):
            raise TaskError("coordinator sender provenance changed before send admission")
        return binding, runtime_id


class CoordinatorExecutionSender(CoordinatorExecutionProvenance):
    def __init__(
        self, queue: TaskQueue, task: Task, reservation: SpawnReservation, incarnation: str
    ) -> None:
        super().__init__(queue, task, incarnation)
        self.reservation = reservation

    @contextmanager
    def attempt(self, kind: SendKind) -> Iterator[ExecutionSendAttempt]:
        attempt = ExecutionSendAttempt()
        prepared = self.prepare()
        if prepared is None:
            yield attempt
            return
        binding, runtime_id = prepared
        identity = ExecutionSendIdentity(
            binding.scope,
            "send:" + uuid.uuid4().hex,
            runtime_id,
            self.reservation.key,
            self.task.generation,
            kind,
        )
        admitted = self.queue.admit_execution_send(
            identity,
            source=binding.source,
            expected_producer_generation=binding.producer_generation,
        )
        if admitted.replayed:
            raise TaskError("execution send replay is observation, not transport permission")
        try:
            yield attempt
        finally:
            self.queue.settle_execution_send(
                identity,
                outcome="sent" if attempt.accepted else "unknown",
                detail=(
                    "coordinator transport returned an accepted result"
                    if attempt.accepted
                    else "coordinator transport did not establish acceptance or absence"
                ),
            )
