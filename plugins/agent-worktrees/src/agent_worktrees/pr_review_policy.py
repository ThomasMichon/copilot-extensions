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


def normalized_review_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return None


def _later(candidate: ReviewEvidence, current: ReviewEvidence) -> bool:
    candidate_time = normalized_review_time(candidate.submitted_at)
    current_time = normalized_review_time(current.submitted_at)
    if candidate_time is not None and current_time is not None:
        return (candidate_time, candidate.id) > (current_time, current.id)
    return candidate.id > current.id


def _latest(reviews: Iterable[ReviewEvidence]) -> ReviewEvidence | None:
    latest = None
    for review in reviews:
        if latest is None or _later(review, latest):
            latest = review
    return latest


def latest_effective_reviews(
    reviews: Iterable[object], author: str = "",
) -> dict[str, ReviewEvidence]:
    """Latest decisive disposition per reviewer.

    Comments and pending drafts never clear a change request. Only that same
    reviewer's later approval, or the provider's dismissal, does.
    """
    author_key = author.casefold()
    latest: dict[str, ReviewEvidence] = {}
    for review in review_evidence(reviews):
        key = review.reviewer.casefold()
        if key == author_key and key:
            continue
        state = "DISMISSED" if review.dismissed else normalize_review_state(review.state)
        if state not in DECISIVE_STATES:
            continue
        if not key:
            if state != "CHANGES_REQUESTED":
                continue
            key = f"<unknown:{review.id}>"
            review = ReviewEvidence(
                id=review.id,
                state=state,
                reviewer="unknown reviewer",
                submitted_at=review.submitted_at,
                commit_id=review.commit_id,
                dismissed=review.dismissed,
            )
        decisive = ReviewEvidence(
            id=review.id,
            state=state,
            reviewer=review.reviewer,
            submitted_at=review.submitted_at,
            commit_id=review.commit_id,
            dismissed=review.dismissed,
        )
        current = latest.get(key)
        if current is None or _later(decisive, current):
            latest[key] = decisive
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
        return _latest(negatives)

    allowed = {"APPROVED"} | (set(COMMENT_STATES) if include_comments else set())
    author_key = author.casefold()
    candidates = [
        review for review in evidence
        if review.reviewer
        and review.reviewer.casefold() != author_key
        and not review.dismissed
        and normalize_review_state(review.state) in allowed
    ]
    if not candidates:
        return None
    review = _latest(candidates)
    if review is None:
        return None
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
    "normalized_review_time",
    "outstanding_change_requests",
    "review_evidence",
]
