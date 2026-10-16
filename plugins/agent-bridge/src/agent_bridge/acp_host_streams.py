"""Session Host stream setup, separate from prompt and update reduction."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .acp_continuation import IdleAcpTransport, idle_operation

if TYPE_CHECKING:
    from .events import EventLog


class AcpHostStreamsMixin:
    @idle_operation
    async def start_streams(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        child_pid: int | None = None,
        closer: Any = None,
        private_continuation: bool = False,
        continuation_events: EventLog | None = None,
    ) -> None:
        """Initialize ACP over a Session Host's relayed stream pair (host mode).

        The child lives in a Session Host that outlives this frontend, so there
        is no local process to own here: ``pid`` is informational, and
        ``shutdown`` DETACHES (closes the streams via ``closer``) rather than
        killing the child. Reaping the child is a separate, explicit act.
        ``reader`` carries agent->client ACP; ``writer`` carries client->agent ACP.
        Private continuation additionally binds the original EventLog and raw
        byte observer before initialization, without enabling later activation.
        """
        if private_continuation:
            from .events import EventLog

            if (
                type(private_continuation) is not bool
                or type(continuation_events) is not EventLog
                or self._connection is not None or self._idle_transport is not None
            ):
                raise RuntimeError("Private ACP continuation requires initial event ownership")
            self._idle_event_log = continuation_events
            self._idle_transport = IdleAcpTransport(reader, writer)
        elif type(private_continuation) is not bool or continuation_events is not None:
            raise RuntimeError("Invalid private ACP continuation option")
        child_already_exited = self._host_child_exit_code is not None
        self._host_mode = True
        self._host_child_pid = child_pid
        self._host_closer = closer
        if not child_already_exited:
            self._host_transport_alive = True
            self._connection_loss_error = None
            self._transport_lost_event.clear()
        await self._init_connection(writer, reader)
