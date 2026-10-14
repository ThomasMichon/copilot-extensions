"""Version-gated cached inventory requests for Bridge rendering readers."""

from __future__ import annotations

from typing import Any, Protocol

from .protocol import WORKTREE_RENDER_CACHE_PROTOCOL_VERSION


class InventoryClient(Protocol):
    def daemon_supports(self, min_version: int) -> bool: ...

    def _request(
        self, method: str, path: str, body: dict | None = None,
        *, params: dict[str, str] | None = None, request_timeout: float | None = None,
    ) -> Any: ...


def worktree_inventory(client: InventoryClient, *, refresh: bool) -> dict[str, Any]:
    supported = client.daemon_supports(WORKTREE_RENDER_CACHE_PROTOCOL_VERSION)
    params = {"refresh" if refresh else "render": "true"} if supported else None
    response = client._request("GET", "/api/v1/worktrees", params=params) or {}
    metadata = response.get("discovery")
    if (
        not supported or not isinstance(metadata, dict)
        or type(metadata.get("version")) is not int or metadata["version"] != 1
    ):
        response["discovery"] = {
            "version": 0,
            "freshness": "unknown",
            "completeness": "unknown",
            "render_latency_contract": False,
        }
    if refresh and not supported:
        response["refresh"] = {"outcome": "unsupported", "pending": None}
    return response
