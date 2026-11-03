"""Resident handoff gates: selected writer, admitted reader, latched retirement."""

from __future__ import annotations

import time

from . import self_retire
from zdd import breadcrumb, process_exit, routing
from single_instance_lease import AlreadyRunningError

ACTION = "successor-handoff"
CAPABILITY = "status-monitor-successor-handoff-v1"


class MonitorHandover:
    def __init__(self, *, passive: bool):
        self.generation: int | None = None
        self.promotion_requested = not passive
        self.retiring = False
        self.announcement: routing.Endpoint | None = None
        self.announced: routing.Endpoint | None = None

    def accept(self, directory, pid: int, payload: dict) -> dict:
        """Observe a selected successor without treating its notice as readiness."""
        from . import locks

        if not isinstance(payload, dict) or set(payload) != {"old", "successor"}:
            return {"observed": False}
        try:
            routing._validate_endpoint_rows({
                "active": payload["successor"], "previous": payload["old"],
            })
            old = routing.Endpoint.from_dict(payload["old"])
            successor = routing.Endpoint.from_dict(payload["successor"])
            table = routing.read_table(directory, strict=True) or {}
            if (
                old is None or successor is None or old.pid != pid or successor.pid == pid
                or old.process_start_time != locks.process_start_time(pid)
                or not successor.process_start_time
                or locks.process_start_time(successor.pid) != successor.process_start_time
                or table.get("active") != successor.to_dict()
                or table.get("previous") != old.to_dict()
            ):
                return {"observed": False}
        except (OSError, ValueError, TypeError, routing.ActivePublicationRefused):
            return {"observed": False}
        self.announcement = successor
        return {"observed": True}

    def announce(self, directory, pid: int) -> None:
        """The incoming resident attempts one bounded notice per exact successor."""
        from . import locks, status_monitor_cutover as cutover

        try:
            witness = breadcrumb.read_breadcrumb(directory)
            table = routing.read_table(directory, strict=True) or {}
            raw_new, raw_old = table.get("active"), table.get("previous")
            successor = routing.Endpoint.from_dict(raw_new) if isinstance(raw_new, dict) else None
            old = routing.Endpoint.from_dict(raw_old) if isinstance(raw_old, dict) else None
            if (
                not isinstance(witness, dict) or witness.get("self_retirement") is not True
                or successor is None or old is None or successor.pid != pid or old.pid == pid
                or witness.get("old") != old.to_dict()
                or (witness.get("new_pid"), witness.get("new_process_start_time"))
                != (pid, successor.process_start_time)
                or locks.process_start_time(pid) != successor.process_start_time
                or self.announced == successor
            ):
                return
            self.announced = successor
            client = cutover.ControlClient(
                old.base_url, token=cutover.load_or_create_control_token(directory.parent),
                timeout=cutover._READ_TIMEOUT_S,
            )
            deadline = time.monotonic() + cutover._READ_TIMEOUT_S
            health = client._request("health", max_response_bytes=4096, total_timeout=cutover._READ_TIMEOUT_S)
            capabilities = health.get("capabilities")
            remaining = deadline - time.monotonic()
            if not isinstance(capabilities, list) or CAPABILITY not in capabilities or remaining <= 0:
                return
            client._request(
                ACTION, {"old": old.to_dict(), "successor": successor.to_dict()},
                max_response_bytes=4096, total_timeout=remaining,
            )
        except (OSError, ValueError, TypeError, ConnectionError):
            pass

    def read_admitted(self) -> bool:
        return self.generation is not None and self.promotion_requested and not self.retiring

    def observe(self, directory, pid: int) -> bool:
        if not self.retiring and self.generation is not None:
            self.retiring = self_retire.is_superseded(
                directory, pid, self.generation, require_ready=True,
            )
        return self.retiring

    def reopen(self) -> None:
        self.retiring = False
        self.promotion_requested = True

    def release_cold_lease(self, lease, directory, pid: int):
        if lease is None:
            return None
        table = routing.read_table(directory)
        raw = table.get("active") if isinstance(table, dict) else None
        if raw is None and isinstance(table, dict):
            previous = table.get("previous")
            endpoint = routing.Endpoint.from_dict(previous) if isinstance(previous, dict) else None
            if self.generation is not None and endpoint is not None and endpoint.generation > self.generation:
                raw = previous
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        successor_dead = False
        if endpoint is not None and endpoint.pid != pid and endpoint.process_start_time is not None:
            try:
                successor_dead = process_exit.observe_original_exit(endpoint.pid, endpoint.process_start_time)
            except (OSError, ValueError, RuntimeError, NotImplementedError):
                pass
        if self.retiring or successor_dead:
            lease.release()
            return None
        return lease

    def idle_guard(self, directory):
        from .status_monitor_cutover import _acquire_cutover_lock

        try:
            return _acquire_cutover_lock(directory, timeout_s=0)
        except AlreadyRunningError:
            return None
