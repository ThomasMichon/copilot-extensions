"""Pure admission policy for the repository's opt-in PR completion steward."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Action = Literal["skip", "wait", "conflict", "rebase", "merge"]


@dataclass(frozen=True)
class CompletionPolicy:
    repository: str
    base_ref: str = "dev"
    consent_label: str = "auto-merge"
    hold_labels: tuple[str, ...] = ("hold", "do-not-merge")
    wip_title_prefixes: tuple[str, ...] = ("WIP", "Draft")


@dataclass(frozen=True)
class ReviewSummary:
    complete: bool
    change_requests: tuple[str, ...]
    copilot_state: str
    copilot_head: str
    copilot_submitted_at: datetime | None
    epoch: datetime


@dataclass(frozen=True)
class CompletionSnapshot:
    repository: str
    number: int
    node_id: str
    state: str
    head_sha: str
    head_ref: str
    base_ref: str
    base_sha: str
    same_repository: bool
    draft: bool
    title: str
    labels: tuple[str, ...]
    invited_collaborator: bool | None
    root_codeowner: bool | None
    reviews: ReviewSummary
    required_checks_verified: bool | None
    mergeable: bool | None
    update_required: bool | None
    rebase_supported: bool | None
    required_checks_error: str = ""


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def decide(snapshot: CompletionSnapshot, policy: CompletionPolicy) -> Decision:
    """Classify one fully observed candidate, without granting write authority."""
    if snapshot.repository != policy.repository or snapshot.base_ref != policy.base_ref:
        return Decision("skip", "outside the admitted repository/base")
    if snapshot.state != "open":
        return Decision("skip", "not open")
    if not snapshot.same_repository:
        return Decision("skip", "fork head")
    if snapshot.head_ref.startswith("release/promote-"):
        return Decision("skip", "release candidate")
    labels = {label.casefold() for label in snapshot.labels}
    if policy.consent_label.casefold() not in labels:
        return Decision("skip", "no completion consent")
    if snapshot.draft:
        return Decision("skip", "draft")
    if any(label.casefold() in labels for label in policy.hold_labels):
        return Decision("skip", "hold label")
    if any(snapshot.title.casefold().startswith(prefix.casefold())
           for prefix in policy.wip_title_prefixes):
        return Decision("skip", "WIP title")
    if snapshot.invited_collaborator is None or snapshot.root_codeowner is None:
        return Decision("wait", "Maintainer membership is unavailable")
    if snapshot.invited_collaborator is not True or snapshot.root_codeowner is not True:
        return Decision("skip", "not an invited root CODEOWNER")
    reviews = snapshot.reviews
    if not reviews.complete:
        return Decision("wait", "review or epoch history is incomplete")
    if reviews.change_requests:
        return Decision("wait", "outstanding reviewer change request")
    if snapshot.mergeable is False:
        return Decision("conflict", "provider-confirmed conflict; submitter resolution required")
    if snapshot.mergeable is not True:
        return Decision("wait", "mergeability is unavailable")
    if reviews.copilot_state != "APPROVED" or reviews.copilot_head != snapshot.head_sha:
        return Decision("wait", "current-head Copilot approval required")
    submitted = reviews.copilot_submitted_at
    if (
        submitted is None or submitted.tzinfo is None or reviews.epoch.tzinfo is None
        or submitted <= reviews.epoch
    ):
        return Decision("wait", "Copilot approval must follow the latest reopen/base change")
    if snapshot.required_checks_verified is not True:
        return Decision("wait", snapshot.required_checks_error
                        or "current required-check evidence is not verified")
    if snapshot.update_required is None:
        return Decision("wait", "branch update policy is unavailable")
    if snapshot.update_required:
        if snapshot.rebase_supported is not True:
            return Decision("wait", "required native rebase is unavailable")
        return Decision("rebase", "required branch update; new review and CI must follow")
    return Decision("merge", "current review, required CI and consent satisfy admission")
