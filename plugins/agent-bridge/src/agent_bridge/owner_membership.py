"""Bounded generation-local projection; canonical custody stays with its owners."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import owner_membership_read as reader
from .owner_membership_records import owner_ref
from .owner_membership_signal import MembershipConnection as MembershipConnection
from .session_observation_contract import (
    ObservationError,
    decode,
    encode,
    position,
    read_position,
    text,
)

CAPABILITY = "bridge-owner-membership-v1"
MAX_OWNERS = 16
MAX_EVENTS = 256
MAX_RING_BYTES = 1024 * 1024
MAX_SEQUENCE = (1 << 63) - 1
log = logging.getLogger("agent-bridge")


@dataclass
class _Scope:
    states: dict[str, bytes]
    admitted_at: int
    readers: int = 0
    failure: str | None = None
    waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = field(default_factory=list)


class MembershipSource:
    """One existing database owner's derived observations, never a claim store."""

    def __init__(
        self, generation: str, authority: str, *, db: Any, file_id: tuple[int, int]
    ) -> None:
        self.generation = generation
        self.authority = authority
        self._db = db
        self._file_id = file_id
        self._signal = db._membership_signal
        self._last_revision = self._signal.revision
        self._capture_lock = threading.Lock()
        self._coverage_gap = False
        self._lock = threading.Lock()
        self._scopes: dict[str, _Scope] = {}
        self._sequence = 0
        self._events: deque[tuple[int, str, bytes]] = deque()
        self._retained_floor = 1
        self._bytes = 0
        self.closed = False
        self.publication_failed = False

    def identity(self, owner: str) -> dict[str, Any]:
        parts = owner.split("/")
        return {
            "provider": "bridge",
            "owner_ref": owner,
            "project": parts[1],
            "worktree_id": "/".join(parts[2:]),
            "head_session_id": None,
            "hosting_generation": self.generation,
            "journal_incarnation": self.generation,
            "hosting_authority": self.authority,
        }

    def has_owner(self, owner: str) -> bool:
        with self._lock:
            return owner in self._scopes

    def seed(self, owner: str, *, acquire: bool = False) -> None:
        """Bounded private read; never acquire the canonical write lock."""
        with self._lock:
            if owner in self._scopes:
                self._scopes[owner].readers += int(acquire)
                return
            if len(self._scopes) >= MAX_OWNERS and all(
                scope.readers for scope in self._scopes.values()
            ):
                raise ObservationError(
                    "membership-overflow", "membership observer scope limit reached"
                )
        if not self._capture_lock.acquire(timeout=0.05):
            raise ObservationError("capture-busy", "membership capture is already in progress")
        try:
            revision = self._signal.revision
            captured = reader.snapshot(
                self._db.db_path,
                (owner,),
                authority=self.authority,
                expected_file=self._file_id,
            )
            states = captured.states[owner]
            if isinstance(states, ObservationError):
                raise states
            if not states:
                raise ObservationError("source-unavailable", "no admitted records for this owner")
            with self._lock:
                if self.closed or revision != self._signal.revision:
                    raise ObservationError(
                        "capture-raced", "membership source changed during capture"
                    )
                if owner in self._scopes:
                    self._scopes[owner].readers += int(acquire)
                    return
                if len(self._scopes) >= MAX_OWNERS:
                    inactive = next(
                        (key for key, scope in self._scopes.items() if not scope.readers),
                        None,
                    )
                    if inactive is None:
                        raise ObservationError(
                            "membership-overflow", "membership observer scope limit reached"
                        )
                    if self._sequence == MAX_SEQUENCE:
                        raise ObservationError(
                            "sequence-exhausted", "membership sequence is exhausted"
                        )
                    # A reclaimed admission cannot resume an older cached cursor,
                    # even when no canonical membership change occurred.
                    self._sequence += 1
                    del self._scopes[inactive]
                if not self._scopes:
                    self._last_revision = revision
                self._scopes[owner] = _Scope(states, self._sequence, int(acquire))
        finally:
            self._capture_lock.release()

    def release(self, owner: str) -> None:
        """Release only this reader; inactive scopes remain bounded replay caches."""
        with self._lock:
            self._scopes[owner].readers -= 1

    @staticmethod
    def _wake(scope: _Scope) -> None:
        for loop, event in tuple(scope.waiters):
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(event.set)
                except RuntimeError:
                    log.debug("Membership observer loop ended before notification")

    def failed(self) -> None:
        """Quarantine failed observations for this generation, not canonical work."""
        with self._lock:
            for scope in self._scopes.values():
                scope.failure = "publication-failed"
                self._wake(scope)

    def refresh(self) -> None:
        """Coalesce dirty notifications into one budgeted, private reader capture."""
        if reader.file_id(self._db.db_path) != self._file_id:
            self.close()
            raise ObservationError("source-changed", "membership database file changed")
        if getattr(self._db, "_owner_membership_failure", False):
            raise ObservationError("publication-failed", "membership notification is unavailable")
        with self._lock:
            if not self._scopes or self.closed or self.publication_failed:
                return
            if self._last_revision == self._signal.revision:
                return
        if not self._capture_lock.acquire(timeout=0.05):
            raise ObservationError("capture-busy", "membership capture is already in progress")
        try:
            with self._lock:
                if self._last_revision == self._signal.revision:
                    return
                owners = tuple(
                    owner for owner, scope in self._scopes.items() if scope.failure is None
                )
            revision = self._signal.revision
            captured = reader.snapshot(
                self._db.db_path,
                owners,
                authority=self.authority,
                expected_file=self._file_id,
            )
            with self._lock:
                if self.closed or revision != self._signal.revision:
                    raise ObservationError(
                        "capture-raced", "membership source changed during capture"
                    )
                self._publish(captured.states)
                self._last_revision = revision
        except ObservationError as exc:
            self._coverage_gap = True
            if exc.code == "source-changed":
                self.close()
            raise
        except Exception as exc:
            self.publication_failed = True
            self.failed()
            log.error("Membership capture failed (%s)", type(exc).__name__)
            raise ObservationError(
                "publication-failed", "membership capture is unavailable"
            ) from exc
        finally:
            self._capture_lock.release()

    def _publish(self, captures: dict[str, dict[str, bytes] | ObservationError]) -> None:
        """Caller holds only the projection lock, never the canonical write lock."""
        for owner, captured in captures.items():
            scope = self._scopes[owner]
            if isinstance(captured, ObservationError):
                scope.failure = captured.code
                self._wake(scope)
                continue
            changes = []
            for key in sorted(scope.states.keys() | captured.keys()):
                before, after = scope.states.get(key), captured.get(key)
                if before == after:
                    continue
                value = decode(after if after is not None else before)
                retirement = value["retirement"]
                kind = (
                    (
                        "registration-disappeared"
                        if value["registry"] == "represented"
                        else "record-disappeared"
                    )
                    if after is None
                    else retirement or "member-upsert"
                )
                changes.append(
                    encode(
                        {
                            "kind": kind,
                            "operation": "tombstone"
                            if after is None
                            or retirement
                            in {
                                "session-retired",
                                "confirmed-relationship-retired",
                                "registration-expired",
                                "registration-taken-over",
                                "unreserved-relationship-closed",
                            }
                            else "upsert",
                            "member": value,
                            "evidence_kind": "observed-change",
                            "grants_claim_release": False,
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                )
            # All encoding finishes before changing this scope or its sequence.
            if self._sequence + len(changes) > MAX_SEQUENCE:
                scope.failure = "sequence-exhausted"
                self._wake(scope)
                continue
            for raw in changes:
                self._sequence += 1
                self._events.append((self._sequence, owner, raw))
                self._bytes += len(raw)
                while len(self._events) > MAX_EVENTS or self._bytes > MAX_RING_BYTES:
                    evicted, _owner, payload = self._events.popleft()
                    self._bytes -= len(payload)
                    self._retained_floor = evicted + 1
            scope.states = captured
            if changes:
                self._wake(scope)

    def frame(self, owner: str, after: str | None) -> dict[str, Any]:
        self.refresh()
        with self._lock:
            if self.publication_failed:
                raise ObservationError(
                    "publication-failed", "membership publication is unavailable"
                )
            scope = self._scopes.get(owner)
            if scope is None:
                raise ObservationError("source-unavailable", "owner is not admitted")
            if self.closed:
                raise ObservationError(
                    "source-stopping", "membership hosting generation is stopping"
                )
            if scope.failure is not None:
                raise ObservationError(scope.failure, "membership publication is unavailable")
            source = self.identity(owner)
            cursor = read_position(after, source) if after is not None else None
            floor = max(
                scope.admitted_at + 1,
                self._retained_floor,
            )
            if cursor is not None and not floor - 1 <= cursor <= self._sequence:
                raise ObservationError(
                    "retention-gap", "membership position is outside the retained range"
                )
            body: dict[str, Any] = {
                "schema": CAPABILITY,
                "version": 1,
                "kind": "snapshot" if cursor is None else "delta",
                "source": source,
                "availability": "observed",
                "completeness": "partial",
                "durability": "unknown",
                "head_session_id": None,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "retained": {"floor": floor, "head": self._sequence, "complete": False},
                "base_position": after,
                "position": position(source, self._sequence),
                "members": [],
                "changes": [],
                "has_more": False,
                "current_answerability": "unknown",
                "limitations": [
                    "generation-local-retention",
                    "partial-bootstrap",
                    "owner-linkage-is-not-custody",
                    "canonical-head-unknown",
                    "coalesced-observed-changes",
                ],
            }
            if self._coverage_gap:
                body["limitations"].append("private-capture-gap")
            if cursor is None:
                for key in sorted(scope.states):
                    raw = scope.states[key]
                    member = decode(raw)
                    if member["retirement"] not in {
                        "session-retired",
                        "confirmed-relationship-retired",
                        "registration-expired",
                        "registration-taken-over",
                        "unreserved-relationship-closed",
                    }:
                        body["members"].append(member)
            else:
                end = cursor
                for sequence, event_owner, raw in self._events:
                    if sequence <= cursor:
                        continue
                    if event_owner != owner:
                        end = sequence
                        continue
                    candidate = {**decode(raw), "id": position(source, sequence)}
                    body["changes"].append(candidate)
                    body["position"] = position(source, sequence)
                    try:
                        encode(body)
                    except ObservationError as exc:
                        if exc.code != "body-too-large":
                            raise
                        body["changes"].pop()
                        if not body["changes"]:
                            raise
                        break
                    end = sequence
                    if len(body["changes"]) == 64:
                        break
                else:
                    end = self._sequence
                body["position"] = position(source, end)
                body["has_more"] = end < self._sequence
            encode(body)
            return body

    async def wait(self, owner: str, after: str, timeout: float = 0.5) -> None:
        loop, event = asyncio.get_running_loop(), asyncio.Event()
        pair = (loop, event)
        with self._lock:
            scope = self._scopes[owner]
            cursor = read_position(after, self.identity(owner))
            if (
                scope.failure
                or self.closed
                or self.publication_failed
                or self._sequence > cursor
                or self._last_revision != self._signal.revision
            ):
                return
            scope.waiters.append(pair)
            revision = self._last_revision
        try:
            await self._signal.wait(revision, event, timeout)
        finally:
            with self._lock:
                if pair in scope.waiters:
                    scope.waiters.remove(pair)

    def close(self) -> None:
        with self._lock:
            self.closed = True
            for scope in self._scopes.values():
                self._wake(scope)
        self._signal.changed()


def source_for(manager: Any, owner: str, *, acquire: bool = False) -> MembershipSource:
    """Bind to an existing serving manager/DB without resolving or starting actors."""
    db = getattr(manager, "db", None)
    generation = getattr(manager, "_generation_id", None)
    if db is None or db.read_only or not isinstance(generation, str) or not generation:
        raise ObservationError("source-unavailable", "membership source is not serving")
    text(generation, "hosting generation")
    selected = owner_ref(owner)
    existing = getattr(db, "_owner_membership_source", None)
    if existing is not None and existing.generation == generation and existing.closed:
        raise ObservationError("source-unavailable", "membership hosting generation is stopping")
    if existing is not None and existing.generation == generation and existing.has_owner(selected):
        if getattr(db, "_owner_membership_failure", False) or existing.publication_failed:
            raise ObservationError("publication-failed", "membership publication is unavailable")
        existing.seed(selected, acquire=acquire)
        return existing
    if not db._membership_bind_lock.acquire(timeout=0.05):
        raise ObservationError("capture-busy", "membership source binding is busy")
    try:
        source = getattr(db, "_owner_membership_source", None)
        if source is not None and source.generation == generation and source.closed:
            raise ObservationError(
                "source-unavailable", "membership hosting generation is stopping"
            )
        if source is None or source.generation != generation:
            if source is not None:
                source.close()
            captured = reader.snapshot(db.db_path, ())
            if getattr(manager, "_generation_id", None) != generation:
                raise ObservationError("source-changed", "membership hosting generation changed")
            source = MembershipSource(
                generation,
                captured.authority,
                db=db,
                file_id=captured.file_id,
            )
            db._owner_membership_source = source
            db._owner_membership_failure = False
        if getattr(db, "_owner_membership_failure", False):
            raise ObservationError("publication-failed", "membership publication is unavailable")
        source.seed(selected, acquire=acquire)
        return source
    finally:
        db._membership_bind_lock.release()
