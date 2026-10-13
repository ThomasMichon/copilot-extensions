"""Strict acquisition domain contract; not an advertised transport capability."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .acquisition_policy import AcquisitionDecision

Mode = Literal["cli", "acp"]
Acquired = Literal["created", "reused", "resumed"]
ACQUISITION_CONTRACT_VERSION: Literal[1] = 1
DEFAULT_CLI_WORKER_TARGET = "task-worker"


class AcquisitionRequest(BaseModel):
    """Owner-supplied facts, separate from provider-verified session evidence."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    version: Literal[1] = ACQUISITION_CONTRACT_VERSION
    target: str = Field(min_length=1)
    mode: Mode
    task_id: str = Field(min_length=1)
    task_generation: int = Field(ge=0, strict=True)
    worktree_id: str = Field(min_length=1)
    allocation: Literal["created", "reused", "targeted"]
    conversation_retired: bool | None = Field(default=None, strict=True)
    intent: Literal["automatic", "fresh", "continue"] = "automatic"
    prior_session_id: str | None = Field(default=None, min_length=1)
    prior_native_session_id: str | None = Field(default=None, min_length=1)
    delivery_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    charter: str | None = None
    model: str | None = None
    effort: str | None = None
    project: str | None = None
    reservation_key: str = Field(min_length=1)
    driver: str = "agent-dispatch"
    verify_timeout: float = Field(default=0.0, ge=0, le=600)

    @model_validator(mode="after")
    def validate_intent(self) -> AcquisitionRequest:
        if self.intent == "continue" and self.conversation_retired is True:
            raise ValueError("strict continuation cannot revive a retired conversation")
        if self.intent == "fresh" and (
            self.prior_session_id or self.prior_native_session_id
        ):
            raise ValueError("fresh acquisition cannot carry a prior conversation")
        return self


class AcquisitionResult(BaseModel):
    """A native conversation is mandatory; a mux/provider handle is not proof."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    version: Literal[1] = ACQUISITION_CONTRACT_VERSION
    target: str = Field(min_length=1)
    mode: Mode
    task_id: str = Field(min_length=1)
    task_generation: int = Field(ge=0, strict=True)
    worktree_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    native_session_id: str = Field(min_length=1)
    acquired: Acquired
    delivery_id: str = Field(min_length=1)


class AcquisitionRefused(RuntimeError):
    """A continuity refusal, never authorization for a replacement launch."""

    def __init__(
        self, code: str, message: str, *, effect: Literal["not_started"] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.effect = effect


def cli_driver(request: AcquisitionRequest) -> str:
    """Keep the requested driver label and qualify its acquisition incarnation."""
    return f"{request.driver}:acquisition:{request.delivery_id}"


def validate_result(
    request: AcquisitionRequest, result: AcquisitionResult,
    *, decision: AcquisitionDecision | None = None,
    retired_native_ids: frozenset[str] = frozenset(),
) -> None:
    """Check actual adapter output before persisting or reporting success."""
    for field in (
        "target", "mode", "task_id", "task_generation", "worktree_id", "delivery_id",
    ):
        if getattr(request, field) != getattr(result, field):
            raise AcquisitionRefused("result_mismatch", f"acquisition changed {field}")
    if decision is not None:
        if result.acquired != decision.acquired:
            raise AcquisitionRefused("decision_changed", "adapter changed the selected outcome")
        chosen = decision.conversation
        if chosen and (
            chosen.session_id != result.session_id
            or chosen.native_session_id != result.native_session_id
        ):
            raise AcquisitionRefused("native_identity_changed", "adapter changed selected identity")
    if result.native_session_id in retired_native_ids:
        raise AcquisitionRefused("retired_identity_reused", "adapter revived a retired native ID")
    fresh = request.intent == "fresh" or request.conversation_retired is True
    recovered_retirement = (
        decision is not None
        and decision.retirement_recovered
        and decision.conversation is not None
        and decision.conversation.task_generation == request.task_generation
        and decision.conversation.task_id == request.task_id
        and decision.conversation.target == request.target
        and decision.conversation.mode == request.mode
        and decision.conversation.worktree_id == request.worktree_id
        and decision.conversation.retired is False
        and decision.conversation.generation_bound
        and request.intent == "automatic"
        and request.conversation_retired is True
    )
    if fresh and result.acquired != "created" and not recovered_retirement:
        raise AcquisitionRefused("retired_or_fresh_reused", "fresh intent requires creation")
    if request.intent == "continue" and result.acquired == "created":
        raise AcquisitionRefused("continuation_recreated", "continuation cannot create")
    if (
        not fresh
        and request.allocation != "created"
        and result.acquired == "created"
        and not (decision and decision.creation_authorized)
    ):
        raise AcquisitionRefused("continuation_recreated", "retained allocation cannot create")
    if request.allocation == "created" and result.acquired != "created":
        raise AcquisitionRefused("new_allocation_reused", "new allocation cannot inherit history")
    if (
        result.acquired == "created"
        and request.prior_native_session_id
        and request.prior_native_session_id == result.native_session_id
    ):
        raise AcquisitionRefused("retired_identity_reused", "creation revived the carried native ID")
    if result.acquired != "created":
        if (
            request.prior_session_id
            and request.prior_session_id != result.session_id
        ):
            raise AcquisitionRefused("handle_changed", "carried session handle changed")
        if (
            request.prior_native_session_id
            and request.prior_native_session_id != result.native_session_id
        ):
            raise AcquisitionRefused("native_identity_changed", "native conversation changed")
