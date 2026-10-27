"""Finite allocation reconciliation owned and drained by the elected resident."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Iterable

import yaml

from . import allocation_link_discovery as discovery, owned_worktree as allocation
from . import config as cfg, tracking
from .tracking_allocation_write import apply_worktree_allocation

CAPABILITY = "worktree-allocation-linker-v1"
_LOG = logging.getLogger(__name__)
_DISCOVERY_FAILURES = (
    OSError, ValueError, RuntimeError, KeyError, TypeError, OverflowError, yaml.YAMLError,
)


def _may_hold_allocation(claim: tracking.ResourceClaim) -> bool:
    metadata = claim.producer
    return (
        claim.metadata_opaque or claim.ref.startswith(allocation.NAMESPACE + ":")
        or claim.has_producer and (
            not isinstance(metadata, dict)
            or metadata.get("lifetime") is None
            or metadata.get("lifetime") == allocation.LIFETIME
            or metadata.get("namespace") == allocation.NAMESPACE
        )
    )


@dataclass(frozen=True)
class Candidate:
    project: str
    owner_ref: str
    path: Path
    token: str

    def request(self) -> dict:
        return {
            "schema": allocation.SCHEMA, "version": allocation.VERSION,
            "operation": "reconcile", "input": {"token": self.token},
            "owner_ref": self.owner_ref, "yaml_path": str(self.path),
        }


class AllocationLinkRuntime:
    """Only retries exact existing responsibility; never creates or cancels."""

    def __init__(
        self, *, elected: Callable[[], bool], context_lock: ContextManager,
        wake: threading.Event, now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._elected, self._context_lock, self._wake, self._now = elected, context_lock, wake, now
        self._lock = threading.Lock()
        self._admitting = False
        self._closed = False
        self._epoch = 0
        self._worker: threading.Thread | None = None
        self._finished = threading.Event()
        self._finished.set()
        self._candidates: dict[tuple[str, str], Candidate] = {}
        self._retry: dict[tuple[str, str], tuple[float, int]] = {}
        self._discover: dict[str, float] = {}
        self._startup_pending: set[str] = set()
        self._initialized = False
        self._next_registry_refresh = 0.0
        self._discovery_error: str | None = None
        self._errors: dict[str, str] = {}
        self._claim_errors: dict[tuple[str, str], str] = {}

    def activate(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("allocation linker is closed")
            if not self._admitting:
                self._epoch += 1
                self._admitting = True

    def close_admission(self) -> None:
        with self._lock:
            self._admitting = False
            self._epoch += 1

    def _eligible(self, epoch: int) -> bool:
        with self._lock:
            admitted = self._admitting and self._epoch == epoch
        return admitted and self._elected()

    def observe(
        self, project: str, records: Iterable[tracking.WorktreeRecord], *,
        inventory_complete: bool = False,
    ) -> bool:
        found: dict[tuple[str, str], Candidate] = {}
        closed: set[tuple[str, str]] = set()
        malformed = False
        for record in records:
            for claim in record.resources:
                metadata = claim.producer
                if not _may_hold_allocation(claim):
                    continue
                try:
                    if not discovery.locally_owned(record):
                        continue
                except _DISCOVERY_FAILURES as exc:
                    malformed = True
                    _LOG.warning("Allocation creator authority unavailable for %s: %s", project, type(exc).__name__)
                    continue
                if claim.metadata_opaque or not allocation.valid_metadata(metadata):
                    malformed = True
                    continue
                creator = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
                if (
                    record.repo != project or metadata["creator"] != creator
                    or allocation.claim_ref(metadata) != claim.ref or record.yaml_path is None
                ):
                    malformed = True
                    continue
                key = (creator, metadata["token"])
                if metadata["phase"] == "closed":
                    closed.add(key)
                    continue
                if key in found:
                    malformed = True
                    continue
                found[key] = Candidate(project, creator, Path(record.yaml_path), metadata["token"])
        with self._lock:
            if not self._admitting:
                return False
            for key in closed:
                self._candidates.pop(key, None)
                self._retry.pop(key, None)
                self._claim_errors.pop(key, None)
            self._candidates.update(found)
            if malformed:
                self._errors[project] = "malformed allocation responsibility; inspect the original ledger"
                self._discover[project] = self._now() + 30.0
            elif inventory_complete:
                self._errors.pop(project, None)
        if malformed:
            _LOG.warning("Allocation discovery for %s found malformed responsibility", project)
        return not malformed

    def discover(self, *, budget: int = 4) -> None:
        """Called under the resident's project-context lock, with no network I/O."""
        with self._lock:
            if not self._admitting:
                return
            refresh = not self._initialized or self._now() >= self._next_registry_refresh
        if refresh:
            try:
                projects = discovery.registered_projects()
            except _DISCOVERY_FAILURES as exc:
                with self._lock:
                    self._discovery_error = type(exc).__name__
                _LOG.warning("Allocation restart discovery unavailable: %s", type(exc).__name__)
                return
            with self._lock:
                self._startup_pending.update(projects.difference(self._discover))
                for project in projects:
                    self._discover.setdefault(project, 0.0)
                if not self._initialized:
                    self._startup_pending.update(projects)
                self._initialized = True
                self._next_registry_refresh = self._now() + 60.0
                self._discovery_error = None
        with self._lock:
            projects = [
                project for due, project in sorted(
                    (due, project) for project, due in self._discover.items()
                ) if due <= self._now()
            ][:budget]
        for project in projects:
            try:
                with cfg.ConfigCacheSession().scope():
                    complete = self.observe(
                        project, discovery.current_records(project), inventory_complete=True,
                    )
            except _DISCOVERY_FAILURES as exc:
                with self._lock:
                    self._errors[project] = "current allocation ledger unavailable: " + type(exc).__name__
                    self._discover[project] = self._now() + 30.0
                _LOG.warning("Allocation ledger discovery unavailable for %s: %s", project, type(exc).__name__)
            else:
                with self._lock:
                    if complete:
                        self._discover[project] = self._now() + 60.0
                        self._startup_pending.discard(project)

    def tick(self) -> None:
        with self._lock:
            if not self._admitting or not self._finished.is_set():
                return
            due = [
                (self._retry.get(key, (0.0, 0))[0], key, candidate)
                for key, candidate in self._candidates.items()
                if self._retry.get(key, (0.0, 0))[0] <= self._now()
            ]
            if not due:
                return
            _, key, candidate = min(due)
            epoch = self._epoch
            self._finished = threading.Event()
            self._worker = threading.Thread(
                target=self._run, args=(epoch, key, candidate, self._finished),
                name="worktree-allocation-link", daemon=False,
            )
            try:
                self._worker.start()
            except BaseException:
                if self._worker.ident is None:
                    self._finished.set()
                raise

    def _run(
        self, epoch: int, key: tuple[str, str], candidate: Candidate, finished: threading.Event,
    ) -> None:
        result = None
        error = None
        try:
            result = apply_worktree_allocation(
                candidate.request(), authority_check=lambda: self._eligible(epoch),
                context_lock=self._context_lock,
            )
            if result.get("error"):
                error = "allocation reconciliation refused; responsibility retained"
        except Exception as exc:
            error = "allocation reconciliation failed: " + type(exc).__name__
        finally:
            try:
                self._complete_attempt(key, candidate, result, error)
            finally:
                finished.set()
                self._wake.set()

    def _complete_attempt(
        self, key: tuple[str, str], candidate: Candidate, result: dict | None, error: str | None,
    ) -> None:
        with self._lock:
            if self._candidates.get(key) != candidate:
                return
            failures = self._retry.get(key, (0.0, 0))[1]
            if error is not None:
                failures += 1
                self._claim_errors[key] = error
            else:
                failures = 0
                self._claim_errors.pop(key, None)
            delay = min(60.0, 5.0 * 2 ** min(failures, 4)) if error else 60.0
            if isinstance(result, dict) and result.get("phase") == "bound":
                delay = 0.0
            if isinstance(result, dict) and result.get("phase") == "closed":
                self._candidates.pop(key, None)
                self._retry.pop(key, None)
            else:
                self._retry[key] = (self._now() + delay, failures)
        if error is not None:
            _LOG.warning("%s: %s", candidate.owner_ref, error)

    def active_count(self) -> int:
        with self._lock:
            return int(not self._finished.is_set())

    def has_demand(self) -> bool:
        with self._lock:
            return bool(
                not self._initialized or self._startup_pending or self._candidates
                or self._errors or self._claim_errors or self._discovery_error
                or not self._finished.is_set()
            )

    def health(self) -> dict:
        with self._lock:
            return {
                "capabilities": [CAPABILITY],
                "allocationLinker": {
                    "admitting": self._admitting,
                    "active": int(not self._finished.is_set()),
                    "pending": len(self._candidates),
                    "discovering": not self._initialized or bool(self._startup_pending),
                    "errors": len(self._errors) + len(self._claim_errors) + int(self._discovery_error is not None),
                },
            }

    def shutdown_and_wait(self) -> None:
        self.close_admission()
        with self._lock:
            self._closed = True
            worker = self._worker
            finished = self._finished
        error = None
        while not finished.is_set():
            try:
                finished.wait()
            except BaseException as exc:
                if error is None:
                    error = exc
        if worker is not None:
            worker.join()
        if error is not None:
            raise error
