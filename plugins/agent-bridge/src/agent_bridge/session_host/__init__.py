"""Session Host -- the stable kernel that decouples a Copilot ``--acp`` child's
lifetime from the agent-bridge frontend.

Background: a ``copilot --acp --stdio`` child dies when the process holding its
stdin/stdout pipes exits. Today agent-bridge owns those pipes directly
(``transport.spawn_local``), so an agent-bridge restart (for an update) closes
the pipes and kills the child -- and killing it mid-turn corrupts its
resumability. The Session Host breaks that coupling: it is the child's *real*
parent, owns the pipes, and exposes a **reattachable** local endpoint so any
frontend generation can attach/detach without the child noticing ("tmux for the
Copilot process").

Design invariant (see effort ``agent-bridge-version-mux``): the host speaks
**1:1 ACP**. Two channels multiplexed over one connection:

* **data channel** -- the child's ACP byte stream, relayed byte-for-byte. ACP
  over stdio is newline-delimited JSON-RPC, so each newline-terminated line is
  one frame; the host numbers frames but never parses their semantics.
* **control channel** -- reattach handshake, a monotonic frame ``seq`` + ``ack``
  cursor (so a reattaching frontend misses nothing and re-reads nothing), child
  liveness, and an explicit terminate.

This package is **additive**: as of Phase 1 it is not yet wired into the default
spawn path (that is Phase 2's reattach work). It ships tested and dormant.
"""

from __future__ import annotations

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
    "MsgType",
    "ProtocolError",
    "pack_frame",
    "pack_liveness",
    "pack_u64",
    "read_message",
    "unpack_frame",
    "unpack_liveness",
    "unpack_u64",
    "write_message",
]
