"""Source-only preparation under the existing supervisor's actual singleton."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from .pool_publication_custody import (
    PoolCatalogRead, PoolMetadataRequest, PoolPublicationError,
    PoolPublicationInputs, PoolPublisherCustody,
)
from .pool_source_snapshot import DeclarationRead, local_input_revision
from .single_instance import SingleInstance, SingletonAdmissionError, lock_path_for
from .supervisor_pool_sender import SupervisorPoolSender

log = logging.getLogger("agent-dispatch.supervisor-daemon")


class SupervisorPoolPublication(SupervisorPoolSender):
    """Explicit preparation/sending, never called by the serve loop."""

    if TYPE_CHECKING:
        machine: str | None
        env: str
        client: Any
        declared_source: Callable[[], Iterable[Any]] | None
        overrides_source: Callable[[], Mapping[str, dict]] | None
        _lock: Any
        _reconcile_deadline: float | None
        _pool_capture_deadline: ContextVar[float | None]
        _reconcile_call_budget: float
        _source_collection_lock: Any
        _source_observations: dict
        _pool_source_snapshot: Any
        _effective_registrations: Callable[[], dict[str, dict]]
        _bounded_read: Callable[[str, Callable], Any]
        _prune_probe_inflight: Callable[[], None]
        _read_source_declarations: Callable[[Callable[[], Iterable[Any]]], Iterable[Any]]
        _read_source_overrides: Callable[[], Mapping[str, dict]]

    def _init_pool_publication(self) -> None:
        self._pool_publisher_custody: PoolPublisherCustody | None = None
        self._init_pool_sender()

    def _build_lock(self) -> SingleInstance:
        from .config import run_dir
        from .supervisor_daemon import supervisor_lease_scope

        scope = supervisor_lease_scope(self.machine, self.env)
        return SingleInstance(lock_path_for(run_dir(), scope))

    def acquire_singleton(self) -> bool:
        """Acquire this machine/environment's crash-safe OS lock, or stand down."""
        if self._lock is None:
            self._lock = self._build_lock()
        return bool(self._lock.acquire())

    def release_singleton(self) -> None:
        """Release this daemon's single-instance lock (best-effort)."""
        if self._lock is None:
            return
        try:
            self._lock.release()
        except Exception:  # pragma: no cover -- best-effort
            log.exception("error releasing supervisor lock")

    def _pool_lock(self) -> SingleInstance:
        lock = self._lock
        if (
            not isinstance(lock, SingleInstance)
            or lock.lock_path != self._build_lock().lock_path
        ):
            raise SingletonAdmissionError("pool metadata requires this scope's actual singleton acquisition")
        return lock

    @contextmanager
    def _pool_preparation_boundary(self) -> Iterator[tuple[SingleInstance, str]]:
        deadline = self._pool_capture_deadline.get()
        token = None
        if deadline is None:
            deadline = time.monotonic() + self._reconcile_call_budget
            token = self._pool_capture_deadline.set(deadline)
        try:
            lock = self._pool_lock()
            with lock.guard_current_acquisition(deadline=deadline) as acquisition:
                if not self._source_collection_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                    raise PoolPublicationError("pool source collection lock deadline exhausted")
                try:
                    if time.monotonic() >= deadline:
                        raise PoolPublicationError("pool preparation deadline exhausted")
                    yield lock, acquisition
                finally:
                    self._source_collection_lock.release()
        finally:
            if token is not None:
                self._pool_capture_deadline.reset(token)

    def _read_pool_local_inputs(self) -> tuple[str, bool]:
        from .registrar_reconcile import declared_registrations

        source = self._read_source_declarations(self.declared_source) if self.declared_source is not None else None
        declarations = declared_registrations(
            list(source) if source is not None else [], machine=self.machine, env=self.env,
        )
        complete = source is None or isinstance(source, DeclarationRead) and source.complete
        overrides = self._read_source_overrides()
        return local_input_revision(copy.deepcopy(declarations), copy.deepcopy(dict(overrides))), complete

    def capture_pool_publication_inputs(self) -> PoolPublicationInputs:
        """Bounded optimistic source rechecks; never send an enrollment/write.

        A matching digest is observed coherence, not an atomic filesystem fence.
        External file writers and configured-cohort admission still need their
        own integration before any production caller can use these inputs.
        """
        with self._pool_preparation_boundary() as (lock, acquisition):
            reasons = []
            catalog = None
            local_recheck_revision = None
            try:
                self._prune_probe_inflight()
                before = self._bounded_read("pool-catalog-before", self.client.pool_catalog_metadata)
                before = PoolCatalogRead.from_dict(before)
                if self._source_observations["pool-catalog-before"].state != "fresh":
                    reasons.append("coordinator-input-read-not-fresh")
                try:
                    self._effective_registrations()
                    snapshot = self._pool_source_snapshot
                    revision, complete = self._bounded_read("pool-local-recheck", self._read_pool_local_inputs)
                    if self._source_observations["pool-local-recheck"].state == "fresh":
                        local_recheck_revision = revision
                    if self._source_observations["pool-local-recheck"].state != "fresh" or not complete:
                        reasons.append("local-input-recheck-not-fresh")
                    if revision != snapshot.observation_revision:
                        reasons.append("local-inputs-changed")
                except Exception as exc:
                    log.warning("pool local input capture unavailable: error_type=%s", type(exc).__name__)
                    reasons.append("local-input-capture-unavailable")
                after = self._bounded_read("pool-catalog-after", self.client.pool_catalog_metadata)
                catalog = PoolCatalogRead.from_dict(after)
                if self._source_observations["pool-catalog-after"].state != "fresh":
                    reasons.append("coordinator-input-recheck-not-fresh")
                if before != catalog:
                    reasons.append("coordinator-inputs-changed")
                if not catalog.permits_scope(self.machine or "", self.env):
                    reasons.append("coordinator-configured-cohort-unavailable-or-excluded")
                reasons.extend(self._pool_source_snapshot.reasons)
            except Exception as exc:
                # This observation authorizes no effects; failures remain explicit.
                log.warning("pool publication input capture unavailable: error_type=%s", type(exc).__name__)
                reasons.append("coordinator-input-capture-unavailable")
            finally:
                self._prune_probe_inflight()
            if self._pool_lock() is not lock:
                raise SingletonAdmissionError("pool source capture changed the singleton scope")
            return PoolPublicationInputs(
                self.machine or "", self.env, acquisition, self._pool_source_snapshot,
                catalog, tuple(sorted(set(reasons))), local_recheck_revision,
            )

    def prepare_pool_metadata_request(self) -> PoolMetadataRequest:
        """Capture and retain one original request without issuing a write RPC."""
        with self._pool_preparation_boundary() as (_lock, acquisition):
            if self._pool_publisher_custody is None:
                self._pool_publisher_custody = PoolPublisherCustody(acquisition)
            custody = self._pool_publisher_custody
            if custody.publisher_id != acquisition or custody.pending is not None:
                raise PoolPublicationError("original pool publisher custody remains unsettled")
            return custody.prepare(self.capture_pool_publication_inputs())
