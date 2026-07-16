"""The Session-Host <-> frontend wire protocol (control + data, multiplexed).

One local connection (AF_UNIX or loopback TCP) carries both logical channels the
design calls for. Message framing is length-prefixed and byte-exact so the ACP
**data** payloads are relayed 1:1:

    <4-byte big-endian total length><1-byte type><payload>

The envelope is versioned independently of ACP (``PROTOCOL_VERSION``) and is
deliberately tiny -- it changes only on a breaking transport change, which is the
rare event the version-mux strategy (Phase 4) exists for.
"""

from __future__ import annotations

import asyncio
import enum
import struct

# Bump only on a breaking envelope change (not on an ACP change -- ACP rides the
# data channel opaquely).
PROTOCOL_VERSION = 1

_U32 = struct.Struct(">I")
_U64 = struct.Struct(">Q")

# Largest single wire message. ACP frames can be large (e.g. a diff), so allow
# generous headroom; a frame exceeding this is a protocol violation.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


class ProtocolError(Exception):
    """Raised on a malformed or oversized wire message."""


class MsgType(bytes, enum.Enum):
    # Frontend -> Host
    ATTACH = b"A"      # payload: u64 last_acked_seq (0 == fresh attach)
    ACK = b"K"         # payload: u64 seq
    WRITE = b"W"       # payload: raw ACP bytes to relay into child stdin
    TERMINATE = b"T"   # payload: empty -- explicit, sanctioned reap
    STATUS = b"S"      # payload: u8 reapable(1/0) -- latest "idle + no active
                       # background tasks" signal, so the host can self-reap an
                       # idle child if the front is lost (#51 auto-reap)
    DETACH = b"D"      # payload: u8 reapable(1/0) -- the front is disconnecting
                       # GRACEFULLY (vs a hard drop, which surfaces only as EOF);
                       # lets the host reap a reapable child promptly instead of
                       # after the unexpected-disconnect grace window

    # Host -> Frontend
    HELLO = b"H"       # payload: u64 max_seq + u64 child_pid
    FRAME = b"F"       # payload: u64 seq + raw ACP frame bytes (verbatim)
    LIVENESS = b"L"    # payload: u8 alive(1/0) + u32 exit_code


def pack_u64(n: int) -> bytes:
    return _U64.pack(n)


def unpack_u64(b: bytes) -> int:
    return _U64.unpack(b[:8])[0]


def pack_frame(seq: int, data: bytes) -> bytes:
    return _U64.pack(seq) + data


def unpack_frame(payload: bytes) -> tuple[int, bytes]:
    return _U64.unpack(payload[:8])[0], payload[8:]


def pack_attach(last_acked: int, nonce: bytes = b"") -> bytes:
    """Encode an ATTACH payload: ``u64 last_acked`` + optional trailing nonce.

    The nonce (if any) rides *after* the fixed 8-byte cursor. A legacy host that
    only reads ``payload[:8]`` transparently ignores it, so adding connect-auth
    is backward-compatible without a protocol-version bump. A host launched with
    a nonce validates the trailing bytes (see :meth:`SessionHost` handling); an
    unsecured host ignores them.
    """
    return _U64.pack(last_acked) + nonce


def unpack_attach(payload: bytes) -> tuple[int, bytes]:
    """Decode an ATTACH payload into ``(last_acked, nonce)``.

    ``nonce`` is empty when the frontend sent none (legacy / unsecured).
    """
    return _U64.unpack(payload[:8])[0], payload[8:]


def pack_liveness(alive: bool, exit_code: int = 0) -> bytes:
    return (b"\x01" if alive else b"\x00") + _U32.pack(exit_code & 0xFFFFFFFF)


def pack_flag(value: bool) -> bytes:
    """Encode a single boolean (STATUS/DETACH ``reapable``) as one byte."""
    return b"\x01" if value else b"\x00"


def unpack_flag(payload: bytes) -> bool:
    """Decode a single-byte boolean; a missing/empty payload reads False."""
    return payload[:1] == b"\x01"


def unpack_liveness(payload: bytes) -> tuple[bool, int]:
    alive = payload[:1] == b"\x01"
    code = _U32.unpack(payload[1:5])[0] if len(payload) >= 5 else 0
    return alive, code


def encode(mtype: MsgType, payload: bytes = b"") -> bytes:
    body = bytes(mtype.value) + payload
    return _U32.pack(len(body)) + body


async def write_message(
    writer: asyncio.StreamWriter, mtype: MsgType, payload: bytes = b"",
) -> None:
    writer.write(encode(mtype, payload))
    await writer.drain()


async def read_message(
    reader: asyncio.StreamReader,
) -> tuple[MsgType | None, bytes] | None:
    """Read one framed message. Returns ``None`` on clean EOF.

    Returns ``(None, payload)`` for a **well-formed message whose type byte this
    build does not recognize** -- forward-compatibility (#51): a newer peer may
    send additive control messages (e.g. STATUS/DETACH) a version-skewed
    host/front has never heard of. The frame is fully consumed and reported as
    an unknown, so the caller's dispatch simply skips it (its ``if/elif`` on the
    type matches nothing) rather than dropping the connection. An **oversized or
    truncated** frame is still a genuine :class:`ProtocolError`.

    A peer that crashes hard (RST) surfaces as EOF/``IncompleteReadError`` and is
    reported as ``None`` -- a clean disconnect, never an exception the host must
    crash on.
    """
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError:
        return None
    (length,) = _U32.unpack(header)
    if length == 0:
        return None
    if length > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"message length {length} exceeds cap {MAX_MESSAGE_BYTES}")
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None
    try:
        mtype: MsgType | None = MsgType(body[:1])
    except ValueError:
        # Unknown but well-formed type: skip (forward-compat, #51).
        mtype = None
    return mtype, body[1:]
