"""Close terminal startup through the coordinator before cleanup or capacity reuse."""

from __future__ import annotations

import logging
import time

from .client import DispatchError
from .queue_records import SpawnState, Status
from .spawn_factories import _machine_from_owner, _parse_fleet_body_handle, _parse_local_body_handle

log = logging.getLogger("agent-dispatch.supervisor")


class TerminalStartupMixin:
    def _retained_cli_machine(self, reservation: dict, task: dict) -> str | None:
        """Resolve a retained native body's location, never the observer's host."""
        session = reservation.get("session_handle")
        if session and task.get("owner_session_id") == session:
            machine = _machine_from_owner(task.get("completed_by") or task.get("owner"))
            if machine:
                return machine
        try:
            attachments = self.client.attachments(task["id"])
        except (DispatchError, AttributeError):
            log.warning("retained CLI location unavailable for task %s", task.get("id"))
            return None
        machines = {
            item["machine"]
            for item in attachments
            if item.get("session_id") == session
            and item.get("worktree_id") == reservation.get("worktree")
            and isinstance(item.get("machine"), str) and item["machine"]
        }
        return next(iter(machines)) if len(machines) == 1 else None

    def _cool_dormant_bodies(self) -> int:
        """Stop suspended bodies while excluding every concurrent resume.

        Preserve the cold session handle. An awaiting-steer task releases its
        exclusive resource after stop publication; resume must reacquire it.
        """
        from . import tracking

        cooled = attempted = 0
        now = time.time()
        for res in self._pool_reservations(state=SpawnState.SPAWNED):
            if attempted >= 10:
                break
            key = str(res.get("key") or "")
            if not key:
                continue
            if now < self._cold_retry_after.get(key, 0.0):
                continue
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if not self._matches_pool(task) or task.get("status") != Status.SUSPENDED:
                continue
            fleet = _parse_fleet_body_handle(res.get("session_handle"))
            local_sid = _parse_local_body_handle(res.get("session_handle"))
            if fleet is None and local_sid is None:
                continue
            cooling_token = self._claim_cooling(res, task)
            if cooling_token is None:
                continue
            attempted += 1
            stopped = False
            try:
                if fleet is not None:
                    stopped = (
                        self.fleet_verdict_fn(*fleet) == tracking.GONE or self.fleet_cold_fn(*fleet)
                    )
                elif local_sid is not None:
                    stopped = (
                        self.local_body_verdict_fn(local_sid) == tracking.GONE
                        or self.local_cold_fn(local_sid)
                    )
            except Exception:
                log.exception("failed to cool dormant reservation %s", key)
            if not stopped:
                self._retain_terminal_startup(res, task, "cooling outcome unproven; guard retained")
                self._cold_retry_after[key] = now + 60.0
                continue
            release_exclusive = bool(task.get("awaiting_steer"))
            try:
                self.client.record_cold(
                    key, release_exclusive=release_exclusive,
                    startup_revision=res.get("startup_revision"), cooling_token=cooling_token,
                )
            except DispatchError:
                log.exception("failed to publish cold reservation %s; guard retained", key)
                self._cold_retry_after[key] = now + 60.0
                continue
            self._cooled_reservations.add(key)
            self._cold_retry_after.pop(key, None)
            cooled += 1
            log.info(
                "cooled dormant worker for task %s (%s)%s",
                task.get("id"), key,
                " (released exclusive_key: awaiting_steer)" if release_exclusive else "",
            )
        return cooled

    def _claim_cooling(self, reservation: dict, task: dict) -> str | None:
        if reservation.get("startup_complete") is not True:
            self._retain_terminal_startup(reservation, task)
            return None
        try:
            current = self.client.claim_spawn_cooling(
                reservation["key"], startup_revision=reservation.get("startup_revision"),
                session_handle=reservation.get("session_handle"), state=reservation["state"],
            )
        except DispatchError:
            log.exception("failed to acquire cooling guard for %s", reservation["key"])
            return None
        token = current.get("cooling_token")
        if (
            isinstance(token, str) and token
            and current.get("state") == reservation["state"]
            and current.get("startup_complete") is True
            and current.get("startup_revision") == reservation.get("startup_revision")
            and current.get("session_handle") == reservation.get("session_handle")
        ):
            return token
        self._retain_terminal_startup(reservation, task, "cooling guard read-back unproven")
        return None

    def _cold_reservation_has_live_process(self, reservation: dict, task: dict) -> bool:
        if reservation.get("startup_complete") is not True:
            self._retain_terminal_startup(reservation, task)
            return True
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
            and current.get("cooling_token") is None
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
