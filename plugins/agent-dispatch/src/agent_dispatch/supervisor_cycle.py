"""One ordered supervision cycle; reservation and lifecycle owners stay unchanged."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from .client import DispatchError
from .spawn_diagnostics import SpawnDiagnostics, diagnostic_text

if TYPE_CHECKING:
    from .supervisor import Supervisor

log = logging.getLogger("agent-dispatch.supervisor")


def poll_once(self: Supervisor, *, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    trace = SpawnDiagnostics(supervisor_id=self.supervisor_id)
    with trace.stage("cycle") as result:
        spawned = _poll(self, now=now, trace=trace)
        result["spawned_count"] = len(spawned)
        return spawned


def _poll(self: Supervisor, *, now: float, trace: SpawnDiagnostics) -> list[str]:
    from . import bridge
    from .embody import EmbodyUnavailable
    from .ordinary_bridge import reserve_for_backend
    from .spawn_factories import SpawnPreparationRetained
    from . import supervisor as owner

    def call(name: str, **kwargs: object) -> None:
        with trace.stage(name):
            getattr(self, name)(**kwargs)

    call("reconcile")
    call("reconcile_reserving")
    call("release_requested_bodies", now=now)
    call("bind_headless_owner_sessions")
    call("renew_restricted_grants")
    call("nudge_idle_headless_tasks", now=now)
    call("cool_dormant_bodies")
    call("release_resumed_cold_tasks", now=now)
    call("recover_stranded_cold_reservations")
    call("recover_stranded_releasing_reservations", now=now)
    if self.evaluator is not None:
        call("advance_via_evaluator")
    if self.heartbeat or self.publish_activity:
        call("hold_live_leases")
    if self.recover:
        call("recover_gone")
    if self.consistency_sweep:
        try:
            call("sweep_spawn_consistency")
        except Exception as exc:  # Existing additive sweeps never gate spawning.
            log.error("spawn-consistency sweep failed: %s", diagnostic_text(str(exc), trace.secrets))
        try:
            call("sweep_task_reservation_consistency")
        except Exception as exc:
            log.error("task-reservation consistency sweep failed: %s", diagnostic_text(str(exc), trace.secrets))
    call("redrive_unclaimed_spawns")
    if self.nudge:
        call("nudge_stalled", now=now)
    with trace.stage("failed_spawn_counts"):
        failed_counts = self._failed_spawn_counts()
    with trace.stage("selection") as selected:
        eligible = list(self._eligible(now))
        selected["eligible_count"] = len(eligible)
    if failed_counts is None:
        log.warning(
            "could not determine failed-spawn counts this cycle; "
            "blocking new spawns rather than risking a retry past "
            "max_attempts (existing active work is unaffected)"
        )
        trace.event("scheduling", "blocked", reason="failed_spawn_counts_unavailable")
        dead_lettered: set[str] = set()
        active = self.max_concurrent
    else:
        with trace.stage("retry_budget") as budget:
            dead_lettered = self._log_dead_lettered(eligible, failed_counts)
            budget["exhausted_count"] = len(dead_lettered)
        with trace.stage("active_reservations"):
            active = len(self._active_reservations())
    spawned: list[str] = []
    for task in eligible:
        task_trace = SpawnDiagnostics(
            supervisor_id=self.supervisor_id, cycle_id=trace.cycle_id,
            task_id=task["id"],
            generation=task.get("generation") if type(task.get("generation")) is int else None,
            secrets={"TASK_PROMPT_SECRET": task["prompt"] if isinstance(task.get("prompt"), str) else ""},
        )
        trace.secrets.clear()
        trace.secrets.update(task_trace.secrets)
        if task["id"] in dead_lettered:
            # The existing signature-based warning bounds per-task dead-letter output.
            continue
        if active >= self.max_concurrent:
            trace.event("scheduling", "blocked", reason="concurrency_limit",
                        active=active, max_concurrent=self.max_concurrent)
            break
        if self.capacity_gate is not None:
            with task_trace.stage("capacity") as capacity:
                available = self.capacity_gate(task)
                capacity["available"] = bool(available)
            if not available:
                task_trace.event("scheduling", "deferred", reason="no_capacity")
                continue
        try:
            with task_trace.stage("governance_before_reserve") as governance:
                before_reserve = self._recheck_governance("pre-mutation:reserve-spawn")
                governance["status"] = before_reserve.get("status") if before_reserve else None
            if before_reserve is not None and before_reserve.get("status") != "ready":
                task_trace.event("scheduling", "refused", reason="governance_before_reserve")
                log.warning(
                    "supervisor refused reservation at %s: %s (%s)",
                    before_reserve.get("checkpoint"), before_reserve.get("reason"),
                    before_reserve.get("status"),
                )
                break
            with task_trace.stage("reserve") as reserved:
                resp = reserve_for_backend(
                    self.client, task, self.supervisor_id,
                    ordinary=getattr(self.spawn_fn, "ordinary_bridge_for", lambda _: False)(task),
                    restricted=getattr(self.spawn_fn, "restricted_bridge_for", lambda _: False)(task),
                )
                reserved["reserved"] = bool(resp.get("reserved"))
        except DispatchError:
            continue
        if resp.get("coordinator_launch") and (
            resp["coordinator_launch"].get("ok") or resp["reservation"].get("state") != "failed"
        ):
            task_trace.event("scheduling", "coordinator_launch",
                             ok=bool(resp["coordinator_launch"].get("ok")))
            return spawned + ([task["id"]] if resp["coordinator_launch"].get("ok") else [])
        if not resp.get("reserved"):
            task_trace.event("scheduling", "deferred", reason="reservation_contended")
            continue
        reservation = resp["reservation"]
        key = reservation["key"]
        task_trace.reservation_key = key
        task_trace.creation_key = reservation.get("creation_key")
        active += 1
        try:
            with task_trace.stage("prepare"):
                spawn_task = self._prepare_spawn_task(task, reservation)
        except SpawnPreparationRetained as exc:
            from .workspace_binding import defer_pending_creation

            with task_trace.stage("defer_pending_creation"):
                if defer_pending_creation(self.client, key, exc):
                    active -= 1
            log.error("spawn preparation retained reservation %s for task %s: %s",
                      key, task["id"], diagnostic_text(str(exc), task_trace.secrets))
            continue
        except (DispatchError, EmbodyUnavailable, bridge.BridgeUnavailable) as exc:
            try:
                with task_trace.stage("fail_preparation"):
                    self.client.fail_spawn(key, detail=f"reusable worktree preparation failed: {exc}")
                    active -= 1
            except DispatchError as bookkeeping_error:
                log.error("bookkeeping failed for reservation %s: %s", key,
                          diagnostic_text(str(bookkeeping_error), task_trace.secrets))
            log.warning("spawn preparation failed for task %s (%s): %s", task["id"], key,
                        diagnostic_text(str(exc), task_trace.secrets))
            continue
        with task_trace.stage("governance_before_spawn") as governance:
            before_spawn = self._recheck_governance("pre-mutation:spawn")
            governance["status"] = before_spawn.get("status") if before_spawn else None
        if before_spawn is not None and before_spawn.get("status") != "ready":
            task_trace.event("scheduling", "refused", reason="governance_before_spawn")
            try:
                with task_trace.stage("release_governance"):
                    detail = f"installation governance changed before spawn: {before_spawn.get('reason')}"
                    if spawn_task.get("spawn_worktree_ownership") == "created":
                        self.client.request_spawn_release(
                            key, detail=detail, disposition="failed",
                            finished_startup_revision=reservation.get("startup_revision"),
                        )
                    else:
                        self.client.fail_spawn(key, detail=detail)
            except DispatchError as exc:
                log.error("bookkeeping failed for reservation %s: %s", key,
                          diagnostic_text(str(exc), task_trace.secrets))
            log.warning(
                "supervisor released reservation %s after %s: %s (%s)",
                key, before_spawn.get("checkpoint"), before_spawn.get("reason"),
                before_spawn.get("status"),
            )
            break
        with task_trace.stage("backend") as backend:
            ok, handle = self.spawn_fn(spawn_task)
            backend["ok"] = ok
            if not ok:
                backend["error"] = diagnostic_text(str(handle.get("error", "spawn failed")), task_trace.secrets)
        if ok and not handle.get("session"):
            ok = False
            handle = {
                **handle,
                "error": handle.get("error") or "spawn reported success with no session id",
            }
            task_trace.event("backend_identity", "refused", reason="missing_session")
        try:
            if ok:
                with task_trace.stage("record_spawn"):
                    self.client.record_spawn(
                        key, session_handle=handle.get("session"), worktree=handle.get("worktree"),
                        startup_complete=True, startup_revision=reservation.get("startup_revision"),
                        **({"session_info": handle["session_info"]} if "session_info" in handle else {}),
                    )
                if self.publish_activity:
                    with task_trace.stage("publish_activity"):
                        self.client.set_activity(task["id"], "ACTIVE", reservation_key=key)
                spawned.append(task["id"])
                log.info("spawned embody for task %s (%s)", task["id"], key)
            else:
                detail = handle.get("error", "spawn failed")
                if handle.get("retained"):
                    task_trace.event("bookkeeping", "retained", reason="explicit_repair_required")
                    log.error("spawn reservation %s retained for explicit repair: %s", key,
                              diagnostic_text(str(detail), task_trace.secrets))
                elif handle.get("deferred"):
                    with task_trace.stage("defer_spawn"):
                        self.client.defer_spawn(
                            key, detail=detail,
                            **({"expected_generation": handle["expected_generation"]}
                               if "expected_generation" in handle else {}),
                        )
                        active -= 1
                    log.info("spawn deferred (carried session busy) for task %s (%s): %s",
                             task["id"], key, diagnostic_text(str(handle.get("error")), task_trace.secrets))
                elif spawn_task.get("spawn_worktree_ownership") == "created" and not handle.get("preserve_workspace"):
                    with task_trace.stage("request_failed_created_spawn_release"):
                        owner.request_failed_created_spawn_release(
                            self.client, key, handle, spawn_task, detail,
                        )
                    log.warning("spawn failed for task %s (%s): %s", task["id"], key,
                                diagnostic_text(str(handle.get("error")), task_trace.secrets))
                else:
                    with task_trace.stage("fail_spawn"):
                        self.client.fail_spawn(key, detail=detail)
                        active -= 1
                    log.warning("spawn failed for task %s (%s): %s", task["id"], key,
                                diagnostic_text(str(handle.get("error")), task_trace.secrets))
        except DispatchError as exc:
            log.error("bookkeeping failed for reservation %s: %s", key,
                      diagnostic_text(str(exc), task_trace.secrets))
    if self.event_wake is not None:
        with trace.stage("event_acknowledge"):
            self.event_wake.acknowledge()
    call("_sync_event_subscriptions")
    return spawned
