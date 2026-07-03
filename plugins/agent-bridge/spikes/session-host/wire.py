"""Length-prefixed control/data wire protocol for the Session-Host spike.

Throwaway spike code (Phase 0, aperture-labs #1761). Not part of the shipped
``agent_bridge`` package -- it lives under ``spikes/`` and is never imported by
the plugin. It exists only to prove the survive-and-reattach primitive.

One loopback TCP connection multiplexes the two logical channels the design
calls for:

* **data channel** -- the child's ACP byte stream, relayed **1:1**. Each
  newline-delimited child frame is carried verbatim inside a ``FRAME`` message
  (the frame bytes, including their trailing newline, are byte-exact).
* **control channel** -- the reattach handshake (``ATTACH`` / ``HELLO``), the
  monotonic frame ``seq`` + ``ACK`` cursor, child liveness, and an explicit
  ``TERMINATE``.

Message framing: ``<4-byte BE total-len><1-byte type><payload>``.
"""

from __future__ import annotations

import socket
import struct

# Frontend -> Host
ATTACH = b"A"      # payload: 8-byte BE last_acked_seq (0 == fresh attach)
ACK = b"K"         # payload: 8-byte BE seq
WRITE = b"W"       # payload: raw bytes to relay into child stdin
TERMINATE = b"T"   # payload: empty -- explicit, sanctioned reap

# Host -> Frontend
HELLO = b"H"       # payload: 8-byte BE max_seq + 8-byte BE child_pid
FRAME = b"F"       # payload: 8-byte BE seq + raw frame bytes (verbatim)
LIVENESS = b"L"    # payload: 1 byte alive(1/0) + 4-byte BE exit code

_U64 = struct.Struct(">Q")
_U32 = struct.Struct(">I")


def send_msg(sock: socket.socket, mtype: bytes, payload: bytes = b"") -> None:
    body = mtype + payload
    sock.sendall(_U32.pack(len(body)) + body)


def _recv_exactly(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            raise
        except OSError:
            # Peer crashed (RST -> ConnectionResetError) or handle closed:
            # treat as EOF so callers see a clean disconnect, not a crash.
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_msg(sock: socket.socket) -> tuple[bytes, bytes] | None:
    """Return ``(mtype, payload)`` or ``None`` on clean EOF."""
    header = _recv_exactly(sock, 4)
    if header is None:
        return None
    (length,) = _U32.unpack(header)
    body = _recv_exactly(sock, length)
    if body is None or length == 0:
        return None
    return body[:1], body[1:]


def pack_u64(n: int) -> bytes:
    return _U64.pack(n)


def unpack_u64(b: bytes) -> int:
    return _U64.unpack(b[:8])[0]


def pack_frame(seq: int, data: bytes) -> bytes:
    return _U64.pack(seq) + data


def unpack_frame(payload: bytes) -> tuple[int, bytes]:
    return _U64.unpack(payload[:8])[0], payload[8:]


def pack_liveness(alive: bool, exit_code: int = 0) -> bytes:
    return (b"\x01" if alive else b"\x00") + _U32.pack(exit_code & 0xFFFFFFFF)
