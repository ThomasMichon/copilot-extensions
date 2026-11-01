"""Explicit metadata sends within the existing future and singleton owner."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from .pool_publication_custody import (
    PoolCatalogRead, PoolMetadataRequest, PoolPublicationError, PoolPublicationInputs,
    PoolPublisherCustody,
)
from .pool_publication_response import read_pool_scope
from .queue_pool_catalog import PoolPublicationScope
from .single_instance import SingleInstance, SingletonAdmissionError

log = logging.getLogger("agent-dispatch.supervisor-daemon")


@dataclass
class PoolMetadataFlight:
    request: PoolMetadataRequest
    future: Future[PoolPublicationScope]
    acknowledged: PoolPublicationScope | None = None


class SupervisorPoolSender:
    """No tick, serve, enrollment activation or placement caller."""

    if TYPE_CHECKING:
        client: Any
        _source_collection_lock: Any
        _pool_publisher_custody: PoolPublisherCustody | None
        _pool_capture_deadline: ContextVar[float | None]
        _reconcile_call_budget: float
        _probe_inflight: set[Future]
        _read_pool: Callable[[], Executor]
        _pool_lock: Callable[[], SingleInstance]
        _pool_preparation_boundary: Callable[[], AbstractContextManager[tuple[SingleInstance, str]]]
        capture_pool_publication_inputs: Callable[[], PoolPublicationInputs]

    def _init_pool_sender(self) -> None:
        self._pool_metadata_flight: PoolMetadataFlight | None = None
        self._pool_metadata_closed = Event()
        self._pool_metadata_admission_lock = Lock()

    def _close_pool_metadata_admission(self) -> None:
        with self._pool_metadata_admission_lock:
            self._pool_metadata_closed.set()

    @contextmanager
    def _pool_request_state(self, deadline: float) -> Iterator[None]:
        if not self._source_collection_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise PoolPublicationError("pool request custody lock deadline exhausted")
        try:
            yield
        finally:
            self._source_collection_lock.release()

    @contextmanager
    def _pool_send_boundary(self, request: PoolMetadataRequest) -> Iterator[None]:
        with self._pool_preparation_boundary() as (lock, acquisition):
            if self._pool_metadata_closed.is_set():
                raise PoolPublicationError("pool metadata send admission is closed")
            custody = self._pool_publisher_custody
            if (
                custody is None or custody.pending is not request
                or custody.publisher_id != acquisition or request.inputs.publisher_id != acquisition
            ):
                raise PoolPublicationError("pool send lost original acquisition or request custody")
            current = self.capture_pool_publication_inputs()
            original = request.inputs
            if (
                current.catalog != original.catalog
                or any(reason.startswith("coordinator-") for reason in current.reasons)
                or "local-inputs-changed" in current.reasons
                or "local-inputs-changed" in original.reasons
                or original.snapshot.observation_revision is None
                or current.snapshot.observation_revision != original.snapshot.observation_revision
                or original.local_recheck_revision != original.snapshot.observation_revision
                or current.local_recheck_revision != original.snapshot.observation_revision
                or current.snapshot.input_revision != original.snapshot.input_revision
                or current.snapshot.definitions != original.snapshot.definitions
                or current.reasons != original.reasons
                or (request.reason is None and not current.coherent)
            ):
                raise PoolPublicationError("pool send original source or coordinator fences changed")
            if self._pool_lock() is not lock or lock.acquisition != acquisition:
                raise SingletonAdmissionError("pool send changed the original singleton scope")
            deadline = self._pool_capture_deadline.get()
            if deadline is None or not self._pool_metadata_admission_lock.acquire(
                timeout=max(0.0, deadline - time.monotonic()),
            ):
                raise PoolPublicationError("pool metadata POST admission deadline exhausted")
            try:
                if self._pool_metadata_closed.is_set():
                    raise PoolPublicationError("pool metadata send admission is closed")
                if time.monotonic() >= deadline:
                    raise PoolPublicationError("pool metadata send deadline exhausted before POST")
            finally:
                self._pool_metadata_admission_lock.release()
        yield

    def _run_pool_metadata_send(
        self, request: PoolMetadataRequest, deadline: float,
    ) -> PoolPublicationScope:
        token = self._pool_capture_deadline.set(deadline)
        try:
            # An accepted original result can be accounted even after release.
            observed = self.client.pool_catalog_metadata()
            PoolCatalogRead.from_dict(observed)
            scope = next(
                (item for item in observed["scopes"] if
                 (item["machine"], item["env"]) == (request.inputs.machine, request.inputs.env)),
                None,
            )
            if scope is not None:
                result = read_pool_scope(scope)
                expected = request.generation + (request.operation == "begin")
                if (
                    result.publisher_id == request.inputs.publisher_id
                    and result.generation == expected
                    and result.revision == (
                        0 if request.operation == "begin" else request.expected_revision + 1
                    )
                ):
                    return result
            return self.client.send_pool_metadata(
                request, boundary=lambda: self._pool_send_boundary(request),
            )
        except Exception as exc:
            log.warning("pool metadata send unresolved: error_type=%s", type(exc).__name__)
            raise
        finally:
            self._pool_capture_deadline.reset(token)

    def _new_pool_metadata_flight(
        self, request: PoolMetadataRequest, deadline: float,
    ) -> PoolMetadataFlight:
        with self._pool_metadata_admission_lock:
            if self._pool_metadata_closed.is_set():
                raise PoolPublicationError("pool metadata send admission is closed")
            future = self._read_pool().submit(self._run_pool_metadata_send, request, deadline)
            self._probe_inflight.add(future)
            flight = PoolMetadataFlight(request, future)
            self._pool_metadata_flight = flight
            return flight

    def _wait_pool_metadata_result(
        self, flight: PoolMetadataFlight, deadline: float,
    ) -> PoolPublicationScope:
        return flight.future.result(timeout=max(0.0, deadline - time.monotonic()))

    def send_pool_metadata_request(self) -> PoolPublicationScope:
        """Send/recover only the original prepared request with a bounded wait.

        A timeout retains the same future until completion. A failed or
        malformed reply retains the request; another explicit invocation first
        inspects the accepted catalog, then retries only under original fences.
        Returning metadata never admits a publisher for allocation.
        """
        deadline = time.monotonic() + self._reconcile_call_budget
        with self._pool_request_state(deadline):
            custody = self._pool_publisher_custody
            if custody is None or custody.pending is None:
                raise PoolPublicationError("pool metadata send requires an original prepared request")
            request = custody.pending
            flight = self._pool_metadata_flight
            if flight is not None and flight.future.done() and (
                flight.future.cancelled() or flight.future.exception() is not None
            ):
                failure = None if flight.future.cancelled() else flight.future.exception()
                log.warning(
                    "completed pool metadata flight failed; recovering original request: error_type=%s",
                    "CancelledError" if failure is None else type(failure).__name__,
                )
                self._pool_metadata_flight = None
                flight = None
            if flight is None or flight.request is not request:
                flight = self._new_pool_metadata_flight(request, deadline)
        try:
            result = self._wait_pool_metadata_result(flight, deadline)
            with self._pool_request_state(deadline):
                if flight.acknowledged is not None:
                    return flight.acknowledged
                try:
                    custody.acknowledge(request, result)
                except PoolPublicationError:
                    if self._pool_metadata_flight is flight:
                        self._pool_metadata_flight = None
                    raise
                flight.acknowledged = result
                if self._pool_metadata_flight is flight:
                    self._pool_metadata_flight = None
        except Exception as exc:
            log.warning("pool metadata result unresolved: error_type=%s", type(exc).__name__)
            if flight.future.done() and (
                flight.future.cancelled() or flight.future.exception() is not None
            ):
                with self._pool_request_state(deadline):
                    if self._pool_metadata_flight is flight:
                        self._pool_metadata_flight = None
            raise
        return result
