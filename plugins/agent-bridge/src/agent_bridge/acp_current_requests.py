"""Bounded read-only observations of the local ACP producer's actual waiters."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Any
from weakref import WeakKeyDictionary

from .session_observation_contract import ObservationError

MAX_CANDIDATES = 64
MAX_REQUESTS = 32
log = logging.getLogger("agent-bridge")


@dataclass(frozen=True)
class PendingOccurrence:
    """A view-only identity, deliberately not an answering selector."""

    occurrence_id: str
    kind: str

    def as_dict(self) -> dict[str, str]:
        return {"occurrence_id": self.occurrence_id, "kind": self.kind}


@dataclass(frozen=True)
class CurrentRequestSnapshot:
    """Current producer evidence, independent of retained event history."""

    client_incarnation: str
    revision: int
    availability: str
    completeness: str
    reason: str
    observed_at: str
    requests: tuple[PendingOccurrence, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "client_incarnation": self.client_incarnation,
            "revision": self.revision,
            "availability": self.availability,
            "completeness": self.completeness,
            "reason": self.reason,
            "observed_at": self.observed_at,
            "requests": [item.as_dict() for item in self.requests],
        }


class CurrentRequests:
    """Project actual client maps; weak identities are not a second waiter store."""

    def __init__(self) -> None:
        self._incarnation = uuid.uuid4().hex
        self._occurrences: WeakKeyDictionary[
            asyncio.Future[Any], tuple[object | None, str | None, object, PendingOccurrence]
        ] = (
            WeakKeyDictionary()
        )
        self._producer: object | None = None
        self._connection: object | None = None
        self._process: object | None = None
        self._session_id: str | None = None
        self._session_epoch = object()
        self._listeners: dict[object, Callable[[], None]] = {}
        self._revision = 0
        self._signature: tuple[Any, ...] | None = None

    def invalidate(self) -> None:
        """Withdraw qualification before a connection initialization can await."""
        self._connection = None
        self.session_changed(None)

    def bind(self, producer: object, connection: object, process: object) -> None:
        """Admit only the callback producer of a successfully initialized connection."""
        self._producer, self._connection, self._process = producer, connection, process
        self._incarnation = uuid.uuid4().hex
        self._signature = None
        self._revision = 0
        self.session_changed(None)

    def session_changed(self, session_id: str | None) -> None:
        if session_id != self._session_id:
            self._session_epoch = object()
        self._session_id = session_id
        self.changed()

    def register(
        self, future: asyncio.Future[Any], kind: str, *,
        producer: object | None = None, session_id: str | None = None,
    ) -> None:
        """Bind a fresh identity at the producer's actual waiter admission."""
        if kind not in {"input", "permission"} or future in self._occurrences:
            raise ValueError("invalid or duplicate current request admission")
        self._occurrences[future] = (
            producer, session_id, self._session_epoch, PendingOccurrence(uuid.uuid4().hex, kind)
        )
        future.add_done_callback(self._finished)
        self.changed()

    def _finished(self, future: asyncio.Future[Any]) -> None:
        self._occurrences.pop(future, None)
        self.changed()

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to invalidations; callers retain exact unsubscribe custody."""
        registration = object()
        self._listeners[registration] = callback

        def unsubscribe() -> None:
            self._listeners.pop(registration, None)

        return unsubscribe

    def changed(self) -> None:
        """Wake readers without emitting or reconstructing a history event."""
        for callback in tuple(self._listeners.values()):
            try:
                callback()
            except Exception:
                log.warning("Current request observer callback failed", exc_info=True)

    def snapshot(
        self, client: Any, *, unavailable_reason: str | None = None,
    ) -> CurrentRequestSnapshot:
        """Read at most 64 candidate slots synchronously on the producer loop."""
        availability, completeness, reason = "unknown", "unknown", "client-disconnected"
        pending: list[PendingOccurrence] = []
        seen: set[asyncio.Future[Any]] = set()
        if unavailable_reason is not None:
            reason = unavailable_reason
        elif client._remote_transport is not None or client._host_mode:
            reason = "unsupported-transport"
        elif (
            self._connection is not None
            and client._connection is self._connection
            and client._process is self._process
            and client.is_running
            and client.acp_session_id
            and client.acp_session_id == self._session_id
            and client._connection_loss_error is None
            and not client._transport_lost_event.is_set()
            and client._process.stdin is not None
            and client._process.stdout is not None
            and not client._process.stdin.is_closing()
            and not client._process.stdout.at_eof()
        ):
            availability, completeness, reason = "observed", "complete", "local-acp-waiters"
            permission = client._pending_permission_future
            slots = len(client._pending_elicitations) + int(permission is not None)
            remaining = MAX_CANDIDATES
            failure = None
            if permission is not None:
                failure = self._append(permission, "permission", pending, seen)
                remaining -= 1
            if failure is None:
                for future in islice(client._pending_elicitations.values(), remaining):
                    failure = self._append(future, "input", pending, seen)
                    if failure is not None:
                        break
            if failure == "producer-replaced":
                availability = completeness = "unknown"
                reason, pending = failure, []
            elif failure is not None or slots > MAX_CANDIDATES:
                completeness, reason = "partial", "waiter-budget"
            elif len(self._occurrences) > sum(future in self._occurrences for future in seen):
                # The answer slot can be overwritten/reset while its callback
                # still awaits. Weak admissions detect lost coverage, not answers.
                completeness, reason = "partial", "waiter-coverage"
        signature = (availability, completeness, reason, tuple(pending))
        if signature != self._signature:
            self._signature = signature
            self._revision += 1
        return CurrentRequestSnapshot(
            self._incarnation,
            self._revision,
            availability,
            completeness,
            reason,
            datetime.now(timezone.utc).isoformat(),
            tuple(pending),
        )

    def _append(
        self, future: asyncio.Future[Any], kind: str, pending: list[PendingOccurrence],
        seen: set[asyncio.Future[Any]],
    ) -> str | None:
        if not isinstance(future, asyncio.Future):
            raise ObservationError("invalid-source", "invalid producer waiter")
        if future in seen:
            raise ObservationError("invalid-source", "duplicate producer waiter")
        seen.add(future)
        if future.done():
            return None
        admitted = self._occurrences.get(future)
        if admitted is None or admitted[3].kind != kind:
            raise ObservationError("invalid-source", "waiter has no producer occurrence")
        producer, session_id, epoch, occurrence = admitted
        if (
            producer is not self._producer or session_id != self._session_id
            or epoch is not self._session_epoch
        ):
            return "producer-replaced"
        if len(pending) == MAX_REQUESTS:
            return "waiter-budget"
        pending.append(occurrence)
        return None
