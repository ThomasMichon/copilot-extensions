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
    out: dict[str, int] = {}
    if isinstance(value, dict):
        for venue, host in value.items():
            v, h = sanitize_port(venue), sanitize_port(host)
            if v is not None and h is not None:
                out[str(v)] = h
    return out
