"""Opt-in native-authorized attachment ownership, never ordinary recovery."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from zdd import routing
from zdd.diagnostics import process_start_time
from zdd.private_peer import PeerProcess
from zdd.private_protocol import (
    Deadline, PeerIdentity, PrivateIPCError, Refusal, read_frame, write_frame,
)
from zdd.private_windows_native import Native
from zdd.routing import Endpoint

from ..predecessor_runtime import _matches_publication, _native_channel_type
from .. import __version__
from . import protocol
from .acp_boundary import AcpWireBoundary, AcpWireStamp

if TYPE_CHECKING:
    from ..predecessor_runtime import RetainedChannel
    from .host import SessionHost, _Front


class AttachmentOperation(str, Enum):
    """Separately granted steps; capture and preparation cannot activate."""

    CAPTURE = "capture-host"
    RESERVE = "reserve-host-attachment"
    ACTIVATE = "activate-host-attachment"
    INSPECT_COMMIT = "inspect-host-commit"


@dataclass(frozen=True)
class _Grant:
    peer: PeerIdentity
    endpoint: Endpoint
    operation: AttachmentOperation
    deadline: Deadline = field(repr=False)
    snapshot: str | None = field(repr=False)


@dataclass
class _Stage:
    ticket: bytes = field(repr=False)
    recovery_snapshot: str = field(repr=False)
    endpoint: Endpoint
    reference: PeerProcess = field(repr=False)
    deadline: Deadline = field(repr=False)
    activated: asyncio.Future[None] = field(repr=False)
    front: _Front | None = field(default=None, repr=False)
    expiry: asyncio.TimerHandle | None = field(default=None, repr=False)


def _self() -> PeerIdentity:
    token = process_start_time(os.getpid())
    if token is None:
        raise PrivateIPCError(Refusal.IDENTITY)
    principal = Native().current_sid() if os.name == "nt" else str(os.geteuid())
    return PeerIdentity(os.getpid(), token, principal)


class PrivateAttachment:
    """One host's native-pinned frontend owner and two-phase attachment.

    An owning launcher explicitly opts in before the first TCP attachment.
    Grants are independently registered on the host event loop, then consumed
    over an authenticated native channel. No listener is automatically created.
    The host retains the original frontend process reference while it is live;
    successor reservation requires that exact reference's exit. A copied TCP
    socket, nonce, frame PID or host-index record conveys no control authority.
    """

    def __init__(
        self, host: SessionHost, root: Path, identity: str, session_id: str,
        *, acp_wire_boundary: bool = False,
    ) -> None:
        from .host import SessionHost

        if (
            type(host) is not SessionHost or host._front is not None
            or type(host._attachment_epoch) is not int or host._attachment_epoch != 0
            or getattr(host, "_private_attachment", None) is not None
            or not isinstance(root, Path)
            or not root.is_absolute() or ".." in root.parts
            or type(identity) is not str or len(identity) != 64
            or any(c not in "0123456789abcdef" for c in identity)
            or type(session_id) is not str or not session_id or "\0" in session_id
            or type(acp_wire_boundary) is not bool
            or host._private_acp_boundary is not None
            or (acp_wire_boundary and (host._max_seq or host._frames or host._reader_task))
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            raise PrivateIPCError(Refusal.AUTHORITY) from None
        self._thread = threading.current_thread()
        self._self = _self()
        self._host, self._root, self._identity, self._session = host, root, identity, session_id
        self._acp_boundary = AcpWireBoundary() if acp_wire_boundary else None
        host._private_acp_boundary = self._acp_boundary
        self._instance = secrets.token_hex(32)
        self._contract = (
            root, identity, session_id, self._instance, self._self, self._acp_boundary,
        )
        self._generation = 0
        self._owner: PeerProcess | None = None
        self._endpoint: Endpoint | None = None
        self._owned_front: _Front | None = None
        self._snapshot: str | None = None
        self._committed_recovery: str | None = None
        self._snapshot_cursor = 0
        self._snapshot_generation = 0
        self._snapshot_acp: AcpWireStamp | None = None
        self._stage: _Stage | None = None
        self._server: asyncio.Server | None = None
        self._sockets: tuple[socket.socket, ...] = ()
        self._grants: dict[str, _Grant] = {}
        self._closed = False
        host._private_attachment = self

    def _check_owner(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise PrivateIPCError(Refusal.AUTHORITY) from None
        if (
            self._closed or os.getpid() != self._self.pid
            or threading.current_thread() is not self._thread
            or loop is not self._loop or _self() != self._self
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)

    def _check(self) -> None:
        self._check_owner()
        if (
            (
                self._root, self._identity, self._session, self._instance,
                self._self, self._acp_boundary,
            ) != self._contract
            or self._host._private_acp_boundary is not self._acp_boundary
            or (self._acp_boundary is not None and type(self._acp_boundary) is not AcpWireBoundary)
            or self._host._private_attachment is not self or self._host._closing
            or not self._host.child_alive or self._host._child_done.is_set()
            or type(self._host._ack_cursor) is not int
            or type(self._host._max_seq) is not int
            or type(self._host._attachment_epoch) is not int
            or type(self._generation) is not int or self._generation < 0
            or self._host._attachment_epoch != self._generation
            or not 0 <= self._host._ack_cursor <= self._host._max_seq
            or self._host._front not in (None, self._owned_front)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            self._check_listener()
        except (OSError, AttributeError, ValueError, TypeError, IndexError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def _check_listener(self) -> None:
        server = self._host._server
        if type(server) is not asyncio.Server or not server.is_serving():
            raise PrivateIPCError(Refusal.AUTHORITY)
        sockets = tuple(server._sockets or ())
        if self._server is None:
            self._server, self._sockets = server, sockets
        if (
            server is not self._server or len(sockets) != 1
            or len(sockets) != len(self._sockets)
            or any(actual is not original for actual, original in zip(sockets, self._sockets))
            or sockets[0].getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1
            or sockets[0].getsockname()[0] not in ("127.0.0.1", "::1")
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)

    def register(
        self, peer: PeerIdentity, endpoint: Endpoint, operation: AttachmentOperation,
        deadline: Deadline, *, snapshot: str | None = None,
    ) -> str:
        """Register independently known caller/publication; never accept a frame identity."""
        self._check()
        deadline.remaining()
        if (
            type(peer) is not PeerIdentity or type(endpoint) is not Endpoint
            or type(operation) is not AttachmentOperation
            or type(endpoint.pid) is not int or endpoint.pid != peer.pid
            or type(endpoint.process_start_time) is not str
            or endpoint.process_start_time != peer.start_time
            or peer.principal != self._self.principal or peer.pid == self._self.pid
            or type(endpoint.generation) is not int or endpoint.generation <= 0
            or type(endpoint.port) is not int or not 0 < endpoint.port <= 65535
            or type(endpoint.version) is not str or not endpoint.version
            or type(endpoint.bind) is not str or not endpoint.bind
            or (snapshot is not None and (
                type(snapshot) is not str
                or len(snapshot) != 64
                or any(c not in "0123456789abcdef" for c in snapshot)
                or (operation is not AttachmentOperation.INSPECT_COMMIT
                    and snapshot != self._snapshot)
            ))
        ):
            raise PrivateIPCError(Refusal.IDENTITY)
        # Expired registrations cannot consume capacity indefinitely.
        for token, grant in tuple(self._grants.items()):
            try:
                grant.deadline.remaining()
            except PrivateIPCError:
                del self._grants[token]
        if len(self._grants) >= 16:
            raise PrivateIPCError(Refusal.CAPACITY)
        token = secrets.token_hex(32)
        self._grants[token] = _Grant(peer, endpoint, operation, deadline, snapshot)
        return token

    def execute(self, channel: RetainedChannel, request: object) -> dict[str, object]:
        """Consume one native grant under exact frontend routing authority."""
        self._check_owner()
        if (
            type(request) is not dict or set(request) != {"grant"}
            or type(request["grant"]) is not str
        ):
            raise PrivateIPCError(Refusal.PROTOCOL)
        grant = self._grants.pop(request["grant"], None)
        if grant is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            self._check()
            grant.deadline.remaining()
            if type(channel) is not _native_channel_type():
                raise PrivateIPCError(Refusal.IDENTITY)
            with routing.routing_lock(
                self._root, deadline=time.monotonic() + grant.deadline.remaining(),
            ):
                channel.check_peer(grant.deadline)
                table = routing.read_table(self._root, strict=True)
                self._check()
                if (
                    channel.peer != grant.peer or type(table) is not dict
                    or not _matches_publication(table.get("active"), grant.endpoint)
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                if grant.operation is AttachmentOperation.CAPTURE:
                    result = self._capture(grant)
                elif grant.operation is AttachmentOperation.RESERVE:
                    result = self._reserve(channel, grant)
                elif grant.operation is AttachmentOperation.INSPECT_COMMIT:
                    result = self._inspect_commit(grant)
                else:
                    result = self._activate(grant)
                grant.deadline.remaining()
                return result
        except routing.RoutingLockTimeout:
            raise PrivateIPCError(Refusal.DEADLINE) from None
        except (OSError, ValueError, TypeError, AttributeError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    async def handle(self, channel: RetainedChannel, deadline: Deadline) -> None:
        """Serve one framed native request without blocking the host event loop."""
        self._check()
        if type(channel) is not _native_channel_type():
            raise PrivateIPCError(Refusal.IDENTITY)
        request = await asyncio.to_thread(read_frame, channel, deadline)
        try:
            response = self.execute(channel, request)
        except PrivateIPCError as error:
            response = {"refusal": error.reason.value}
        await asyncio.to_thread(write_frame, channel, response, deadline)

    def _same_owner(self, grant: _Grant) -> bool:
        return (
            self._owner is not None and self._endpoint is not None
            and self._owner.peer == grant.peer
            and _matches_publication(grant.endpoint.to_dict(), self._endpoint)
        )

    def _capture(self, grant: _Grant) -> dict[str, object]:
        if (
            not self._same_owner(grant) or self._owner.exited(grant.deadline)
            or self._host._front is not self._owned_front or self._owned_front is None
            or self._owned_front.closed or self._owned_front.reader.at_eof()
            or self._stage is not None
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        stamp = (
            self._acp_boundary.capture(self._host.ack_cursor, self._host.max_seq)
            if self._acp_boundary is not None else None
        )
        self._snapshot = secrets.token_hex(32)
        self._committed_recovery = None
        self._snapshot_acp = stamp
        self._snapshot_cursor = self._host.ack_cursor
        self._snapshot_generation = self._generation
        return self._receipt(stamp)

    def _receipt(self, stamp: AcpWireStamp | None) -> dict[str, object]:
        peer = self._owned_front.writer.get_extra_info("peername")
        if (
            type(peer) is not tuple or len(peer) not in (2, 4)
            or peer[0] not in ("127.0.0.1", "::1")
            or type(peer[1]) is not int or not 0 < peer[1] <= 65535
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        return {
            "schema": 1, "host": dict(self._self.__dict__), "instance": self._instance,
            "session_id": self._session, "config_identity": self._identity,
            "attachment_generation": self._generation,
            "frontend": self._endpoint.to_dict(), "snapshot": self._snapshot,
            "ack_cursor": self._snapshot_cursor, "max_seq": self._host.max_seq,
            "child_pid": self._host.child_pid, "protocol_version": protocol.PROTOCOL_VERSION,
            "host_version": __version__,
            "data_endpoint": {
                "bind": self._sockets[0].getsockname()[0],
                "port": self._sockets[0].getsockname()[1],
            },
            **({"acp_wire": asdict(stamp)} if stamp is not None else {}),
            "frontend_data_endpoint": {"bind": peer[0], "port": peer[1]},
        }

    def _inspect_commit(self, grant: _Grant) -> dict[str, object]:
        """Observe only an already committed, still-current recovery token."""
        if (
            not self._same_owner(grant) or self._owner.exited(grant.deadline)
            or grant.snapshot is None or grant.snapshot != self._snapshot
            or grant.snapshot != self._committed_recovery
            or self._generation != self._snapshot_generation
            or self._stage is not None or self._owned_front is None
            or self._host._front is not self._owned_front
            or self._owned_front.closed or self._owned_front.reader.at_eof()
            or self._owned_front.writer.is_closing()
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        stamp = (
            self._acp_boundary.capture(self._host.ack_cursor, self._host.max_seq)
            if self._acp_boundary is not None else None
        )
        if self._acp_boundary is not None and stamp != self._snapshot_acp:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if self._host.ack_cursor != self._snapshot_cursor:
            raise PrivateIPCError(Refusal.AUTHORITY)
        return self._receipt(stamp)

    def _reserve(self, channel: RetainedChannel, grant: _Grant) -> dict[str, object]:
        if self._stage is not None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if self._owner is not None:
            if self._acp_boundary is not None:
                self._acp_boundary.require_unchanged(
                    self._snapshot_acp, self._host.ack_cursor, self._host.max_seq,
                )
            if (
                grant.snapshot is None or grant.snapshot != self._snapshot
                or self._generation != self._snapshot_generation
                or self._endpoint is None
                or grant.endpoint.generation <= self._endpoint.generation
                or grant.peer == self._owner.peer
                or not self._owner.exited(grant.deadline)
                or self._host.ack_cursor < self._snapshot_cursor
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
        elif self._generation or grant.snapshot is not None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        reference = channel.retain_peer(grant.deadline)
        try:
            if (
                type(reference) is not PeerProcess or reference.peer != grant.peer
                or reference.exited(grant.deadline)
            ):
                raise PrivateIPCError(Refusal.IDENTITY)
            channel.check_peer(grant.deadline)
            ticket = secrets.token_hex(32).encode("ascii")
            stage = _Stage(
                ticket, secrets.token_hex(32), grant.endpoint, reference, grant.deadline,
                self._loop.create_future(),
            )
            stage.expiry = self._loop.call_later(
                grant.deadline.remaining(), self._discard_stage,
            )
            self._stage = stage
            return {
                "ticket": ticket.decode("ascii"), "prepared": False, "attached": False,
                "recovery_snapshot": stage.recovery_snapshot,
                "recovery_generation": self._generation + 1,
            }
        except BaseException:
            reference.close()
            raise

    async def read_ticket(self, reader: asyncio.StreamReader) -> bytes:
        """Accept only the fixed-size private handshake within the reservation budget."""
        self._check()
        stage = self._stage
        if stage is None or stage.front is not None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            remaining = stage.deadline.remaining()
            return await asyncio.wait_for(
                protocol.read_private_attach(reader), timeout=remaining,
            )
        except (asyncio.TimeoutError, TimeoutError, asyncio.IncompleteReadError, protocol.ProtocolError):
            raise PrivateIPCError(Refusal.PROTOCOL) from None

    async def prepare_stream(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, ticket: bytes,
    ) -> _Front:
        """Wait outside routing authority; no HELLO, ACP, ACK or owner displacement."""
        from .host import _Front

        self._check()
        stage = self._stage
        if (
            stage is None or stage.front is not None or type(ticket) is not bytes
            or not secrets.compare_digest(ticket, stage.ticket)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        stage.deadline.remaining()
        front = _Front(reader, writer, next_seq=0)
        stage.front = front
        try:
            await asyncio.wait_for(
                asyncio.shield(stage.activated), timeout=stage.deadline.remaining(),
            )
            self._check()
            if self._owned_front is not front or self._host._front is not front:
                raise PrivateIPCError(Refusal.AUTHORITY)
            return front
        except BaseException:
            if self._stage is stage:
                self._discard_stage()
            raise

    def _activate(self, grant: _Grant) -> dict[str, object]:
        stage = self._stage
        if (
            stage is None or stage.front is None or stage.front.closed
            or stage.front.writer.is_closing() or stage.front.reader.at_eof()
            or stage.reference.peer != grant.peer
            or not _matches_publication(grant.endpoint.to_dict(), stage.endpoint)
            or stage.reference.exited(grant.deadline)
            or (self._owner is not None and (
                not self._owner.exited(grant.deadline) or grant.snapshot != self._snapshot
                or self._generation != self._snapshot_generation
                or self._host.ack_cursor < self._snapshot_cursor
            ))
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        stage.deadline.remaining()
        grant.deadline.remaining()
        if self._owner is not None and self._acp_boundary is not None:
            self._acp_boundary.require_unchanged(
                self._snapshot_acp, self._host.ack_cursor, self._host.max_seq,
            )
        old = self._owned_front
        if self._owner is not None:
            self._owner.close()
        if old is not None:
            old.closed = True
            old.writer.close()
        stage.front.next_seq = self._host.ack_cursor + 1
        self._host._cancel_reap_timer()
        self._host._graceful_detach = False
        self._host._front = self._owned_front = stage.front
        self._owner, self._endpoint = stage.reference, stage.endpoint
        self._generation += 1
        self._host._attachment_epoch += 1
        # The reservation delivered this token before commit; it is not valid
        # until this exact stage owns the host and its native owner later exits.
        self._snapshot = stage.recovery_snapshot
        self._committed_recovery = stage.recovery_snapshot
        self._snapshot_generation = self._generation
        self._snapshot_cursor = self._host.ack_cursor
        self._stage = None
        if stage.expiry is not None:
            stage.expiry.cancel()
        stage.activated.set_result(None)
        return {
            "attached": True, "attachment_generation": self._generation,
            "ack_cursor": self._host.ack_cursor,
        }

    def _discard_stage(self) -> None:
        stage, self._stage = self._stage, None
        if stage is not None:
            if stage.expiry is not None:
                stage.expiry.cancel()
            if stage.front is not None:
                stage.front.closed = True
                stage.front.writer.close()
            stage.reference.close()
            if not stage.activated.done():
                stage.activated.cancel()

    def close(self) -> None:
        """Release only owned preparation and native references; never reap the child."""
        if (
            threading.current_thread() is not self._thread
            or os.getpid() != self._self.pid or asyncio.get_running_loop() is not self._loop
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if not self._closed:
            self._closed = True
            self._grants.clear()
            self._discard_stage()
            if self._owner is not None:
                self._owner.close()
            if self._owned_front is not None:
                self._owned_front.closed = True
                self._owned_front.writer.close()
