"""Resident handoff gates: selected writer, admitted reader, latched retirement."""

from __future__ import annotations

from . import self_retire
from zdd import diagnostics, routing
from single_instance_lease import AlreadyRunningError


class MonitorHandover:
    def __init__(self, *, passive: bool):
        self.generation: int | None = None
        self.promotion_requested = not passive
        self.retiring = False

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
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        successor_dead = (
            endpoint is not None and type(endpoint.pid) is int and endpoint.pid > 0
            and endpoint.pid != pid and not diagnostics.lock_data_is_live({
                "pid": endpoint.pid, "start_time": endpoint.process_start_time,
            })
        )
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
