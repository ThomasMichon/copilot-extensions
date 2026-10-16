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


class InventoryClientMixin:
    """Machine inventory and version-gated worktree rendering facades."""

    def list_machines(self) -> list[dict[str, Any]]:
        """GET /api/v1/machines"""
        machines, _errors = self.list_machines_with_diagnostics()
        return machines

    def list_machines_with_diagnostics(
        self: InventoryClient,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """GET /api/v1/machines, including invalid-topology diagnostics."""
        resp = self._request("GET", "/api/v1/machines")
        if not resp:
            return [], []
        errors = [str(e) for e in resp.get("topology_errors", [])]
        return resp.get("machines", []), errors

    def list_worktrees_for_render(self: InventoryClient) -> dict[str, Any]:
        """Read the prompt snapshot; legacy daemons retain their blocking path."""
        return worktree_inventory(self, refresh=False)

    def refresh_worktrees(self: InventoryClient) -> dict[str, Any]:
        """Join the owned live refresh with a bounded daemon-side wall wait."""
        return worktree_inventory(self, refresh=True)


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
