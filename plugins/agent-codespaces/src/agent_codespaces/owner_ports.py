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
    return _port_map(value)


def _port_map(value: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if isinstance(value, dict):
        for key, port in value.items():
            k, p = sanitize_port(key), sanitize_port(port)
            if k is not None and p is not None:
                out[str(k)] = p
    return out
