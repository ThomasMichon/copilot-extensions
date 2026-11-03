"""Bounded successor retirement, subordinate to existing monitor custody."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from single_instance_lease import AlreadyRunningError
from zdd import breadcrumb, diagnostics, routing

from . import locks, runtime_monitor_retirements as retirements

GRACE_SECONDS = 300.0
CAPABILITY = "status-monitor-retirement-grace-v1"


def _endpoint(raw) -> routing.Endpoint | None:
    return routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None


def _directory(root: Path) -> Path:
    directory = root / "status-monitor-retirement-grace"
    if directory.is_symlink():
        raise ValueError("Monitor retirement grace directory must not be linked")
    return directory


def _path(root: Path, predecessor: dict) -> Path:
    name = retirements._owner(predecessor["pid"], predecessor["start"]).rsplit(":", 1)[1]
    return _directory(root) / (name + ".json")


def _read(path: Path) -> dict | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or path.stat().st_size > 32768:
        raise ValueError("Monitor retirement grace is not a bounded ordinary file")
    record = json.loads(path.read_text(encoding="utf-8"))
    _validate(record, path)
    return record


def _validate(record: dict, path: Path) -> None:
    if (
        not isinstance(record, dict)
        or set(record) != {"version", "old", "successor", "ready_at"}
        or type(record["version"]) is not int or record["version"] != 1
        or type(record["ready_at"]) not in (int, float)
        or not math.isfinite(record["ready_at"]) or record["ready_at"] <= 0
    ):
        raise ValueError("Invalid monitor retirement grace record")
    routing._validate_endpoint_rows({"active": record["successor"], "previous": record["old"]})
    old = routing.Endpoint.from_dict(record["old"])
    successor = routing.Endpoint.from_dict(record["successor"])
    if (
        old is None or successor is None or old.pid == successor.pid
        or not old.process_start_time or not successor.process_start_time
        or path.stem != retirements._owner(old.pid, old.process_start_time).rsplit(":", 1)[1]
    ):
        raise ValueError("Monitor retirement grace native identity is unavailable")


def arm(root: Path, *, ready, clock=time.time) -> None:
    """The ready successor records one deadline without changing legacy grants."""
    if not ready():
        return
    route_root = root / "status-monitor-routing"
    table = routing.read_table(route_root, strict=True) or {}
    successor = _endpoint(table.get("active"))
    old = _endpoint(table.get("previous"))
    if (
        successor is None or old is None or successor.pid != os.getpid()
        or successor.pid == old.pid or not successor.process_start_time
        or locks.process_start_time(successor.pid) != successor.process_start_time
    ):
        return
    directory = _directory(root)
    directory.mkdir(exist_ok=True)
    inventory = root / "status-monitor-retirements"
    if not inventory.exists():
        return
    with retirements.tracking._RecordLock(inventory / "inventory", require_sidecar=True):
        for _record_path, predecessor in retirements._records(root):
            if (
                predecessor["state"] != "retiring"
                or (predecessor["pid"], predecessor["start"]) != (old.pid, old.process_start_time)
            ):
                continue
            path = _path(root, predecessor)
            existing = _read(path)
            if existing is not None:
                if existing["old"] != old.to_dict():
                    raise RuntimeError("Monitor retirement grace predecessor changed")
                return
            if not ready() or routing.read_table(route_root, strict=True) != table:
                return
            record = {
                "version": 1, "old": old.to_dict(),
                "successor": successor.to_dict(), "ready_at": clock(),
            }
            _validate(record, path)
            retirements._write_record(path, record)
            return


def _release_previous(root: Path, grace: dict) -> None:
    route_root = root / "status-monitor-routing"
    old = routing.Endpoint.from_dict(grace["old"])
    released = routing.release_previous_guarded(
        route_root, expected_active=routing.Endpoint.from_dict(grace["successor"]),
        expected_previous=old, lock_deadline=time.monotonic() + 5.0,
    )
    if not released and (routing.read_table(route_root, strict=True) or {}).get("previous") == old.to_dict():
        raise RuntimeError("Monitor retirement fallback release remains pending")


def settle_exited(root: Path, predecessor: dict) -> None:
    """Settle grace before custody; caller holds inventory and proved native exit."""
    path = _path(root, predecessor)
    grace = _read(path)
    if grace is None:
        return
    if (
        predecessor["state"] != "retiring"
        or (predecessor["pid"], predecessor["start"])
        != (grace["old"]["pid"], grace["old"]["process_start_time"])
    ):
        raise RuntimeError("Monitor retirement grace lacks its original committed custody")
    _release_previous(root, grace)
    path.unlink()


def retirement_callback(retire, supported_capabilities: set[str]):
    def cleanup(old):
        if CAPABILITY in supported_capabilities:
            return {"exit_confirmed": False, "reason": "successor-resident-grace-pending"}
        return retire(old)

    return cleanup


def _force(root: Path, path: Path, grace: dict, predecessor: dict) -> None:
    from . import status_monitor_cutover as cutover
    from . import status_monitor_recovery_contract as recovery

    route_root = root / "status-monitor-routing"
    try:
        guard = cutover._acquire_cutover_lock(route_root, timeout_s=0)
    except AlreadyRunningError:
        return
    try:
        if breadcrumb.breadcrumb_path(route_root).exists():
            return
        inventory = root / "status-monitor-retirements"
        with retirements.tracking._RecordLock(inventory / "inventory", require_sidecar=True):
            records = dict(retirements._records(root))
            expected_path = inventory / path.name
            if records.get(expected_path) != predecessor or _read(path) != grace:
                return
            with routing.routing_lock(route_root, deadline=time.monotonic() + 5.0):
                table = routing.read_table(route_root, strict=True) or {}
                successor = _endpoint(table.get("active"))
                if (
                    successor is None or successor.pid != os.getpid()
                    or successor.pid == predecessor["pid"] or not successor.process_start_time
                    or locks.process_start_time(successor.pid) != successor.process_start_time
                    or not recovery.successor_healthy(
                        successor.client_host, successor.port,
                        lambda url: cutover.ControlClient(
                            url, token=cutover.load_or_create_control_token(root),
                        ),
                        expected=successor,
                    )
                    or routing.read_table(route_root, strict=True) != table
                    or locks.process_start_time(predecessor["pid"]) != predecessor["start"]
                ):
                    return
                outcome = diagnostics.terminate_pid_if_identity(
                    predecessor["pid"], predecessor["start"], exit_timeout=5.0,
                )
            _release_previous(root, grace)
            if outcome.get("exit_confirmed") is not True:
                raise RuntimeError("Grace-expired monitor native retirement remains unconfirmed")
            path.unlink()
    finally:
        guard.release()


def reconcile(root: Path, *, clock=time.time) -> None:
    """The selected resident attempts expiry; original custody settles on exit."""
    records = retirements._records(root)
    for _record_path, predecessor in records:
        path = _path(root, predecessor)
        grace = _read(path)
        if grace is None:
            continue
        if (
            predecessor["state"] != "retiring"
            or (predecessor["pid"], predecessor["start"])
            != (grace["old"]["pid"], grace["old"]["process_start_time"])
        ):
            raise RuntimeError("Monitor retirement grace lacks its original committed custody")
        exited = retirements._exit_state(predecessor)
        if exited is None:
            raise RuntimeError("Grace-expired monitor native identity is unavailable")
        if exited is True:
            inventory = root / "status-monitor-retirements"
            with retirements.tracking._RecordLock(inventory / "inventory", require_sidecar=True):
                if dict(retirements._records(root)).get(_record_path) != predecessor or _read(path) != grace:
                    continue
                settle_exited(root, predecessor)
        elif clock() - grace["ready_at"] >= GRACE_SECONDS:
            _force(root, path, grace, predecessor)
