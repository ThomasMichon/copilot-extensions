"""work-coalescing-singleton -- fold many callers onto one warm daemon.

See ``docs/patterns/work-coalescing-singleton.md`` for the full design this
package implements: the wire protocol, the two-phase timeout budget
(boot-wait vs. per-request deadline), and the ref-count/linger idle-exit
algorithm.
"""

from __future__ import annotations

from .client import (
    DaemonUnavailable,
    call_with_fallback,
    new_client_id,
    release,
    request,
    subscribe,
)
from .server import CoalescingServer, Unavailable

__all__ = [
    "CoalescingServer",
    "DaemonUnavailable",
    "Unavailable",
    "call_with_fallback",
    "new_client_id",
    "release",
    "request",
    "subscribe",
]
