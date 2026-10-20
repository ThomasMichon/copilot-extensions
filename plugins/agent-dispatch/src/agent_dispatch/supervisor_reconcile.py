"""Bounded discovery, provider and receipt waits for the singleton master."""

from __future__ import annotations

import copy
import logging
import time
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError, Executor, Future, ThreadPoolExecutor, TimeoutError
from typing import Any

from .companion import CompanionIndeterminate

log = logging.getLogger("agent-dispatch.supervisor-daemon")


class BoundedReconciliation:
    """Internal master mixin; futures remain in the existing shutdown accounting."""

    def _init_bounded_reconciliation(self) -> None:
        self._reconcile_deadline = None
        self._reconcile_token = None
        self._receipt_reconcile_future = None
        self._receipt_executor = None
        self._read_executor = None
        self._publication_executor = None
        self._provider_executor = None
        self._inventory_executor = None
        self._progress_executor = None
        self._progress_future = None
        self._progress_worker_active = False
        self._progress_latest = None
        self._progress_cache = None
        self._status_callback = None
        self._status_latest = None
        self._status_start_latest = None
        self._progress_lock = threading.Lock()
        self._retired_bounded_executors = []
        self._declaration_publisher = None
        self._resolution_futures = {}
        self._selection_futures = {}
        self._discovery_futures = {}
        self._last_store_registrations: list[dict] | None = None
        self._progress_callback = None
        self._phase_name = "idle"
        self._progress_warning_after: dict[str, float] = {}

    def _shutdown_bounded_pools(self, drained: bool) -> None:
        for retired in self._retired_bounded_executors:
            retired.shutdown(wait=False, cancel_futures=True)
        if not any(not future.done() for future in self._probe_inflight):
            self._retired_bounded_executors.clear()
        for field in (
            "_receipt_executor", "_read_executor", "_publication_executor",
            "_provider_executor", "_inventory_executor", "_progress_executor",
        ):
            pool = getattr(self, field)
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
                if not drained:
                    self._retired_bounded_executors.append(pool)
                setattr(self, field, None)

    def _phase(self, phase: str) -> None:
        self._phase_name = phase
        with self._progress_lock:
            self._progress_latest = {
                "phase": phase, "observed_at": self.clock(), "cycle_id": self._cycle_count,
            }
        self._start_progress_worker()

    def _start_progress_worker(self) -> None:
        with self._progress_lock:
            previous = self._progress_future
            if self._progress_worker_active:
                if previous is None or not previous.done():
                    return
                self._progress_worker_active = False
            if previous is not None and previous.done() and not previous.cancelled():
                try:
                    previous.result()
                except Exception as exc:
                    self._warn_progress_failure("progress-worker-unavailable", exc)
            try:
                if self._progress_executor is None:
                    self._progress_executor = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="dispatch-progress",
                    )
                self._progress_worker_active = True
                self._progress_future = self._progress_executor.submit(self._publish_progress)
            except RuntimeError as exc:
                self._progress_worker_active = False
                if self._progress_executor is not None:
                    self._progress_executor.shutdown(wait=False, cancel_futures=True)
                    self._progress_executor = None
                self._warn_progress_failure("progress-worker-unavailable", exc)
                return
            self._probe_inflight.add(self._progress_future)

    def queue_final_status(self, summary, callback: Callable) -> None:
        summary.completed_at = self.clock()
        with self._progress_lock:
            self._status_latest = (summary, callback, self._cycle_count)
            self._progress_latest = {
                "phase": "complete", "observed_at": summary.completed_at,
                "cycle_id": self._cycle_count,
            }
        self._start_progress_worker()

    def queue_cycle_start(self, cycle_id: int, started_at: float, callback: Callable) -> None:
        with self._progress_lock:
            self._status_start_latest = (cycle_id, started_at, callback)
            self._progress_latest = {"phase": "starting", "observed_at": started_at, "cycle_id": cycle_id}
        self._start_progress_worker()

    def _publish_progress(self) -> None:
        while True:
            with self._progress_lock:
                snapshot = self._progress_latest
                if snapshot is None:
                    self._progress_worker_active = False
                    return
                self._progress_latest = None
                status = self._status_latest
                self._status_latest = None
                start = self._status_start_latest
                self._status_start_latest = None
            if start is not None:
                try:
                    cycle_id, started_at, callback = start
                    callback(cycle_id, started_at)
                except (OSError, ValueError, TypeError, RuntimeError) as exc:
                    self._warn_progress_failure("publication-unavailable", exc)
            try:
                controller = self._companion_controller
                operations = getattr(controller, "receipt_operation_progress", None)
                if controller is None:
                    snapshot.update(receipt_operations=None, observation_error="receipt-progress-pending")
                elif not callable(operations):
                    snapshot.update(receipt_operations=None, observation_error="receipt-progress-unsupported")
                else:
                    snapshot["receipt_operations"] = operations()
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                self._warn_progress_failure("receipt-progress-unavailable", exc)
                snapshot.update(receipt_operations=None, observation_error="receipt-progress-unavailable")
            self._progress_cache = snapshot
            if self._progress_callback is not None:
                try:
                    self._progress_callback(snapshot)
                except (OSError, ValueError, TypeError, RuntimeError) as exc:
                    self._warn_progress_failure("publication-unavailable", exc)
            if status is not None and status[2] == snapshot["cycle_id"]:
                try:
                    summary, callback, _cycle_id = status
                    callback(summary)
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
        cached = self._progress_cache
        if cached is not None and cached["cycle_id"] == self._cycle_count:
            snapshot.update({key: cached[key] for key in ("receipt_operations", "observation_error") if key in cached})
            snapshot["receipt_observed_at"] = cached["observed_at"]
        else:
            snapshot.update(receipt_operations=None, observation_error="receipt-progress-pending")
        return snapshot

    def _provider_pool(self) -> Executor:
        if self._provider_executor is None:
            self._provider_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dispatch-provider")
        return self._provider_executor

    def _inventory_pool(self) -> Executor:
        if self._inventory_executor is None:
            self._inventory_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dispatch-inventory")
        return self._inventory_executor

    def _prime_receipt_inventory(self, adopted: set[str]) -> None:
        try:
            self._reconcile_companion_receipts(adopted)
        except (OSError, ValueError, RuntimeError) as exc:
            log.warning("receipt inventory unavailable: error_type=%s", type(exc).__name__)

    def _drain_tracked_futures(self, timeout: float) -> None:
        from .supervisor_daemon import _wait_futures

        deadline = time.monotonic() + timeout
        while True:
            self._prune_probe_inflight()
            inventory = getattr(self._companion(), "receipt_futures", None)
            if callable(inventory):
                self._probe_inflight.update(inventory())
            pending = set(self._managed_runtime_inflight) | self._probe_inflight
            if self._managed_cleanup_future is not None:
                pending.add(self._managed_cleanup_future)
            unseen = pending - self._shutdown_waited_futures
            if not unseen:
                return
            _, not_done = _wait_futures(unseen, timeout=max(0.0, deadline - time.monotonic()))
            self._shutdown_waited_futures.update(pending)
            self._shutdown_drained = self._shutdown_drained and not not_done
            if not_done:
                return

    def _publish_declarations(self, registrations: list[dict]) -> set[str]:
        from .supervisor_daemon_registration_publish import BoundedDeclarationPublisher

        if self._declaration_publisher is None:
            self._declaration_publisher = BoundedDeclarationPublisher()
        if self._publication_executor is None:
            self._publication_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="dispatch-publication",
            )
        try:
            return self._declaration_publisher.poll(
                self.client, registrations, executor=self._publication_executor,
                deadline=self._reconcile_deadline if self._reconcile_deadline is not None
                else time.monotonic() + self._reconcile_call_budget,
            )
        finally:
            if self._declaration_publisher.future is not None:
                self._probe_inflight.add(self._declaration_publisher.future)

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

    def _store_registrations(self) -> list[dict]:
        publisher = self._declaration_publisher
        epoch = publisher.withdrawal_epoch if publisher is not None else 0

        def read():
            registrations = self.client.list_registrations(
                machine=self.machine, env=self.env, include_paused=False,
            )
            return epoch, registrations, copy.deepcopy(registrations)

        try:
            started_epoch, registrations, snapshot = self._bounded_read(
                "registrations", read,
            )
        except (CancelledError, TimeoutError) as exc:
            if self._last_store_registrations is None:
                raise CompanionIndeterminate(
                    "registration discovery pending; no confirmed registry snapshot"
                ) from exc
            log.warning(
                "registration discovery unavailable: error_type=%s; retaining confirmed registrations",
                type(exc).__name__,
            )
            return self._last_store_registrations
        if publisher is not None:
            cleared = publisher.observe_registry({reg["id"] for reg in registrations}, started_epoch)
            if cleared is None:
                log.warning("registration discovery predates confirmed withdrawal; discarding stale snapshot")
                if self._last_store_registrations is None:
                    raise CompanionIndeterminate("registration discovery has no confirmed current snapshot")
                return self._last_store_registrations
            self._published_declared_ids.difference_update(cleared)
        self._last_store_registrations = snapshot
        return registrations

    def _declared(self) -> list[dict]:
        if self.declared_source is None:
            return []
        from .registrar_reconcile import declared_registrations

        try:
            registrations = self._bounded_read("declarations", lambda: declared_registrations(
                list(self.declared_source()), machine=self.machine, env=self.env,
            ))
        except TimeoutError as exc:
            if self._last_declared is None:
                raise CompanionIndeterminate(
                    "declaration discovery pending; no confirmed declaration snapshot"
                ) from exc
            log.warning("declaration discovery remains pending; retaining confirmed declarations")
            return self._last_declared
        except Exception as exc:
            log.warning(
                "failed to read declared profile set: error_type=%s; retaining confirmed declarations",
                type(exc).__name__,
            )
            if self._last_declared is None:
                raise CompanionIndeterminate(
                    "declaration discovery unavailable; no confirmed declaration snapshot"
                ) from exc
            return self._last_declared
        self._last_declared = registrations
        self._published_declared_ids = self._publish_declarations(registrations)
        return registrations

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

    def _invalidate_absent_companion_resolutions(self, present: set[str]) -> None:
        for cache in (self._resolution_futures, self._selection_futures):
            for rid in set(cache) - present:
                _, future = cache[rid]
                future.cancel()
                if future.done():
                    cache.pop(rid)
                else:
                    cache[rid] = (None, future)

    def _resolve_companion(self, rid: str, registration: dict, authority: str) -> Any:
        return self._bounded_companion_read(
            self._resolution_futures, rid, authority,
            lambda: self._companion().resolve(
                copy.deepcopy(registration), machine=self.machine, env=self.env,
            ),
            subject="companion provider",
        )

    def _selected_managed(self, rid: str, authority: str) -> Any:
        return self._bounded_companion_read(
            self._selection_futures, rid, authority,
            lambda: self._companion().selected_managed(rid),
            subject="managed selection",
        )

    def _bounded_companion_read(
        self, cache: dict[str, tuple[str | None, Future]], rid: str, authority: str,
        read: Callable[[], Any], *, subject: str,
    ) -> Any:
        if self._reconcile_deadline is None:
            return read()
        entry = cache.get(rid)
        if entry is not None and entry[0] != authority:
            entry[1].cancel()
            if not entry[1].done():
                cache[rid] = (None, entry[1])
                raise CompanionIndeterminate(f"prior {subject} remains pending")
            cache.pop(rid)
            entry = None
        if entry is None:
            future = self._provider_pool().submit(read)
            self._probe_inflight.add(future)
            cache[rid] = (authority, future)
        else:
            future = entry[1]
        try:
            return future.result(timeout=max(0.0, self._reconcile_deadline - time.monotonic()))
        except CancelledError as exc:
            raise CompanionIndeterminate(f"{subject} observation was cancelled") from exc
        except TimeoutError as exc:
            raise CompanionIndeterminate(f"{subject} remains pending") from exc
        finally:
            if future.done():
                cache.pop(rid, None)

    def _reconcile_companion_receipts(self, adopted: set[str]) -> None:
        controller = self._companion()
        deadline = (
            self._reconcile_deadline if self._reconcile_deadline is not None
            else time.monotonic() + self._reconcile_call_budget
        )
        inventory = getattr(controller, "request_receipt_inventory", None)
        if callable(inventory):
            try:
                self._managed_uncertain.update(inventory(
                    adopted, inventory_executor=self._inventory_pool(), executor=self._receipt_pool(),
                    deadline=deadline,
                    operation_budget=self._reconcile_call_budget,
                    cycle_tag=self._reconcile_token,
                    availability_executor=self._provider_pool(),
                ))
            finally:
                self._probe_inflight.update(controller.receipt_futures())
            return
        bounded = getattr(controller, "reconcile_receipts_bounded", None)
        if callable(bounded):
            try:
                self._managed_uncertain.update(bounded(
                    adopted, executor=self._receipt_pool(),
                    deadline=deadline,
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
                0.0, deadline - time.monotonic(),
            ))
        except TimeoutError:
            if self._receipt_reconcile_future.done():
                raise
            log.warning("companion receipt reconciliation remains pending")
        finally:
            if self._receipt_reconcile_future.done():
                self._receipt_reconcile_future = None

    def _withdraw_stopped_managed(self, desired: dict) -> None:
        from .registrations import RegistrationKind

        adopted = set(self._companion_resolutions) | {
            rid for rid, unit in self._units.items()
            if unit.kind == RegistrationKind.PLUGIN_COMPANION
        } | (self._managed_uncertain & set(desired))
        try:
            self._reconcile_companion_receipts(adopted)
        except (CompanionIndeterminate, OSError, ValueError, RuntimeError) as exc:
            log.warning("stopped companion receipt withdrawal remains pending: error_type=%s", type(exc).__name__)
