"""Optional selected observations, produced only after resident roster acceptance."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from . import config as cfg
from . import owner_observation_contract as c
from . import owner_observation_source as source
from .owner_observation_history import OwnerObservationHistory
from .owner_observation_runtime import OwnerObservationRuntime
from .owner_observation_transport import CAPABILITIES, HEARTBEAT_SECONDS
from .worktrees_projection import WorktreesProjection
from .worktrees_projection_index import ProjectionError, ProjectionUnavailable

if TYPE_CHECKING:
    from .worktrees_runtime import WorktreesRuntime

logger = logging.getLogger(__name__)
MAX_SELECTED = 128
ERRORS = (c.ObservationError, ProjectionError, ProjectionUnavailable, OSError, sqlite3.Error)


def fingerprint(value: source.AcceptedObservation) -> tuple[str, str | None, str]:
    record = value.record
    return record.source, record.head_session_id, record.record_fence["facts_sha256"]


class OwnerObservationAdapter:
    def __init__(self, owner: WorktreesRuntime) -> None:
        self.owner = owner
        self._operations = threading.RLock()
        self._condition = threading.Condition()
        self._capture = threading.local()
        self._store: WorktreesProjection | None = None
        self.runtime: OwnerObservationRuntime | None = None
        self._closed: str | None = "unready"
        self._error: str | None = None
        self._demand: dict[tuple[str, str], float] = {}
        self._held: dict[tuple[str, str], int] = {}
        self._ready: dict[tuple[str, str], source.AcceptedObservation] = {}
        self._errors: dict[tuple[str, str], c.ObservationError] = {}
        self._pending: set[tuple[str, str]] = set()
        self._publishing: dict[str, int] = {}
        self._streams = 0
        self._readers = 0
        self._producers = 0

    def _authority(self, store: WorktreesProjection, *, read: bool = False) -> bool:
        if not source.authority(self.owner, store, read=read):
            return False
        capture = getattr(self._capture, "value", None)
        if capture is not None:
            record = capture.record
            current = source.selected(self.owner, store, *record.key, read=read)
            if fingerprint(current) != fingerprint(capture):
                raise c.ObservationError("accepted selected facts advanced", "stale-source")
        return True

    def activate(self) -> None:
        with self._operations:
            store = self.owner.store
            old = self.runtime
            if old is not None and self._store is not store:
                old.close_admission("superseded")
                old.shutdown()
                old = None
            try:
                if old is None:
                    history = OwnerObservationHistory(
                        store.db_path,
                        resident_generation=store.generation,
                        writer_id=store._writer_id,
                        authority_check=lambda: self._authority(store),
                        read_authority_check=lambda: self._authority(store, read=True),
                    )
                    old = OwnerObservationRuntime(
                        history,
                        now=self.owner._now,
                        heartbeat=HEARTBEAT_SECONDS,
                        wake=lambda: (
                            self.owner._wake.set() if self.owner._wake is not None else None
                        ),
                    )
                self._store, self.runtime = store, old
                old.activate(expected_generation=source.previous_generation(store.db_path))
                with self._condition:
                    self._closed, self._error = None, None
                    self._ready.clear()
                    self._errors.clear()
                    self._condition.notify_all()
            except ERRORS as error:
                self._unavailable(error)

    def _unavailable(self, error: Exception) -> None:
        logger.warning("optional worktree observation index unavailable: %s", error)
        with self._condition:
            self._error = str(error)[:1024] or type(error).__name__
            self._closed = "unavailable"
            self._condition.notify_all()
        if self.runtime is not None:
            self.runtime.close_admission("unavailable")

    def status(self) -> dict:
        with self._condition:
            result = {
                "contract_version": 1,
                "status": self._closed or "ready",
                "generation": self.owner.store.generation,
                "source": "resident-current-roster",
                "completeness": "partial",
                "error": self._error,
            }
            store = self._store
            if result["status"] == "ready" and store is not self.owner.store:
                result.update(status="unready", error="observation generation is not attached")
        if result["status"] == "ready" and store is not None:
            try:
                if not source.authority(self.owner, store, read=True):
                    result.update(status="superseded", error="resident roster owner changed")
            except ERRORS as error:
                result.update(
                    status=error.code if isinstance(error, c.ObservationError) else "unavailable",
                    error=str(error)[:1024],
                )
        return result

    def capabilities(self) -> list[str]:
        status = self.status()
        with self._condition:
            return (
                list(CAPABILITIES)
                if self.runtime is not None
                and self._error is None
                and status["status"] in {"ready", "draining"}
                else []
            )

    def _prune(self) -> None:
        cutoff = self.owner._now() - 30.0
        for key in list(self._demand):
            if self._demand[key] < cutoff and key not in self._held and key not in self._pending:
                self._demand.pop(key)
                self._ready.pop(key, None)
                self._errors.pop(key, None)

    def select(
        self, payload: dict, *, history: bool = False
    ) -> tuple[c.Selection, OwnerObservationRuntime]:
        selected, _values = c.request(payload, history=history)
        if cfg._PROJECT_NAME_RE.fullmatch(selected.project) is None:
            raise c.ObservationError("invalid observation project")
        key = selected.project, selected.worktree_id
        with self._condition:
            if self._closed is not None or self.runtime is None:
                raise c.ObservationError(
                    self._error or "observation admission closed", self._closed or "unready"
                )
            self._prune()
            if key not in self._demand and len(self._demand) >= MAX_SELECTED:
                raise c.ObservationError("observation selection limit reached", "selection-limit")
            self._demand[key] = self.owner._now()
            unready = key not in self._ready or key in self._errors
            runtime = self.runtime
        self.owner.register_project_demand(selected.project, unready=unready)
        return selected, runtime

    def gate(
        self, selected: c.Selection, runtime: OwnerObservationRuntime
    ) -> source.AcceptedObservation:
        key = selected.project, selected.worktree_id
        with self._condition:
            if runtime is not self.runtime:
                raise c.ObservationError("observation resident changed", "superseded")
            if self._closed is not None:
                raise c.ObservationError("observation admission closed", self._closed)
            if selected.project in self._publishing:
                raise c.ObservationError("accepted publication is being qualified", "unready")
            error, ready = self._errors.get(key), self._ready.get(key)
        if error is not None:
            raise error
        if ready is None:
            raise c.ObservationError("selected observation awaits roster publication", "unready")
        store = self._store
        if store is None:
            raise c.ObservationError("observation source is not attached", "unready")
        current = source.selected(self.owner, store, *key)
        if fingerprint(current) != fingerprint(ready):
            code = (
                "head-changed"
                if (current.record.head_session_id != ready.record.head_session_id)
                else "unready"
            )
            raise c.ObservationError("accepted selection has not been materialized", code)
        return ready

    def point(self, payload: dict, *, history: bool = False) -> dict:
        with self._condition:
            self._readers += 1
        try:
            selected, runtime = self.select(payload, history=history)
            before = self.gate(selected, runtime)
            result = runtime.history_page(payload) if history else runtime.read(payload)
            after = self.gate(selected, runtime)
            if fingerprint(before) != fingerprint(after):
                raise c.ObservationError("selection changed during point read", "stale-source")
            return result
        except ERRORS as error:
            if isinstance(error, c.ObservationError) and error.code == "superseded":
                self.close_admission("superseded")
            return c.error(
                error.code if isinstance(error, c.ObservationError) else "unavailable", str(error)
            )
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    def projects(self) -> set[str]:
        with self._condition:
            self._prune()
            return {key[0] for key in self._demand} if self._closed is None else set()

    def counts(self) -> dict[str, int]:
        with self._condition:
            return {
                "streams": self._streams,
                "readers": self._readers,
                "producer": self._producers,
                "publication": sum(self._publishing.values()),
                "demand": len(self._demand) if self._closed is None else 0,
            }

    def _selection_failed(self, key: tuple[str, str], error: Exception) -> None:
        failure = (
            error
            if isinstance(error, c.ObservationError)
            else c.ObservationError(str(error), "unavailable")
        )
        with self._condition:
            self._errors[key] = failure
        if self.runtime is not None:
            self.runtime._changed(key)
        if failure.code == "superseded":
            self.close_admission("superseded")

    def publication_failed(self, project: str, error: Exception) -> None:
        with self._condition:
            keys = [key for key in self._demand if key[0] == project]
        for key in keys:
            self._selection_failed(key, c.ObservationError(str(error), "unavailable"))

    @contextmanager
    def publication(self, project: str) -> Iterator[None]:
        with self._condition:
            registered = self._closed is None
            if registered:
                self._publishing[project] = self._publishing.get(project, 0) + 1
        try:
            yield
        finally:
            if registered:
                with self._condition:
                    self._publishing[project] -= 1
                    if not self._publishing[project]:
                        self._publishing.pop(project)
                    self._condition.notify_all()

    def accepted(self, project: str) -> None:
        with self._operations:
            with self._condition:
                if self._closed is not None or self.runtime is None:
                    return
                self._prune()
                store = self._store
                if store is None:
                    return
                keys = [key for key in self._demand if key[0] == project]
                self._producers += 1
            try:
                for key in keys:
                    with self._condition:
                        if self._closed is not None:
                            break
                        self._pending.add(key)
                    try:
                        accepted = source.selected(self.owner, store, *key, read=False)
                        previous = self._ready.get(key)
                        if previous is not None and accepted.revision < previous.revision:
                            raise c.ObservationError(
                                "late accepted selection refused", "stale-source"
                            )
                        self._capture.value = accepted
                        self._materialize(accepted)
                        if not self._authority(store):
                            raise c.ObservationError(
                                "resident changed after materialization", "superseded"
                            )
                        with self._condition:
                            self._ready[key] = accepted
                            self._errors.pop(key, None)
                    except (OSError, sqlite3.Error) as error:
                        self._unavailable(error)
                        break
                    except (c.ObservationError, ProjectionError, ProjectionUnavailable) as error:
                        self._selection_failed(key, error)
                    finally:
                        self._capture.value = None
                        with self._condition:
                            self._pending.discard(key)
                            self._condition.notify_all()
            finally:
                with self._condition:
                    self._producers -= 1
                    self._condition.notify_all()

    def _materialize(self, accepted: source.AcceptedObservation) -> None:
        runtime = self.runtime
        if runtime is None:
            raise c.ObservationError("observation runtime is absent", "unready")
        record = accepted.record
        selected = c.Selection(*record.key, record.head_session_id)
        try:
            old, _cursor, journal = runtime.history.last_record(selected)
        except c.ObservationError as error:
            if error.code != "unready":
                raise
            runtime.initialize(record)
            return
        if old.source != record.source:
            runtime.initialize(record, expected_source_generation=journal)
        else:
            store = self._store
            if store is None:
                raise c.ObservationError("observation source is not attached", "unready")
            try:
                runtime.initialize(record)
            except c.ObservationError as error:
                if error.code != "stale-record":
                    raise
                old, cursor, journal = runtime.history.last_record(selected)
                batch = f"roster:{store.generation}:{accepted.revision}:{record.worktree_id}"
                runtime.publish(
                    record,
                    [source.compact_change(old, record, batch, journal)],
                    batch_id=batch,
                    expected_cursor=cursor,
                )

    def stream(self, payload: dict, disconnected: Callable[[], bool]) -> Iterator[dict]:
        from .owner_observation_provider_feed import stream

        yield from stream(self, payload, disconnected)

    def close_admission(self, reason: str) -> None:
        with self._condition:
            if self._closed is not None and self._closed in {"closed", "shutdown", "superseded"}:
                reason = self._closed
            self._closed = reason
            self._condition.notify_all()
        if self.runtime is not None:
            self.runtime.close_admission(reason)

    def wait_closed(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: (
                    self._streams == self._readers == self._producers == 0 and not self._publishing
                ),
                timeout,
            )

    def shutdown(self) -> None:
        self.close_admission("closed")
        with self._operations:
            if self.runtime is not None:
                self.runtime.shutdown()
