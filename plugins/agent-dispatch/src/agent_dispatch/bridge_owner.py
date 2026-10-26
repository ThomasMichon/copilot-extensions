"""Passive exact-worktree reads through the existing local Bridge contract."""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from http.client import HTTPResponse
from pathlib import Path
from typing import Any

from .bridge_remote import (
    LocalBridgeRemoteClient,
    RemoteBridgeOperationError,
    RemoteBridgeUnavailable,
)


class _DeadlineReader(io.RawIOBase):
    def __init__(self, sock: Any, deadline: float) -> None:
        self._sock = sock
        self._raw = sock.makefile("rb", buffering=0)
        self._deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Local Bridge owner read deadline expired")
        self._sock.settimeout(remaining)
        return self._raw.readinto(buffer)

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


class _ResponseSocket:
    def __init__(self, sock: Any, deadline: float) -> None:
        self._sock = sock
        self._deadline = deadline

    def makefile(self, mode: str) -> Any:
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline))


def _bounded_open(request: Any, *, timeout: float, deadline: float) -> Any:
    # Recompute the remaining budget at every socket receive, including status
    # and header lines. A per-read socket timeout alone permits endless trickles.
    class DeadlineHandler:
        def do_open(self, connection: Any, request: Any, **kwargs: Any) -> Any:
            def connect(host: str, **options: Any) -> Any:
                result = connection(host, **options)
                result.response_class = lambda sock, *a, **k: HTTPResponse(
                    _ResponseSocket(sock, deadline), *a, **k,
                )
                return result
            return super().do_open(connect, request, **kwargs)

    class HTTPHandler(DeadlineHandler, urllib.request.HTTPHandler):
        pass

    class HTTPSHandler(DeadlineHandler, urllib.request.HTTPSHandler):
        pass

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Local Bridge owner read deadline expired")
    return urllib.request.build_opener(HTTPHandler(), HTTPSHandler()).open(
        request, timeout=min(timeout, remaining),
    )


def _owner_context(primitive: Any, peer_launch: Any, root: Path, raw: str) -> dict[str, Any]:
    # Use packaged code already imported by this process; authority is still
    # reread and validated for every call, never retained as a cached decision.
    if (
        not root.is_absolute() or root.name != "agent-dispatch"
        or root.parent.name != "plugins" or root.parent.parent.parent.name != "marketplaces"
    ):
        raise ValueError("Explicit context requires a canonical cell install root")
    cell = root.parent.parent
    durable = cell.parent.parent
    declared = json.loads(raw) if raw.lstrip().startswith("{") else None
    pointer = declared.get("installReceipt") if declared is not None else raw
    if not isinstance(pointer, str) or not pointer:
        raise ValueError("Explicit context must name an installation receipt")
    own = peer_launch._active_context(
        primitive, Path(pointer), durable, "agent-dispatch", cell, dict(os.environ),
    )
    if not primitive.paths_equal(own["pluginRoot"], root):
        raise ValueError("Explicit context does not belong to this owner installation")
    if declared is not None and any(
        value != own.get(key) for key, value in declared.items() if key != "action"
    ):
        raise ValueError("Explicit inline context conflicts with its canonical receipt")
    own["invocationGovernance"] = peer_launch._governance(
        primitive, own, durable, dict(os.environ),
    )
    return own


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

        own = _owner_context(primitive, peer_launch, root, raw)
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

    def _open(self, url: str, token: str, **kwargs: Any) -> Any:
        return super()._open(
            url, token, **kwargs, _opener=partial(_bounded_open, deadline=self._deadline),
        )

    def resolve(self, worktree: str, *, timeout: float) -> dict[str, Any]:
        self._deadline = time.monotonic() + timeout
        query = urllib.parse.urlencode({"handle": worktree})
        try:
            data = self._request(
                "GET", f"/api/v1/live-sessions/resolve?{query}",
                timeout=timeout, required_protocol=1,
            )
        except RemoteBridgeOperationError as exc:
            if time.monotonic() >= self._deadline:
                raise TimeoutError("Local Bridge owner read deadline expired") from exc
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
        if time.monotonic() >= self._deadline:
            raise TimeoutError("Local Bridge owner read deadline expired")
        if (
            not isinstance(data.get("session_id"), str)
            or not data["session_id"].strip()
            or data.get("worktree_id") != worktree
        ):
            raise RemoteBridgeOperationError("Agent Bridge returned invalid owner metadata")
        return data
