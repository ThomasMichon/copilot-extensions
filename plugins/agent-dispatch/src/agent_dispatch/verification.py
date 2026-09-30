"""Coordinator-side whole-goal verification helpers for submitted tasks."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any

from . import telemetry
from .events import EventBus
from . import remote_dispatch
from .producers.evaluator import (
    Abandon,
    Confirm,
    EvaluatorError,
    load_registration_evaluator,
    NoOp,
)
from .queue import Status, TaskError, TaskQueue
from .registrations import RegistrationKind

log = logging.getLogger("agent-dispatch.verification")


def _event_task_dict(task: dict[str, Any]) -> dict[str, Any]:
    result = dict(task)
    result["has_result"] = result.pop("result", None) is not None or bool(
        result.get("has_result")
    )
    return result


def _publish(bus: EventBus | None, event_type: str, task: dict[str, Any]) -> None:
    if bus is None:
        return
    event_task = _event_task_dict(task)
    bus.publish({"type": event_type, "task": event_task})
    telemetry.emit(telemetry.task_lifecycle_event(event_type, event_task))


def _release_handoff_claim(task: dict[str, Any]) -> None:
    from . import handoff_claim_release

    handoff_claim_release.release_if_handoff(task)


def _event_notes(queue: TaskQueue, task_id: str) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for row in queue.events(task_id):
        note = row.get("note")
        if isinstance(note, str) and note.startswith("event note: "):
            notes.append(
                {
                    "ts": row.get("ts"),
                    "sender": row.get("worker"),
                    "note": note.removeprefix("event note: "),
                }
            )
    return notes


def _active_evaluators(
    queue: TaskQueue,
    *,
    current_machine: str | None,
    current_env: str,
) -> list[tuple[dict[str, Any], Any]]:
    registry: list[tuple[dict[str, Any], Any]] = []
    for record in queue.list_registrations(
        kind=RegistrationKind.EVALUATOR,
        include_paused=False,
    ):
        if record.machine not in (None, current_machine):
            continue
        if str(record.env or "default") != current_env:
            continue
        spec = record.spec or {}
        evaluator_ref = spec.get("evaluator_ref")
        if not isinstance(evaluator_ref, str) or not evaluator_ref:
            continue
        try:
            loaded = load_registration_evaluator(spec)
        except EvaluatorError as exc:
            log.warning(
                "skipping evaluator registration %s (%s): %s",
                record.id,
                evaluator_ref,
                exc,
            )
            continue
        registry.append((spec, loaded))
    return registry


def _evaluator_for_task(
    registrations: list[tuple[dict[str, Any], Any]],
    *,
    repo: str | None,
    evaluator_ref: str,
) -> Any | None:
    exact: list[Any] = []
    global_matches: list[Any] = []
    for spec, loaded in registrations:
        if spec.get("evaluator_ref") != evaluator_ref:
            continue
        if spec.get("all_repos"):
            global_matches.append(loaded)
            continue
        if spec.get("repo") == repo:
            exact.append(loaded)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        log.warning(
            "skipping verification for evaluator_ref %s in repo %s: multiple repo-scoped registrations",
            evaluator_ref,
            repo,
        )
        return None
    if len(global_matches) == 1:
        return global_matches[0]
    if len(global_matches) > 1:
        log.warning(
            "skipping verification for evaluator_ref %s: multiple all-repos registrations",
            evaluator_ref,
        )
    return None


def _verification_report(
    *,
    task_id: str,
    trigger: str,
    eligible: bool,
    reason: str,
    decisions: list[dict[str, Any]] | None = None,
    applied: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = {
        "task_id": task_id,
        "trigger": trigger,
        "eligible": eligible,
        "reason": reason,
    }
    if decisions is not None:
        report["decisions"] = decisions
    if applied is not None:
        report["applied"] = applied
    return report


def evaluate_submitted_task(
    queue: TaskQueue,
    task_id: str,
    *,
    bus: EventBus | None = None,
    trigger: str,
    current_machine: str | None = None,
    current_env: str | None = None,
) -> dict[str, Any]:
    """Run the whole-goal evaluator for one explicitly-triggered submitted task."""
    task = queue.get(task_id)
    if task is None:
        raise TaskError(f"no such task {task_id!r}")
    if task.status != Status.SUBMITTED:
        raise TaskError(
            f"cannot verify a {task.status!r} task (submitted verification is scoped to {Status.SUBMITTED!r})"
        )
    if not task.require_verification:
        raise TaskError(f"task {task_id!r} does not require verification")
    if not task.evaluator_ref:
        raise TaskError(f"task {task_id!r} has no evaluator_ref to verify against")
    if current_machine is None:
        current_machine = remote_dispatch.local_machine()
    current_env = current_env or os.environ.get("AGENT_DISPATCH_ENV") or "default"
    registrations = _active_evaluators(
        queue,
        current_machine=current_machine,
        current_env=current_env,
    )
    if not registrations:
        return _verification_report(
            task_id=task_id,
            trigger=trigger,
            eligible=False,
            reason="no active evaluator registrations are visible on this machine/environment",
        )
    evaluator = _evaluator_for_task(
        registrations,
        repo=task.repo,
        evaluator_ref=task.evaluator_ref,
    )
    if evaluator is None:
        return _verification_report(
            task_id=task_id,
            trigger=trigger,
            eligible=False,
            reason="no registered evaluator matches this task's repo/evaluator_ref scope",
        )

    task_payload = asdict(task)
    task_payload["event_notes"] = _event_notes(queue, task_id)
    event = {"type": "task.submitted", "task": task_payload}
    try:
        decisions = evaluator.evaluate(event)
    except Exception as exc:
        log.exception(
            "verification evaluation failed for task %s via evaluator_ref %s",
            task.id,
            task.evaluator_ref,
        )
        return _verification_report(
            task_id=task_id,
            trigger=trigger,
            eligible=True,
            reason=f"evaluator execution failed: {type(exc).__name__}",
        )

    rendered = [decision.to_dict() for decision in decisions]
    applied: list[dict[str, Any]] = []
    try:
        for decision in decisions:
            current = queue.get(task_id)
            if (
                current is None
                or current.status != Status.SUBMITTED
                or current.generation != task.generation
            ):
                return _verification_report(
                    task_id=task_id,
                    trigger=trigger,
                    eligible=True,
                    reason="task changed while verification was in flight; retry required",
                    decisions=rendered,
                    applied=applied,
                )
            if isinstance(decision, Confirm):
                if current is not None and current.status == Status.COMPLETED:
                    applied.append({"decision": "complete", "completed": asdict(current)})
                    continue
                confirmed = asdict(
                    queue.confirm(
                        task_id,
                        actor="evaluator",
                        expected_generation=task.generation,
                        expected_owner_session_id=task.owner_session_id,
                        expected_status=Status.SUBMITTED,
                        expected_updated_at=task.updated_at,
                    )
                )
                _release_handoff_claim(confirmed)
                _publish(bus, "task.completed", confirmed)
                applied.append({"decision": "complete", "completed": confirmed})
                continue
            if isinstance(decision, Abandon):
                outcome = queue.abandon_with_outcome(
                    task_id,
                    worker_id="evaluator",
                    permitted=True,
                    reason=decision.reason,
                    expected_status=Status.SUBMITTED,
                    expected_generation=task.generation,
                    expected_owner_session_id=task.owner_session_id,
                    expected_updated_at=task.updated_at,
                )
                abandoned = asdict(outcome.task)
                if outcome.event_type is not None:
                    _release_handoff_claim(abandoned)
                if outcome.event_type is not None:
                    _publish(bus, outcome.event_type, abandoned)
                applied.append({"decision": "abandon", "abandoned": abandoned})
                continue
            if isinstance(decision, NoOp):
                applied.append(decision.to_dict())
                continue
            raise EvaluatorError(
                "whole-goal verification evaluators may decide only complete/abandon/noop"
            )
    except EvaluatorError as exc:
        return _verification_report(
            task_id=task_id,
            trigger=trigger,
            eligible=True,
            reason=str(exc),
            decisions=rendered,
            applied=applied,
        )

    return _verification_report(
        task_id=task_id,
        trigger=trigger,
        eligible=True,
        reason="submitted verification evaluated",
        decisions=rendered,
        applied=applied,
    )
