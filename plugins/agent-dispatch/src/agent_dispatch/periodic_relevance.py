"""Reconsider registrar-owned unbound work through the existing receiver."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
import json
import logging
import math
import subprocess
from typing import Any
import uuid

from .coordinator_relevance import local_policy
from .events import EventBus
from .loop_governance import LoopGovernance
from .producers.evaluator import (
    MAX_SCRIPT_EVALUATOR_TIMEOUT, load_registration_evaluator,
)
from .queue import TaskError, TaskQueue
from .queue_relevance_registration import RelevanceRegistration
from .relevance_evaluation import PeriodicRelevancePolicy, RelevanceJudgment
from .relevance_source import RelevanceSource

log = logging.getLogger(__name__)


def validate_periodic_relevance(
    interval: float, sources: Any, is_active: Any, control_token: str | None,
) -> None:
    if not math.isfinite(interval) or interval < 0:
        raise ValueError("periodic relevance interval must be finite and nonnegative")
    if interval > 0 and (sources is None or not callable(is_active) or not control_token):
        raise ValueError("periodic relevance requires registrar, active owner and control authority")


class PeriodicRelevanceMaintenance:
    def __init__(
        self, queue: TaskQueue, *, sources: Any, bus: EventBus, interval: float,
        is_active: Callable[[], bool] | None, gate: Any, governance: LoopGovernance,
    ) -> None:
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        if interval > 0:
            if is_active is None:
                raise ValueError("periodic relevance requires active-owner authority")
            self.task = asyncio.create_task(drain_periodic_relevance(
                queue, RelevanceSource(sources), bus=bus, interval=interval,
                is_active=is_active, gate=gate, governance=governance,
                stop_event=self.stop_event,
            ))

    async def close(self) -> None:
        if self.task is not None:
            self.stop_event.set()
            # The script owns a finite timeout; await its real thread/DB work.
            await self.task


def _publish(bus: EventBus, outcome: Any) -> None:
    from .coordinator_task_views import _event_task_dict
    from . import telemetry
    from .handoff_claim_release import release_if_handoff

    task = asdict(outcome.task)
    release_if_handoff(task, cancel_pending=True)
    event_task = _event_task_dict(task)
    bus.publish({"type": outcome.event_type, "task": event_task})
    telemetry.emit(telemetry.task_lifecycle_event(outcome.event_type, event_task))


def reconsider_unbound_tasks(
    queue: TaskQueue, source: RelevanceSource, *, bus: EventBus | None = None,
    runner: Callable[..., Any] = subprocess.run,
    can_continue: Callable[[], bool] = lambda: True,
) -> dict:
    """One bounded registrar pass; source checks never hold the queue write lock."""
    admission = source.prepare()
    with local_policy() as (machine, env, _disabled):
        with queue._connect() as conn:
            conn.execute("BEGIN")
            effective = admission(conn, RelevanceRegistration("", machine, env))
    policies = {}
    for rid, reg in effective.registrations.items():
        if (
            reg["kind"] == "evaluator" and reg["status"] == "active"
            and (reg["machine"], reg["env"]) == (machine, env)
        ):
            try:
                policy = PeriodicRelevancePolicy.from_spec(reg["spec"])
                if policy is not None:
                    policies[rid] = policy
            except ValueError:
                log.warning("invalid periodic evaluator %s", rid, exc_info=True)
    result: dict[str, list] = {"accepted": [], "errors": []}
    for rid in queue.rotate_relevance_evaluators(list(policies)):
        if not can_continue():
            break
        policy = policies[rid]
        try:
            with local_policy() as (machine, env, disabled):
                entry = queue.claim_relevance_evaluation(
                    RelevanceRegistration(rid, machine, env), policy,
                    admission=admission, disabled_ids=disabled,
                    lease_seconds=MAX_SCRIPT_EVALUATOR_TIMEOUT + 60,
                )
            if entry is None:
                continue
        except (TaskError, ValueError, OSError) as exc:
            result["errors"].append({"registration_id": rid, "error": str(exc)})
            continue
        report = json.loads(entry["report_json"]) if entry["report_json"] else None
        try:
            if report is None:
                snapshot = json.loads(entry["snapshot_json"])
                evaluator = load_registration_evaluator(snapshot["registration_spec"], runner=runner)
                decisions = evaluator.evaluate({
                    "type": "task.reconsider", "task": snapshot["task"],
                    "relevance_snapshot": {
                        key: value for key, value in snapshot.items()
                        if key not in {"task", "registration_spec"}
                    },
                })
                if len(decisions) != 1 or not isinstance(decisions[0], RelevanceJudgment):
                    raise ValueError("periodic evaluator must return one relevance judgment")
                judgment = decisions[0]
                report = {
                    "request_id": str(uuid.uuid4()),
                    "expected_generation": snapshot["generation"],
                    "expected_revision": snapshot["revision"],
                    "registration_revision": snapshot["registration_revision"],
                    "effective_revision": snapshot["effective_revision"],
                    "verdict": judgment.verdict, "reason": judgment.reason,
                    "observation_ref": judgment.observation_ref,
                }
                queue.save_relevance_evaluation(entry, report)
            if not can_continue():
                queue.finish_relevance_evaluation(
                    entry, policy, verdict=None, error="coordinator no longer active",
                    preserve_report=True,
                )
                break
            # Discovery is refreshed after the external source check, outside DB
            # writes. The receiver rechecks direct rows and holds atomically.
            report_admission = source.prepare()
            with local_policy() as (machine, env, disabled):
                if not can_continue():
                    queue.finish_relevance_evaluation(
                        entry, policy, verdict=None, error="coordinator no longer active",
                        preserve_report=True,
                    )
                    break
                accepted = queue.reconcile_task_relevance(
                    entry["task_id"], **{
                        key: value for key, value in report.items()
                        if key not in {"registration_revision", "effective_revision"}
                    },
                    scope=RelevanceRegistration(
                        rid, machine, env, report["registration_revision"], report["effective_revision"],
                    ),
                    reporter=f"registration:{rid}", disabled_ids=disabled,
                    registration_admission=report_admission, evaluation_token=entry["token"],
                    on_transition=(lambda outcome: _publish(bus, outcome)) if bus is not None else None,
                )
            result["accepted"].append(accepted)
            queue.finish_relevance_evaluation(
                entry, policy, verdict=report["verdict"],
                error=report["reason"] if report["verdict"] == "unknown" else None,
            )
        except Exception as exc:
            # Local receipt lookup distinguishes a rejected transaction from a
            # committed effect whose callback/response failed. Unknown stays saved.
            preserve = report is not None
            try:
                receipt = queue.relevance_receipt(
                    entry["task_id"], report["request_id"], registration_id=rid,
                ) if report is not None else None
                preserve = False
                if receipt is not None:
                    result["accepted"].append(receipt["acceptance"])
                queue.finish_relevance_evaluation(
                    entry, policy, verdict=report["verdict"] if receipt else None,
                    error=str(exc), preserve_report=False,
                )
            except Exception:
                if preserve:
                    try:
                        queue.finish_relevance_evaluation(
                            entry, policy, verdict=None, error=str(exc), preserve_report=True,
                        )
                    except TaskError:
                        pass
                log.warning("periodic evaluation recovery remains pending", exc_info=True)
            result["errors"].append({"task_id": entry["task_id"], "error": str(exc)})
    return result


async def drain_periodic_relevance(
    queue: TaskQueue, source: RelevanceSource, *, bus: EventBus,
    interval: float, is_active: Callable[[], bool], gate: Any,
    stop_event: asyncio.Event,
    governance: LoopGovernance,
) -> None:
    """Lifespan-owned, cooperatively stopped; never a separate daemon."""
    def run() -> None:
        def active() -> bool:
            if stop_event.is_set() or gate.draining or not is_active():
                return False
            verdict = governance.recheck("pre-mutation:periodic-relevance")
            return verdict is None or verdict.get("status") == "ready"

        with gate.track_claim():
            if active():
                result = reconsider_unbound_tasks(queue, source, bus=bus, can_continue=active)
                if result["errors"]:
                    log.warning("periodic relevance retained %d failed checks", len(result["errors"]))

    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run)
        except Exception:
            log.warning("periodic relevance pass failed; no closure inferred", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
