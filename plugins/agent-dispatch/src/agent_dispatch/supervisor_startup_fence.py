"""Close terminal startup through the coordinator before cleanup or capacity reuse."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .client import DispatchError
from .queue_records import SpawnState, Status
from .spawn_factories import _machine_from_owner, _parse_fleet_body_handle, _parse_local_body_handle

if TYPE_CHECKING:
    from collections.abc import Callable

    from .client import DispatchClient
    from .spawn_factories import FleetColdFn, FleetVerdictFn, LocalBodyVerdictFn, LocalColdFn

log = logging.getLogger("agent-dispatch.supervisor")

# Distinguishes a claim refused only by a delivered/current owner wake (which a
# proven-absent body may waive) from every other refusal.
_WAKE_EXCLUDED = object()
_WAKE_EXCLUSION_TEXT = "current owner wake excludes physical cooling"


class TerminalStartupMixin:
    if TYPE_CHECKING:
        client: DispatchClient
        fleet_verdict_fn: FleetVerdictFn
        fleet_cold_fn: FleetColdFn
        local_body_verdict_fn: LocalBodyVerdictFn
        local_cold_fn: LocalColdFn
        _cooled_reservations: set[str]
        _cold_retry_after: dict[str, float]
        _terminal_startup_blocks: set[str]
        _pool_reservations: Callable[..., list[dict[str, Any]]]
        _matches_pool: Callable[[dict[str, Any]], bool]

    def _retained_cli_machine(self, reservation: dict[str, Any], task: dict[str, Any]) -> str | None:
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
                # must not hold its lane forever. The probe is charged to the
                # sweep budget and an unproven verdict backs off like a failed
                # stop, so excluded reservations cannot monopolize the sweep.
                attempted += 1
                gone_before = self._dormant_body_gone(fleet, local_sid)
                cooling_token = (
                    self._claim_cooling(res, task, body_gone=True) if gone_before else None
                )
                if not isinstance(cooling_token, str):
                    self._cold_retry_after[key] = now + 60.0
                    continue
            elif isinstance(cooling_token, str):
                attempted += 1
            if not isinstance(cooling_token, str):
                continue
            stopped = False
            try:
                if gone_before:
                    # The guard was granted on the pre-claim GONE verdict and
                    # nothing is stopped, so publish cold directly: a second
                    # probe could only strand the guard without a stop.
                    stopped = True
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
        self, reservation: dict[str, Any], task: dict[str, Any], *, body_gone: bool = False,
        report_wake_exclusion: bool = False,
    ) -> str | object | None:
        if reservation.get("startup_complete") is not True:
            self._retain_terminal_startup(reservation, task)
            return None
        try:
            claim_kwargs: dict[str, Any] = {"body_gone": True} if body_gone else {}
            # The coordinator owns validation of absent/malformed fields.
            revision: Any = reservation.get("startup_revision")
            handle: Any = reservation.get("session_handle")
            current = self.client.claim_spawn_cooling(
                reservation["key"], startup_revision=revision,
                session_handle=handle, state=reservation["state"],
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

    def _cold_reservation_has_live_process(self, reservation: dict[str, Any], task: dict[str, Any]) -> bool:
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

    def _fence_terminal_startup(self, reservation: dict[str, Any], task: dict[str, Any]) -> bool:
        if reservation.get("startup_fenced") is True:
            return True
        try:
            revision: Any = reservation.get("startup_revision")
            fenced = self.client.fence_spawn_startup(reservation["key"], startup_revision=revision)
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
        self, reservation: dict[str, Any], task: dict[str, Any], reason: str = "startup incomplete",
    ) -> None:
        if reservation["key"] not in self._terminal_startup_blocks:
            log.warning(
                "terminal task %s retains unresolved startup %s; "
                "capacity remains occupied until startup is reconciled (%s)",
                task["id"], reservation["key"], reason,
            )
            self._terminal_startup_blocks.add(reservation["key"])
