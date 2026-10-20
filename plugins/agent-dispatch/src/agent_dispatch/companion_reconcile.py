"""Exact-generation receipt reconciliation; observation is not restart authority."""

from __future__ import annotations

import logging
import fnmatch
import math
import os
import threading
import time
from concurrent.futures import CancelledError, Executor, Future, TimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from plugin_activation import write_json_object_atomic

from . import companion as lifecycle
from .managed_runtime import ManagedRuntimeError, _canonical_digest, _read_metadata, _RootLock, _safe_directory

log = logging.getLogger(__name__)


@dataclass
class _AvailabilityRead:
    future: Future
    valid: bool = True
    consumed: bool = False


class ReceiptReconciliation:
    """Internal controller mixin sharing its receipt and process-identity primitives."""

    def _init_receipt_reconciliation(self) -> None:
        self._receipt_operations: dict[str, Future] = {}
        self._receipt_submission_records: dict[str, tuple[Future, dict]] = {}
        self._receipt_adopted: set[str] = set()
        self._receipt_locks_guard = threading.Lock()
        self._receipt_metadata_locks = {}
        self._receipt_availability_guard = threading.RLock()
        self._receipt_availability: dict[str, _AvailabilityRead] = {}
        self._receipt_availability_executor: Executor | None = None
        self._receipt_availability_deadline = 0.0
        self._receipt_availability_tag = None
        self._receipt_owner_future: Future | None = None
        self._receipt_owner_identity: tuple[int, str] | None = None
        self._receipt_inventory_future: Future | None = None
        self._receipt_inventory_blocked: set[str] = set()
        self._receipt_inventory_tag = None
        self._receipt_last_scheduled: str | None = None

    def receipt_futures(self) -> tuple[Future, ...]:
        with self._receipt_availability_guard:
            availability = tuple(entry.future for entry in self._receipt_availability.values())
        return tuple(self._receipt_operations.values()) + (
            (self._receipt_owner_future,) if self._receipt_owner_future is not None else ()
        ) + ((self._receipt_inventory_future,) if self._receipt_inventory_future is not None else ()) + availability

    def request_receipt_inventory(
        self, adopted: set[str], *, inventory_executor: Executor, executor: Executor,
        deadline: float, operation_budget: float, cycle_tag=None,
        availability_executor: Executor | None = None,
    ) -> set[str]:
        self._receipt_adopted = set(adopted)
        tag = cycle_tag if cycle_tag is not None else deadline
        with self._receipt_availability_guard:
            self._receipt_availability_executor = availability_executor
            self._receipt_availability_deadline = deadline
            if tag != self._receipt_availability_tag:
                self._receipt_availability_tag = tag
                for rid, entry in tuple(self._receipt_availability.items()):
                    future = entry.future
                    if rid not in adopted:
                        future.cancel()
                        if not future.done():
                            entry.valid = False
                            continue
                    elif not (future.done() and (entry.consumed or future.cancelled())):
                        continue
                    self._receipt_availability.pop(rid)
        inventory_tag = (tag, frozenset(adopted))
        if self._receipt_inventory_future is None and self._receipt_inventory_tag != inventory_tag:
            self._receipt_inventory_tag = inventory_tag
            self._receipt_inventory_future = inventory_executor.submit(
                self._inventory_snapshot, executor, operation_budget,
            )
        future = self._receipt_inventory_future
        if future is None:
            return self._receipt_inventory_blocked | set(self._receipt_operations)
        if not future.done():
            return self._receipt_inventory_blocked | set(self._receipt_operations)
        try:
            blocked = future.result()
        except TimeoutError:
            if future.done():
                raise
        else:
            self._receipt_inventory_blocked = blocked
        finally:
            if future.done():
                self._receipt_inventory_future = None
        return self._receipt_inventory_blocked | set(self._receipt_operations)

    def _inventory_snapshot(self, executor: Executor, budget: float) -> set[str]:
        return self.reconcile_receipts_bounded(
            self._receipt_adopted, executor=executor,
            deadline=self.monotonic() + budget, _inventory=True,
        )

    def _receipt_owner_token(self, executor: Executor, deadline: float) -> str | None:
        pid = os.getpid()
        if self._receipt_owner_identity is not None and self._receipt_owner_identity[0] == pid:
            return self._receipt_owner_identity[1]
        if self._receipt_owner_future is None:
            self._receipt_owner_future = executor.submit(lambda: (pid, self.token_source(pid)))
        future = self._receipt_owner_future
        try:
            owner, token = future.result(
                timeout=max(0.0, deadline - self.monotonic())
            )
        except TimeoutError:
            if future.done():
                raise
            return None
        finally:
            if future.done() and self._receipt_owner_future is future:
                self._receipt_owner_future = None
        if owner != pid or not isinstance(token, str) or not token:
            raise lifecycle.CompanionIndeterminate("receipt operation owner identity is unavailable")
        self._receipt_owner_identity = (owner, token)
        return token

    def _operation_path(self, registration_id: str) -> Path:
        return self._receipt_path(registration_id).with_suffix(".reconcile-operation.json")

    @contextmanager
    def _receipt_metadata_lock(self, path: Path):
        path = path.absolute()
        with self._receipt_locks_guard:
            lock, users = self._receipt_metadata_locks.get(path, (threading.RLock(), 0))
            self._receipt_metadata_locks[path] = (lock, users + 1)
        acquired = False
        try:
            acquired = lock.acquire(timeout=0.1)
            if not acquired:
                raise lifecycle.CompanionIndeterminate("receipt metadata remains in use")
            yield
        finally:
            if acquired:
                lock.release()
            with self._receipt_locks_guard:
                _, users = self._receipt_metadata_locks[path]
                if users == 1:
                    self._receipt_metadata_locks.pop(path)
                else:
                    self._receipt_metadata_locks[path] = (lock, users - 1)

    def _write_operation(self, rid: str, record: dict) -> None:
        with self._receipt_metadata_lock(self._operation_path(rid)):
            with self._receipt_availability_guard:
                entry = self._receipt_availability.get(rid)
                if entry is not None:
                    entry.valid = False
            history = _safe_directory(self.receipt_dir.absolute(), "reconcile-history")
            identity = {key: record[key] for key in (
                "registration_id", "generation_digest", "owner_pid", "owner_start_token",
                "started_at", "state",
            )}
            milestone = history / f"{_canonical_digest(identity)}.json"
            if not milestone.exists() and not milestone.is_symlink():
                write_json_object_atomic(milestone, record)
            else:
                previous = self._receipt_metadata(milestone)
                if any(previous.get(key) != value for key, value in identity.items()):
                    raise lifecycle.CompanionIndeterminate("receipt operation history is inconsistent")
            write_json_object_atomic(self._operation_path(rid), record)

    def _receipt_metadata(self, path: Path) -> dict:
        try:
            with self._receipt_metadata_lock(path):
                return _read_metadata(self.receipt_dir.absolute(), path.absolute())
        except (ManagedRuntimeError, OSError) as exc:
            raise lifecycle.CompanionIndeterminate(
                "receipt metadata unavailable"
            ) from exc

    def _receipt_generation(self, registration_id: str) -> dict:
        path = self._selection_path(registration_id)
        receipt_path = self._receipt_path(registration_id)
        receipt = (
            self._receipt_metadata(receipt_path)
            if receipt_path.exists() or receipt_path.is_symlink() else None
        )
        if receipt is not None:
            if type(receipt.get("schema_version")) is not int or receipt["schema_version"] != lifecycle._RECEIPT_VERSION:
                raise lifecycle.CompanionIndeterminate("companion receipt version is unsupported")
            if receipt.get("registration_id") != registration_id:
                raise lifecycle.CompanionIndeterminate("companion receipt identity is inconsistent")
        return {
            "receipt": receipt,
            "selection": self._receipt_metadata(path)
            if path.exists() or path.is_symlink() else None,
        }

    def _operation_record(self, registration_id: str) -> dict | None:
        path = self._operation_path(registration_id)
        if not path.exists() and not path.is_symlink():
            return None
        record = self._receipt_metadata(path)
        if (
            type(record.get("schema_version")) is not int or record["schema_version"] != 1
            or record.get("registration_id") != registration_id
            or not isinstance(record.get("state"), str)
            or record.get("state") not in {"pending", "timeout", "unknown", "complete", "superseded"}
            or not isinstance(record.get("generation"), dict)
            or set(record["generation"]) != {"receipt", "selection"}
            or any(value is not None and not isinstance(value, dict) for value in record["generation"].values())
            or record.get("generation_digest") != _canonical_digest(record["generation"])
            or type(record.get("owner_pid")) is not int
            or record["owner_pid"] <= 0
            or not isinstance(record.get("owner_start_token"), str)
            or not record["owner_start_token"]
            or any(type(record.get(key)) not in (int, float) for key in (
                "deadline", "started_at", "observed_at",
            ))
            or any(not math.isfinite(record[key]) for key in (
                "deadline", "started_at", "observed_at",
            ))
        ):
            raise lifecycle.CompanionIndeterminate("receipt operation identity is invalid")
        return record

    def _assert_receipt_available(self, registration_id: str) -> None:
        with self._receipt_availability_guard:
            executor = self._receipt_availability_executor
        if executor is None:
            self._check_receipt_available(registration_id)
            return
        with self._receipt_availability_guard:
            entry = self._receipt_availability.get(registration_id)
            if entry is not None and not entry.valid and entry.future.done():
                self._receipt_availability.pop(registration_id)
                entry = None
            if entry is None:
                entry = _AvailabilityRead(executor.submit(self._check_receipt_available, registration_id))
                self._receipt_availability[registration_id] = entry
            future = entry.future
            deadline = self._receipt_availability_deadline
        accepted = False
        try:
            try:
                future.result(timeout=max(0.0, min(0.05, deadline - time.monotonic())))
            except TimeoutError as exc:
                if not future.done():
                    raise lifecycle.CompanionIndeterminate("receipt availability remains pending") from exc
                future.result()
            with self._receipt_availability_guard:
                current = self._receipt_availability.get(registration_id)
                if current is None or current is not entry or not current.valid:
                    raise lifecycle.CompanionIndeterminate("receipt availability observation is superseded")
                current.consumed = True
                accepted = True
        except CancelledError as exc:
            raise lifecycle.CompanionIndeterminate("receipt availability observation was cancelled") from exc
        finally:
            if future.done() and not accepted:
                with self._receipt_availability_guard:
                    current = self._receipt_availability.get(registration_id)
                    if current is entry:
                        self._receipt_availability.pop(registration_id)

    def _check_receipt_available(self, registration_id: str) -> None:
        record = self._operation_record(registration_id)
        if record is not None and record["state"] in {"pending", "timeout", "unknown"}:
            raise lifecycle.CompanionIndeterminate("receipt reconciliation outcome is not confirmed")
        self._receipt_generation(registration_id)

    def _retirement_readback(self, generation: dict, *, deadline: float) -> bool:
        if self.monotonic() >= deadline:
            return False
        receipt = generation["receipt"]
        if receipt is None:
            return True
        pid, token = receipt.get("pid"), receipt.get("start_token")
        if type(pid) is not int or not isinstance(token, str) or not token:
            return False
        current = self.token_source(pid)
        confirmed = current != token and not (
            current is None and (lifecycle._process_exists(pid) or lifecycle._process_group_exists(pid))
        )
        return confirmed and self.monotonic() < deadline

    def _readback_receipt_operation(self, rid: str, operation: dict, deadline: float) -> None:
        with _RootLock(
            self.receipt_dir, scope=_canonical_digest(rid),
            timeout=min(0.1, max(0.0, deadline - self.monotonic())),
        ):
            current = self._operation_record(rid)
            if current is None or any(
                current[key] != operation[key] for key in
                ("generation_digest", "owner_pid", "owner_start_token", "started_at")
            ):
                return
            generation = self._receipt_generation(rid)
            state = "complete" if (
                all(value is None for value in generation.values())
                and self._retirement_readback(operation["generation"], deadline=deadline)
            ) else "unknown"
            if state == "complete" and any(
                value is not None for value in self._receipt_generation(rid).values()
            ):
                state = "unknown"
            if state == "complete" and not self._managed_retention().selection_withdrawn(
                rid, self.receipt_dir, deadline=deadline,
            ):
                state = "unknown"
            with self._receipt_metadata_lock(self._operation_path(rid)):
                latest = self._operation_record(rid)
                if latest == current:
                    self._write_operation(rid, {
                        **current, "state": state, "observed_at": time.time(),
                    })

    def _receipt_paths(self, pattern: str) -> list[Path]:
        try:
            with os.scandir(self.receipt_dir) as entries:
                return sorted(Path(entry.path) for entry in entries if fnmatch.fnmatch(entry.name, pattern))
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.warning("receipt directory observation unavailable: error_type=%s", type(exc).__name__)
            raise lifecycle.CompanionIndeterminate("receipt directory observation is unavailable") from exc

    def receipt_operation_progress(self) -> list[dict]:
        progress = []
        unavailable = None
        for path in self._receipt_paths("*.reconcile-operation.json"):
            try:
                raw = self._receipt_metadata(path)
                rid = raw.get("registration_id")
                if not isinstance(rid, str) or path != self._operation_path(rid):
                    raise lifecycle.CompanionIndeterminate("receipt operation progress identity is invalid")
                record = self._operation_record(rid)
                if record is None:
                    continue
            except (lifecycle.CompanionIndeterminate, OSError) as exc:
                log.warning("receipt progress unavailable for %s: error_type=%s", path.name, type(exc).__name__)
                if unavailable is None:
                    unavailable = exc
                continue
            progress.append({key: record[key] for key in (
                "registration_id", "generation_digest", "state", "started_at", "observed_at",
            )})
        if unavailable is not None:
            raise lifecycle.CompanionIndeterminate("receipt operation progress is unavailable") from unavailable
        return progress

    def reconcile_receipts_bounded(
        self, adopted_registration_ids: set[str], *, executor: Executor, deadline: float,
        _inventory: bool = False,
    ) -> set[str]:
        """One tracked operation per receipt; restart reads back rather than replays."""
        if not _inventory:
            self._receipt_adopted = set(adopted_registration_ids)
        for rid, future in tuple(self._receipt_operations.items()):
            if future.done():
                submission = self._receipt_submission_records.get(rid)
                captured = submission[1] if submission is not None and submission[0] is future else None
                try:
                    if future.cancelled() and captured is not None:
                        self._supersede_unstarted_receipt(rid, captured)
                    else:
                        future.result()
                except (
                    lifecycle.CompanionError, lifecycle.CompanionIndeterminate,
                    ManagedRuntimeError, OSError, CancelledError,
                ) as exc:
                    log.warning("receipt operation incomplete for %s: error_type=%s", rid, type(exc).__name__)
                    if future.cancelled() and captured is not None:
                        continue
                self._receipt_operations.pop(rid)
                if captured is not None:
                    self._receipt_submission_records.pop(rid)
        ids: set[str] = set()
        for pattern, path_for in (
            ("*.companion.json", self._receipt_path),
            ("*.managed-launch.json", self._selection_path),
            ("*.reconcile-operation.json", self._operation_path),
        ):
            for path in self._receipt_paths(pattern):
                try:
                    record = self._receipt_metadata(path)
                    rid = record.get("registration_id")
                    if not isinstance(rid, str) or not rid or path != path_for(rid):
                        raise lifecycle.CompanionIndeterminate("receipt path does not match its identity")
                except (lifecycle.CompanionIndeterminate, OSError) as exc:
                    log.warning("receipt inventory unavailable for %s: error_type=%s", path.name, type(exc).__name__)
                    continue
                ids.add(rid)
        blocked: set[str] = set()
        ordered = sorted(ids)
        if self._receipt_last_scheduled is not None:
            ordered = (
                [rid for rid in ordered if rid > self._receipt_last_scheduled]
                + [rid for rid in ordered if rid <= self._receipt_last_scheduled]
            )
        for rid in ordered:
            try:
                self._schedule_receipt(
                    rid, blocked, executor, deadline,
                    self._receipt_adopted if _inventory else adopted_registration_ids,
                )
            except (lifecycle.CompanionError, lifecycle.CompanionIndeterminate, ManagedRuntimeError, OSError) as exc:
                blocked.add(rid)
                log.warning("receipt reconciliation unavailable for %s: error_type=%s", rid, type(exc).__name__)
        return blocked

    def _schedule_receipt(
        self, rid: str, blocked: set[str], executor: Executor, deadline: float, adopted: set[str],
    ) -> None:
        record = self._operation_record(rid)
        if record is not None and record["state"] in {"pending", "timeout", "unknown"}:
            blocked.add(rid)
            if rid not in self._receipt_operations:
                if record["state"] != "unknown":
                    self._write_operation(rid, {
                        **record, "state": "unknown", "observed_at": time.time(),
                    })
                if len(self._receipt_operations) < 2 and self.monotonic() < deadline:
                    self._receipt_operations[rid] = executor.submit(
                        self._readback_receipt_operation, rid, record, deadline,
                    )
                    self._receipt_last_scheduled = rid
            elif self.monotonic() >= record["deadline"]:
                with self._receipt_metadata_lock(self._operation_path(rid)):
                    latest = self._operation_record(rid)
                    if latest is not None and latest["state"] == "pending":
                        self._write_operation(rid, {
                            **latest, "state": "timeout", "observed_at": time.time(),
                        })
            return
        if rid in adopted:
            return
        if len(self._receipt_operations) >= 2 or self.monotonic() >= deadline:
            return
        generation = self._receipt_generation(rid)
        if all(value is None for value in generation.values()):
            return
        token = self._receipt_owner_token(executor, deadline)
        if token is None or self.monotonic() >= deadline:
            return
        record = {
            "schema_version": 1, "registration_id": rid,
            "generation": generation, "generation_digest": _canonical_digest(generation),
            "state": "pending", "owner_pid": os.getpid(), "owner_start_token": token,
            "started_at": time.time(), "deadline": deadline, "observed_at": time.time(),
        }
        self._write_operation(rid, record)
        execution = Future()

        def execute():
            if not execution.set_running_or_notify_cancel():
                return
            try:
                self._reconcile_receipt_generation(rid, record)
            except BaseException as error:
                execution.set_exception(error)
                raise
            else:
                execution.set_result(None)

        try:
            future = executor.submit(execute)
        except RuntimeError as exc:
            self._receipt_submission_records[rid] = (execution, record)
            self._receipt_operations[rid] = execution
            if execution.cancel():
                self._supersede_unstarted_receipt(rid, record)
            raise lifecycle.CompanionIndeterminate("receipt operation submission was rejected") from exc
        self._receipt_submission_records[rid] = (future, record)
        self._receipt_operations[rid] = future
        self._receipt_last_scheduled = rid
        blocked.add(rid)

    def _supersede_unstarted_receipt(self, rid: str, captured: dict) -> None:
        with self._receipt_metadata_lock(self._operation_path(rid)):
            current = self._operation_record(rid)
            if (
                current is not None
                and captured["owner_pid"] == os.getpid()
                and current["state"] in {"pending", "timeout", "unknown"}
                and all(current[key] == captured[key] for key in (
                    "generation_digest", "owner_pid", "owner_start_token", "started_at",
                ))
            ):
                self._write_operation(rid, {
                    **current, "state": "superseded", "observed_at": time.time(),
                })

    def _reconcile_receipt_generation(self, rid: str, operation: dict) -> None:
        state = "unknown"
        try:
            with _RootLock(self.receipt_dir, scope=_canonical_digest(rid), timeout=0.1):
                current = self._operation_record(rid)
                if (
                    current is None
                    or any(current[key] != operation[key] for key in (
                        "generation_digest", "owner_pid", "owner_start_token", "started_at",
                    ))
                    or self.token_source(operation["owner_pid"]) != operation["owner_start_token"]
                ):
                    raise lifecycle.CompanionIndeterminate("receipt operation owner is superseded")
                generation = operation["generation"]
                if self._receipt_generation(rid) != generation:
                    state = "superseded"
                    return
                managed = (
                    generation["selection"] is not None
                    or generation["receipt"] is not None
                    and "managed_snapshot" in generation["receipt"]
                )
                snapshot = self.selected_managed(rid) if managed else None
                validated = None
                if snapshot is not None:
                    validated = self._managed_retention().validate_withdrawal(
                        rid, self.receipt_dir, deadline=operation["deadline"],
                    )
                if self.monotonic() >= operation["deadline"]:
                    raise lifecycle.CompanionIndeterminate("receipt operation execution deadline expired")
                if rid in self._receipt_adopted or self._receipt_generation(rid) != generation:
                    state = "superseded"
                    return
                if generation["receipt"] is not None:
                    self._retire_receipt(generation["receipt"], confirm_exit=True)
                    if self._read_receipt(rid) != generation["receipt"]:
                        raise lifecycle.CompanionIndeterminate("receipt changed during retirement")
                    self._delete_receipt(rid)
                    if self._read_receipt(rid) is not None:
                        raise lifecycle.CompanionIndeterminate("receipt deletion is not confirmed")
                # Retirement may consume the execution budget; finish only exact metadata.
                completion_deadline = self.monotonic() + 1
                if snapshot is not None:
                    self.forget_managed(
                        rid, _operation=operation, _validated=validated,
                        _deadline=completion_deadline,
                    )
                if not self._managed_retention().selection_withdrawn(
                    rid, self.receipt_dir, deadline=completion_deadline,
                ):
                    raise lifecycle.CompanionIndeterminate("managed selection withdrawal is not confirmed")
                state = "complete"
        finally:
            with self._receipt_metadata_lock(self._operation_path(rid)):
                current = self._operation_record(rid)
                if current is not None and all(
                    current[key] == operation[key]
                    for key in ("generation_digest", "owner_pid", "owner_start_token", "started_at")
                ):
                    self._write_operation(rid, {
                        **current, "state": state, "observed_at": time.time(),
                    })
