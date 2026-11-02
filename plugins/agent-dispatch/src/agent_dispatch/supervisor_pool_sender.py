"""Explicit metadata sends within the existing future and singleton owner."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from .client_errors import DispatchError
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


@dataclass
class PoolMetadataBinding:
    request: PoolMetadataRequest
    client: Any
    target: object
    post_admitted: bool = False
    closed: bool = False
    children: set[Future] = field(default_factory=set)


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
        self._pool_metadata_binding: PoolMetadataBinding | None = None
        self._pool_metadata_owner: PoolMetadataBinding | None = None
        self._pool_send_binding: ContextVar[PoolMetadataBinding | None] = ContextVar(
            "pool_send_binding", default=None,
        )
        self._pool_metadata_closed = Event()
        self._pool_metadata_admission_lock = Lock()

    def _bind_pool_metadata_request(self, request: PoolMetadataRequest, client: Any) -> None:
        self._pool_metadata_binding = PoolMetadataBinding(
            request, client, getattr(client, "base_url", None),
        )
        if self._pool_metadata_owner is None:
            self._pool_metadata_owner = self._pool_metadata_binding

    def _require_pool_metadata_target(self, client: Any) -> None:
        owner = self._pool_metadata_owner
        if owner is not None and (
            getattr(client, "base_url", None) != owner.target
            or owner.target is None and client is not owner.client
        ):
            raise PoolPublicationError("pool publisher coordinator target changed after settlement")

    def _original_pool_metadata_binding(self, request: PoolMetadataRequest) -> PoolMetadataBinding:
        binding = self._pool_metadata_binding
        if (
            binding is None or binding.request is not request or binding.closed
            or getattr(binding.client, "base_url", None) != binding.target
        ):
            raise PoolPublicationError("pool metadata lost its original coordinator client target")
        return binding

    def _track_pool_metadata_future(self, future: Future) -> None:
        binding = self._pool_send_binding.get()
        if binding is not None:
            binding.children = {child for child in binding.children if not child.done()}
            binding.children.add(future)

    @staticmethod
    def _require_pool_metadata_children_retired(binding: PoolMetadataBinding) -> None:
        if any(not future.done() for future in binding.children):
            raise PoolPublicationError("original pool metadata source operations remain in progress")

    def _close_retained_pool_metadata_client(self, *, defer: bool = False) -> None:
        binding = self._pool_metadata_binding
        if binding is None or binding.closed:
            return
        if defer and any(not future.done() for future in binding.children):
            return
        self._require_pool_metadata_children_retired(binding)
        if binding.client is self.client:
            return
        flight = self._pool_metadata_flight
        if flight is not None and not flight.future.done():
            raise PoolPublicationError("original pool metadata client still has an in-flight operation")
        close = getattr(binding.client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                if not defer:
                    raise
                log.exception("accepted pool metadata transport retirement deferred")
                return
        binding.closed = True

    def _replace_pool_metadata_client(self, new_client: Any) -> None:
        deadline = time.monotonic() + self._reconcile_call_budget
        with self._pool_request_state(deadline):
            old = self.client
            self.client = new_client
            binding = self._pool_metadata_binding
            custody = self._pool_publisher_custody
            retained = (
                binding is not None and binding.client is old and not binding.closed
                and (
                    custody is not None and custody.pending is binding.request
                    or any(not future.done() for future in binding.children)
                )
            )
        if old is not new_client and not retained:
            close = getattr(old, "close", None)
            if callable(close):
                close()
            if binding is not None and binding.client is old:
                binding.closed = True

    def discard_unaccepted_pool_metadata_request(self, request: PoolMetadataRequest) -> None:
        """Supersede only local preparation or a definitively refused original."""
        deadline = time.monotonic() + self._reconcile_call_budget
        with self._pool_request_state(deadline):
            custody = self._pool_publisher_custody
            if custody is None or custody.pending is not request:
                raise PoolPublicationError("pool discard requires the original pending request")
            binding = self._pool_metadata_binding
            if binding is None or binding.request is not request:
                raise PoolPublicationError("pool discard lost its original request binding")
            self._require_pool_metadata_children_retired(binding)
            flight = self._pool_metadata_flight
            if flight is not None:
                if not flight.future.done():
                    raise PoolPublicationError("original pool metadata flight remains in progress")
                if not flight.future.cancelled() and flight.future.exception() is None:
                    raise PoolPublicationError("original pool metadata result remains unacknowledged")
            if binding.post_admitted:
                raise PoolPublicationError("original pool metadata POST may have been accepted")
            self._close_retained_pool_metadata_client()
            custody.pending = None
            self._pool_metadata_flight = None
            self._pool_metadata_binding = None
            if custody.position is None:
                self._pool_metadata_owner = None
            log.info("discarded original pool metadata request before POST admission")

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
            binding = self._original_pool_metadata_binding(request)
            if self.client is not binding.client:
                raise PoolPublicationError("pool metadata coordinator client changed before POST")
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
                self._original_pool_metadata_binding(request)
                binding.post_admitted = True
            finally:
                self._pool_metadata_admission_lock.release()
        yield

    def _run_pool_metadata_send(
        self, request: PoolMetadataRequest, deadline: float,
    ) -> PoolPublicationScope:
        token = self._pool_capture_deadline.set(deadline)
        binding_token = None
        try:
            binding = self._original_pool_metadata_binding(request)
            binding_token = self._pool_send_binding.set(binding)
            prior_admission = binding.post_admitted
            client = binding.client
            # An accepted original result can be accounted even after release.
            observed = client.pool_catalog_metadata()
            self._original_pool_metadata_binding(request)
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
            try:
                return client.send_pool_metadata(
                    request, boundary=lambda: self._pool_send_boundary(request),
                )
            except DispatchError as exc:
                if exc.is_pool_catalog_rejection(request.operation):
                    binding.post_admitted = prior_admission
                raise
        except Exception as exc:
            log.warning("pool metadata send unresolved: error_type=%s", type(exc).__name__)
            raise
        finally:
            if binding_token is not None:
                self._pool_send_binding.reset(binding_token)
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
                    self._original_pool_metadata_binding(request)
                    custody.acknowledge(request, result)
                except PoolPublicationError:
                    if self._pool_metadata_flight is flight:
                        self._pool_metadata_flight = None
                    raise
                flight.acknowledged = result
                if self._pool_metadata_flight is flight:
                    self._pool_metadata_flight = None
                self._close_retained_pool_metadata_client(defer=True)
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
