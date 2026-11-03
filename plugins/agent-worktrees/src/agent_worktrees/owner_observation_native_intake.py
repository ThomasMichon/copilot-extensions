"""Optional resident-executed save intake; never an RPC accepting publisher evidence."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

from . import config as cfg
from . import owner_observation_contract as c
from . import owner_observation_native_entries as semantic
from . import owner_observation_source as source
from . import tracking_save_notifications as notifications
from .owner_observation_native_publish import NativeProof, publish_native, validate_current
from .owner_observation_runtime import OwnerObservationRuntime
from .tracking_save_capture import DeliveryIssue, SaveReceipt
from .worktrees_projection_index import ProjectionError, ProjectionUnavailable

if TYPE_CHECKING:
    from .worktrees_runtime import WorktreesRuntime

logger = logging.getLogger(__name__)
MAX_PENDING = 32
MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Context:
    epoch: object
    provider_epoch: object
    runtime_epoch: object
    generation: str
    path: str
    worktree_id: str
    verb: str
    uncertain: threading.Event = field(default_factory=threading.Event, compare=False)


@dataclass(frozen=True)
class Envelope:
    context: Context
    receipt: SaveReceipt


def path_key(value: str) -> str:
    return os.path.normcase(os.path.abspath(value))


class NativeIntake:
    def __init__(self, owner: WorktreesRuntime) -> None:
        self.owner = owner
        self._condition = threading.Condition()
        self._local = threading.local()
        self._epoch = object()
        self._active = False
        self._unsubscribe = None
        self._queue: deque[Envelope] = deque()
        self._bytes = 0
        self._computing = 0
        self._producing = 0
        self._leased = 0
        self._leased_bytes = 0
        self._omitted = 0
        self._error: str | None = None

    def activate(self) -> None:
        with self._condition:
            self._epoch = object()
            self._active = self.owner.observation_status()["status"] == "ready"
        if self._active and self._unsubscribe is None:
            try:
                self._unsubscribe = notifications.observe_receipts(self.receive)
            except (TypeError, ValueError) as error:
                self._loss(str(error))
                with self._condition:
                    self._active = False

    def _loss(self, message: str) -> None:
        logger.warning("Native save observation remains partial: %s", message)
        with self._condition:
            self._omitted += 1
            self._error = message[:1024]

    def _context(self, payload: dict) -> Context | None:
        adapter = self.owner.observations
        runtime = adapter.runtime
        args = payload.get("args")
        verb = payload.get("verb")
        if (
            runtime is None
            or not isinstance(verb, str)
            or verb not in semantic.DOMAINS
            or not isinstance(args, dict)
            or not isinstance(args.get("yaml_path"), str)
            or not isinstance(args.get("worktree_id"), str)
            or not args["worktree_id"]
        ):
            return None
        if getattr(self._local, "context", None) is not None or not notifications.capture_idle():
            self._loss("admitted record capture overlaps nested/deferred notification work")
            return None
        try:
            if not source.authority(self.owner, self.owner.store):
                return None
        except (c.ObservationError, OSError, sqlite3.Error):
            return None
        with self._condition:
            if not self._active:
                return None
            return Context(
                self._epoch,
                adapter._epoch,
                runtime._epoch,
                self.owner.store.generation,
                path_key(args["yaml_path"]),
                args["worktree_id"],
                verb,
            )

    def compute(self, kind: str, payload: dict) -> dict:
        from . import tracking_write

        # Native observability is optional; the existing canonical compute is not gated by it.
        with self._condition:
            self._computing += 1
        previous = getattr(self._local, "context", None)
        if previous is not None:
            previous.uncertain.set()
        try:
            self._local.context = self._context(payload) if isinstance(payload, dict) else None
            return tracking_write.compute(kind, payload)
        finally:
            self._local.context = previous
            with self._condition:
                self._computing -= 1
                self._condition.notify_all()

    def receive(self, event: SaveReceipt | DeliveryIssue) -> None:
        context = getattr(self._local, "context", None)
        if context is None:
            return
        if isinstance(event, DeliveryIssue):
            self._loss("process-local delivery incomplete; affected native range is unknown")
            return
        if event.after.identity is None:
            self._loss("saved source identity is omitted; native range cannot be qualified")
            return
        try:
            self._scope(context, event)
        except c.ObservationError as error:
            self._loss(str(error))
            return
        with self._condition:
            if not self._active or context.epoch is not self._epoch:
                self._omitted += 1
                return
            if (
                len(self._queue) + self._leased >= MAX_PENDING
                or self._bytes + self._leased_bytes + event.encoded_bytes > MAX_BYTES
            ):
                self._omitted += 1
                self._error = "native intake queue overflow"
                logger.warning("Native intake queue overflow after successful canonical save")
                return
            self._queue.append(Envelope(context, event))
            self._bytes += event.encoded_bytes
        identity = event.after.identity
        if identity is not None:
            self.owner.register_project_demand(identity.project, unready=True)
        if self.owner._wake is not None:
            self.owner._wake.set()

    @staticmethod
    def _scope(context: Context, receipt: SaveReceipt) -> None:
        identity = receipt.after.identity
        if (
            context.uncertain.is_set()
            or path_key(receipt.path) != context.path
            or identity is None
            or identity.worktree_id != context.worktree_id
        ):
            raise c.ObservationError(
                "native receipt does not qualify the exact admitted record",
                "unqualified-native",
            )

    def _guard(
        self, context: Context, runtime: OwnerObservationRuntime, receipt: SaveReceipt
    ) -> None:
        self._scope(context, receipt)
        adapter = self.owner.observations
        with self._condition:
            if not self._active or context.epoch is not self._epoch:
                raise c.ObservationError("native producer admission ended", "superseded")
        if (
            context.provider_epoch is not adapter._epoch
            or adapter.runtime is not runtime
            or context.runtime_epoch is not runtime._epoch
            or context.generation != self.owner.store.generation
        ):
            raise c.ObservationError("native producer epoch changed", "superseded")
        runtime._admit(context.runtime_epoch)
        if not source.authority(self.owner, self.owner.store):
            raise c.ObservationError("native writer no longer owns the index", "writer-fenced")

    def accepted(self, project: str) -> None:
        adapter = self.owner.observations
        with self._condition:
            if not self._active or self._producing:
                return
            work = list(self._queue)
            self._leased, self._leased_bytes = len(work), self._bytes
            self._queue.clear()
            self._bytes = 0
            self._producing += 1
        keep = []
        try:
            for envelope in work:
                receipt = envelope.receipt
                identity = receipt.after.identity
                if identity is None or identity.project != project:
                    keep.append(envelope)
                    continue
                runtime = adapter.runtime
                try:
                    if runtime is None:
                        raise c.ObservationError("native receiver unavailable", "unready")
                    self._guard(envelope.context, runtime, receipt)
                    semantic.validate(receipt, envelope.context.verb)
                    expected = (
                        cfg.project_dir(identity.project)
                        / "worktrees"
                        / (identity.worktree_id + ".yaml")
                    )
                    if path_key(receipt.path) != path_key(str(expected)):
                        raise c.ObservationError(
                            "native receipt is not the owning canonical record path",
                            "source-replaced",
                        )
                    current = source.selected(
                        self.owner,
                        self.owner.store,
                        identity.project,
                        identity.worktree_id,
                        read=False,
                    )
                    validate_current(current.record, receipt)
                    if not semantic.entries(receipt, envelope.context.verb):
                        continue
                    adapter.select(
                        {
                            "contract_version": 1,
                            "project": identity.project,
                            "worktree_id": identity.worktree_id,
                            "expected_head_session_id": current.record.head_session_id,
                            "after_cursor": None,
                        }
                    )
                    with adapter._operations:
                        key = identity.project, identity.worktree_id
                        with adapter._condition:
                            adapter._pending.add(key)
                        adapter._capture.value = current
                        try:
                            selected = c.Selection(*key, current.record.head_session_id)
                            try:
                                old, _, journal = runtime.history.last_record(selected)
                            except c.ObservationError as error:
                                if error.code != "unready":
                                    raise
                                runtime.initialize(current.record)
                            else:
                                if old.source != current.record.source:
                                    runtime.initialize(
                                        current.record, expected_source_generation=journal
                                    )
                            proof = NativeProof(
                                receipt,
                                envelope.context.verb,
                                partial(self._guard, envelope.context, runtime, receipt),
                                semantic.digest(receipt),
                            )
                            publish_native(runtime, current.record, proof)
                            self._guard(envelope.context, runtime, receipt)
                            with adapter._condition:
                                adapter._ready[key] = current
                                adapter._errors.pop(key, None)
                        finally:
                            adapter._capture.value = None
                            with adapter._condition:
                                adapter._pending.discard(key)
                                adapter._condition.notify_all()
                except (
                    c.ObservationError,
                    OSError,
                    sqlite3.Error,
                    ProjectionError,
                    ProjectionUnavailable,
                ) as error:
                    self._loss(str(error))
                    if isinstance(error, c.ObservationError) and error.code in {
                        "unqualified-native",
                        "source-replaced",
                        "superseded",
                        "invalid-request",
                        "batch-conflict",
                        "event-conflict",
                    }:
                        continue
                    keep.append(envelope)
                    if runtime is not None and getattr(error, "admitted", False):
                        runtime._changed((identity.project, identity.worktree_id))
        finally:
            with self._condition:
                self._leased = self._leased_bytes = 0
                if not self._active:
                    self._omitted += len(keep)
                    keep = []
                for envelope in reversed(keep):
                    size = envelope.receipt.encoded_bytes
                    if len(self._queue) < MAX_PENDING and self._bytes + size <= MAX_BYTES:
                        self._queue.appendleft(envelope)
                        self._bytes += size
                    else:
                        self._omitted += 1
                self._producing -= 1
                self._condition.notify_all()

    def status(self) -> dict:
        with self._condition:
            return {
                "supported": self._active,
                "coverage": "resident-qualified-writes-only",
                "history_completeness": "unknown",
                "omitted": self._omitted,
                "error": self._error,
            }

    def counts(self) -> dict[str, int]:
        with self._condition:
            return {
                "native-receipt-queued": len(self._queue),
                "native-receipt-producer": self._producing,
                "native-receipt-compute": self._computing,
                "native-receipt-leased": self._leased,
            }

    def close_admission(self) -> None:
        with self._condition:
            self._active = False
            self._epoch = object()
            lost = len(self._queue)
            self._queue.clear()
            self._bytes = 0
            self._condition.notify_all()
        if lost:
            self._loss(f"{lost} queued native receipts retired without materialization")
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def wait_closed(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: self._computing == self._producing == 0 and not self._queue,
                timeout,
            )
