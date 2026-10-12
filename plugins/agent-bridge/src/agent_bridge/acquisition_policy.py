"""Acquisition selection over authoritative evidence, with no launch side effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from .acquisition_contract import (
    Acquired, AcquisitionRefused, AcquisitionRequest, Mode,
)


@dataclass(frozen=True)
class ConversationEvidence:
    """Adapter-verified binding; never inferred from a directory or caller ID."""

    target: str
    mode: Mode
    task_id: str
    task_generation: int
    worktree_id: str
    session_id: str
    native_session_id: str | None
    liveness: Literal["live", "stopped", "unknown"]
    retired: bool | None
    busy: bool = False
    charter: str | None = None
    model: str | None = None
    effort: str | None = None
    generation_bound: bool = False


@dataclass(frozen=True)
class AcquisitionEvidence:
    """A complete holder snapshot and ground-layer head for the target venue."""

    authority_known: bool
    head_native_session_id: str | None
    conversations: tuple[ConversationEvidence, ...] = ()
    pending_transition: bool = False


@dataclass(frozen=True)
class AcquisitionDecision:
    acquired: Acquired
    conversation: ConversationEvidence | None = None
    creation_authorized: bool = False
    retirement_recovered: bool = False


def select_acquisition(
    request: AcquisitionRequest, evidence: AcquisitionEvidence,
) -> AcquisitionDecision:
    """Reuse/resume only an exact task-bound native identity, otherwise refuse."""
    if not evidence.authority_known:
        raise AcquisitionRefused("authority_unknown", "target authority could not be verified")
    if evidence.pending_transition:
        raise AcquisitionRefused("transition_pending", "a worktree transition is pending")
    conversations = evidence.conversations
    if any(
        item.worktree_id != request.worktree_id or item.target != request.target
        for item in conversations
    ):
        raise AcquisitionRefused("evidence_scope_mismatch", "holder evidence names another target")
    if any(item.liveness == "unknown" for item in conversations):
        raise AcquisitionRefused("liveness_unknown", "holder liveness is unknown")
    live = [item for item in conversations if item.liveness == "live"]
    if len(live) > 1:
        raise AcquisitionRefused("multiple_live_holders", "multiple sessions hold the target")

    fresh = request.intent == "fresh" or request.conversation_retired is True
    if fresh:
        if request.intent == "automatic" and request.conversation_retired is True:
            current = [
                item for item in conversations
                if item.task_id == request.task_id
                and item.task_generation == request.task_generation
                and item.mode == request.mode
                and item.retired is False
                and item.generation_bound
                and item.native_session_id == evidence.head_native_session_id
            ]
            if len(current) == 1:
                continuation = request.model_copy(update={"conversation_retired": False})
                return replace(
                    select_acquisition(continuation, evidence),
                    retirement_recovered=True,
                )
        if live:
            raise AcquisitionRefused("live_holder", "fresh intent does not authorize takeover")
        return AcquisitionDecision("created", creation_authorized=True)

    if request.allocation == "created":
        if request.intent == "continue":
            raise AcquisitionRefused("continuation_missing", "new allocation has no conversation")
        if conversations or evidence.head_native_session_id or (
            request.prior_session_id or request.prior_native_session_id
        ):
            raise AcquisitionRefused("new_allocation_conflict", "new allocation has prior ownership")
        return AcquisitionDecision("created", creation_authorized=True)

    if request.conversation_retired is not False:
        raise AcquisitionRefused("retirement_unknown", "retained allocation lacks retirement evidence")

    matches = [
        item for item in conversations
        if item.task_id == request.task_id
        and item.task_generation <= request.task_generation
        and item.mode == request.mode
        and (not request.prior_session_id or item.session_id == request.prior_session_id)
        and (
            not request.prior_native_session_id
            or item.native_session_id == request.prior_native_session_id
        )
    ]
    if len(matches) != 1:
        raise AcquisitionRefused("continuity_ambiguous", "no single task-bound conversation")
    selected = matches[0]
    if selected.retired is not False:
        raise AcquisitionRefused("conversation_retired", "conversation retirement is not cleared")
    if not selected.native_session_id:
        raise AcquisitionRefused("native_identity_missing", "provider has no native identity proof")
    if evidence.head_native_session_id != selected.native_session_id:
        raise AcquisitionRefused("head_mismatch", "ground-layer head does not name this conversation")
    if live and live[0] != selected:
        raise AcquisitionRefused("foreign_live_holder", "another session holds the target")
    if selected.busy:
        raise AcquisitionRefused("session_busy", "matching conversation cannot accept this turn")
    if any(
        getattr(request, field) is not None
        and getattr(selected, field) != getattr(request, field)
        for field in ("charter", "model", "effort")
    ):
        raise AcquisitionRefused("options_mismatch", "existing execution options differ")
    return AcquisitionDecision(
        "reused" if selected.liveness == "live" else "resumed", selected,
    )
