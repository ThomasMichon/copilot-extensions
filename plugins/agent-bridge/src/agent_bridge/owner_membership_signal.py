"""Constant-work commit notification; no queries or observer callbacks in writers."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from typing import Any

from .session_observation_contract import ObservationError

MAX_LOOPS = 4
MAX_WAITERS = 64


class MembershipSignal:
    """One coalesced callback per bounded observer loop, not one callback per row."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 0
        self._waiters: dict[asyncio.AbstractEventLoop, set[asyncio.Event]] = {}
        self._scheduled: set[asyncio.AbstractEventLoop] = set()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def changed(self) -> None:
        with self._lock:
            self._revision += 1
            self._scheduled.intersection_update(self._waiters)
            loops = tuple(loop for loop in self._waiters if loop not in self._scheduled)
            self._scheduled.update(loops)
        # At most MAX_LOOPS operations. Fan-out runs later, outside the writer.
        for loop in loops:
            try:
                loop.call_soon_threadsafe(self._deliver, loop)
            except RuntimeError:
                with self._lock:
                    self._scheduled.discard(loop)
                    self._waiters.pop(loop, None)

    def _deliver(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._scheduled.discard(loop)
            events = tuple(self._waiters.get(loop, ()))
        for event in events:
            event.set()

    async def wait(self, revision: int, event: asyncio.Event, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if revision != self._revision:
                return
            if (
                loop not in self._waiters
                and len(self._waiters) >= MAX_LOOPS
                or sum(len(items) for items in self._waiters.values()) >= MAX_WAITERS
            ):
                raise ObservationError("source-unavailable", "membership observer limit reached")
            self._waiters.setdefault(loop, set()).add(event)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return
        finally:
            with self._lock:
                items = self._waiters.get(loop)
                if items is not None:
                    items.discard(event)
                    if not items:
                        self._waiters.pop(loop, None)


class MembershipConnection(sqlite3.Connection):
    """Preserve SQLite outcomes; successful membership commits only mark dirty."""

    membership_database: Any = None
    _membership_notify = True
    _settled_changes = 0

    def _changed(self) -> None:
        signal = getattr(self.membership_database, "_membership_signal", None)
        if signal is not None:
            try:
                signal.changed()
            except Exception:
                # The reader reports the failed optional boundary; writers do
                # not perform logging I/O or query work for observer failures.
                self.membership_database._owner_membership_failure = True

    def commit(self) -> None:
        changed = self.total_changes != self._settled_changes
        super().commit()
        self._settled_changes = self.total_changes
        if changed and self._membership_notify:
            self._changed()

    def commit_membership(self, membership: bool) -> None:
        previous = self._membership_notify
        self._membership_notify = membership
        try:
            self.commit()
        finally:
            self._membership_notify = previous

    def rollback(self) -> None:
        super().rollback()
        self._settled_changes = self.total_changes

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> bool:
        changed = self.total_changes != self._settled_changes
        result = super().__exit__(kind, value, traceback)
        self._settled_changes = self.total_changes
        if kind is None and changed and self._membership_notify:
            self._changed()
        return result

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        pending = self.in_transaction and self.total_changes != self._settled_changes
        before = self.total_changes
        try:
            return super().executescript(sql_script)
        finally:
            # A script may commit before a later statement raises. Dirty is not
            # a mutation event; private snapshot reconciliation checks facts.
            if pending or not self.in_transaction and self.total_changes != before:
                if not self.in_transaction:
                    self._settled_changes = self.total_changes
                if self._membership_notify:
                    self._changed()


def commit(conn: sqlite3.Connection, *, membership: bool) -> None:
    """Tag an audited producer without changing plain SQLite compatibility."""
    if isinstance(conn, MembershipConnection):
        conn.commit_membership(membership)
    else:
        conn.commit()
