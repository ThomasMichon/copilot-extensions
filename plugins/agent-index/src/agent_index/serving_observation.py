"""Read-only, project-independent observation of the local Index service."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from zdd import breadcrumb, diagnostics, process_exit, routing

from .__main__ import _owned_service_status
from .config import routing_dir

SCHEMA = "agent-index.local-serving-observation"
VERSION = 1
_SLOT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,255}\Z")


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "version": VERSION, "state": "unknown",
        "generation": None, "reason": reason,
    }


def _local_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ready_health(value: Any, endpoint: routing.Endpoint, token: str | None = None) -> bool:
    return (
        isinstance(value, dict)
        and value.get("plugin") == "agent-index"
        and value.get("status") == "ok"
        and value.get("promoted") is True
        and type(value.get("pid")) is int
        and value["pid"] == endpoint.pid
        and value.get("version") == endpoint.version
        and isinstance(value.get("instanceToken"), str)
        and 0 < len(value["instanceToken"]) <= 256
        and (token is None or value["instanceToken"] == token)
    )


def observe_local_serving(root: Path | None = None) -> dict[str, Any]:
    """Attest a routed local owner; absence or ambiguity is never inactivity."""
    root = Path(root) if root is not None else routing_dir()
    if not root.is_absolute():
        return _unknown("routing-root-unavailable")
    try:
        if os.path.lexists(breadcrumb.breadcrumb_path(root)):
            return _unknown("cutover-pending")
        table = routing.read_table(root, strict=True)
        if table is None:
            return _unknown("route-unavailable")
        routing._validate_endpoint_rows(table)
        raw = table.get("active")
        if not isinstance(raw, dict):
            return _unknown("route-unavailable")
        endpoint = routing.Endpoint.from_dict(raw)
        if (
            endpoint is None
            or type(raw.get("pid")) is not int or raw["pid"] <= 0
            or type(raw.get("port")) is not int or not 0 < raw["port"] < 65536
            or type(raw.get("generation")) is not int or raw["generation"] <= 0
            or not isinstance(endpoint.version, str) or _SLOT.fullmatch(endpoint.version) is None
            or endpoint.version.endswith(".") or not endpoint.process_start_time
            or not _local_host(endpoint.client_host)
        ):
            return _unknown("route-unverified")
        if diagnostics.process_start_time(endpoint.pid) != endpoint.process_start_time:
            return _unknown("process-unverified")
        with process_exit.open_process(endpoint.pid, endpoint.process_start_time) as process:
            if process.wait(0) is not False:
                return _unknown("process-unverified")
            health = _owned_service_status(
                endpoint.base_url, expected_version=endpoint.version,
                expected_pid=endpoint.pid, timeout=2.0,
            )
            if not _ready_health(health, endpoint):
                return _unknown("health-unverified")
            token = health["instanceToken"]
            confirmed = _owned_service_status(
                endpoint.base_url, expected_version=endpoint.version,
                expected_pid=endpoint.pid, expected_instance_token=token, timeout=2.0,
            )
            if (
                not _ready_health(confirmed, endpoint, token)
                or process.wait(0) is not False
                or diagnostics.process_start_time(endpoint.pid) != endpoint.process_start_time
                or routing.read_table(root, strict=True) != table
            ):
                return _unknown("ownership-changed")
            if os.path.lexists(breadcrumb.breadcrumb_path(root)):
                return _unknown("cutover-pending")
        return {
            "schema": SCHEMA, "version": VERSION, "state": "ready",
            "generation": endpoint.version, "pid": endpoint.pid,
        }
    except Exception:
        return _unknown("observation-unavailable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = observe_local_serving()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"agent-index local service: {result['state']}")
    return 0 if result["state"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
