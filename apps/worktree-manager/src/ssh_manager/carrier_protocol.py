"""Wire protocol primitives for the persistent SSH stdio carrier.

Envelope framing, encode/decode, handshake validation, and the small
error hierarchy shared by both the client (`carrier_persistent`) and
server (`carrier_server`) sides of the carrier protocol.
"""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, BinaryIO, Protocol

PROTOCOL_VERSION = 1
MIN_PROTOCOL_VERSION = 1
DEFAULT_MAX_FRAME_SIZE = 1024 * 1024
DEFAULT_MAX_QUEUED_FRAMES = 128
DEFAULT_MAX_BUFFERED_BYTES = 4 * 1024 * 1024

_HEADER = struct.Struct(">I")


class EnvelopeType(str, Enum):
    """Protocol envelope kinds."""

    HELLO = "hello"
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    HEARTBEAT = "heartbeat"
    CANCEL = "cancel"
    ERROR = "error"


class CarrierError(RuntimeError):
    """Base carrier failure with an explicit reconnectability signal."""

    def __init__(self, message: str, *, reconnectable: bool = False) -> None:
        super().__init__(message)
        self.reconnectable = reconnectable


class CarrierProtocolError(CarrierError):
    """Malformed, oversized, or incompatible protocol data."""


class CarrierBackpressure(CarrierError):
    """A bounded output queue cannot accept more data."""


class CarrierUnavailable(CarrierError):
    """The SSH transport is unavailable and may be reconnected."""


class CarrierStale(CarrierUnavailable):
    """The peer stopped making heartbeat or subscription progress."""


