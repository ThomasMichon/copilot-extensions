"""Exact persisted managed IDs are independent of default listing visibility."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from .bridge_inventory import project_bridges, select_bridge


def resolve(
    client: Any, handle: str, report: dict[str, Any], *,
    project: str | None, machine: str | None, namespace: str | None,
) -> dict[str, Any] | None:
    from .client import BridgeClientError

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}", handle):
        return None
    try:
        info = client._request("GET", f"/api/v1/sessions/{quote(handle, safe='')}")
    except BridgeClientError as exc:
        if exc.status == 404:
            return None
        raise
    if not (
        isinstance(info, dict) and isinstance(info.get("session_id"), str)
        and isinstance(info.get("acp_session_id"), str) and info["acp_session_id"]
        and handle in (info["session_id"], info["acp_session_id"])
        and info.get("at_rest") is False and info.get("read_only") is False
        and info.get("status") != "ended"
    ):
        return None
    owner = info.get("machine")
    if info.get("target_type") == "local":
        owner = owner or report.get("host")
    observed = project_bridges([{
        "kind": "managed", "handle": info["session_id"], "sources": ["managed"],
        "session": {**info, "machine": owner},
    }], {"managed_exact": {"availability": "available"}},
        project=project, machine=machine, namespace=namespace, include_retained_managed=True,
    )
    if not observed["bridges"]:
        return None
    selected = select_bridge(observed, handle)
    selected["sources"] = {**report["sources"], **observed["sources"]}
    selected["complete"] = report["complete"]
    return selected
