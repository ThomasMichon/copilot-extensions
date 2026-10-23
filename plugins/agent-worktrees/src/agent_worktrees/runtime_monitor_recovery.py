"""Installer-owned recovery while both runtime generations remain retained."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from zdd import breadcrumb, diagnostics, routing
from zdd.cutover_lock import CutoverLock

from . import locks
from .status_monitor_cutover import ControlClient


def recover_owned_publication(
    root: Path, record: dict, predecessor: tuple[int, str] | None,
    handles: list[subprocess.Popen],
) -> bool:
    """Retry attributable publication; never delegate to a blocked next owner."""
    route_root = root / "status-monitor-routing"
    if record.get("pid") != os.getpid() or record.get("state") not in (
        "publication-uncertain", "started", "spawned", "flipped", "draining", "rolled_back",
    ):
        return False
    handle = next((child for child in handles if child.pid == record.get("new_pid")), None)
    if handle is None:
        return False
    start = record.get("new_process_start_time")
    if handle.poll() is None and (
        not isinstance(start, str) or diagnostics.process_start_time(handle.pid) != start
    ):
        return False
    old = routing.Endpoint.from_dict(record.get("old")) if isinstance(record.get("old"), dict) else None
    if old is not None and predecessor is None:
        return False
    if predecessor is not None and (
        old is None or old.pid != predecessor[0]
        or locks.process_start_time(old.pid) != predecessor[1]
    ):
        return False
    new_port = record.get("new_port")
    if type(new_port) is not int or not 0 < new_port < 65536:
        return False
    with CutoverLock(route_root):
        if breadcrumb.read_breadcrumb(route_root) != record:
            return False
        table = routing.read_table(route_root, strict=True)
        if not isinstance(table, dict):
            return False
        active = routing.Endpoint.from_dict(table.get("active")) if isinstance(table.get("active"), dict) else None
        if active is not None and active.pid not in (handle.pid, old.pid if old is not None else None):
            return False
        candidate = ControlClient(f"http://127.0.0.1:{new_port}")
        health = candidate.health() if handle.poll() is None else None
        committed = bool(
            health and health.get("pid") == handle.pid and health.get("status") == "ready"
            and health.get("published") is True
        )
        if committed:
            routing.publish_active(
                route_root, bind="127.0.0.1", port=new_port, pid=handle.pid,
                process_start_time=start, demote_existing=True,
            )
            if old is not None:
                client = ControlClient(old.base_url, config_dir=route_root, timeout=65.0)
                if client.health().get("pid") != old.pid:
                    return False
                drained = client.drain(timeout=300.0, poll=0.2, force=False)
                if drained.get("drained") is not True:
                    return False
                client.shutdown()
        else:
            # A live formerly-published or unhealthy candidate is not a passive
            # failure. Keep custody until its service outcome can be established.
            if health is not None and (
                health.get("pid") != handle.pid or health.get("published") is not False
                or health.get("status") != "ready"
            ):
                return False
            if old is None:
                return False
            client = ControlClient(old.base_url)
            if client.health().get("pid") != old.pid:
                return False
            routing.publish_active(
                route_root, bind=old.bind, port=old.port, pid=old.pid,
                process_start_time=old.process_start_time, demote_existing=True,
            )
            client.undrain()
            restored = client.health()
            if (
                restored.get("status") != "ready" or restored.get("published") is not True
                or restored.get("pid") != old.pid
            ):
                return False
            if handle.poll() is None:
                handle.terminate()
        breadcrumb.write_breadcrumb(
            route_root, state="committed" if committed else "rolled_back",
            old=record.get("old"), new_port=new_port, new_pid=handle.pid,
            publication_witness=record.get("publication_witness"),
            new_process_start_time=start, started_at=record.get("started_at"),
        )
    return True