class CarrierRemoteError(CarrierError):
    """A structured error returned by the remote carrier endpoint."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.code = str(payload.get("code") or "remote_error")
        super().__init__(
            str(payload.get("message") or "remote carrier error"),
            reconnectable=bool(payload.get("reconnectable", False)),
        )


@dataclass(frozen=True)
class Envelope:
    """One framed carrier envelope."""

    type: EnvelopeType
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    subscription_id: str | None = None
    replayable: bool = False
    position: str | int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type.value}
        if self.request_id is not None:
            data["request_id"] = self.request_id
        if self.subscription_id is not None:
            data["subscription_id"] = self.subscription_id
        if self.replayable:
            data["replayable"] = True
        if self.position is not None:
            data["position"] = self.position
        if self.payload:
            data["payload"] = self.payload
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Envelope:
        if not isinstance(data, dict):
            raise CarrierProtocolError("carrier envelope must be an object")
        try:
            kind = EnvelopeType(data["type"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CarrierProtocolError("carrier envelope has an invalid type") from exc
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise CarrierProtocolError("carrier envelope payload must be an object")
        request_id = data.get("request_id")
        subscription_id = data.get("subscription_id")
        if request_id is not None and not isinstance(request_id, str):
            raise CarrierProtocolError("request_id must be a string")
        if subscription_id is not None and not isinstance(subscription_id, str):
            raise CarrierProtocolError("subscription_id must be a string")
        return cls(
            type=kind,
            payload=payload,
            request_id=request_id,
            subscription_id=subscription_id,
            replayable=bool(data.get("replayable", False)),
            position=data.get("position"),
        )


def hello_envelope(*, max_frame_size: int = DEFAULT_MAX_FRAME_SIZE) -> Envelope:
    """Build the version-negotiation hello."""
    return Envelope(
        EnvelopeType.HELLO,
        payload={
            "protocol_version": PROTOCOL_VERSION,
            "min_protocol_version": MIN_PROTOCOL_VERSION,
            "max_frame_size": int(max_frame_size),
        },
    )


def encode_envelope(
    envelope: Envelope,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> bytes:
    """Encode one envelope as a four-byte length prefix plus JSON bytes."""
    try:
        body = json.dumps(
            envelope.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CarrierProtocolError("carrier envelope is not JSON serializable") from exc
    if not body or len(body) > max_frame_size:
        raise CarrierProtocolError(
            f"carrier frame size {len(body)} exceeds limit {max_frame_size}"
        )
    return _HEADER.pack(len(body)) + body


def decode_envelope(
    body: bytes,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> Envelope:
    """Decode a frame body after enforcing its bound."""
    if not body or len(body) > max_frame_size:
        raise CarrierProtocolError(
            f"carrier frame size {len(body)} exceeds limit {max_frame_size}"
        )
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CarrierProtocolError("carrier frame is not valid UTF-8 JSON") from exc
    return Envelope.from_dict(data)


async def read_envelope(
    reader: asyncio.StreamReader,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> Envelope:
    """Read one bounded length-prefixed envelope."""
    header = await reader.readexactly(_HEADER.size)
    (size,) = _HEADER.unpack(header)
    if size <= 0 or size > max_frame_size:
        raise CarrierProtocolError(
            f"carrier frame size {size} exceeds limit {max_frame_size}"
        )
    return decode_envelope(
        await reader.readexactly(size),
        max_frame_size=max_frame_size,
    )


def read_envelope_sync(
    reader: BinaryIO,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> Envelope | None:
    """Read one envelope from a blocking stream, returning ``None`` on clean EOF."""
    header = reader.read(_HEADER.size)
    if not header:
        return None
    while len(header) < _HEADER.size:
        chunk = reader.read(_HEADER.size - len(header))
        if not chunk:
            raise EOFError("carrier stdin ended inside a frame header")
        header += chunk
    (size,) = _HEADER.unpack(header)
    if size <= 0 or size > max_frame_size:
        raise CarrierProtocolError(
            f"carrier frame size {size} exceeds limit {max_frame_size}"
        )
    body = b""
    while len(body) < size:
        chunk = reader.read(size - len(body))
        if not chunk:
            raise EOFError("carrier stdin ended inside a frame")
        body += chunk
    return decode_envelope(body, max_frame_size=max_frame_size)


def write_envelope_sync(
    writer: BinaryIO,
    envelope: Envelope,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> None:
    """Write and flush one envelope to a blocking stream."""
    writer.write(encode_envelope(envelope, max_frame_size=max_frame_size))
    writer.flush()


def validate_hello(envelope: Envelope) -> int:
    """Validate a peer hello and return the negotiated protocol version."""
    if envelope.type is not EnvelopeType.HELLO:
        raise CarrierProtocolError("carrier peer did not send hello first")
    try:
        peer_current = int(envelope.payload["protocol_version"])
        peer_min = int(envelope.payload["min_protocol_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CarrierProtocolError("carrier hello has invalid version fields") from exc
    peer_max_frame_size = negotiated_frame_size(envelope)
    low = max(MIN_PROTOCOL_VERSION, peer_min)
    high = min(PROTOCOL_VERSION, peer_current)
    if low > high:
        raise CarrierProtocolError(
            f"no compatible carrier protocol (local {MIN_PROTOCOL_VERSION}-"
            f"{PROTOCOL_VERSION}, peer {peer_min}-{peer_current})"
        )
    if peer_max_frame_size <= 0:
        raise CarrierProtocolError("carrier hello has invalid max_frame_size")
    return high


def negotiated_frame_size(
    envelope: Envelope,
    *,
    local_max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> int:
    """Return the smaller valid local/peer outbound frame limit."""
    try:
        peer_max_frame_size = int(envelope.payload["max_frame_size"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CarrierProtocolError(
            "carrier hello has invalid max_frame_size"
        ) from exc
    if peer_max_frame_size <= 0 or local_max_frame_size <= 0:
        raise CarrierProtocolError("carrier hello has invalid max_frame_size")
    return min(local_max_frame_size, peer_max_frame_size)


class _Process(Protocol):
    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None
    returncode: int | None

    async def wait(self) -> int: ...
