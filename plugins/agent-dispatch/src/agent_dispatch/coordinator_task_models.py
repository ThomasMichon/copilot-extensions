"""Pydantic request-body models for coordinator task routes.

Split out of :mod:`agent_dispatch.coordinator_tasks` purely to keep that
module under this repo's module-size cap -- pure data-shape declarations,
no route/business logic.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
)
from pydantic_core import PydanticCustomError

from .coordinator_categories import TaskCategory
from .queue import (
    ResultTooLargeError,
    ResultValidationError,
    StructuredResult,
    encode_result,
)


def _strict_structured_result(value: Any) -> Any:
    if value is None:
        return value
    try:
        encode_result(value)
    except ResultTooLargeError as exc:
        raise PydanticCustomError("result_too_large", str(exc)) from exc
    except ResultValidationError as exc:
        raise ValueError(str(exc)) from exc
    return value


HttpStructuredResult = Annotated[
    StructuredResult, BeforeValidator(_strict_structured_result)
]


class ProducerScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    source: str


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    repo: str | None = None
    prompt: str = ""
    category: TaskCategory = "work"
    proposed: bool = False
    requires: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    affinity: dict[str, str] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    payload_ref: str | None = None
    payload_inline: str | None = None
    target_machine: str | None = None
    target_worktree: str | None = None
    target_repo: str | None = None
    exclusive_key: str | None = None
    supersede_exclusive_key: bool = False
    source: str | None = None
    origin_ref: str | None = None
    evaluator_ref: str | None = None
    require_verification: bool = False
    dedup_key: str | None = None
    producer_scope: ProducerScopeBody | None = None
    producer_id: str | None = None
    producer_generation: StrictInt | None = None
    producer_capability: str | None = None
    producer_request_id: str | None = None
    goal: str | None = None
    done_criteria: str | None = None
    not_before: FiniteFloat = 0.0
    claim_as: str | None = None


class ProducerScopeHandoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    source: str
    producer_id: str
    expected_generation: StrictInt
    required_label: str | None = None


class ClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str | None = None
    repo: str | None = None
    all_repos: bool = False
    machine: str | None = None
    worktree: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    task_id: str | None = None
    lease_seconds: int | None = None
    evaluation: bool = False


class WorkerBody(BaseModel):
    worker_id: str
    owner_session_id: str | None = None


class ActivityBody(BaseModel):
    activity: str | None = None
    reservation_key: str
    observed_at: float | None = None
    admission_fence: dict | None = None


class OwnerSessionBody(BaseModel):
    worker_id: str
    owner_session_id: str
    expected_generation: int | None = None


class TaskGrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1)
    owner_session_id: str = Field(min_length=1)
    expected_generation: StrictInt = Field(ge=0)
    expected_epoch: StrictInt = Field(ge=0)


class YieldBody(BaseModel):
    worker_id: str
    note: str | None = None
    exclude: str | None = None
    release_spawn: bool = True


class SuspendBody(BaseModel):
    worker_id: str
    reason: str
    expected_status: str | None = None
    expected_generation: int | None = None
    expected_owner_session_id: str | None = None
    reject_pending_steer: bool = True
    cooldown_seconds: float | None = None


class ResumeBody(BaseModel):
    worker_id: str
    wake: bool = True
    message: str | None = None
    adopt_session: bool = False
    adopt_owner_session_id: str | None = None
    reuse_session: bool = False
    expected_owner_session_id: str | None = None
    expected_generation: int | None = None


class ReleaseBody(BaseModel):
    worker_id: str
    reason: str | None = None


class CompleteBody(BaseModel):
    worker_id: str
    result_ref: str | None = None
    result: HttpStructuredResult = None  # type: ignore[assignment]
    expected_status: str | None = None
    expected_owner_session_id: str | None = None
    expected_generation: int | None = None


class ProgressBody(BaseModel):
    worker_id: str
    phase: str = ""
    summary: str
    blocker: str | None = None
    pr: str | None = None


class CardBody(BaseModel):
    worker_id: str
    card: dict


class SteerBody(BaseModel):
    fields: dict = Field(default_factory=dict)
    sender: str | None = None
    wake: bool = True
    message: str | None = None
    expected_status: str | None = None
    expected_revision: str | None = None
    idempotency_key: str | None = None


class CardDraftBody(BaseModel):
    fields: dict = Field(default_factory=dict)


class GuardedCardDraftBody(CardDraftBody):
    expected_revision: str = Field(min_length=1, strict=True)
    expected_draft_revision: str = Field(min_length=1, strict=True)


class SteerTakeBody(BaseModel):
    worker_id: str
    all_pending: bool = False


class AbandonBody(BaseModel):
    worker_id: str | None = None
    permitted: bool = False
    reason: str | None = None
    expected_status: str | None = None
    expected_generation: int | None = None
    expected_owner_session_id: str | None = None


class ResetBody(BaseModel):
    reason: str | None = None
    expected_status: str | None = None
    expected_generation: int | None = None
    expected_owner_session_id: str | None = None


class ConfirmBody(BaseModel):
    actor: str | None = None
    expected_status: str | None = None
    expected_generation: int | None = None


class ReopenBody(BaseModel):
    reason: str | None = None
    steer_fields: dict | None = None
    sender: str | None = None
    expected_status: str | None = None
    expected_generation: int | None = None


class HoldBody(BaseModel):
    reason: str
    actor: str
    expected_status: str | None = None


class UnholdBody(BaseModel):
    actor: str | None = None
    expected_status: str | None = None


class UnexcludeBody(BaseModel):
    exclude: str | None = None
    actor: str | None = None
    expected_status: str | None = None
