"""Explicit native-bound idle client activation; no serving or ADOPT admission."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zdd import routing
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal
from zdd.routing import Endpoint

from .acp_continuation import (
    decode_idle_continuation, prepare_idle_continuation, require_prepared_continuation,
)
from .acp_durability import require_idle_durable_contents, verify_idle_durability
from .db import Database
from .db_adoption import open_existing_writable
from .events import EventLog
from .predecessor_runtime import RetainedChannel, _matches_publication
from .resolver_runtime import _encode
from .session_host import protocol
from .session_host.acp_adapter import AcpStreams, prepare_acp_streams, start_acp_relays
from .session_host.client import SessionHostClient
from .session_host.private_attachment import _self
from .session_host.private_commit import verify_host_commit
from .session_host.private_receipt import HostReceipt, VerifiedHostReceipt, decode_host_receipt

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .runtime_idle import PendingIdleSession

CommitQuery = Callable[[Deadline], Awaitable[tuple[RetainedChannel, object]]]


class NativeIdleActivation:
    """Custody of one private raw connection, inert pair and idle SDK candidate.

    The integrating owner supplies independently registered native queries.
    Activation consumes a fresh INSPECT_COMMIT response, never an ACTIVATE
    success flag, copied receipt or TCP ticket alone. The original deadline
    covers staging, readback and activation; failure leaves the client inert.
    """

    @classmethod
    async def prepare(
        cls, root: Path, frontend: Endpoint, original: VerifiedHostReceipt,
        reservation: object, continuation: object, durability: object,
        log: EventLog, source: Database, deadline: Deadline,
        *, on_permission: Callable[..., Any] | None = None,
        runtime_binding: PendingIdleSession | None = None,
    ) -> NativeIdleActivation:
        """Send only ATTACH_PRIVATE and prepare a socketpair without relay tasks."""
        if (
            type(original) is not VerifiedHostReceipt or type(frontend) is not Endpoint
            or type(deadline) is not Deadline or type(log) is not EventLog
            or type(source) is not Database or not source.read_only
            or (runtime_binding is None and log._db is not source) or not isinstance(root, Path)
            or not root.is_absolute() or ".." in root.parts
            or type(reservation) is not dict or set(reservation) != {
                "ticket", "prepared", "attached", "recovery_snapshot", "recovery_generation",
            }
            or reservation["prepared"] is not False or reservation["attached"] is not False
            or any(
                type(reservation[name]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", reservation[name]) is None
                for name in ("ticket", "recovery_snapshot")
            )
            or type(reservation["recovery_generation"]) is not int
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        state = decode_idle_continuation(continuation)
        host = decode_host_receipt(original.private_payload(deadline))
        peer = _self()
        if (
            state.host != host or host.acp_wire is None
            or frontend.pid != peer.pid or frontend.process_start_time != peer.start_time
            or peer.principal != host.host.principal
            or frontend.generation <= host.frontend.generation
            or frontend.pid == host.frontend.pid
            or reservation["recovery_generation"] != host.attachment_generation + 1
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if runtime_binding is not None:
            from .runtime_idle import PendingIdleSession

            if type(runtime_binding) is not PendingIdleSession:
                raise PrivateIPCError(Refusal.AUTHORITY)
            runtime_binding.verify(deadline)
            if (
                runtime_binding.log is not log or runtime_binding.source is not source
                or runtime_binding.original is not original
                or runtime_binding.frontend != frontend
                or runtime_binding.pending.runtime.origins.config_root != root
                or runtime_binding._continuation != _encode(continuation)
                or runtime_binding._durability != _encode(durability)
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
        owner = cls()
        owner._root, owner._frontend, owner._original = root, frontend, original
        owner._host, owner._deadline, owner._peer = host, deadline, peer
        owner._continuation = json.loads(_encode(continuation))
        owner._durability = json.loads(_encode(durability))
        owner._log, owner._source = log, source
        owner._token = reservation["recovery_snapshot"]
        owner._generation = reservation["recovery_generation"]
        owner._loop, owner._thread = asyncio.get_running_loop(), threading.current_thread()
        owner._active = owner._closed = owner._attempted = False
        owner._commit = owner._database = owner._client = None
        owner._runtime_binding = runtime_binding
        owner._raw = owner._streams = None
        try:
            remaining = deadline.remaining()
            owner._raw = await asyncio.wait_for(
                SessionHostClient.connect(host.data_endpoint.bind, port=host.data_endpoint.port),
                remaining,
            )
            remaining = deadline.remaining()
            await asyncio.wait_for(
                protocol.write_message(
                    owner._raw._writer, protocol.MsgType.ATTACH_PRIVATE,
                    reservation["ticket"].encode("ascii"),
                ), remaining,
            )
            remaining = deadline.remaining()
            owner._streams = await asyncio.wait_for(prepare_acp_streams(), remaining)
            owner._client = prepare_idle_continuation(
                owner._continuation, original, log,
                owner._streams.reader, owner._streams.writer, deadline,
                on_event=(
                    runtime_binding._callback if runtime_binding is not None
                    else lambda kind, data: log.append(kind, data)
                ),
                on_permission=on_permission, closer=owner.aclose,
            )
            owner._custody = (owner._client, owner._streams, owner._raw, log, source, runtime_binding)
            owner._callbacks = (
                owner._client._on_event, owner._client._on_permission, owner._client._host_closer,
            )
            owner._graph = owner._stream_graph()
            owner._contract = (
                root, frontend, original, host, deadline, peer, log, source,
                owner._token, owner._generation, owner._loop, owner._thread,
                _encode(owner._continuation), _encode(owner._durability),
                owner._raw, owner._streams, owner._client, owner._graph,
                owner._custody,
                runtime_binding,
                owner._callbacks,
            )
            if runtime_binding is not None:
                runtime_binding.bind(owner, deadline)
            owner._check()
            return owner
        except BaseException:
            await owner.aclose()
            raise

    @property
    def client(self) -> AcpClient:
        """The candidate remains inert until this owner's activation succeeds."""
        return self._client

    def _stream_graph(self) -> tuple[object, ...]:
        raw, pair = self._raw, self._streams
        if type(raw) is not SessionHostClient or type(pair) is not AcpStreams:
            raise PrivateIPCError(Refusal.AUTHORITY)
        return (
            raw._reader, raw._writer, raw._writer.transport,
            raw._writer.get_extra_info("socket"),
            pair.reader, pair.writer, pair.writer.transport,
            pair.writer.get_extra_info("socket"),
            pair._pair_reader, pair._pair_writer, pair._pair_writer.transport,
            pair._pair_writer.get_extra_info("socket"),
        )

    def _check(self) -> None:
        if (
            self._closed or self._active or asyncio.get_running_loop() is not self._loop
            or threading.current_thread() is not self._thread or _self() != self._peer
            or self._loop.get_task_factory() is not None
            or (
                self._root, self._frontend, self._original, self._host, self._deadline,
                self._peer, self._log, self._source, self._token, self._generation,
                self._loop, self._thread, _encode(self._continuation), _encode(self._durability),
                self._raw, self._streams, self._client, self._graph,
                self._custody,
                self._runtime_binding,
                self._callbacks,
            ) != self._contract
            or any(a is not b for a, b in zip(self._stream_graph(), self._graph))
            or self._raw._closed or not self._raw.child_alive
            or self._raw._reader.at_eof() or self._raw._writer.is_closing()
            or self._raw._writer.transport.get_write_buffer_size()
            or self._streams._tasks
            or self._client._idle_transport.reader is not self._streams.reader
            or self._client._idle_transport.writer is not self._streams.writer
            or self._log._db is not self._log_database() or not self._source.read_only
            or any(a is not b for a, b in zip(
                (self._client._on_event, self._client._on_permission, self._client._host_closer),
                self._callbacks,
            ))
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        for reader, writer in (
            (self._streams.reader, self._streams.writer),
            (self._streams._pair_reader, self._streams._pair_writer),
        ):
            if (
                reader._buffer or reader.at_eof() or writer.is_closing()
                or writer.transport.get_write_buffer_size()
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
        self._deadline.remaining()
        self._original.private_payload(self._deadline)
        require_prepared_continuation(self._client, self._continuation, self._log)
        if self._runtime_binding is not None:
            self._runtime_binding.verify(self._deadline)
            if (
                self._runtime_binding._owner is not self
                or self._client._on_event is not self._runtime_binding._callback
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)

    def _log_database(self) -> Database:
        return (
            self._source if self._runtime_binding is None
            else self._runtime_binding.database
        )

    def _bind(self, receipt: HostReceipt) -> None:
        """Bind actual connected four-tuple to the native host's committed front."""
        local = self._raw._writer.get_extra_info("sockname")
        remote = self._raw._writer.get_extra_info("peername")
        peer = receipt.frontend_data_endpoint
        if (
            peer is None or type(local) is not tuple or type(remote) is not tuple
            or local[:2] != (peer.bind, peer.port)
            or remote[:2] != (receipt.data_endpoint.bind, receipt.data_endpoint.port)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)

    async def activate(self, query: CommitQuery) -> None:
        """Reconcile commit, consume HELLO, read committed bytes, then fence activation.

        ``query`` must perform a freshly registered native INSPECT_COMMIT and
        return its still-live channel and response. Neither its boolean outcome
        nor any cached verification can substitute for native host evidence.
        A bound passive manager stays unreachable; no ordinary discovery or
        ACP initialization runs and no serving/ADOPT permission follows.
        """
        self._check()
        if self._attempted:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._attempted = True
        held = None
        try:
            remaining = self._deadline.remaining()
            channel, payload = await asyncio.wait_for(query(self._deadline), remaining)
            held = verify_host_commit(
                payload, channel, self._original, self._frontend,
                self._token, self._generation, self._deadline,
            )
            committed = decode_host_receipt(held.private_payload(self._deadline))
            self._bind(committed)
            self._check()
            remaining = self._deadline.remaining()
            hello = await asyncio.wait_for(protocol.read_message(self._raw._reader), remaining)
            if (
                hello is None or hello[0] is not protocol.MsgType.HELLO
                or len(hello[1]) < 16
                or protocol.unpack_u64(hello[1]) != committed.max_seq
                or protocol.unpack_u64(hello[1][8:16]) != committed.child_pid
                or self._raw._reader._buffer
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            await verify_idle_durability(
                self._durability, self._continuation, self._log, self._source, self._deadline,
                log_database=self._log_database() if self._runtime_binding is not None else None,
            )
            self._check()
            held.close()
            held = None
            remaining = self._deadline.remaining()
            channel, payload = await asyncio.wait_for(query(self._deadline), remaining)
            held = verify_host_commit(
                payload, channel, self._original, self._frontend,
                self._token, self._generation, self._deadline,
            )
            self._bind(decode_host_receipt(held.private_payload(self._deadline)))
            self._check()
            with routing.routing_lock(
                self._root, deadline=time.monotonic() + self._deadline.remaining(),
            ):
                table = routing.read_table(self._root, strict=True)
                if not _matches_publication(table.get("active"), self._frontend):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                self._check()
                if self._raw._reader._buffer:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                held.private_payload(self._deadline)
                require_idle_durable_contents(
                    self._durability, self._continuation, self._log, self._source, self._deadline,
                    log_database=self._log_database() if self._runtime_binding is not None else None,
                )
                self._database = (
                    open_existing_writable(self._source) if self._runtime_binding is None
                    else self._runtime_binding.database
                )
                self._check()
                self._deadline.remaining()
                conn = self._client._connection._conn
                if (
                    not callable(getattr(conn, "_receive_loop", None))
                    or not callable(getattr(conn, "_on_receive_error", None))
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                self._log._db = self._database
                self._client._idle_transport.inert = False
                self._streams.on_transport_lost = self._client.mark_transport_lost
                self._streams.on_child_exit = self._client.mark_host_child_exited
                start_acp_relays(self._raw, self._streams)
                conn._recv_task = conn._tasks.create(
                    conn._receive_loop(), name="acp.Connection.receive",
                    on_error=conn._on_receive_error,
                )
                self._client._host_transport_alive = True
                if self._runtime_binding is not None:
                    self._runtime_binding.commit(self, self._deadline)
                self._active, self._commit = True, held
                held = None
        except routing.RoutingLockTimeout:
            await self.aclose()
            raise PrivateIPCError(Refusal.DEADLINE) from None
        except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
            await self.aclose()
            raise PrivateIPCError(Refusal.AUTHORITY) from None
        except BaseException:
            await self.aclose()
            raise
        finally:
            if held is not None:
                held.close()

    async def aclose(self) -> None:
        """Release only this frontend's sockets/tasks/DB and retained host duplicate."""
        if self._closed:
            return
        self._closed = True
        client, streams, raw, log, source, binding = getattr(
            self, "_custody",
            (self._client, self._streams, self._raw, self._log, self._source, self._runtime_binding),
        )
        if streams is not None:
            for task in streams._tasks:
                task.cancel()
        if raw is not None:
            raw._writer.close()
        if client is not None:
            client._host_transport_alive = False
            client._idle_transport.inert = True
            client._host_closer = None
            await client._connection.close()
        if streams is not None:
            await streams.aclose()
        if raw is not None:
            await raw.close()
        if self._database is not None and binding is None:
            log._db = source
            self._database.close()
        if binding is not None and binding._owner is self:
            binding.release(self)
        if self._commit is not None:
            self._commit.close()
