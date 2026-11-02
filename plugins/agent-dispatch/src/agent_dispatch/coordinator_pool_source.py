"""Projection and opt-in maintenance against this coordinator's actual registrar."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from .coordinator_loops import DrainGate
from .pool_cohort_preparation import PoolCohortPreparation
from .pool_publication_custody import PoolPublicationError
from .queue import TaskQueue
from .queue_pool_cohort import PoolConfiguredCohort
from .queue_records import TaskError
from .registrar_configuration import ConfiguredSupervisorCohort
from .registrar_discovery import RegistrarSources, pointers_file

log = logging.getLogger("agent-dispatch.coordinator")


class CoordinatorPoolSource:
    """Keep whole-cohort publication with the coordinator, never a host master."""

    def __init__(
        self, queue: TaskQueue, sources: RegistrarSources, *,
        is_active: Callable[[], bool], gate: DrainGate,
    ) -> None:
        self.queue = queue
        self.sources = sources
        self._is_active = is_active
        self._gate = gate
        self._queue_path = Path(queue.db_path).absolute()
        self._source_path = pointers_file(sources.base).absolute()
        self._closed = False
        self._preparation = PoolCohortPreparation(sources, queue, checkpoint=self._require_active)
        self._maintenance: asyncio.Task[PoolConfiguredCohort] | None = None

    def _require_queue(self) -> None:
        if Path(self.queue.db_path).absolute() != self._queue_path:
            raise TaskError("configured cohort coordinator target changed")

    def _require_source(self, observation: ConfiguredSupervisorCohort) -> None:
        if pointers_file(self.sources.base).absolute() != self._source_path:
            raise TaskError("configured cohort registrar target changed")
        current = self.sources.configured_supervisor_cohort()
        if pointers_file(self.sources.base).absolute() != self._source_path:
            raise TaskError("configured cohort registrar target changed")
        if current != observation:
            raise TaskError("configured cohort does not match the coordinator registrar source")

    def _require_active(self) -> None:
        self._require_queue()
        if pointers_file(self.sources.base).absolute() != self._source_path:
            raise TaskError("configured cohort registrar target changed")
        if self._closed or self._gate.draining or not self._is_active():
            raise TaskError("fresh configured cohort projection requires the active coordinator")

    def _reconcile(self) -> PoolConfiguredCohort:
        with self._gate.track_claim():
            self._require_queue()
            self._require_active()
            preparation = self._preparation
            request = preparation.pending
            if request is None:
                request = preparation.prepare()
            try:
                return preparation.project(request)
            except (PoolPublicationError, TaskError):
                try:
                    preparation.discard_unaccepted(request)
                except PoolPublicationError:
                    log.warning("configured cohort maintenance retains its unresolved original request")
                raise

    async def maintain(self, *, timeout: float) -> PoolConfiguredCohort:
        """Use the existing GC cadence; a timeout never starts another worker."""
        if self._closed:
            raise PoolPublicationError("configured cohort maintenance is closed")
        if self._maintenance is None:
            self._maintenance = asyncio.create_task(asyncio.to_thread(self._reconcile))
            self._maintenance.add_done_callback(self._maintenance_finished)
        task = self._maintenance
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        finally:
            if task.done() and self._maintenance is task:
                self._maintenance = None

    @staticmethod
    def _maintenance_finished(task: asyncio.Task[PoolConfiguredCohort]) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.warning(
                "configured cohort maintenance failed: error_type=%s",
                type(task.exception()).__name__,
            )

    async def close(self, *, timeout: float = 10.0) -> bool:
        """Close admission and retain drain custody until the actual worker ends."""
        self._closed = True
        task = self._maintenance
        if task is None:
            return True
        _done, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            log.warning("configured cohort maintenance has not drained; original worker retained")
            return False
        self._maintenance = None
        return True

    def project(
        self, observation: ConfiguredSupervisorCohort, *, expected_revision: int,
    ) -> PoolConfiguredCohort:
        """Recover original history first; fresh writes need observed owner input.

        These source rechecks are optimistic, not an atomic filesystem fence.
        No provider-side pointer lock is acquired across a client that may
        already hold that same registrar's writer lock.
        """
        self._require_queue()
        accepted = self.queue.recover_pool_configured_cohort(
            observation, expected_revision=expected_revision,
        )
        if accepted is not None:
            return accepted
        with self._gate.track_claim():
            self._require_active()
            self._require_source(observation)
            self._require_queue()
            self._require_active()
            result = self.queue.observe_pool_configured_cohort(
                observation, expected_revision=expected_revision,
            )
            self._require_queue()
            self._require_source(observation)
            return result
