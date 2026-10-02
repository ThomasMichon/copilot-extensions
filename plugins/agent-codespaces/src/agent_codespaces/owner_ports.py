"""Port sanitizers for the Connection Owner's registry (``connection-owner.json``)."""

from __future__ import annotations

from typing import Any


def sanitize_port(value: Any) -> int | None:
    """A valid TCP port, or ``None``."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def sanitize_reverse_forwards(value: Any) -> dict[str, int]:
    """``{venue_port: host_port}`` with both valid ports; anything else dropped."""
    return _port_map(value)


def sanitize_local_forwards(value: Any) -> dict[str, int]:
    """``{host_port: venue_port}`` with both valid ports; anything else dropped."""
    return _port_map(value, allow_zero_key=True)


def sanitize_hold_forwards(hold: Any) -> None:
    """Sanitize a loaded hold's extra forward maps in place."""
    hold.reverse_forwards = sanitize_reverse_forwards(hold.reverse_forwards)
    hold.local_forwards = sanitize_local_forwards(hold.local_forwards)
    if hasattr(hold, "assigned_local_forwards"):
        hold.assigned_local_forwards = _matching_assigned(hold)


def set_hold_forwards(
    hold: Any, reverse: Any = None, local: Any = None, assigned: Any = None,
) -> None:
    """Replace each extra forward map that was given (``None`` keeps it).

    ``assigned`` (with ``local``) restores a snapshot of the dynamic-port
    provenance, e.g. when a failed rejoin puts the prior forwards back.
    """
    if reverse is not None:
        hold.reverse_forwards = sanitize_reverse_forwards(reverse)
    if local is not None:
        hold.local_forwards = sanitize_local_forwards(local)
        if hasattr(hold, "assigned_local_forwards"):
            if assigned is not None:
                hold.assigned_local_forwards = sanitize_local_forwards(assigned)
            hold.assigned_local_forwards = _matching_assigned(hold)


def clear_session_forwards(hold: Any) -> None:
    """Drop the forwards that exist only for session tenants."""
    hold.daemon_port = None
    hold.reverse_forwards = {}
    hold.local_forwards = {}
    if hasattr(hold, "assigned_local_forwards"):
        hold.assigned_local_forwards = {}


def _port_map(value: Any, *, allow_zero_key: bool = False) -> dict[str, int]:
    out: dict[str, int] = {}
    if isinstance(value, dict):
        for key, port in value.items():
            k, p = sanitize_port(key), sanitize_port(port)
            if k is None and allow_zero_key:
                try:
                    k = 0 if int(key) == 0 else None
                except (TypeError, ValueError):
                    k = None
            if k is not None and p is not None:
                out[str(k)] = p
    return out


def _matching_assigned(hold: Any) -> dict[str, int]:
    assigned = sanitize_local_forwards(getattr(hold, "assigned_local_forwards", {}))
    local = sanitize_local_forwards(getattr(hold, "local_forwards", {}))
    return {host: venue for host, venue in assigned.items() if local.get(host) == venue}
