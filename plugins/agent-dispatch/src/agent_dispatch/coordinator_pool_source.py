"""Fresh projection admission against this coordinator's actual registrar."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .coordinator_loops import DrainGate
from .queue import TaskQueue
from .queue_pool_cohort import PoolConfiguredCohort
from .queue_records import TaskError
from .registrar_configuration import ConfiguredSupervisorCohort
from .registrar_discovery import RegistrarSources, pointers_file


class CoordinatorPoolSource:
    """Bind the existing projection route to one source, without publishing it."""

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
        if self._gate.draining or not self._is_active():
            raise TaskError("fresh configured cohort projection requires the active coordinator")

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
