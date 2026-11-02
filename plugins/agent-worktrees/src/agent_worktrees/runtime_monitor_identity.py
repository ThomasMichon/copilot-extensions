"""Resolve the serving monitor's retained runtime without trusting a stale lock."""

from __future__ import annotations

import os
import time
import logging
from contextlib import nullcontext
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
        from .runtime_monitor_retirements import _exit_state, retirement_only

        if candidate.process_start_time is not None and _exit_state({
            "pid": candidate.pid, "start": candidate.process_start_time,
        }) is True:
            continue
        if diagnostics._pid_alive(candidate.pid):
            if key != "active":
                if retirement_only(directory.parent, candidate.to_dict()):
                    continue
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
    ) or (
        require_async_retirement and endpoint.process_start_time != before
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
        from .status_monitor_recovery_contract import CAPABILITY as RECOVERY_CAPABILITY, ReceiverIdentity

        capabilities = health.get("capabilities")
        if not isinstance(capabilities, list) or CAPABILITY not in capabilities:
            raise ValueError("Serving monitor does not support safe asynchronous retirement")
        if RECOVERY_CAPABILITY not in capabilities:
            raise ValueError("Serving monitor does not support guarded preparation recovery")
        ReceiverIdentity.parse({
            "pid": pid, "processStartTime": before, "runtimeGeneration": health.get("runtime_generation"),
        })
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


def async_predecessor(
    root: Path, predecessor: dict | None, *,
    observed: tuple[int, str, str | None] | None, incumbent_expected: bool,
) -> tuple[dict | None, dict]:
    """Bind admitted predecessor custody to the exact route used by publication."""
    if observed is None:
        if incumbent_expected:
            raise ValueError("Live unrouted monitor cannot safely retire asynchronously")
        return None, {}
    native = {"pid": observed[0], "start": observed[1], "slot": observed[2]}
    if predecessor is not None and any(predecessor.get(key) != value for key, value in native.items()):
        raise ValueError("Serving predecessor changed before retirement reservation")
    from . import status_monitor_recovery_contract as recovery

    expected = recovery.active_endpoint(root / "status-monitor-routing")
    if (expected.pid, expected.process_start_time) != observed[:2]:
        raise ValueError("Serving predecessor route identity changed")
    return {
        **native, "release_claim": (predecessor or {}).get("release_claim"),
    }, {
        "expected_old": expected,
        "refuse_old": lambda _active: (
            None if locks.process_start_time(expected.pid) == expected.process_start_time
            else "asynchronous predecessor identity changed"
        ),
    }


def publish_lock(path: Path, *, extra: dict, routing_held: bool = False) -> bool:
    """Renew discovery only while this exact native owner holds the route."""
    directory = path.parent / "status-monitor-routing"
    try:
        with nullcontext() if routing_held else routing.routing_lock(directory, deadline=time.monotonic() + 5.0):
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
