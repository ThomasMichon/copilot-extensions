"""Bounded discovery, provider and receipt waits for the singleton master."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor, TimeoutError
from typing import Any

from .companion import CompanionError, CompanionIndeterminate
from .managed_runtime import ManagedRuntimeError

log = logging.getLogger("agent-dispatch.supervisor-daemon")


class BoundedReconciliation:
    """Internal master mixin; futures remain in the existing shutdown accounting."""

    def _init_bounded_reconciliation(self) -> None:
        self._reconcile_deadline = None
        self._receipt_reconcile_future = None
        self._receipt_executor = None
        self._read_executor = None
        self._resolution_futures = {}
        self._discovery_futures = {}
        self._last_store_registrations = []
        self._progress_callback = None
        self._phase_name = "idle"
        self._progress_warning_after: dict[str, float] = {}

    def _shutdown_bounded_pools(self, drained: bool) -> None:
        for field in ("_receipt_executor", "_read_executor"):
            pool = getattr(self, field)
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
                if drained:
                    setattr(self, field, None)

    def _phase(self, phase: str) -> None:
        self._phase_name = phase
        if self._progress_callback is not None:
            try:
                self._progress_callback(self.progress_snapshot())
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                self._warn_progress_failure("publication-unavailable", exc)

    def _warn_progress_failure(self, reason: str, exc: Exception) -> None:
        now = time.monotonic()
        if now < self._progress_warning_after.get(reason, float("-inf")):
            return
        self._progress_warning_after[reason] = now + 30.0
        error_type = (
            "io" if isinstance(exc, OSError)
            else "invalid-observation" if isinstance(exc, (ValueError, TypeError))
            else "runtime-state"
        )
        log.warning(
            "supervisor optional progress unavailable: reason=%s error_type=%s",
            reason, error_type,
        )

    def progress_snapshot(self) -> dict:
        snapshot = {
            "phase": self._phase_name, "observed_at": self.clock(),
            "cycle_id": self._cycle_count,
        }
        try:
            operations = getattr(self._companion(), "receipt_operation_progress", None)
            snapshot["receipt_operations"] = operations() if callable(operations) else []
        except (OSError, ValueError, CompanionError, CompanionIndeterminate, ManagedRuntimeError) as exc:
            self._warn_progress_failure("receipt-progress-unavailable", exc)
            snapshot.update(receipt_operations=None, observation_error="receipt-progress-unavailable")
        return snapshot

    def _bounded_read(self, key: str, read: Callable[[], Any]) -> Any:
        if self._reconcile_deadline is None:
            return read()
        future = self._discovery_futures.get(key)
        if future is None:
            future = self._read_pool().submit(read)
            self._discovery_futures[key] = future
            self._probe_inflight.add(future)
        try:
            return future.result(timeout=max(0.0, self._reconcile_deadline - time.monotonic()))
        finally:
            if future.done():
                self._discovery_futures.pop(key, None)

    def _receipt_pool(self) -> Executor:
        if self._receipt_executor is None:
            self._receipt_executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="dispatch-receipt",
            )
        return self._receipt_executor

    def _read_pool(self) -> Executor:
        if self._read_executor is None:
            self._read_executor = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="dispatch-discovery",
            )
        return self._read_executor

    def _resolve_companion(self, rid: str, registration: dict, authority: str) -> Any:
        if self._reconcile_deadline is None:
            return self._companion().resolve(
                copy.deepcopy(registration), machine=self.machine, env=self.env,
            )
        entry = self._resolution_futures.get(rid)
        if entry is not None and entry[0] != authority:
            entry[1].cancel()
            if not entry[1].done():
                raise CompanionIndeterminate("prior companion provider remains pending")
            self._resolution_futures.pop(rid)
            entry = None
        if entry is None:
            captured = copy.deepcopy(registration)
            future = self._read_pool().submit(lambda: self._companion().resolve(
                captured, machine=self.machine, env=self.env,
            ))
            self._probe_inflight.add(future)
            self._resolution_futures[rid] = (authority, future)
        else:
            future = entry[1]
        try:
            return future.result(timeout=max(0.0, self._reconcile_deadline - time.monotonic()))
        except TimeoutError as exc:
            raise CompanionIndeterminate("companion provider remains pending") from exc
        finally:
            if future.done():
                self._resolution_futures.pop(rid, None)

    def _reconcile_companion_receipts(self, adopted: set[str]) -> None:
        controller = self._companion()
        bounded = getattr(controller, "reconcile_receipts_bounded", None)
        if callable(bounded):
            try:
                self._managed_uncertain.update(bounded(
                    adopted, executor=self._receipt_pool(),
                    deadline=self._reconcile_deadline if self._reconcile_deadline is not None
                    else time.monotonic() + self._reconcile_call_budget,
                ))
            finally:
                self._probe_inflight.update(controller.receipt_futures())
            return
        if self._receipt_reconcile_future is None:
            self._receipt_reconcile_future = self._receipt_pool().submit(
                self._companion().reconcile_receipts, adopted,
            )
            self._probe_inflight.add(self._receipt_reconcile_future)
        try:
            self._receipt_reconcile_future.result(timeout=max(
                0.0, (self._reconcile_deadline or time.monotonic()) - time.monotonic(),
            ))
        except TimeoutError:
            log.warning("companion receipt reconciliation remains pending")
        else:
            self._receipt_reconcile_future = None
