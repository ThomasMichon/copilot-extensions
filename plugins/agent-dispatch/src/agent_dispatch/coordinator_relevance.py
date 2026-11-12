"""Provider-first, control-authorized relevance for effective local sources."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import os
from typing import Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .coordinator_auth import _make_control_auth
from .coordinator_task_views import _event_task_dict
from .queue import TaskError, TaskQueue
from .queue_common import CompletionOutcome
from .queue_relevance_registration import RelevanceRegistration
from .registrar_discovery import RegistrarSources
from .relevance_source import RelevanceSource
from .task_relevance_contract import RELEVANCE_PATH, RELEVANCE_PROTOCOL, relevance_contract


class RelevanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1, le=1)
    registration_id: str = Field(min_length=1, max_length=4096)
    registration_revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    effective_revision: str | None = Field(default=None, min_length=1, max_length=4096)
    request_id: str = Field(min_length=1, max_length=4096)
    expected_generation: int = Field(strict=True, ge=0, le=2**63 - 1)
    expected_revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    verdict: Literal["needed", "obsolete", "unknown"]
    reason: str = Field(min_length=1, max_length=4096)
    observation_ref: str = Field(min_length=1, max_length=4096)


@contextmanager
def local_policy() -> Iterator[tuple[str, str, frozenset[str]]]:
    from . import config, overrides, remote_dispatch

    machine = remote_dispatch.local_machine()
    if not machine:
        raise TaskError("relevance executing machine is unavailable")
    env = os.environ.get("AGENT_DISPATCH_ENV") or "default"
    with overrides.locked_overrides(config.overrides_path()) as policy:
        yield machine, env, frozenset(overrides.overridden_off_ids(policy))


def register_relevance_routes(
    app: FastAPI, queue: TaskQueue, *, control_token: str | None,
    sources: RegistrarSources | None = None,
) -> None:
    source = RelevanceSource(sources)
    auth = _make_control_auth(
        control_token or None, lambda *_args: None, operation="task_relevance",
        code_prefix="relevance_control",
    )
    dependencies = [Depends(auth)]

    def envelope(result: dict) -> dict:
        return {"protocol": RELEVANCE_PROTOCOL, "version": 1, "result": result}

    def transition(outcome: CompletionOutcome) -> None:
        from . import telemetry
        from .handoff_claim_release import release_if_handoff

        task = asdict(outcome.task)
        release_if_handoff(task, cancel_pending=True)
        event_task = _event_task_dict(task)
        app.state.bus.publish({"type": outcome.event_type, "task": event_task})
        telemetry.emit(telemetry.task_lifecycle_event(outcome.event_type, event_task))

    @app.get(f"{RELEVANCE_PATH}/contract", dependencies=dependencies)
    def contract() -> dict:
        return relevance_contract(control_token)

    @app.get(f"{RELEVANCE_PATH}/tasks/{{task_id}}", dependencies=dependencies)
    def snapshot(task_id: str, registration_id: str) -> dict:
        try:
            with local_policy() as (machine, env, disabled):
                result = queue.relevance_snapshot(
                    task_id, registration=RelevanceRegistration(registration_id, machine, env),
                    disabled_ids=disabled,
                    registration_admission=source.resolve,
                )
        except (TaskError, OSError, ValueError, TimeoutError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return envelope(result)

    @app.post(f"{RELEVANCE_PATH}/tasks/{{task_id}}", dependencies=dependencies)
    def reconcile(task_id: str, body: RelevanceBody, request: Request) -> dict:
        gate = request.app.state.drain_gate
        try:
            with gate.track_claim():
                if gate.draining:
                    raise TaskError("relevance decisions are inhibited while draining")
                with local_policy() as (machine, env, disabled):
                    result = queue.reconcile_task_relevance(
                        task_id, request_id=body.request_id,
                        scope=RelevanceRegistration(
                            body.registration_id, machine, env, body.registration_revision,
                            body.effective_revision,
                        ),
                        expected_generation=body.expected_generation,
                        expected_revision=body.expected_revision, verdict=body.verdict,
                        reason=body.reason, observation_ref=body.observation_ref,
                        reporter=f"registration:{body.registration_id}", disabled_ids=disabled,
                        on_transition=transition,
                        registration_admission=source.resolve,
                    )
        except (TaskError, OSError, ValueError, TimeoutError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return envelope(result)

    @app.get(
        f"{RELEVANCE_PATH}/tasks/{{task_id}}/receipts/{{request_id:path}}",
        dependencies=dependencies,
    )
    def receipt(task_id: str, request_id: str, registration_id: str) -> dict:
        try:
            result = queue.relevance_receipt(
                task_id, request_id, registration_id=registration_id,
            )
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(404, detail="no such relevance receipt")
        return envelope(result)
