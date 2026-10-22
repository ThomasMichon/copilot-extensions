"""Native paginated review evidence, with a caller-owned request boundary."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

from ..pr_contract import Review
from ..pr_review_policy import KNOWN_REVIEW_STATES, normalized_review_time
from .base import ProviderError


def read_reviews(
    repo: str, number: int, *, host: str,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    is_transient: Callable[[str], bool], sanitized_errors: bool = False,
) -> tuple[Review, ...]:
    """Read every ascending page; incomplete evidence raises explicitly."""
    reviews: list[Review] = []
    page = 1
    page_size = 100
    while True:
        proc = invoke([
            "gh", "api", "--hostname", host,
            f"/repos/{repo}/pulls/{number}/reviews?per_page={page_size}&page={page}",
        ])
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise ProviderError(
                f"GitHub review page {page} failed: "
                + ("provider read unavailable" if sanitized_errors else detail),
                transient=(is_transient(detail) or sanitized_errors
                           and any(code in detail.lower() for code in ("http 401", "http 403"))),
            )
        try:
            batch = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Malformed GitHub review page {page}: {exc}", transient=True) from exc
        if not isinstance(batch, list):
            raise ProviderError(f"GitHub review page {page} is not a list", transient=True)
        if not batch:
            break
        for row in batch:
            if not isinstance(row, dict):
                raise ProviderError(f"Malformed GitHub review on page {page}", transient=True)
            rid = row.get("id")
            if not isinstance(rid, int):
                raise ProviderError(f"GitHub review on page {page} has no integer id", transient=True)
            state = str(row.get("state", "")).upper()
            user = row.get("user")
            login = user.get("login") if isinstance(user, dict) else None
            if not isinstance(login, str) or not login.strip():
                raise ProviderError(
                    f"GitHub review on page {page} has no reviewer identity", transient=True,
                )
            if state not in KNOWN_REVIEW_STATES:
                raise ProviderError(
                    f"GitHub review on page {page} has unknown state", transient=True,
                )
            submitted_at = row.get("submitted_at")
            if state != "PENDING" and normalized_review_time(submitted_at) is None:
                raise ProviderError(
                    f"GitHub review on page {page} has no valid submission time", transient=True,
                )
            reviews.append(Review(
                id=rid, state=state, user=login, submitted_at=str(submitted_at or ""),
                commit_id=str(row.get("commit_id", "") or ""), dismissed=state == "DISMISSED",
            ))
        if len(batch) < page_size:
            break
        page += 1
    return tuple(reviews)
