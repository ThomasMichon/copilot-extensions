"""Agent-bridge routing-table helpers that preserve forwarded venue routes."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any


def active_route(config_dir: str | Path) -> dict[str, Any] | None:
    """The raw ``active`` entry of ``active.json``, or ``None``."""
    try:
        from zdd.routing import read_table

        table = read_table(config_dir)
    except Exception:
        return None
    active = table.get("active") if isinstance(table, dict) else None
    return active if isinstance(active, dict) else None


def active_route_is_forward(active: dict[str, Any] | None) -> bool:
    """Whether an ``active.json`` entry is a venue-forwarded host bridge."""
    if active is None:
        return False
    try:
        if int(active.get("port") or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if active.get("forwarded") is True:
        return True
    return active.get("pid") is None and "generation" not in active


def _client_host(bind: Any) -> str:
    if bind in ("0.0.0.0", "", None):
        return "127.0.0.1"
    if bind == "::":
        return "::1"
    return str(bind)


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def forwarded_route_base_url(
    config_dir: str | Path,
    *,
    verify_listener: bool = False,
) -> str | None:
    """Return the base URL for a forwarded active route, including legacy rows.

    Older venue launchers wrote ``{"port": N}`` without the ``bind`` field that
    ``zdd.routing.Endpoint`` now requires. Treat those as loopback forwards so
    clients never fall back to the configured/default local daemon port.
    """
    active = active_route(config_dir)
    if not active_route_is_forward(active):
        return None
    try:
        port = int(active["port"])
    except (KeyError, TypeError, ValueError):
        return None
    host = _client_host(active.get("bind"))
    if verify_listener and not _listening(host, port):
        return None
    from zdd.routing import format_authority

    return f"http://{format_authority(host, port)}"
