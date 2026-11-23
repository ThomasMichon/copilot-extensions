"""Version-gated cached inventory requests for Bridge rendering readers."""

from __future__ import annotations

from typing import Any, Protocol

from .protocol import WORKTREE_RENDER_CACHE_PROTOCOL_VERSION


class InventoryClient(Protocol):
    def daemon_supports(self, min_version: int) -> bool: ...

    def list_machines_with_diagnostics(self) -> tuple[list[dict[str, Any]], list[str]]: ...

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None,
        *, params: dict[str, str] | None = None, request_timeout: float | None = None,
    ) -> Any: ...


class InventoryClientMixin:
    """Machine inventory and version-gated worktree rendering facades."""

    def resolve_bridge(
        self: InventoryClient, handle: str, *, project: str | None = None, machine: str | None = None,
        namespace: str | None = None, all_projects: bool = False,
        hosts: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve exact aliases through the negotiated inventory without execution."""
        from .bridge_inventory import BridgeSelectionError, select_bridge
        from .client import BridgeClientError

        report = InventoryClientMixin._list_bridges(
            self,
            project=project, machine=machine, namespace=namespace,
            all_projects=all_projects, hosts=hosts, identity_version=2,
        )
        try:
            return select_bridge(report, handle)
        except BridgeSelectionError as error:
            raise BridgeClientError(
                error.status, {"reason": error.reason, "selector": handle},
            ) from error

    def list_bridges(
        self: InventoryClient, *, project: str | None = None, machine: str | None = None,
        namespace: str | None = None, status: str | None = None, live: bool = False,
        all_projects: bool = False, hosts: list[str] | None = None, local_only: bool = False,
    ) -> dict[str, Any]:
        return InventoryClientMixin._list_bridges(
            self, project=project, machine=machine, namespace=namespace, status=status,
            live=live, all_projects=all_projects, hosts=hosts, local_only=local_only,
        )

    def _list_bridges(
        self: InventoryClient, *, project: str | None = None, machine: str | None = None,
        namespace: str | None = None, status: str | None = None, live: bool = False,
        all_projects: bool = False, hosts: list[str] | None = None, local_only: bool = False,
        identity_version: int = 1,
    ) -> dict[str, Any]:
        from .bridge_fleet_inventory import validate_report
        from .client import BridgeClientError
        from .client_protocol_floor import protocol_floored_request
        from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

        if not project and not all_projects:
            raise ValueError("select a project or explicitly select all projects")
        params = {
            key: value for key, value in {
                "project": project, "machine": machine, "namespace": namespace, "status": status,
                "live": "true" if live else None,
                "hosts": ",".join(hosts) if hosts else None,
                "local_only": "true" if local_only else None,
            }.items() if value is not None
        }
        send = protocol_floored_request(
            self, PLUGIN_READINESS_PROTOCOL_VERSION,
            capabilities={"bridge_inventory": identity_version},
        )
        try:
            return validate_report(send(
                "GET", "/api/v1/bridges", params=params or None,
                request_timeout=(15.0 * ((len(hosts) + 3) // 4) + 10.0) if hosts else None,
            ))
        except ValueError as error:
            raise BridgeClientError(502, "Bridge conversation inventory is unconfirmed") from error

    def list_machines(self: InventoryClient) -> list[dict[str, Any]]:
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

    def list_targets(self: InventoryClient, *, refresh: bool = False) -> dict[str, Any]:
        """List the negotiated union without treating missing metadata as absence."""
        from .client import BridgeClientError
        from .client_protocol_floor import protocol_floored_request
        from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

        send = protocol_floored_request(
            self, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities={"target_inventory": 1},
        )
        result = send("GET", "/api/v1/targets", params={"refresh": "true"} if refresh else None)
        if not (
            isinstance(result, dict) and type(result.get("version")) is int
            and result["version"] == 1 and isinstance(result.get("targets"), list)
            and isinstance(result.get("sources"), dict)
            and all(
                isinstance(item, dict) and item.get("kind") in {"managed", "session_host", "live", "worktree"}
                and isinstance(item.get("handle"), str) and bool(item["handle"])
                for item in result["targets"]
            )
        ):
            raise BridgeClientError(502, "Bridge target inventory is unconfirmed")
        return result

    def hydrate_worktree(
        self: InventoryClient, worktree_id: str, *, request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """Hydrate its current native head without a load, prompt or retry."""
        from urllib.parse import quote
        from .client import BridgeClientError
        from .client_protocol_floor import protocol_floored_request
        from .models import SessionInfo
        from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

        send = protocol_floored_request(
            self, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities={"worktree_metadata_hydration": 1},
        )
        result = send(
            "POST", f"/api/v1/worktrees/{quote(worktree_id, safe='')}/metadata",
            request_timeout=request_timeout, allow_retry=False,
        )
        try:
            if not isinstance(result, dict):
                raise ValueError("missing workspace hydration response")
            info = SessionInfo.model_validate(result)
            marker = result.get("metadata_hydration")
            if not (
                info.worktree_id == worktree_id and info.acp_session_id
                and info.durable_session_id == info.acp_session_id
                and isinstance(marker, dict) and type(marker.get("version")) is int
                and marker["version"] == 1 and marker.get("native_execution") is False
            ):
                raise ValueError("unconfirmed hydrated workspace identity")
        except (ValueError, TypeError, AttributeError) as exc:
            raise BridgeClientError(502, "Workspace metadata hydration is unconfirmed; inspect, do not replay") from exc
        return result


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
