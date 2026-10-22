"""Close terminal startup through the coordinator before cleanup or capacity reuse."""

from __future__ import annotations

import logging

from .client import DispatchError

log = logging.getLogger("agent-dispatch.supervisor")


class TerminalStartupMixin:
    def _cooling_snapshot_current(self, reservation: dict) -> bool:
        from .queue_records import SpawnState

        try:
            current = self.client.get_reservation(reservation["key"])
        except DispatchError:
            log.exception("failed to refresh dormant reservation %s", reservation["key"])
            return False
        return (
            current.get("state") == SpawnState.SPAWNED
            and current.get("startup_complete") is True
            and current.get("startup_revision") == reservation.get("startup_revision")
            and current.get("session_handle") == reservation.get("session_handle")
        )

    def _cold_reservation_has_live_process(self, reservation: dict, task: dict) -> bool:
        if reservation.get("startup_complete") is not True:
            self._retain_terminal_startup(reservation, task)
            return True
        from .queue_records import SpawnState, Status

        if task.get("status") in Status.TERMINAL:
            return not self._fence_terminal_startup(reservation, task)
        try:
            current = self.client.get_reservation(reservation["key"])
        except DispatchError as exc:
            self._retain_terminal_startup(reservation, task, str(exc))
            return True
        return not (
            current.get("state") == SpawnState.COLD
            and current.get("startup_complete") is True
            and current.get("startup_revision") == reservation.get("startup_revision")
            and current.get("session_handle") == reservation.get("session_handle")
        )

    def _fence_terminal_startup(self, reservation: dict, task: dict) -> bool:
        if reservation.get("startup_fenced") is True:
            return True
        try:
            fenced = self.client.fence_spawn_startup(
                reservation["key"], startup_revision=reservation.get("startup_revision"),
            )
        except DispatchError as exc:
            self._retain_terminal_startup(reservation, task, str(exc))
            return False
        if (
            fenced.get("startup_fenced") is True
            and fenced.get("startup_revision") == reservation.get("startup_revision")
            and fenced.get("session_handle") == reservation.get("session_handle")
        ):
            return True
        self._retain_terminal_startup(reservation, task, "startup fence read-back unproven")
        return False

    def _retain_terminal_startup(
        self, reservation: dict, task: dict, reason: str = "startup incomplete",
    ) -> None:
        if reservation["key"] not in self._terminal_startup_blocks:
            log.warning(
                "terminal task %s retains unresolved startup %s; "
                "capacity remains occupied until startup is reconciled (%s)",
                task["id"], reservation["key"], reason,
            )
            self._terminal_startup_blocks.add(reservation["key"])
