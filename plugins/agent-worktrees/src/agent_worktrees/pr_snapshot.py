"""Provider-neutral review and snapshot data shared by the PR command family."""

from __future__ import annotations

from dataclasses import dataclass
from .pr_checks import CheckFailure


REVIEW_STATE_EVENT = {
    "REQUEST_CHANGES": "changes_requested",
    "CHANGES_REQUESTED": "changes_requested",
    "APPROVED": "approved",
    "COMMENT": "commented",
    "COMMENTED": "commented",
}


@dataclass(frozen=True)
class Review:
    """One submitted PR review, normalized across providers."""

    id: int
    state: str
    user: str
    submitted_at: str = ""
    commit_id: str = ""
    dismissed: bool = False


@dataclass(frozen=True)
class PRSnapshot:
    """A point-in-time view shared by watch, status and consent classification."""

    pr_state: str = "open"
    merged: bool = False
    head_sha: str = ""
    base_ref: str = ""
    updated_at: str = ""
    """Provider timestamp for the PR's latest mutation."""
    reviews: tuple[Review, ...] = ()
    author: str = ""
    mergeable: bool | None = None
    """Conflict mergeability; None means the provider has not computed it."""
    checks_state: str = ""
    """Observed head-check rollup: success, failure, pending, none, or unknown ("")."""
    labels: tuple[str, ...] = ()
    title: str = ""
    draft: bool = False
    check_failures: tuple[CheckFailure, ...] = ()
    checks_complete: bool | None = None
    check_failures_omitted: int = 0

    @property
    def max_review_id(self) -> int:
        """High-water mark over submitted reviews, excluding pending drafts."""
        return max(
            (r.id for r in self.reviews if r.state.upper() in REVIEW_STATE_EVENT),
            default=0,
        )

    @property
    def closed_unmerged(self) -> bool:
        return self.pr_state == "closed" and not self.merged
