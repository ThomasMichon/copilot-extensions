"""Strict paginated Gitea review metadata under the existing provider transport."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..pr_snapshot import Review
from ..pr_review_policy import KNOWN_REVIEW_STATES, normalize_review_state
from .base import ProviderError

if TYPE_CHECKING:
    from .gitea import GiteaProvider


def _review(value: object) -> Review:
    if not isinstance(value, dict):
        raise ValueError("review is not an object")
    actor = value.get("user")
    if (
        type(value.get("id")) is not int or value["id"] < 1
        or not isinstance(actor, dict)
        or not isinstance(actor.get("login"), str) or not actor["login"].strip()
        or not isinstance(value.get("state"), str) or not value["state"].strip()
        or normalize_review_state(value["state"]) not in KNOWN_REVIEW_STATES | {"REQUEST_REVIEW"}
        or type(value.get("dismissed", False)) is not bool
        or any(value.get(name) is not None and not isinstance(value[name], str)
               for name in ("submitted_at", "commit_id"))
    ):
        raise ValueError("invalid review metadata")
    return Review(
        id=value["id"], state=value["state"], user=actor["login"],
        submitted_at=value.get("submitted_at") or "",
        commit_id=value.get("commit_id") or "",
        dismissed=value.get("dismissed", False),
    )


def all_reviews(
    owner: GiteaProvider, repo: str, number: int, api_base: str, token: str,
) -> tuple[Review, ...]:
    """Return all pages or fail; malformed/overlapping pages are not empty history."""
    reviews: list[Review] = []
    seen: set[int] = set()
    page, page_size = 1, 50
    while True:
        status, body = owner._curl_with_retry(
            "GET",
            owner._api(api_base, f"/repos/{repo}/pulls/{number}/reviews?limit={page_size}&page={page}"),
            token,
        )
        if status != 200:
            from .gitea import _is_transient
            raise ProviderError(
                f"Gitea reviews lookup failed (HTTP {status}) on page {page} for {repo}#{number}",
                transient=_is_transient(status),
            )
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
        if not batch:
            return tuple(reviews)
        page += 1
