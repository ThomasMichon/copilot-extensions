"""Passive owner projection runtime; no listener, record scan or service launch."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from typing import Any

from . import owner_observation_contract as c
from .owner_observation_append import (
    PreparationOverflow,
    checked_entry,
    delivery_failed,
    freeze_record,
    prepare,
)
from .owner_observation_failure import MutationFailure
from .owner_observation_history import CanonicalObservation, OwnerObservationHistory

MAX_SUBSCRIBERS = 32
HEARTBEAT_SECONDS = 2.0
DEMAND_SECONDS = 30.0


def observed_entry(
    previous: CanonicalObservation,
    current: CanonicalObservation,
    batch_id: str,
    journal: str,
) -> dict:
    """Describe observations, never infer claim/effort/lifecycle transitions."""
    value = {
        "kind": "observed-change",
        "evidence_kind": "observed-change",
        "native_kind": None,
        "occurred_at": None,
        "observed_at": current.observed_at,
        "actor": {"kind": "unknown", "id": None, "qualification": "unknown", "session_id": None},
        "correlation": dict.fromkeys(c.CORRELATION_FIELDS),
        "relations": [],
        "record_fence": current.record_fence,
        "source_ref": {
            "source": current.source,
            "generation": journal,
            "record_id": batch_id,
            "payload_ref": None,
        },
        "summary": "Owner observed selected record changes",
        "details": None,
        "reason": None,
        "phase": None,
        "payload": {
            "previous_availability": previous.availability,
            "availability": current.availability,
            "previous_completeness": previous.completeness,
            "completeness": current.completeness,
            "head_before": previous.head_session_id,
            "head_after": current.head_session_id,
        },
    }
    checked_entry(value)
    before = c.utf8(c.encode(previous.facts, c.BODY_BYTES))
    after = c.utf8(c.encode(current.facts, c.BODY_BYTES))
    if before != after:
        payload = {**value["payload"], "before": previous.facts, "after": current.facts}
        try:
            c.bounded(payload, 5)
            c.encode({**value, "payload": payload}, c.BODY_BYTES - 1024)
        except c.ObservationError:
            value["payload"].update(
                facts_omitted=True,
                before_sha256=hashlib.sha256(before).hexdigest(),
                after_sha256=hashlib.sha256(after).hexdigest(),
                before_utf8_bytes=len(before),
                after_utf8_bytes=len(after),
                capture_source_before=previous.source,
                capture_source_after=current.source,
            )
        else:
            value["payload"] = payload
    checked_entry(value)
    return value


class OwnerObservationRuntime:
    """One-slot wake notifications; replay remains in the owner index."""

    def __init__(
        self,
        history: OwnerObservationHistory,
        *,
        wake=None,
        now=time.monotonic,
        heartbeat: float = HEARTBEAT_SECONDS,
    ) -> None:
        if type(heartbeat) not in (int, float) or not 0 < heartbeat <= 60:
            raise c.ObservationError("invalid owner heartbeat interval")
        self.history, self.wake, self.now, self.heartbeat = history, wake, now, heartbeat
        self._operations = threading.RLock()
        self._condition = threading.Condition()
        self._closed: str | None = "unready"
        self._terminated = False
        self._subscribers: dict[object, dict[str, Any]] = {}
        self._demand: dict[str, float] = {}
        self._unready: set[str] = set()

    def activate(self, *, expected_generation: str | None = None) -> None:
        with self._operations:
            if self._terminated:
                raise c.ObservationError("owner observation runtime is closed", "closed")
            initialized = False
            reason = "unavailable"
            try:
                self.history.activate(expected_generation=expected_generation)
                self.history.check_owner()
                with self._condition:
                    self._closed = None
                    self._unready.clear()
                initialized = True
            except c.ObservationError as error:
                if error.code == "superseded":
                    reason = "superseded"
                raise
            finally:
                if not initialized:
                    self.close_admission(reason)

    def close_admission(self, reason: str = "draining") -> None:
        c.reference(reason, "owner admission reason")
        with self._condition:
            self._closed = reason
            for subscriber in self._subscribers.values():
                if subscriber["closed"] not in {"closed", "shutdown", "superseded"}:
                    subscriber["closed"] = reason
            self._condition.notify_all()

    def shutdown(self) -> None:
        with self._operations:
            self._terminated = True
            self.close_admission("closed")
            self.history.close()

    def _admit(self) -> None:
        with self._condition:
            if self._closed is not None:
                raise c.ObservationError("owner observation admission is closed", self._closed)

    def _request(self, selected: c.Selection) -> None:
        with self._condition:
            self._admit()
            previous = self._demand.get(selected.project)
            if previous is None or previous < self.now() - DEMAND_SECONDS:
                self._unready.discard(selected.project)
            self._demand[selected.project] = self.now()

    def _missing(self, selected: c.Selection) -> None:
        with self._condition:
            if self._closed is None and selected.project not in self._unready:
                self._unready.add(selected.project)
                if self.wake is not None:
                    self.wake()

    def demand_projects(self) -> set[str]:
        with self._condition:
            cutoff = self.now() - DEMAND_SECONDS
            self._demand = {key: at for key, at in self._demand.items() if at >= cutoff}
            self._unready.intersection_update(self._demand)
            return set(self._demand) | {
                item["selected"].project for item in self._subscribers.values()
            }

    def active_count(self) -> int:
        with self._condition:
            return len(self._subscribers)

    def wait_closed(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: not self._subscribers, timeout)

    def _changed(self, key: tuple[str, str]) -> None:
        with self._condition:
            self._unready.discard(key[0])
            for subscriber in self._subscribers.values():
                if (subscriber["selected"].project, subscriber["selected"].worktree_id) == key:
                    subscriber["dirty"] = True
            self._condition.notify_all()

    def initialize(
        self,
        record: CanonicalObservation,
        *,
        expected_source_generation: str | None = None,
    ) -> dict:
        record = freeze_record(record)
        with self._operations:
            self._admit()
            try:
                result = self.history.initialize(
                    record,
                    expected_source_generation=expected_source_generation,
                )
            except c.ObservationError as error:
                if error.code == "superseded":
                    self.close_admission("superseded")
                elif isinstance(error, MutationFailure) and error.invalidated:
                    self._changed(record.key)
                raise
            self._changed(record.key)
            return result

    def publish(
        self,
        record: CanonicalObservation,
        entries: list[dict],
        *,
        batch_id: str,
        expected_cursor: str,
        post_save: bool = False,
    ) -> dict:
        c.reference(batch_id, "observation batch identity")
        record = freeze_record(record)
        try:
            record, entries, _digest = prepare(record, entries, post_save)
        except PreparationOverflow as error:
            with self._operations:
                self._admit()
                delivery_failed(
                    self.history,
                    c.Selection(*record.key, record.head_session_id),
                    record,
                    expected_cursor,
                    error,
                    batch_id=batch_id,
                    entries=error.entries,
                )
                self._changed(record.key)
            raise c.ObservationError(
                "admitted owner publication requires reconciliation",
                "cursor-unavailable",
            ) from error
        with self._operations:
            self._admit()
            try:
                result = self.history.publish(
                    record,
                    entries,
                    batch_id=batch_id,
                    expected_cursor=expected_cursor,
                    post_save=post_save,
                )
            except c.ObservationError as error:
                if error.code == "superseded":
                    self.close_admission("superseded")
                elif isinstance(error, MutationFailure) and error.invalidated:
                    self._changed(record.key)
                elif error.code == "cursor-unavailable":
                    self._changed(record.key)
                raise
            self._changed(record.key)
            return result

    def reconcile(self, record: CanonicalObservation, *, batch_id: str) -> dict:
        """Only explicitly captured differences, not sampled mutation history."""
        c.reference(batch_id, "observation batch identity")
        record = freeze_record(record)
        with self._operations:
            self._admit()
            try:
                return self.initialize(record)
            except c.ObservationError as error:
                if error.code != "stale-record":
                    raise
            selected = c.Selection(*record.key, record.head_session_id)
            previous, cursor, journal = self.history.last_record(selected)
            try:
                item = observed_entry(previous, record, batch_id, journal)
            except PreparationOverflow as error:
                delivery_failed(
                    self.history,
                    selected,
                    record,
                    cursor,
                    error,
                    batch_id=batch_id,
                    entries=error.entries,
                )
                self._changed(record.key)
                raise c.ObservationError(
                    "admitted owner change requires reconciliation",
                    "cursor-unavailable",
                ) from error
            return self.publish(
                record,
                [item],
                batch_id=batch_id,
                expected_cursor=cursor,
            )

    def read(self, payload: dict) -> dict:
        selected = None
        try:
            selected, values = c.request(payload)
            with self._operations:
                self._request(selected)
                return self.history.snapshot(selected, values["after_cursor"])[0]
        except c.ObservationError as error:
            if error.code == "unready" and selected is not None:
                self._missing(selected)
            if error.code == "superseded":
                self.close_admission("superseded")
            return c.error(error.code, str(error))
        except (OSError, sqlite3.Error) as error:
            return c.error("unavailable", str(error))

    def history_page(self, payload: dict) -> dict:
        selected = None
        try:
            selected, values = c.request(payload, history=True)
            with self._operations:
                self._request(selected)
                return self.history.page(
                    selected,
                    values["source_generation"],
                    values["before_cursor"],
                    values["limit"],
                )
        except c.ObservationError as error:
            if error.code == "unready" and selected is not None:
                self._missing(selected)
            if error.code == "superseded":
                self.close_admission("superseded")
            return c.error(error.code, str(error))
        except (OSError, sqlite3.Error) as error:
            return c.error("unavailable", str(error))

    @staticmethod
    def _notice(previous: dict, kind: str, reason: str | None = None) -> dict:
        source = c.Source(
            *(
                previous[key]
                for key in (
                    "project",
                    "worktree_id",
                    "source",
                    "source_generation",
                    "resident_generation",
                    "head_session_id",
                )
            )
        )
        return c.frame(
            kind, source, previous["cursor"], {} if reason is None else {"reason": reason}
        )

    def stream(self, payload: dict, disconnected):
        try:
            selected, values = c.request(payload)
        except c.ObservationError as error:
            yield c.error(error.code, str(error))
            return
        key = object()
        with self._condition:
            reason = self._closed
            if reason is None and len(self._subscribers) >= MAX_SUBSCRIBERS:
                reason = "subscriber-limit"
            if reason is None:
                subscriber = {"selected": selected, "closed": None, "dirty": True}
                self._subscribers[key] = subscriber
        if reason is not None:
            yield c.error(reason, "owner observation feed admission closed")
            return
        previous = None
        try:
            while not disconnected():
                initial = previous is None
                with self._condition:
                    reason, subscriber["dirty"] = subscriber["closed"], False
                if reason is not None:
                    yield (
                        c.error(reason, "owner observation closed before snapshot")
                        if previous is None
                        else (self._notice(previous, "going-away", reason))
                    )
                    return
                with self._operations:
                    self._request(selected)
                    if previous is None:
                        result, gap = self.history.snapshot(selected, values["after_cursor"])
                    else:
                        result, gap = self.history.delta(selected, previous["cursor"])
                with self._condition:
                    reason = subscriber["closed"]
                if reason is not None:
                    yield (
                        c.error(reason, "owner observation closed before snapshot")
                        if previous is None
                        else (self._notice(previous, "going-away", reason))
                    )
                    return
                if gap is not None and not initial:
                    yield gap
                refreshed = previous is not None and any(
                    result[field] != previous[field]
                    for field in (
                        "observed_at",
                        "facts",
                        "availability",
                        "completeness",
                        "durability",
                        "head_session_id",
                        "source_generation",
                        "resident_generation",
                        "history_floor",
                    )
                )
                if (
                    previous is None
                    or result["cursor"] != previous["cursor"]
                    or gap is not None
                    or refreshed
                ):
                    yield result
                    previous = result
                if gap is not None and initial:
                    yield gap
                if result["availability"] == "head-changed":
                    yield self._notice(result, "going-away", "head-changed")
                    return
                while not disconnected():
                    with self._condition:
                        self._condition.wait_for(
                            lambda: subscriber["dirty"] or subscriber["closed"] is not None,
                            timeout=self.heartbeat,
                        )
                        if subscriber["dirty"] or subscriber["closed"] is not None:
                            break
                    self.history.check_owner()
                    yield self._notice(previous, "heartbeat")
        except c.ObservationError as error:
            if error.code == "unready":
                self._missing(selected)
            if error.code == "superseded":
                self.close_admission("superseded")
            yield (
                c.error(error.code, str(error))
                if previous is None
                else (self._notice(previous, "going-away", error.code))
            )
        except (OSError, sqlite3.Error) as error:
            yield c.error("unavailable", str(error))
        finally:
            with self._condition:
                self._subscribers.pop(key, None)
                self._condition.notify_all()
