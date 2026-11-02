"""Finish a committed publication in the existing selected resident."""

from __future__ import annotations

import logging
import os

from single_instance_lease import AlreadyRunningError
from zdd import breadcrumb, routing

from . import locks, status_monitor_cutover as cutover
from . import runtime_monitor_retirements as retirements


def recover(root, *, promote, ready) -> None:
    directory = root / "status-monitor-routing"
    record = breadcrumb.read_breadcrumb(directory)
    if not isinstance(record, dict) or record.get("state") not in (
        "flipped", "committed", "publication-uncertain",
    ):
        return
    pid, start = os.getpid(), locks.process_start_time(os.getpid())
    if record.get("new_pid") != pid or start is None or record.get("new_process_start_time") != start:
        return
    guard = None
    try:
        guard = cutover._acquire_cutover_lock(directory, timeout_s=0)
        if breadcrumb.read_breadcrumb(directory) != record:
            return
        table = routing.read_table(directory, strict=True)
        raw = table.get("active") if isinstance(table, dict) else None
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if endpoint is None or (endpoint.pid, endpoint.process_start_time) != (pid, start):
            return
        if record["state"] != "committed":
            # Re-publish the exact selected native owner through the normal
            # durability boundary before enabling any deferred retirement grant.
            routing.publish_active(
                directory, bind=endpoint.bind, port=endpoint.port, pid=pid,
                version=endpoint.version, generation=endpoint.generation,
                process_start_time=start, demote_existing=True,
            )
            breadcrumb.write_breadcrumb(
                directory, state="committed", old=record.get("old"),
                new_port=endpoint.port, new_pid=pid, new_process_start_time=start,
                publication_witness=record.get("publication_witness"),
                started_at=record.get("started_at"),
            )
        old = record.get("old")
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
        if promote().get("adopted") is True and ready():
            breadcrumb.clear_breadcrumb(directory)
    except AlreadyRunningError:
        return
    except (OSError, ValueError, RuntimeError) as error:
        logging.getLogger(__name__).error("Committed monitor completion remains pending: %s", error)
    finally:
        if guard is not None:
            guard.release()
