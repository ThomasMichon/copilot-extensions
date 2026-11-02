"""Finish a committed publication in the existing selected resident."""

from __future__ import annotations

import logging
import os

from single_instance_lease import AlreadyRunningError
from zdd import breadcrumb, diagnostics, routing
from zdd.cutover_lock import CutoverLock, CutoverLockedError

from . import locks, status_monitor_cutover as cutover
from . import runtime_monitor_retirements as retirements


def finish(root, record, endpoint, *, promote, ready) -> bool:
    """Finish this exact asynchronous publication under the caller's cutover custody."""
    directory = root / "status-monitor-routing"
    if (
        record.get("self_retirement") is not True
        or breadcrumb.read_breadcrumb(directory) != record
        or (record.get("new_pid"), record.get("new_process_start_time"), record.get("new_port"))
        != (endpoint.pid, endpoint.process_start_time, endpoint.port)
        or locks.process_start_time(endpoint.pid) != endpoint.process_start_time
    ):
        raise ValueError("Asynchronous publication witness identity changed")
    old = record.get("old")
    if old is not None and (
        not isinstance(old, dict) or type(old.get("pid")) is not int
        or not isinstance(old.get("process_start_time"), str)
        or not old["process_start_time"]
    ):
        raise ValueError("Asynchronous predecessor witness identity is unavailable")
    if old is not None:
        routing._validate_endpoint_rows({"active": old})
    table = routing.read_table(directory, strict=True)
    raw = table.get("active") if isinstance(table, dict) else None
    if not isinstance(raw, dict) or routing.Endpoint.from_dict(raw) != endpoint:
        raise ValueError("Asynchronous selected owner changed before retirement grant")
    if record["state"] != "committed":
        routing.confirm_active(directory, endpoint)
        breadcrumb.write_breadcrumb(
            directory, state="committed", old=old,
            new_port=endpoint.port, new_pid=endpoint.pid,
            new_process_start_time=endpoint.process_start_time,
            publication_witness=record.get("publication_witness"),
            started_at=record.get("started_at"), self_retirement=True,
        )
    for _path, predecessor in retirements._records(root):
        if (
            isinstance(old, dict)
            and predecessor["pid"] == old.get("pid")
            and predecessor["start"] == old.get("process_start_time")
            and predecessor["state"] == "prepared"
        ):
            retirements.commit(
                root, {key: predecessor[key] for key in ("pid", "start", "slot", "release_claim")},
            )
    if not ready() and promote().get("adopted") is not True:
        raise ValueError("Asynchronous publication promotion remains pending")
    if not ready():
        return False
    table = routing.read_table(directory, strict=True)
    raw = table.get("active") if isinstance(table, dict) else None
    if not isinstance(raw, dict) or routing.Endpoint.from_dict(raw) != endpoint:
        raise ValueError("Asynchronous serving owner changed before witness cleanup")
    if not breadcrumb.clear_breadcrumb(directory):
        raise OSError("Asynchronous publication witness cleanup remains pending")
    return True


def recover(root, *, promote, ready) -> None:
    directory = root / "status-monitor-routing"
    record = breadcrumb.read_breadcrumb(directory)
    if not isinstance(record, dict) or record.get("self_retirement") is not True or record.get("state") not in (
        "started", "flipped", "committed", "publication-uncertain",
    ):
        return
    pid, start = os.getpid(), locks.process_start_time(os.getpid())
    if record.get("new_pid") != pid or start is None or record.get("new_process_start_time") != start:
        if type(record.get("pid")) is not int or diagnostics._pid_alive(record["pid"]):
            return
    guard = None
    sdk_guard = CutoverLock(directory)
    try:
        guard = cutover._acquire_cutover_lock(directory, timeout_s=0)
        sdk_guard.acquire()
        if breadcrumb.read_breadcrumb(directory) != record:
            return
        table = routing.read_table(directory, strict=True)
        raw = table.get("active") if isinstance(table, dict) else None
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if endpoint is None or (
            (endpoint.pid, endpoint.process_start_time) != (pid, start)
            or endpoint.port != record.get("new_port")
        ):
            from .runtime_monitor_abort import recover_abandoned

            recover_abandoned(root)
            return
        finish(root, record, endpoint, promote=promote, ready=ready)
    except (AlreadyRunningError, CutoverLockedError):
        return
    except (OSError, ValueError, RuntimeError) as error:
        logging.getLogger(__name__).error("Committed monitor completion remains pending: %s", error)
    finally:
        sdk_guard.release()
        if guard is not None:
            guard.release()
