"""Typed private host observations, verified against separately held native peers."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Literal

from pydantic import TypeAdapter
from zdd.private_peer import PeerProcess
from zdd.private_protocol import MAX_FRAME_BYTES, Deadline, PeerIdentity, PrivateIPCError, Refusal
from zdd.routing import Endpoint

from ..predecessor_runtime import RetainedChannel, _matches_publication, _native_channel_type
from ..resolver_runtime import _encode
from .acp_boundary import AcpWireStamp


@dataclass(frozen=True)
class DataEndpoint:
    bind: str
    port: int


@dataclass(frozen=True)
class HostReceipt:
    """Wire observation only; decoding never grants native authority.

    ``config_identity`` is the host's stable launch context, not the changing
    composite runtime snapshot identity. Native process references stay local.
    """

    schema: Literal[1]
    host: PeerIdentity
    instance: str
    session_id: str
    config_identity: str
    attachment_generation: int
    frontend: Endpoint
    snapshot: str = field(repr=False)
    ack_cursor: int
    max_seq: int
    child_pid: int
    protocol_version: int
    host_version: str
    data_endpoint: DataEndpoint
    acp_wire: AcpWireStamp | None = None
    frontend_data_endpoint: DataEndpoint | None = None


_ADAPTER = TypeAdapter(HostReceipt)


def decode_host_receipt(payload: object) -> HostReceipt:
    try:
        wire = _encode(payload)
        if len(wire) > MAX_FRAME_BYTES - 2048:
            raise PrivateIPCError(Refusal.SIZE)
        receipt = _ADAPTER.validate_json(wire, strict=True)
        canonical = json.loads(_ADAPTER.dump_json(receipt))
        for name in ("acp_wire", "frontend_data_endpoint"):
            if type(payload) is dict and name not in payload:
                canonical.pop(name)
        if (
            type(receipt.schema) is not int
            or _encode(canonical) != wire
            or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (
                receipt.instance, receipt.config_identity, receipt.snapshot,
            ))
            or not receipt.session_id or "\0" in receipt.session_id
            or receipt.attachment_generation <= 0
            or not 0 <= receipt.ack_cursor <= receipt.max_seq
            or receipt.child_pid <= 0 or receipt.host.pid <= 0
            or not receipt.host.start_time or not receipt.host.principal
            or receipt.frontend.pid <= 0 or not receipt.frontend.process_start_time
            or receipt.frontend.generation <= 0
            or not 0 < receipt.frontend.port <= 65535
            or not receipt.frontend.version or not receipt.frontend.bind
            or receipt.protocol_version != 1 or not receipt.host_version
            or receipt.data_endpoint.bind not in ("127.0.0.1", "::1")
            or not 0 < receipt.data_endpoint.port <= 65535
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if receipt.acp_wire is not None and (
            any(type(value) is not int or not 0 <= value < (1 << 63)
                for value in receipt.acp_wire.__dict__.values())
            or receipt.acp_wire.cursor != receipt.ack_cursor
            or receipt.ack_cursor != receipt.max_seq
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if receipt.frontend_data_endpoint is not None and (
            receipt.frontend_data_endpoint.bind not in ("127.0.0.1", "::1")
            or not 0 < receipt.frontend_data_endpoint.port <= 65535
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        return receipt
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


class VerifiedHostReceipt:
    """Caller-local retained native host evidence; never serializable custody."""

    def __init__(
        self, payload: object, channel: RetainedChannel, host: PeerIdentity,
        frontend: Endpoint, session_id: str, host_context: str, deadline: Deadline,
    ) -> None:
        if (
            type(channel) is not _native_channel_type() or type(host) is not PeerIdentity
            or type(frontend) is not Endpoint or type(deadline) is not Deadline
            or type(session_id) is not str or type(host_context) is not str
        ):
            raise PrivateIPCError(Refusal.IDENTITY)
        channel.check_peer(deadline)
        receipt = decode_host_receipt(payload)
        if (
            channel.peer != host or receipt.host != host
            or type(frontend) is not Endpoint
            or not _matches_publication(receipt.frontend.to_dict(), frontend)
            or receipt.session_id != session_id or receipt.config_identity != host_context
        ):
            raise PrivateIPCError(Refusal.IDENTITY)
        reference = channel.retain_peer(deadline)
        try:
            if (
                type(reference) is not PeerProcess or reference.peer != host
                or reference.exited(deadline)
            ):
                raise PrivateIPCError(Refusal.IDENTITY)
            channel.check_peer(deadline)
            self._reference = reference
            self._deadline = deadline
            self._wire = _encode(payload)
            self._thread = threading.current_thread()
            self._pid = os.getpid()
            self._contract = (
                reference, host, self._wire, self._thread, self._pid, deadline,
            )
            self._closed = False
        except BaseException:
            reference.close()
            raise

    def private_payload(self, deadline: Deadline) -> dict[str, object]:
        if (
            type(deadline) is not Deadline or self._closed or os.getpid() != self._pid
            or threading.current_thread() is not self._thread
            or (
                self._reference, self._reference.peer, self._wire,
                self._thread, self._pid, self._deadline,
            ) != self._contract
        ):
            raise PrivateIPCError(Refusal.IDENTITY)
        self._deadline.remaining()
        deadline.remaining()
        if self._reference.exited(deadline):
            raise PrivateIPCError(Refusal.IDENTITY)
        return json.loads(self._wire)

    def close(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if not self._closed:
            self._closed = True
            self._contract[0].close()
