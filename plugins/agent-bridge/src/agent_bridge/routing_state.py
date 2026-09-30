"""Agent-bridge routing-table helpers that preserve forwarded venue routes."""

from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any


class ForwardedRouteRefused(RuntimeError):
    """Raised when local daemon publication would overwrite a venue forward."""


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


def publish_daemon_route_unless_forwarded(
    config_dir: str | Path,
    *,
    bind: str,
    port: int,
    pid: int,
    version: str | None,
):
    """Publish a local daemon route unless active.json is a venue forward.

    The forwarded-route refusal runs under zdd's routing lock immediately before
    publication, closing the race between a direct service start's early
    snapshot guard and its actual startup publication.
    """
    from zdd import routing

    def refuse_forward(active: dict | None) -> str | None:
        if active_route_is_forward(active):
            return "active route is a venue-forwarded host bridge"
        return None

    try:
        return routing.publish_active_with_previous_guarded(
            config_dir,
            bind=bind,
            port=port,
            pid=pid,
            version=version,
            demote_existing=True,
            expected_active=None,
            refuse_current=refuse_forward,
            require_expected_active=False,
        )
    except routing.ActivePublicationRefused as exc:
        raise ForwardedRouteRefused(str(exc)) from exc


def skip_forwarded_daemon_start(exc: Exception) -> None:
    """Log and exit cleanly when startup races a venue-forwarded route."""
    message = (
        "agent-bridge startup skipped: active.json points at a "
        f"venue-forwarded host bridge ({exc})"
    )
    logging.getLogger("agent-bridge").info(message)
    print(f"[agent-bridge] SKIP: {message}")
    raise SystemExit(0) from exc
