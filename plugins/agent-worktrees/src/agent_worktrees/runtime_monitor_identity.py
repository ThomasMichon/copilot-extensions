"""Resolve the serving monitor's retained runtime without trusting a stale lock."""

from __future__ import annotations

import os
import time
import logging
from pathlib import Path

from zdd import diagnostics, routing

from . import locks, status_monitor_cutover as cutover


def routed_identity(
    runtime_home: Path | None = None, *, require_async_retirement: bool = False,
) -> tuple[int, str, str | None] | None:
    directory = cutover.routing_dir() if runtime_home is None else cutover.routing_dir(runtime_home)
    snapshot = routing.read_table(directory, strict=True)
    endpoints = []
    for key in ("active", "previous"):
        raw = (snapshot or {}).get(key)
        if raw is None:
            continue
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if endpoint is None or type(endpoint.pid) is not int or endpoint.pid <= 0:
            raise ValueError(f"Routed monitor {key} identity is malformed")
        endpoints.append((key, endpoint))
    endpoint = None
    for key, candidate in endpoints:
        if diagnostics._pid_alive(candidate.pid):
            if key != "active":
                raise ValueError("Live fallback monitor requires owner-led route recovery")
            endpoint = candidate
            break
    if endpoint is None:
        return None
    pid = endpoint.pid
    if type(pid) is not int or pid <= 0:
        raise ValueError("Routed monitor has no attributable PID")
    if not diagnostics._pid_alive(pid):
        return None
    before = locks.process_start_time(pid)
    if before is None or (
        endpoint.process_start_time is not None and endpoint.process_start_time != before
    ):
        raise ValueError("Routed monitor process identity is unconfirmed")
    client_options = {} if runtime_home is None else {"token": cutover.load_or_create_control_token(runtime_home)}
    health = cutover.ControlClient(endpoint.base_url, **client_options).health()
    if (
        health.get("pid") != pid
        or health.get("status") not in {"ready", "draining", "unavailable"}
        or locks.process_start_time(pid) != before
        or routing.read_table(directory, strict=True) != snapshot
    ):
        raise ValueError("Routed monitor identity changed during admission")
    slot = health.get("runtime_slot")
    prefix = health.get("runtime_prefix")
    if require_async_retirement:
        from .runtime_monitor_retirements import CAPABILITY

        capabilities = health.get("capabilities")
        if not isinstance(capabilities, list) or CAPABILITY not in capabilities:
            raise ValueError("Serving monitor does not support safe asynchronous retirement")
        if not isinstance(prefix, str) or not Path(prefix).is_absolute():
            raise ValueError("Serving monitor runtime prefix is unavailable")
    if isinstance(prefix, str) and Path(prefix).is_absolute():
        path = Path(prefix).resolve(strict=True)
        root = directory.parent if runtime_home is None else runtime_home
        slot = path.name if path.parent == (root / "versions").resolve() else None
    return pid, before, slot if isinstance(slot, str) else None


def monitor_slot(root: Path, identity: tuple[int, str]) -> str | None:
    record = locks.read_lock(root / "status-monitor.lock")
    if locks.lock_is_live(record) and record.get("pid") == identity[0]:
        if (
            locks.process_start_time(identity[0]) != identity[1]
            or not isinstance(record.get("prefix"), str)
            or not record["prefix"]
        ):
            raise ValueError("Serving monitor runtime identity changed before retention")
        prefix = Path(record["prefix"]).resolve(strict=True)
        return prefix.name if prefix.parent == (root / "versions").resolve() else None
    else:
        routed = cutover.routed_monitor_identity(root)
        if (
            routed is None or routed[:2] != identity or not routed[2]
            or Path(routed[2]).name != routed[2]
        ):
            raise ValueError("Routed monitor runtime identity cannot be retained")
        prefix = (root / "versions" / routed[2]).resolve(strict=True)
    if prefix.parent == (root / "versions").resolve():
        return prefix.name
    raise ValueError("Serving monitor runtime is outside the retained runtime root")


def publish_lock(path: Path, *, extra: dict) -> bool:
    """Renew discovery only while this exact native owner holds the route."""
    directory = path.parent / "status-monitor-routing"
    try:
        with routing.routing_lock(directory, deadline=time.monotonic() + 5.0):
            table = routing.read_table(directory, strict=True)
            raw = table.get("active") if isinstance(table, dict) else None
            endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
            if (
                endpoint is None or endpoint.pid != os.getpid()
                or endpoint.process_start_time != locks.process_start_time(os.getpid())
            ):
                return False
            written = locks.write_lock(path, extra=extra)
            if not written:
                logging.getLogger(__name__).error("Monitor discovery publication failed")
            return written
    except (OSError, ValueError, routing.RoutingLockTimeout, routing.ActivePublicationRefused) as error:
        logging.getLogger(__name__).error("Monitor discovery publication deferred: %s", error)
        return False
