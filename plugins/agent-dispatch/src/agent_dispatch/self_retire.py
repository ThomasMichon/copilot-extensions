"""Owner-liveness tether for the coordinator's own generation: self-retire when
superseded.

A zero-downtime redeploy stands a new coordinator up beside the old one and flips
the shared routing table so clients follow it. If the deploy orchestrator never
sends the old coordinator its retire signal (it crashed, or the cutover was
abandoned), the demoted generation lingers as a stranded ``serve --passive``
process. The fix: a coordinator that observes it has been demoted -- a newer
generation flipped the routing table and now serves clients -- drains to its safe
cutover point (no in-flight claim) and exits on its own instead of lingering.

This module supplies only the **fail-safe decision** -- ``is_superseded`` -- as a
pure, fully-injectable function. The coordinator wires it into a guarded
background loop (see ``coordinator.py`` lifespan); the loop is opt-in and
additionally gates on the coordinator being at its safe cutover point (the
``DrainGate`` reports no in-flight claim) before it exits, so a claim mid-flight
is never dropped.

**Fail-safe by construction.** ``is_superseded`` returns ``True`` only when the
routing table's ``active`` entry is a *different* pid, at a *strictly higher*
generation, that is *actually listening* (a live successor). Every ambiguous
state -- no table, no/parse-broken ``active`` entry, our own pid still active, a
not-higher generation, or a successor that is not (yet) accepting connections --
returns ``False`` (stay alive). The genuinely-active coordinator always reads its
own pid as ``active`` and therefore can never self-retire; only a demoted
generation with a confirmed live successor ever can.
"""

from __future__ import annotations

import socket

from zdd import routing
from zdd.routing import Endpoint

# A loopback connect to a live successor returns in well under a millisecond;
# this bounds the probe so a slow/unreachable successor never blocks the caller
# (and, being non-listening, simply yields "not superseded -- stay alive").
_PROBE_TIMEOUT_S = 0.25


def _is_listening(host: str, port: int, *, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    """Return True iff something accepts a TCP connection at ``host:port``.

    Mirrors ``zdd.routing._listening`` (kept local so this module depends only on
    zdd's *public* surface). Any socket error is treated as "not listening",
    which the caller reads as "no confirmed live successor -- stay alive".
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def is_superseded(
    config_dir,
    my_pid: int,
    my_generation: int,
    *,
    read_table=routing.read_table,
    is_listening=_is_listening,
) -> bool:
    """Has a live, strictly-newer coordinator generation superseded us?

    ``True`` **only** when the routing table's ``active`` endpoint is a different
    pid than ``my_pid``, at a generation strictly greater than ``my_generation``,
    and is currently accepting connections. Any other (ambiguous) state returns
    ``False`` -- the fail-safe default is to stay alive.

    ``read_table`` and ``is_listening`` are injected for testing.
    """
    data = read_table(config_dir)
    if not isinstance(data, dict):
        return False
    raw = data.get("active")
    if not isinstance(raw, dict):
        return False
    ep = Endpoint.from_dict(raw)
    if ep is None:
        return False
    # Our own pid still holding the active slot => we are NOT superseded.
    if ep.pid is None or ep.pid == my_pid:
        return False
    # Only a strictly-newer generation counts as a successor (defeats a stale or
    # equal-generation entry, and any pid-reuse coincidence at our generation).
    if ep.generation <= my_generation:
        return False
    # Require a *live* successor: the newer generation must actually be serving.
    if not is_listening(ep.client_host, ep.port):
        return False
    return True
