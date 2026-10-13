"""Exact-generation receipt reconciliation; observation is not restart authority."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from concurrent.futures import Executor, Future, TimeoutError
from pathlib import Path

from plugin_activation import write_json_object_atomic

from . import companion as lifecycle
from .managed_runtime import ManagedRuntimeError, _canonical_digest, _read_metadata, _RootLock, _safe_directory


class ReceiptReconciliation:
    """Internal controller mixin sharing its receipt and process-identity primitives."""

    def _init_receipt_reconciliation(self) -> None:
        self._receipt_operations: dict[str, Future] = {}
        self._receipt_adopted: set[str] = set()
        self._receipt_state_lock = threading.RLock()
        self._receipt_owner_future: Future | None = None
        self._receipt_owner_identity: tuple[int, str] | None = None

    def receipt_futures(self) -> tuple[Future, ...]:
        return tuple(self._receipt_operations.values()) + (
            (self._receipt_owner_future,) if self._receipt_owner_future is not None else ()
        )

    def _receipt_owner_token(self, executor: Executor, deadline: float) -> str | None:
        pid = os.getpid()
        if self._receipt_owner_identity is not None and self._receipt_owner_identity[0] == pid:
            return self._receipt_owner_identity[1]
        if self._receipt_owner_future is None:
            self._receipt_owner_future = executor.submit(lambda: (pid, self.token_source(pid)))
        try:
            owner, token = self._receipt_owner_future.result(
                timeout=max(0.0, deadline - self.monotonic())
            )
        except TimeoutError:
            if self._receipt_owner_future.done():
                raise
            return None
        if owner != pid or not isinstance(token, str) or not token:
            raise lifecycle.CompanionIndeterminate("receipt operation owner identity is unavailable")
        self._receipt_owner_identity = (owner, token)
        return token

    def _operation_path(self, registration_id: str) -> Path:
        return self._receipt_path(registration_id).with_suffix(".reconcile-operation.json")

    def _write_operation(self, rid: str, record: dict) -> None:
        history = _safe_directory(self.receipt_dir.absolute(), "reconcile-history")
        identity = {key: record[key] for key in (
            "registration_id", "generation_digest", "owner_pid", "owner_start_token",
            "started_at", "state",
        )}
        milestone = history / f"{_canonical_digest(identity)}.json"
        if not milestone.exists() and not milestone.is_symlink():
            write_json_object_atomic(milestone, record)
        else:
            previous = _read_metadata(self.receipt_dir.absolute(), milestone.absolute())
            if any(previous.get(key) != value for key, value in identity.items()):
                raise lifecycle.CompanionIndeterminate("receipt operation history is inconsistent")
        write_json_object_atomic(self._operation_path(rid), record)

    def _receipt_generation(self, registration_id: str) -> dict:
        path = self._selection_path(registration_id)
        return {
            "receipt": self._read_receipt(registration_id),
            "selection": _read_metadata(self.receipt_dir.absolute(), path.absolute())
            if path.exists() or path.is_symlink() else None,
        }

    def _operation_record(self, registration_id: str) -> dict | None:
        path = self._operation_path(registration_id)
        if not path.exists() and not path.is_symlink():
            return None
        record = _read_metadata(self.receipt_dir.absolute(), path.absolute())
        if (
            type(record.get("schema_version")) is not int or record["schema_version"] != 1
            or record.get("registration_id") != registration_id
            or record.get("state") not in {"pending", "timeout", "unknown", "complete", "superseded"}
            or not isinstance(record.get("generation"), dict)
            or set(record["generation"]) != {"receipt", "selection"}
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
        record = self._operation_record(registration_id)
        if record is not None and record["state"] in {"pending", "timeout", "unknown"}:
            raise lifecycle.CompanionIndeterminate("receipt reconciliation outcome is not confirmed")

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
            with self._receipt_state_lock:
                latest = self._operation_record(rid)
                if latest == current:
                    self._write_operation(rid, {
                        **current, "state": state, "observed_at": time.time(),
                    })

    def receipt_operation_progress(self) -> list[dict]:
        if not self.receipt_dir.exists():
            return []
        progress = []
        for path in sorted(self.receipt_dir.glob("*.reconcile-operation.json")):
            raw = _read_metadata(self.receipt_dir.absolute(), path.absolute())
            rid = raw.get("registration_id")
            if not isinstance(rid, str) or path != self._operation_path(rid):
                raise lifecycle.CompanionIndeterminate("receipt operation progress identity is invalid")
            record = self._operation_record(rid)
            progress.append({key: record[key] for key in (
                "registration_id", "generation_digest", "state", "started_at", "observed_at",
            )})
        return progress

    def reconcile_receipts_bounded(
        self, adopted_registration_ids: set[str], *, executor: Executor, deadline: float
    ) -> set[str]:
        """One tracked operation per receipt; restart reads back rather than replays."""
        with self._receipt_state_lock:
            self._receipt_adopted = set(adopted_registration_ids)
        if not self.receipt_dir.exists():
            return set()
        ids: set[str] = set()
        for pattern, path_for in (
            ("*.companion.json", self._receipt_path),
            ("*.managed-launch.json", self._selection_path),
            ("*.reconcile-operation.json", self._operation_path),
        ):
            for path in self.receipt_dir.glob(pattern):
                record = _read_metadata(self.receipt_dir.absolute(), path.absolute())
                rid = record.get("registration_id")
                if not isinstance(rid, str) or not rid or path != path_for(rid):
                    raise lifecycle.CompanionIndeterminate("receipt path does not match its identity")
                ids.add(rid)
        blocked: set[str] = set()
        for rid in sorted(ids):
            future = self._receipt_operations.get(rid)
            if future is not None and future.done():
                self._receipt_operations.pop(rid)
                try:
                    future.result()
                except (
                    lifecycle.CompanionError, lifecycle.CompanionIndeterminate,
                    ManagedRuntimeError, OSError,
                ) as exc:
                    logging.getLogger(__name__).warning("receipt operation incomplete for %s: %s", rid, exc)
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
                elif self.monotonic() >= record["deadline"]:
                    with self._receipt_state_lock:
                        latest = self._operation_record(rid)
                        if latest is not None and latest["state"] == "pending":
                            self._write_operation(rid, {
                                **latest, "state": "timeout", "observed_at": time.time(),
                            })
                continue
            if rid in adopted_registration_ids:
                continue
            if len(self._receipt_operations) >= 2 or self.monotonic() >= deadline:
                continue
            generation = self._receipt_generation(rid)
            if all(value is None for value in generation.values()):
                continue
            token = self._receipt_owner_token(executor, deadline)
            if token is None or self.monotonic() >= deadline:
                continue
            record = {
                "schema_version": 1, "registration_id": rid,
                "generation": generation, "generation_digest": _canonical_digest(generation),
                "state": "pending", "owner_pid": os.getpid(), "owner_start_token": token,
                "started_at": time.time(), "deadline": deadline, "observed_at": time.time(),
            }
            self._write_operation(rid, record)
            self._receipt_operations[rid] = executor.submit(self._reconcile_receipt_generation, rid, record)
            blocked.add(rid)
        return blocked

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
                snapshot = self.selected_managed(rid)
                if snapshot is not None:
                    self._managed_retention().validate_withdrawal(
                        rid, self.receipt_dir, deadline=operation["deadline"],
                    )
                if self.monotonic() >= operation["deadline"]:
                    raise lifecycle.CompanionIndeterminate("receipt operation execution deadline expired")
                if rid in self._receipt_adopted or self._receipt_generation(rid) != generation:
                    state = "superseded"
                    return
                if generation["receipt"] is not None:
                    self._retire_receipt(generation["receipt"], confirm_exit=snapshot is not None)
                    if self._read_receipt(rid) != generation["receipt"]:
                        raise lifecycle.CompanionIndeterminate("receipt changed during retirement")
                    self._delete_receipt(rid)
                    if self._read_receipt(rid) is not None:
                        raise lifecycle.CompanionIndeterminate("receipt deletion is not confirmed")
                if generation["selection"] is not None:
                    self.forget_managed(rid, _operation=operation)
                state = "complete"
        finally:
            with self._receipt_state_lock:
                current = self._operation_record(rid)
                if current is not None and all(
                    current[key] == operation[key]
                    for key in ("generation_digest", "owner_pid", "owner_start_token", "started_at")
                ):
                    self._write_operation(rid, {
                        **current, "state": state, "observed_at": time.time(),
                    })
