"""Fresh registrar projection preparation; no sender, activation or attestation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from .pool_publication_custody import PoolPublicationError
from .queue import TaskQueue
from .queue_pool_cohort import PoolConfiguredCohort, _encode_observation
from .queue_records import TaskError
from .registrar_configuration import ConfiguredSupervisorCohort
from .registrar_discovery import RegistrarSources, pointers_file


@dataclass(frozen=True)
class PoolCohortProjectionRequest:
    """One original owner observation and its original coordinator CAS."""

    observation: ConfiguredSupervisorCohort
    previous: PoolConfiguredCohort
    source_path: Path
    coordinator_path: Path

    @property
    def expected_revision(self) -> int:
        return self.previous.revision

    def to_dict(self) -> dict[str, object]:
        return {
            "expected_revision": self.expected_revision,
            "observation": json.loads(_encode_observation(self.observation)),
        }


def _comparable_observation(observation: ConfiguredSupervisorCohort) -> None:
    try:
        _encode_observation(observation)
    except TaskError as exc:
        raise PoolPublicationError("configured owner observation is malformed") from exc
    if observation.source_revision is None and not (
        observation.state == "unconfirmed" and observation.reason == "pointer-registry-absent"
    ):
        raise PoolPublicationError(
            "configured owner observation has no comparable source evidence"
        )


class PoolCohortPreparation:
    """Explicit source-only custody through the actual registrar and queue owners."""

    def __init__(self, sources: RegistrarSources, queue: TaskQueue) -> None:
        if not isinstance(sources, RegistrarSources) or not isinstance(queue, TaskQueue):
            raise PoolPublicationError(
                "cohort preparation requires the registrar and queue owners"
            )
        self._sources = sources
        self._queue = queue
        self._lock = Lock()
        self._pending: PoolCohortProjectionRequest | None = None
        self._settled: tuple[PoolCohortProjectionRequest, PoolConfiguredCohort] | None = None

    @contextmanager
    def _state(self) -> Iterator[None]:
        if not self._lock.acquire(timeout=5):
            raise PoolPublicationError("configured cohort custody lock deadline exhausted")
        try:
            yield
        finally:
            self._lock.release()

    @property
    def pending(self) -> PoolCohortProjectionRequest | None:
        with self._state():
            return self._pending

    @property
    def settled(self) -> tuple[PoolCohortProjectionRequest, PoolConfiguredCohort] | None:
        with self._state():
            return self._settled

    def prepare(self) -> PoolCohortProjectionRequest:
        """Bracket two fresh whole owner reads; retain no request on incoherence."""
        with self._state():
            if self._pending is not None:
                raise PoolPublicationError("original configured cohort request remains unresolved")
            source_path = pointers_file(self._sources.base).absolute()
            coordinator_path = Path(self._queue.db_path).absolute()
            before = self._queue.pool_configured_cohort()
            observation = self._sources.configured_supervisor_cohort()
            _comparable_observation(observation)
            recheck = self._sources.configured_supervisor_cohort()
            _comparable_observation(recheck)
            after = self._queue.pool_configured_cohort()
            if (
                source_path != pointers_file(self._sources.base).absolute()
                or coordinator_path != Path(self._queue.db_path).absolute()
            ):
                raise PoolPublicationError(
                    "configured cohort source or coordinator target changed"
                )
            if observation != recheck:
                raise PoolPublicationError("configured owner source changed during preparation")
            if before != after:
                raise PoolPublicationError("coordinator cohort changed during preparation")
            if self._settled is not None:
                original, previous = self._settled
                if (
                    source_path != original.source_path
                    or coordinator_path != original.coordinator_path
                ):
                    raise PoolPublicationError(
                        "configured cohort owner target changed after settlement"
                    )
                if after.revision < previous.revision or (
                    after.revision == previous.revision and after != previous
                ):
                    raise PoolPublicationError(
                        "coordinator cohort regressed or reused its revision"
                    )
            request = PoolCohortProjectionRequest(
                observation,
                after,
                source_path,
                coordinator_path,
            )
            self._pending = request
            return request

    def _acknowledge(
        self,
        request: PoolCohortProjectionRequest,
        result: PoolConfiguredCohort,
    ) -> None:
        if self._pending is not request:
            raise PoolPublicationError("cohort result does not match the original request")
        expected = request.expected_revision + (
            request.previous.observation != request.observation
        )
        if (
            not isinstance(result, PoolConfiguredCohort)
            or type(result.revision) is not int
            or result.revision != expected
            or result.observation != request.observation
        ):
            raise PoolPublicationError(
                "cohort result failed the original observation and CAS fences"
            )
        self._settled = (request, result)
        self._pending = None

    def acknowledge(
        self,
        request: PoolCohortProjectionRequest,
        result: PoolConfiguredCohort,
    ) -> None:
        """Account for an original result even after the source or cohort changes."""
        with self._state():
            self._acknowledge(request, result)

    def recover(self) -> PoolConfiguredCohort | None:
        """Inspect original provider history only; never re-read a source or write."""
        with self._state():
            request = self._pending
            if request is None:
                raise PoolPublicationError("cohort recovery requires an original prepared request")
            if Path(self._queue.db_path).absolute() != request.coordinator_path:
                raise PoolPublicationError("cohort recovery lost the original coordinator target")
            result = self._queue.recover_pool_configured_cohort(
                request.observation,
                expected_revision=request.expected_revision,
            )
            if result is not None:
                self._acknowledge(request, result)
            return result
