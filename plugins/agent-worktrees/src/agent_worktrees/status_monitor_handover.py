"""Resident handoff gates: selected writer, admitted reader, latched retirement."""

from __future__ import annotations

from . import self_retire
from zdd import process_exit, routing
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
