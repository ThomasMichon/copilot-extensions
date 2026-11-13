"""Reattachable ACP byte-stream broker owned by the Copilot execution core."""

from .protocol import (
    MsgType,
    ProtocolError,
    pack_frame,
    pack_liveness,
    pack_u64,
    read_message,
    unpack_frame,
    unpack_liveness,
    unpack_u64,
    write_message,
)

__all__ = [
    "MsgType", "ProtocolError", "pack_frame", "pack_liveness", "pack_u64",
    "read_message", "unpack_frame", "unpack_liveness", "unpack_u64", "write_message",
]
