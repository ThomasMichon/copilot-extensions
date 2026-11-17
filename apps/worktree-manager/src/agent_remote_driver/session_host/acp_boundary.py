"""Host-owned wire quiescence, not copied SDK Futures or reducer durability."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from zdd.private_protocol import PrivateIPCError, Refusal

from .protocol import MAX_MESSAGE_BYTES

log = logging.getLogger("agent-bridge.session-host")
_MAX_PENDING = 128
_MAX_ID_BYTES = 512
_MAX_COUNTER = (1 << 63) - 1
_RpcId = tuple[type, int | str]


@dataclass(frozen=True)
class AcpWireStamp:
    client_bytes: int
    child_bytes: int
    client_messages: int
    child_messages: int
    next_client_request_id: int
    cursor: int


def _rpc_id(value: object) -> _RpcId:
    if type(value) is int and -_MAX_COUNTER <= value <= _MAX_COUNTER:
        return (int, value)
    if (
        type(value) is str and value and "\0" not in value
        and len(value.encode("utf-8")) <= _MAX_ID_BYTES
    ):
        return (str, value)
    raise ValueError("unsupported RPC id")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("non-JSON constant")


class AcpWireBoundary:
    """Observe complete bidirectional NDJSON without changing the relay.

    Unsupported bytes permanently poison this optional continuation boundary,
    not ordinary child progress. Only request ids/methods and partial parser
    bytes remain here; no native handle, SDK task or request Future is copied.
    """

    def __init__(self) -> None:
        self._buffers = {True: b"", False: b""}
        self._pending: dict[bool, dict[_RpcId, str]] = {True: {}, False: {}}
        self._bytes = {True: 0, False: 0}
        self._messages = {True: 0, False: 0}
        self._next_client_request_id = 0
        self._fault = False

    @classmethod
    def from_idle_stamp(cls, stamp: AcpWireStamp) -> AcpWireBoundary:
        """Seed an inert observer; the data itself conveys no host authority."""
        if type(stamp) is not AcpWireStamp or any(
            type(value) is not int or not 0 <= value <= _MAX_COUNTER
            for value in stamp.__dict__.values()
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        boundary = cls()
        boundary._bytes = {True: stamp.client_bytes, False: stamp.child_bytes}
        boundary._messages = {True: stamp.client_messages, False: stamp.child_messages}
        boundary._next_client_request_id = stamp.next_client_request_id
        return boundary

    def invalidate(self) -> None:
        """Latch unsupported or uncertain transport state; never guess success."""
        if not self._fault:
            log.warning("Private ACP wire continuation boundary unavailable")
        self._fault = True
        self._buffers = {True: b"", False: b""}
        self._pending = {True: {}, False: {}}

    def observe(self, raw: bytes, *, to_child: bool) -> None:
        """Observe split/coalesced wire bytes; a fault never disrupts forwarding."""
        if self._fault:
            return
        if (
            type(raw) is not bytes or type(to_child) is not bool
            or len(raw) > MAX_MESSAGE_BYTES
        ):
            self.invalidate()
            return
        self._bytes[to_child] += len(raw)
        if self._bytes[to_child] > _MAX_COUNTER:
            self.invalidate()
            return
        data = self._buffers[to_child] + raw
        chunks = data.split(b"\n")
        self._buffers[to_child] = chunks.pop()
        if len(self._buffers[to_child]) > MAX_MESSAGE_BYTES:
            self.invalidate()
            return
        try:
            for line in chunks:
                if not line or len(line) > MAX_MESSAGE_BYTES:
                    raise ValueError("unsupported frame")
                message = json.loads(
                    line.decode("utf-8"), object_pairs_hook=_unique_object,
                    parse_constant=_invalid_constant,
                )
                self._observe_message(message, to_child)
                self._messages[to_child] += 1
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            self.invalidate()

    def _observe_message(self, message: object, to_child: bool) -> None:
        if type(message) is not dict or message.get("jsonrpc") != "2.0":
            raise ValueError("unsupported envelope")
        if "method" in message:
            method = message["method"]
            if (
                type(method) is not str or not method or "\0" in method
                or len(method.encode("utf-8")) > _MAX_ID_BYTES
                or "result" in message or "error" in message
                or (message.get("params") is not None
                    and type(message["params"]) not in (dict, list))
            ):
                raise ValueError("unsupported request")
            if "id" not in message:
                return
            key = _rpc_id(message["id"])
            pending = self._pending[to_child]
            if key in pending or len(pending) >= _MAX_PENDING:
                raise ValueError("unsupported outstanding requests")
            pending[key] = method
            request_id = message["id"]
            if to_child and type(request_id) is int:
                self._next_client_request_id = max(
                    self._next_client_request_id, request_id + 1,
                )
            return
        if ("result" in message) == ("error" in message) or "id" not in message:
            raise ValueError("unsupported response")
        if "error" in message and (
            type(message["error"]) is not dict
            or type(message["error"].get("code")) is not int
            or type(message["error"].get("message")) is not str
        ):
            raise ValueError("unsupported RPC error")
        key = _rpc_id(message["id"])
        if key not in self._pending[not to_child]:
            raise ValueError("unmatched RPC response")
        del self._pending[not to_child][key]

    def capture(self, ack_cursor: int, max_seq: int) -> AcpWireStamp:
        """Refuse pending RPC/parser state; ACK remains delivery, not DB commit."""
        if (
            self._fault or any(self._buffers.values()) or any(self._pending.values())
            or type(ack_cursor) is not int or type(max_seq) is not int
            or not 0 <= ack_cursor == max_seq <= _MAX_COUNTER
            or self._next_client_request_id > _MAX_COUNTER
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        return AcpWireStamp(
            self._bytes[True], self._bytes[False],
            self._messages[True], self._messages[False],
            self._next_client_request_id, ack_cursor,
        )

    def require_unchanged(self, stamp: AcpWireStamp | None, ack: int, maximum: int) -> None:
        """A later idle state does not retroactively make a stale capture safe."""
        if type(stamp) is not AcpWireStamp or self.capture(ack, maximum) != stamp:
            raise PrivateIPCError(Refusal.AUTHORITY)
