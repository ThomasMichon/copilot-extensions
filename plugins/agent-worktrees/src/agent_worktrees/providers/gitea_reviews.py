"""Strict paginated Gitea review metadata under the existing provider transport."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..pr_snapshot import Review
from ..pr_review_policy import KNOWN_REVIEW_STATES, normalize_review_state
from .base import ProviderError
from .gitea_pagination import CountedResponse
from .observation_credentials import MAX_SNAPSHOT_REQUESTS, ObservationCredentials

if TYPE_CHECKING:
    from .gitea import GiteaProvider


class ReviewCollection(tuple[Review, ...]):
    """Preserve review tuple consumers while carrying separately qualified coverage."""

    complete: bool | None

    def __new__(
        cls, reviews: Iterable[Review], *, complete: bool | None,
    ) -> ReviewCollection:
        selected = super().__new__(cls, reviews)
        selected.complete = complete
        return selected


def _review(value: object) -> Review:
    if not isinstance(value, dict):
        raise ValueError("review is not an object")
    actor = value.get("user")
    state = normalize_review_state(value.get("state")) if isinstance(value.get("state"), str) else ""
    team = value.get("team")
    team_request = (
        state == "REQUEST_REVIEW" and actor is None and isinstance(team, dict)
        and type(team.get("id")) is int and team["id"] > 0
        and isinstance(team.get("name"), str) and bool(team["name"].strip())
    )
    if (
        type(value.get("id")) is not int or value["id"] < 1
        or (not team_request and (
            not isinstance(actor, dict)
            or not isinstance(actor.get("login"), str) or not actor["login"].strip()
        ))
        or not isinstance(value.get("state"), str) or not value["state"].strip()
        or state not in KNOWN_REVIEW_STATES | {"REQUEST_REVIEW"}
        or type(value.get("dismissed", False)) is not bool
        or any(value.get(name) is not None and not isinstance(value[name], str)
               for name in ("submitted_at", "commit_id"))
    ):
        raise ValueError("invalid review metadata")
    return Review(
        id=value["id"], state=value["state"], user=actor["login"] if isinstance(actor, dict) else "",
        submitted_at=value.get("submitted_at") or "",
        commit_id=value.get("commit_id") or "",
        dismissed=value.get("dismissed", False),
    )


def all_reviews(
    owner: GiteaProvider, repo: str, number: int, api_base: str,
    token: str | ObservationCredentials,
) -> ReviewCollection:
    """Traverse raw-page count bounds even when pending-draft filtering empties a page."""
    reviews: list[Review] = []
    seen: set[int] = set()
    page, page_size = 1, 50
    total: int | None = None
    while page <= MAX_SNAPSHOT_REQUESTS:
        response = owner._curl_with_retry(
            "GET",
            owner._api(api_base, f"/repos/{repo}/pulls/{number}/reviews?limit={page_size}&page={page}"),
            token,
        )
        status, body = response
        if status != 200:
            from .gitea import _is_transient
            raise ProviderError(
                f"Gitea reviews lookup failed (HTTP {status}) on page {page} for {repo}#{number}",
                transient=_is_transient(status),
            )
        count = response.total_count if isinstance(response, CountedResponse) else None
        if page == 1:
            total = count
        elif total is not None and count != total:
            raise ProviderError("Gitea review count changed or became unavailable", transient=True)
        try:
            batch = json.loads(body)
            if not isinstance(batch, list) or len(batch) > page_size:
                raise ValueError("invalid review page")
            for value in batch:
                review = _review(value)
                if review.id in seen:
                    raise ValueError("overlapping review pages")
                seen.add(review.id)
                reviews.append(review)
        except (ValueError, TypeError):
            raise ProviderError(f"Gitea returned malformed reviews on page {page}") from None
        if total is not None:
            if len(seen) > total:
                raise ProviderError("Gitea review count contradicts acquired rows", transient=True)
            # Every raw page has a positive page size. Count is therefore a
            # conservative last-page bound even when the server clamps limit.
            if len(seen) == total or page >= total:
                return ReviewCollection(reviews, complete=True)
        elif not batch:
            return ReviewCollection(reviews, complete=None)
        page += 1
    raise ProviderError("Gitea review traversal reached its page bound", transient=True)
