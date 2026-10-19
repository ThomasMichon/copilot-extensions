"""Pure reviewer-lifecycle policy shared by PR readers and mutation guards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

DECISIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
COMMENT_STATES = frozenset({"COMMENTED"})
KNOWN_REVIEW_STATES = DECISIVE_STATES | COMMENT_STATES | {"PENDING"}


@dataclass(frozen=True)
class ReviewEvidence:
    id: int
    state: str
    reviewer: str
    submitted_at: str = ""
    commit_id: str = ""
    dismissed: bool = False


def normalize_review_state(state: object) -> str:
    value = state.upper() if isinstance(state, str) else ""
    return {
        "REQUEST_CHANGES": "CHANGES_REQUESTED",
        "COMMENT": "COMMENTED",
    }.get(value, value)


def _coerce(review: object, index: int) -> ReviewEvidence:
    if isinstance(review, ReviewEvidence):
        return review
    if isinstance(review, dict):
        rid = review.get("id")
        return ReviewEvidence(
            id=rid if isinstance(rid, int) else index,
            state=normalize_review_state(review.get("state")),
            reviewer=str(review.get("reviewer") or review.get("author") or review.get("user") or ""),
            submitted_at=str(review.get("submitted_at") or review.get("submittedAt")
                             or review.get("at") or ""),
            commit_id=str(review.get("commit_id") or review.get("commit") or ""),
            dismissed=review.get("dismissed") is True,
        )
    return ReviewEvidence(
        id=getattr(review, "id", index) if isinstance(getattr(review, "id", index), int) else index,
        state=normalize_review_state(getattr(review, "state", "")),
        reviewer=str(getattr(review, "reviewer", "") or getattr(review, "user", "")),
        submitted_at=str(getattr(review, "submitted_at", "") or ""),
        commit_id=str(getattr(review, "commit_id", "") or ""),
        dismissed=getattr(review, "dismissed", False) is True,
    )


def review_evidence(reviews: Iterable[object]) -> tuple[ReviewEvidence, ...]:
    return tuple(_coerce(review, index) for index, review in enumerate(reviews, 1))


def _order(review: ReviewEvidence) -> tuple[int, datetime, int]:
    try:
        parsed = datetime.fromisoformat(review.submitted_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return 1, parsed.astimezone(timezone.utc), review.id
    except (AttributeError, ValueError):
        return 0, datetime.min.replace(tzinfo=timezone.utc), review.id


def latest_effective_reviews(
    reviews: Iterable[object], author: str = "",
) -> dict[str, ReviewEvidence]:
    """Latest decisive disposition per reviewer.

    Comments and pending drafts never clear a change request. Only that same
    reviewer's later approval, or the provider's dismissal, does.
    """
    author_key = author.casefold()
    latest: dict[str, ReviewEvidence] = {}
    for review in sorted(review_evidence(reviews), key=_order):
        key = review.reviewer.casefold()
        if not key or key == author_key:
            continue
        state = "DISMISSED" if review.dismissed else normalize_review_state(review.state)
        if state not in DECISIVE_STATES:
            continue
        latest[key] = ReviewEvidence(
            id=review.id,
            state=state,
            reviewer=review.reviewer,
            submitted_at=review.submitted_at,
            commit_id=review.commit_id,
            dismissed=review.dismissed,
        )
    return latest


def outstanding_change_requests(
    reviews: Iterable[object], author: str = "",
) -> tuple[str, ...]:
    return tuple(sorted(
        (review.reviewer for review in latest_effective_reviews(reviews, author).values()
         if review.state == "CHANGES_REQUESTED"),
        key=str.casefold,
    ))


def effective_review(
    reviews: Iterable[object], author: str = "", *, include_comments: bool = False,
) -> ReviewEvidence | None:
    """Return the effective review with active negatives taking precedence."""
    evidence = review_evidence(reviews)
    negatives = [
        review for review in latest_effective_reviews(evidence, author).values()
        if review.state == "CHANGES_REQUESTED"
    ]
    if negatives:
        return max(negatives, key=_order)

    allowed = {"APPROVED"} | (set(COMMENT_STATES) if include_comments else set())
    author_key = author.casefold()
    candidates = [
        review for review in evidence
        if review.reviewer.casefold() != author_key
        and not review.dismissed
        and normalize_review_state(review.state) in allowed
    ]
    if not candidates:
        return None
    review = max(candidates, key=_order)
    return ReviewEvidence(
        id=review.id,
        state=normalize_review_state(review.state),
        reviewer=review.reviewer,
        submitted_at=review.submitted_at,
        commit_id=review.commit_id,
    )


__all__ = [
    "ReviewEvidence",
    "KNOWN_REVIEW_STATES",
    "effective_review",
    "latest_effective_reviews",
    "normalize_review_state",
    "outstanding_change_requests",
    "review_evidence",
]
