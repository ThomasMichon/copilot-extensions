"""Passive exact-worktree reads through the existing local Bridge contract."""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

from .bridge_remote import (
    LocalBridgeRemoteClient,
    RemoteBridgeOperationError,
    RemoteBridgeUnavailable,
)


def _config_dir() -> Path | None:
    """Retain native same-cell admission without launching a peer interpreter."""
    from . import peer_launch
    from .install_paths import install_dir

    raw = os.environ.get(peer_launch.CONTEXT_ENV, "")
    root = install_dir()
    if not raw:
        if root.parent.name == "plugins" and root.parent.parent.parent.name == "marketplaces":
            raise RemoteBridgeUnavailable("Local owner read requires installation context")
        return None
    try:
        from . import _installation_context as primitive

        own = peer_launch.validate_owner("agent-dispatch", root, raw)
        cell = Path(own["cellRoot"])
        durable = cell.parent.parent
        receipt = cell / "plugins" / "agent-bridge" / "install.json"
        peer = peer_launch._active_context(
            primitive, receipt, durable, "agent-bridge", cell, {},
        )
        environment = peer_launch.peer_environment(peer, dict(os.environ))
        peer_launch._governance(primitive, peer, durable, environment)
        return Path(peer["pluginRoot"])
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        raise RemoteBridgeUnavailable("Local owner read installation context refused") from exc


class LocalOwnerSessionClient(LocalBridgeRemoteClient):
    """Read metadata only: no ensure, providers, or session/transcript enumeration.

    Reuse Dispatch's local HTTP discovery, auth construction and protocol checks.
    The legacy resolve operation is in HTTP generation 1; remote carrier support
    is not a prerequisite. A successful empty/invalid response is not absence.
    """

    def __init__(self) -> None:
        super().__init__(config_dir=_config_dir())

    def resolve(self, worktree: str, *, timeout: float) -> dict[str, Any]:
        query = urllib.parse.urlencode({"handle": worktree})
        try:
            data = self._request(
                "GET", f"/api/v1/live-sessions/resolve?{query}",
                timeout=timeout, required_protocol=1,
            )
        except RemoteBridgeOperationError as exc:
            # Only this endpoint's documented miss is authoritative. A generic
            # 404 (including health/unsupported routes) is still an unreadable peer.
            cause = exc.__cause__
            if (
                exc.status == 404
                and str(exc) == "no live session for handle"
                and isinstance(cause, urllib.error.HTTPError)
                and urllib.parse.urlsplit(cause.url).path == "/api/v1/live-sessions/resolve"
            ):
                return {}
            raise
        if (
            not isinstance(data.get("session_id"), str)
            or not data["session_id"].strip()
            or data.get("worktree_id") != worktree
        ):
            raise RemoteBridgeOperationError("Agent Bridge returned invalid owner metadata")
        return data
