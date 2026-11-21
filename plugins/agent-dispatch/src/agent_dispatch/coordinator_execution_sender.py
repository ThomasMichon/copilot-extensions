"""Custody at coordinator-owned transport boundaries in already bound scopes."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from . import ci_launch_observation
from . import coordinator_execution_runtime as runtime
from .execution_scope_binding import resolve_execution_installation
from .loop_governance import LoopGovernance
from .queue import TaskQueue
from .queue_common import Task
from .queue_execution_custody import ExecutionSendIdentity, SendKind, execution_scope_qualified
from .queue_execution_scopes import ExecutionScopeBinding
from .queue_records import SpawnReservation, TaskError
from .registrations import RegistrationRecord


@dataclass
class ExecutionSendAttempt:
    accepted: bool = False
    staging_handle: str | None = None

    def sent(self, *, staging_handle: str | None = None) -> None:
        self.accepted = True
        self.staging_handle = staging_handle


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
        if self.task.repo is None or self.task.source is None:
            return None
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
        self, queue: TaskQueue, task: Task, reservation: SpawnReservation, incarnation: str,
        registration: RegistrationRecord | None = None,
    ) -> None:
        super().__init__(queue, task, incarnation)
        self.reservation = reservation
        self.registration = registration

    @contextmanager
    def ci_launch(self, venue: str, workspace: str) -> Iterator[ExecutionSendAttempt]:
        try:
            token = ci_launch_observation.begin_if_unqualified(
                self.queue, self.task, self.reservation, self.registration, venue, workspace,
            )
        except (sqlite3.Error, OSError) as exc:
            raise ci_launch_observation.ObservationConflict(
                "CI launch observation admission is unconfirmed"
            ) from exc
        if token is None:
            with self.attempt("launch") as attempt:
                yield attempt
            return
        legacy = ExecutionSendAttempt()
        handle = None
        try:
            yield legacy
            handle = legacy.staging_handle if legacy.accepted else None
        finally:
            try:
                ci_launch_observation.settle(self.queue, self.reservation.key, token, handle)
            except (TaskError, sqlite3.Error, OSError):
                # Keep pending; observation failure must not discard an accepted body's handle.
                logging.getLogger(__name__).exception("CI launch settlement not recorded")

    @contextmanager
    def attempt(self, kind: SendKind) -> Iterator[ExecutionSendAttempt]:
        attempt = ExecutionSendAttempt()
        prepared = self.prepare()
        if prepared is None:
            if kind == "launch" and self.task.source == "ci-repair":
                raise ci_launch_observation.ObservationConflict("CI launch requires its target")
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
