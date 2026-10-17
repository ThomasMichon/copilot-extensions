"""Borrowed pending-runtime resources for native-bound idle ACP continuation."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal
from zdd.routing import Endpoint

from .acp_continuation import decode_idle_continuation
from .acp_durability import decode_idle_durability
from .db import Database
from .events import EventLog
from .models import SessionStatus
from .resolver_runtime import _encode
from .session_host.private_receipt import VerifiedHostReceipt, decode_host_receipt
from .session_manager import Session, SessionManager
from .session_snapshot import _SEMANTIC

if TYPE_CHECKING:
    from .acp_activation import NativeIdleActivation
    from .runtime_transfer import PendingRuntime


class PendingIdleSession:
    """One captured manager session, never a second database or serving grant."""

    def __init__(
        self, pending: PendingRuntime, app: FastAPI, frontend: Endpoint,
        original: VerifiedHostReceipt, continuation: object, durability: object,
        deadline: Deadline,
    ) -> None:
        from .runtime_transfer import PendingRuntime

        if (
            type(pending) is not PendingRuntime or not isinstance(app, FastAPI)
            or type(frontend) is not Endpoint or type(original) is not VerifiedHostReceipt
            or type(deadline) is not Deadline or pending.serving
            or pending._writable_database is None or pending.runtime.sessions is None
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        pending.verify_resources(app)
        idle = decode_idle_continuation(continuation)
        durable = decode_idle_durability(durability)
        host = decode_host_receipt(original.private_payload(deadline))
        matches = [
            item for item in pending.runtime.sessions.sessions
            if item.session_id == host.session_id
        ]
        if len(matches) != 1:
            raise PrivateIPCError(Refusal.AUTHORITY)
        metadata = matches[0]
        observed = metadata.observation
        if (
            metadata.host != host or idle.host != host
            or metadata.status is not SessionStatus.IDLE
            or not observed.client_present or observed.prompt_pending
            or observed.lifecycle_locked or observed.turn_start_locked
            or metadata.acp_session_id != idle.acp_session_id
            or durable.session_id != metadata.session_id
            or durable.acp_session_id != metadata.acp_session_id
            or len(durable.turns) != metadata.turn_count
            or metadata.next_event_id != durable.next_event_id
            or metadata.handoff_pending or metadata.stop_requested
            or metadata.model_override != idle.preferences.model_override
            or metadata.effort_override != idle.preferences.effort_override
            or frontend.pid != pending._self_peer.pid
            or frontend.process_start_time != pending._self_peer.start_time
            or frontend.generation <= host.frontend.generation
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        manager = app.state.session_manager
        session = manager._sessions.get(metadata.session_id)
        if type(manager) is not SessionManager or type(session) is not Session:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self.pending, self.app, self.frontend, self.original = pending, app, frontend, original
        self.source, self.database = pending._passive_database, pending._writable_database
        self.manager, self.session, self.log = manager, session, session.event_log
        if type(self.source) is not Database or type(self.log) is not EventLog:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._metadata = metadata
        self._continuation, self._durability = _encode(continuation), _encode(durability)
        self._loop = asyncio.get_running_loop()
        self._sessions = tuple(manager._sessions.items())
        self._owner: NativeIdleActivation | None = None
        self._active = self._closed = False
        self._callback = self.on_event
        self._contract = (
            pending, app, frontend, original, self.source, self.database,
            manager, session, self.log, _encode(asdict(metadata)),
            self._continuation, self._durability, self._loop, self._sessions, self._callback,
        )
        self.verify(deadline)

    def verify(self, deadline: Deadline) -> None:
        """Check the borrowed graph before any native activation task can run."""
        self.pending.verify_resources(self.app)
        session, manager = self.session, self.manager
        if (
            self._closed or self._active or asyncio.get_running_loop() is not self._loop
            or (
                self.pending, self.app, self.frontend, self.original, self.source, self.database,
                manager, session, self.log, _encode(asdict(self._metadata)),
                self._continuation, self._durability, self._loop, self._sessions, self._callback,
            ) != self._contract
            or self.pending.serving or self.app.state.session_manager is not manager
            or tuple(manager._sessions.items()) != self._sessions
            or self.pending._passive_database is not self.source
            or self.pending._writable_database is not self.database
            or (self._owner is not None and (
                self.pending._idle_sessions.get(session.session_id) is not self
            ))
            or self.log._db is not self.database or self.database.read_only
            or self.database._read_only_file_id != self.source._read_only_file_id
            or self.database.db_path != self.source.db_path
            or manager._host_index.all() or manager.is_draining
            or session.client is not None or session.event_log is not self.log
            or session._prompt_task is not None or session._lifecycle_lock.locked()
            or session._turn_start_lock.locked() or session.subscriber_count
            or session.has_active_background_tasks
            or any(getattr(manager, name) for name in (
                "_forwards", "_relays", "_container_locks", "_codespace_locks",
                "_remote_reap_tasks", "_auto_handoff_tasks",
            ))
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        expected = {name: getattr(self._metadata, name) for name in _SEMANTIC}
        actual = {name: getattr(session, name) for name in _SEMANTIC}
        if (
            _encode(asdict(session.target)) != _encode(asdict(self._metadata.target))
            or _encode({**actual, "target": asdict(session.target)})
            != _encode({**expected, "target": asdict(self._metadata.target)})
            or session._crossed_thresholds != set(self._metadata.crossed_thresholds)
            or session._handoff_pending != self._metadata.handoff_pending
            or session._stop_requested != self._metadata.stop_requested
            or session._stop_notices != self._metadata.stop_notices
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self.original.private_payload(deadline)
        deadline.remaining()

    def bind(self, owner: NativeIdleActivation, deadline: Deadline) -> None:
        from .acp_activation import NativeIdleActivation

        self.verify(deadline)
        if (
            type(owner) is not NativeIdleActivation or self._owner is not None
            or self.pending._idle_sessions.get(self.session.session_id) is not self
            or owner._log is not self.log or owner._source is not self.source
            or owner._original is not self.original or owner._frontend != self.frontend
            or _encode(owner._continuation) != self._continuation
            or _encode(owner._durability) != self._durability
            or owner.client._on_event is not self._callback
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._owner = owner

    def commit(self, owner: NativeIdleActivation, deadline: Deadline) -> None:
        """Called synchronously inside the native owner's current-route fence."""
        self.verify(deadline)
        if (
            owner is not self._owner or not owner.client.is_running
            or owner.client.acp_session_id != self.session.acp_session_id
            or owner.client._on_event is not self._callback
            or owner._database is not self.database
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        owner.client.session_host_client = owner._raw
        self.session.client = owner.client
        self._active = True

    def on_event(self, kind: str, data: dict[str, Any]) -> None:
        """Route through the same manager reducers used for ordinary clients."""
        if (
            not self._active or self._closed or self._owner is None
            or self.manager._sessions.get(self.session.session_id) is not self.session
            or self.session.client is not self._owner.client
            or self.session.event_log is not self.log or self.log._db is not self.database
            or self.app.state.db is not self.database or self.manager._db is not self.database
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self.manager._route_acp_event(self.session, kind, data)

    def release(self, owner: NativeIdleActivation) -> None:
        """Never close or rebind the runtime's borrowed database."""
        if owner is not self._owner:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._closed = True
        if self.session.client is owner.client:
            self.session.client = None

    async def aclose(self) -> None:
        if self._owner is not None:
            await self._owner.aclose()
        self._closed = True
