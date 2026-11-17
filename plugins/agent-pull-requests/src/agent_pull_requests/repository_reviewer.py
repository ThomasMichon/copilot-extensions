"""Cache-only reviewer views composed with the existing pure attention owner."""

from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import re
from typing import Any

from . import attention
from .repository_activity import review_evidence
from .repository_contract import RepositoryIdentity
from .repository_subscriptions import RESPONSE_BYTES, _integer

PROTOCOL = "agent-pull-requests.reviewer-actions/v1"


def capability() -> dict[str, Any]:
    """Actual selected activity support, independent of protected workspace readers."""
    return {
        "protocol": PROTOCOL, "storage": "repository_activity_version:1",
        "operation": "next-actions", "forge_reads": False, "effects": "none",
        "reviewed_base": "unknown", "response_bytes": RESPONSE_BYTES,
        "page_cursor": "projection-sha256",
    }


def _activity(
    facts: dict[str, Any], activity: dict[str, Any], reviewer: str, observed_at: int,
) -> tuple[attention.ReviewActivity, dict[str, Any] | None]:
    unknown = attention.ReviewActivity(reviewer, "unknown")
    if facts["reviews_complete"] is not True:
        return unknown, None
    reviews = []
    for row in facts["reviews"]:
        if not isinstance(row["user"], str):
            return unknown, None
        if row["user"].casefold() != reviewer.casefold():
            continue
        state = row["state"]
        if state in ("PENDING", "REQUEST_REVIEW"):
            continue
        evidence = review_evidence(row, observed_at)
        if evidence is None:
            return unknown, None
        reviews.append((evidence.submitted_at, evidence.review_id, row, evidence.signature))
    if not reviews:
        return attention.ReviewActivity(reviewer, "initial"), None
    matching = [item for item in reviews if item[2]["commit_id"] == facts["head_sha"]]
    if matching:
        row = max(matching, key=lambda item: item[:2])[2]
        return attention.ReviewActivity(reviewer, "head-reviewed", row["id"]), row
    selected = max(reviews, key=lambda item: item[:2])
    if any(item[3] not in activity["candidate_reviews"] for item in reviews):
        return attention.ReviewActivity(reviewer, "order-unknown", selected[1]), None
    return attention.ReviewActivity(reviewer, "updated", selected[1]), selected[2]


def page(
    state: dict[str, Any], identity: RepositoryIdentity, consumer: str, *,
    reviewer: str, now: int, max_age_seconds: int = 1800,
    after: int = 0, limit: int = 16, cursor: str | None = None,
) -> dict[str, Any]:
    """Rank one admitted scope; never acquire a provider, claim work or write handled state."""
    if not isinstance(reviewer, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", reviewer):
        raise ValueError("explicit reviewer identity required")
    _integer(max_age_seconds, 1, 86400)
    _integer(after, 0, 800)
    _integer(limit, 1, 16)
    if cursor is not None and (
        not isinstance(cursor, str) or re.fullmatch(r"[a-f0-9]{64}", cursor) is None
    ) or after and cursor is None:
        raise ValueError("reviewer continuation requires its projection cursor")
    repository = next((row for row in state["repositories"]
                       if RepositoryIdentity.parse(row["identity"]).key == identity.key), None)
    result: dict[str, Any] = {
        "protocol": PROTOCOL, "identity": identity.document(), "reviewer": reviewer,
        "consumer": consumer, "event_cursor": state["next_sequence"] - 1, "now": now,
        "inventory": repository["inventory"] if repository is not None else {"outcome": "unknown"},
        "next_actions": [], "unavailable": [], "more": False, "next_after": None,
    }
    projected: list[attention.Attention] = []
    rows: dict[int, dict[str, Any]] = {}
    reviews: dict[int, dict[str, Any] | None] = {}
    for pull in repository["pulls"] if repository is not None else ():
        number, facts, activity = pull["number"], pull["facts"], pull["activity"]
        if not pull["member"]:
            continue
        if facts is None or activity is None or pull["observed_at"] is None:
            result["unavailable"].append({"pr": number, "reason": "activity-unavailable"})
            continue
        try:
            candidate = attention.Candidate(facts["head_sha"], facts["base_sha"])
        except ValueError:
            result["unavailable"].append({"pr": number, "reason": "candidate-unavailable"})
            continue
        key = attention.PRKey(
            identity.provider, identity.endpoint, identity.repo, number, identity.account, identity.namespace,
        )
        review, original = _activity(facts, activity, reviewer, pull["observed_at"])
        cause = activity["conflict"] if facts["mergeable"] is False else activity["candidate"]
        selected = attention.Facts(
            key, candidate, attention.Cursor(1, state["next_sequence"] - 1),
            pull["observed_at"], (cause or activity["candidate"])["since"],
            result["inventory"]["outcome"] == "complete" and not pull["error"],
            state="merged" if facts["merged"] else "closed-unmerged" if facts["pr_state"] == "closed" else "open",
            draft=facts["draft"], conflict=facts["mergeable"] is False,
        )
        # A cache refresh can observe a different base, but a review head alone
        # never supplies the reviewed base needed by attention.Handled.
        item = attention.project(
            selected, attention.Interest(
                "maintainer", bool(facts["author"]) and facts["author"].casefold() != reviewer.casefold(),
                consumer,
            ),
            now=now, max_age_seconds=max_age_seconds, review_activity=review,
        )
        if not facts["author"]:
            item = replace(item, status="unavailable", reasons=("author-unavailable",))
        if item.status == "unavailable":
            result["unavailable"].append({"pr": number, "reason": item.reasons[0]})
        projected.append(item)
        rows[number], reviews[number] = pull, original
    ordered = attention.rank(projected, now=now, aging_seconds=300)
    actions = []
    for item in ordered:
        pull, original = rows[item.key.number], reviews[item.key.number]
        rebase = "rebase" in item.reasons
        cause = pull["activity"]["conflict" if rebase else "candidate"]
        row = {
            "pr": item.key.number, "action": "rebase" if rebase else (
                "new_verdict" if "follow-up" in item.reasons else "initial_verdict"
            ),
            "reason": "stale_branch" if rebase else (
                "author_update" if "follow-up" in item.reasons else "author_create"
            ),
            "actor": pull["facts"]["author"] if rebase else reviewer,
            "actor_role": "author" if rebase else "reviewer", "reasons": list(item.reasons),
            "candidate": asdict(item.candidate), "cause": cause, "review": original,
            "review_basis": "observed-before-candidate" if "follow-up" in item.reasons else None,
            "pending_since": item.pending_since, "observed_at": item.observed_at,
            "expires_at": item.expires_at, "priority": item.priority,
        }
        actions.append(row)
    projection = {
        key: value for key, value in result.items()
        if key not in {"now", "next_actions", "more", "next_after"}
    }
    projection.update(next_actions=actions, max_age_seconds=max_age_seconds)
    result["cursor"] = hashlib.sha256(json.dumps(
        projection, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    if cursor is not None and cursor != result["cursor"]:
        raise ValueError("reviewer page changed; restart from its current cursor")
    for row in actions[after:after + limit]:
        if len(json.dumps({**result, "next_actions": [*result["next_actions"], row]}).encode()) > RESPONSE_BYTES - 8192:
            break
        result["next_actions"].append(row)
    result["more"] = len(ordered) > after + len(result["next_actions"])
    result["next_after"] = after + len(result["next_actions"]) if result["more"] else None
    return result
