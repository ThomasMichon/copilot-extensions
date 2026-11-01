"""Source-only preparation under the existing supervisor's actual singleton."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from .pool_publication_custody import (
    PoolCatalogRead, PoolMetadataRequest, PoolPublicationError,
    PoolPublicationInputs, PoolPublisherCustody,
)
from .pool_source_snapshot import DeclarationRead, local_input_revision
from .single_instance import SingleInstance, SingletonAdmissionError, lock_path_for

log = logging.getLogger("agent-dispatch.supervisor-daemon")


class SupervisorPoolPublication:
    """No serve-loop caller, publisher enrollment or metadata write transport."""

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

    def _init_pool_publication(self) -> None:
        self._pool_publisher_custody: PoolPublisherCustody | None = None

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

    def _pool_acquisition(self) -> tuple[SingleInstance, str]:
        lock = self._lock
        if (
            not isinstance(lock, SingleInstance)
            or lock.lock_path != self._build_lock().lock_path
            or not (acquisition := lock.acquisition)
        ):
            raise SingletonAdmissionError("pool metadata requires this scope's actual singleton acquisition")
        return lock, acquisition

    def _read_pool_local_inputs(self) -> tuple[str, bool]:
        from . import overrides as ov
        from .registrar_reconcile import declared_registrations

        source = self.declared_source() if self.declared_source is not None else None
        declarations = declared_registrations(
            list(source) if source is not None else [], machine=self.machine, env=self.env,
        )
        complete = source is None or isinstance(source, DeclarationRead) and source.complete
        if self.overrides_source is None:
            from .config import overrides_path

            overrides = ov.load_overrides(overrides_path(), strict=True)
        else:
            overrides = self.overrides_source()
            ov.validate_overrides(overrides)
        return local_input_revision(copy.deepcopy(declarations), copy.deepcopy(dict(overrides))), complete

    def capture_pool_publication_inputs(self) -> PoolPublicationInputs:
        """Bounded optimistic source rechecks; never send an enrollment/write.

        A matching digest is observed coherence, not an atomic filesystem fence.
        External file writers and configured-cohort admission still need their
        own integration before any production caller can use these inputs.
        """
        lock, acquisition = self._pool_acquisition()
        with lock.guard_acquisition(acquisition), self._source_collection_lock:
            capture_token = self._pool_capture_deadline.set(time.monotonic() + self._reconcile_call_budget)
            reasons = []
            catalog = None
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
                    if self._source_observations["pool-local-recheck"].state != "fresh" or not complete:
                        reasons.append("local-input-recheck-not-fresh")
                    if revision != snapshot.input_revision:
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
                reasons.extend(self._pool_source_snapshot.reasons)
            except Exception as exc:
                # This observation authorizes no effects; failures remain explicit.
                log.warning("pool publication input capture unavailable: error_type=%s", type(exc).__name__)
                reasons.append("coordinator-input-capture-unavailable")
            finally:
                self._pool_capture_deadline.reset(capture_token)
                self._prune_probe_inflight()
            if self._pool_acquisition() != (lock, acquisition):
                raise SingletonAdmissionError("pool source capture changed the singleton scope")
            return PoolPublicationInputs(
                self.machine or "", self.env, acquisition, self._pool_source_snapshot,
                catalog, tuple(sorted(set(reasons))),
            )

    def prepare_pool_metadata_request(self) -> PoolMetadataRequest:
        """Capture and retain one original request without issuing a write RPC."""
        lock, acquisition = self._pool_acquisition()
        with lock.guard_acquisition(acquisition), self._source_collection_lock:
            if self._pool_publisher_custody is None:
                self._pool_publisher_custody = PoolPublisherCustody(acquisition)
            custody = self._pool_publisher_custody
            if custody.publisher_id != acquisition or custody.pending is not None:
                raise PoolPublicationError("original pool publisher custody remains unsettled")
            return custody.prepare(self.capture_pool_publication_inputs())
