"""Supersession decision -- has a live, strictly-newer generation replaced us?

A zero-downtime redeploy stands the new daemon up beside the old and flips a
routing table so clients follow it. That can leave a *demoted* daemon running
with nothing to shut it down -- if the deploy orchestrator never sends the retire
signal (it crashed, or the cutover was abandoned), the old generation lingers for
hours as a stranded passive process holding a port and memory. The fix: a daemon
that observes it has been demoted drains and exits on its own.

This module supplies only the **fail-safe decision** -- :func:`is_superseded` --
as a pure, fully-injectable function operating on a plain routing-table ``dict``
(the ``active``/``previous`` shape published by ``zdd.routing``). It carries **no
dependency on any routing library**: the consumer reads the table however it
likes and passes the parsed ``dict`` in. The daemon wires the decision into a
guarded background loop that additionally gates on the daemon being *idle* before
it exits, so an in-flight turn on a demoted daemon is never cut mid-flight.

**Fail-safe by construction.** :func:`is_superseded` returns ``True`` only when
the table's ``active`` entry is a *different* pid, at a *strictly higher*
generation, that is *actually listening* (a live successor). Every ambiguous
state -- no table, no/parse-broken ``active`` entry, our own pid still active, a
not-higher generation, or a successor that is not (yet) accepting connections --
returns ``False`` (stay alive). The genuinely-active daemon always reads its own
pid as ``active`` and therefore can never self-retire.

Extracted from agent-bridge's ``self_retire`` module.
"""

from __future__ import annotations

import os
import socket
import sys

# A loopback connect to a live successor returns in well under a millisecond;
# this bounds the probe so a slow/unreachable successor never blocks the caller
# (and, being non-listening, simply yields "not superseded -- stay alive").
_PROBE_TIMEOUT_S = 0.25


def pid_alive(pid: int | None) -> bool:
    """Best-effort liveness check for a recorded pid.

    Conservative on the *unknown* axis: returns ``True`` when liveness cannot be
    determined (a permission error, or an unreadable platform), so callers that
    use this to *protect* a process never discard one they cannot prove dead.
    Reapers that must not act on ambiguity should pair this with a positive
    identity check, not rely on it alone.
    """
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def is_listening(host: str, port: int, *, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    """Return ``True`` iff something accepts a TCP connection at ``host:port``.

    Any socket error is treated as "not listening", which a supersession caller
    reads as "no confirmed live successor -- stay alive".
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _client_host(bind: str) -> str:
    """Map a wildcard bind address to a loopback the client can dial.

    Mirrors ``zdd.routing.Endpoint.client_host`` without importing zdd: a daemon
    bound on ``0.0.0.0``/``::`` is reachable locally at ``127.0.0.1``/``::1``.
    """
    if bind in ("0.0.0.0", ""):
        return "127.0.0.1"
    if bind == "::":
        return "::1"
    return bind


def is_superseded(
    table: dict | None,
    my_pid: int,
    my_generation: int,
    *,
    is_listening=is_listening,  # injectable TCP-probe collaborator
) -> bool:
    """Has a live, strictly-newer daemon generation superseded us?

    ``table`` is a routing-table ``dict`` in the ``zdd.routing`` shape::

        {"active": {"pid": int, "generation": int, "bind": str, "port": int}, ...}

    Returns ``True`` **only** when the ``active`` endpoint is a different pid than
    ``my_pid``, at a generation strictly greater than ``my_generation``, and is
    currently accepting connections. Any other state returns ``False`` -- the
    fail-safe default is to stay alive.

    ``is_listening`` is injectable for testing.
    """
    if not isinstance(table, dict):
        return False
    raw = table.get("active")
    if not isinstance(raw, dict):
        return False
    pid = raw.get("pid")
    # Our own pid still holding the active slot => we are NOT superseded.
    if not isinstance(pid, int) or pid == my_pid:
        return False
    try:
        generation = int(raw.get("generation", 0))
    except (TypeError, ValueError):
        return False
    # Only a strictly-newer generation counts as a successor (defeats a stale or
    # equal-generation entry, and any pid-reuse coincidence at our generation).
    if generation <= my_generation:
        return False
    try:
        port = int(raw.get("port", 0))
    except (TypeError, ValueError):
        return False
    if port <= 0:
        return False
    host = _client_host(str(raw.get("bind", "")))
    # Require a *live* successor: the newer generation must actually be serving.
    return bool(is_listening(host, port))
