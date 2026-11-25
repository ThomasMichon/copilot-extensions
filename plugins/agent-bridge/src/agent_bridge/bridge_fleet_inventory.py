"""Bounded fleet reads through the existing authenticated remote Bridge carrier."""

from __future__ import annotations

import asyncio
from typing import Any

from .bridge_inventory import _scope_path, inventory
from .bridge_merge import merge_rows
from .remote_errors import RemoteBridgeError

MAX_HOSTS = 16
HOST_TIMEOUT = 15.0


def validate_report(value: Any) -> dict[str, Any]:
    if not (
        isinstance(value, dict)
        and type(value.get("version")) is int
        and value["version"] == 1
        and isinstance(value.get("bridges"), list)
        and isinstance(value.get("sources"), dict)
        and type(value.get("complete")) is bool
        and all(
            isinstance(row, dict)
            and isinstance(row.get("handle"), str)
            and row["handle"]
            and isinstance(row.get("status"), str)
            and isinstance(row.get("control"), str)
            and (row.get("route") is None or isinstance(row["route"], dict))
            and (row.get("live") is None or type(row["live"]) is bool)
            and all(
                row.get(key) is None or isinstance(row[key], str)
                for key in (
                    "machine",
                    "project",
                    "worktree_id",
                    "session_id",
                    "directory",
                    "namespace",
                )
            )
            for row in value["bridges"]
        )
    ):
        raise ValueError("unconfirmed_bridge_inventory")
    return value


async def fleet_inventory(
    request: Any,
    hosts: list[str],
    **scope: Any,
) -> dict[str, Any]:
    from .remote_operations import RemoteOperationService, validate_host

    if not hosts or len(hosts) > MAX_HOSTS:
        raise ValueError(f"select between 1 and {MAX_HOSTS} Bridge hosts")
    selected = list(dict.fromkeys(validate_host(host) for host in hosts))
    resolver = request.app.state.resolver
    local = getattr(getattr(resolver, "_local_machine", None), "key", None)
    service = getattr(request.app.state, "remote_operations", None)
    service = service or RemoteOperationService(resolver)
    limit = asyncio.Semaphore(4)

    async def read(host: str) -> tuple[str, dict[str, Any]]:
        async with limit:
            try:
                if host == local:
                    result = await asyncio.wait_for(
                        inventory(request, local_only=True), HOST_TIMEOUT
                    )
                else:
                    result = await asyncio.wait_for(service.bridge_inventory(host), HOST_TIMEOUT)
                return host, {
                    "availability": "available",
                    **validate_report(result),
                    "identity_complete": type(result.get("identity_version")) is int
                    and result["identity_version"] >= 2,
                }
            except (
                RemoteBridgeError,
                OSError,
                ValueError,
                TimeoutError,
                asyncio.TimeoutError,
            ) as error:
                return host, {
                    "availability": "unavailable",
                    "reason": error.code
                    if isinstance(error, RemoteBridgeError)
                    else type(error).__name__,
                    "complete": False,
                }

    observations = dict(await asyncio.gather(*(read(host) for host in selected)))
    seen: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for host, result in observations.items():
        for original in result.get("bridges", []):
            row = {**original, "route": {**(original.get("route") or {}), "host": host}}
            row["route"].setdefault("handle", row["handle"])
            key: tuple[Any, ...] = (
                row.get("machine"),
                row.get("project"),
                row.get("namespace"),
                _scope_path(row.get("directory")),
                row.get("worktree_id") or row.get("session_id") or row["handle"],
            )
            # Missing execution identity cannot establish equivalence across hosts.
            if not key[0]:
                key = (host, *key)
            seen.setdefault(key, []).append(row)
    reconciled = [merge_rows(rows) for rows in seen.values()]
    bridges = [
        row
        for row, _conflict in reconciled
        if not any(
            value is not None and row.get(name) != value
            for name, value in (
                ("project", scope.get("project")),
                ("machine", scope.get("machine")),
                ("namespace", scope.get("namespace")),
                ("status", scope.get("status")),
            )
        )
        and (not scope.get("live") or row.get("live") is True)
    ]
    return {
        "version": 1,
        "identity_version": 2,
        "bridges": bridges,
        "complete": not any(conflict for _row, conflict in reconciled)
        and all(
            result["complete"] is True and result.get("identity_complete") is True
            for result in observations.values()
        ),
        "sources": {
            "hosts": {
                host: {key: value for key, value in result.items() if key != "bridges"}
                for host, result in observations.items()
            }
        },
    }
