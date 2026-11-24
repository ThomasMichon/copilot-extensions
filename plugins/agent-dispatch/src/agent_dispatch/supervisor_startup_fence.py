"""Close terminal startup through the coordinator before cleanup or capacity reuse."""

from __future__ import annotations

import logging
import time

from .client import DispatchError
from .queue_records import SpawnState, Status
from .spawn_factories import _machine_from_owner, _parse_fleet_body_handle, _parse_local_body_handle

log = logging.getLogger("agent-dispatch.supervisor")

# Distinguishes a claim refused only by a delivered/current owner wake (which a
# proven-absent body may waive) from every other refusal.
_WAKE_EXCLUDED = object()
_WAKE_EXCLUSION_TEXT = "current owner wake excludes physical cooling"


class TerminalStartupMixin:
    def _retained_cli_machine(self, reservation: dict, task: dict) -> str | None:
        """Resolve a retained native body's location, never the observer's host."""
        execution_machine = reservation.get("execution_machine")
        if execution_machine is not None:
            if isinstance(execution_machine, str) and execution_machine.strip():
                return execution_machine
            log.warning("retained CLI execution machine is invalid for reservation %s", reservation.get("key"))
            return None
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
            gone_before = False
            cooling_token = self._claim_cooling(res, task, report_wake_exclusion=True)
            if cooling_token is _WAKE_EXCLUDED:
                # A body observed gone after the claim was refused has nothing
                # to stop, so a delivered owner wake cannot race a stop and
                # must not hold its lane forever.
                gone_before = self._dormant_body_gone(fleet, local_sid)
                cooling_token = (
                    self._claim_cooling(res, task, body_gone=True) if gone_before else None
                )
            if not isinstance(cooling_token, str):
                continue
            attempted += 1
            stopped = False
            try:
                if gone_before:
                    # Never physically stop under the waived wake exclusion.
                    stopped = self._dormant_body_gone(fleet, local_sid)
                elif fleet is not None:
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

    def _dormant_body_gone(self, fleet: tuple[str, str] | None, local_sid: str | None) -> bool:
        from . import tracking

        try:
            if fleet is not None:
                return bool(self.fleet_verdict_fn(*fleet) == tracking.GONE)
            if local_sid is not None:
                return bool(self.local_body_verdict_fn(local_sid) == tracking.GONE)
        except Exception:
            log.exception("dormant body liveness probe failed")
        return False

    def _claim_cooling(
        self, reservation: dict, task: dict, *, body_gone: bool = False,
        report_wake_exclusion: bool = False,
    ) -> str | object | None:
        if reservation.get("startup_complete") is not True:
            self._retain_terminal_startup(reservation, task)
            return None
        try:
            claim_kwargs: dict = {"body_gone": True} if body_gone else {}
            current = self.client.claim_spawn_cooling(
                reservation["key"], startup_revision=reservation.get("startup_revision"),
                session_handle=reservation.get("session_handle"), state=reservation["state"],
                **claim_kwargs,
            )
        except DispatchError as exc:
            if report_wake_exclusion and _WAKE_EXCLUSION_TEXT in str(exc):
                log.info("owner wake excludes cooling %s; probing body", reservation["key"])
                return _WAKE_EXCLUDED
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
