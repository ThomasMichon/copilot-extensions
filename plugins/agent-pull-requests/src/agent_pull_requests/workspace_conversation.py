"""Body-free projection of the existing normalized ADO observation, without IO."""

from __future__ import annotations

import json
import math

from .attention import Candidate, PRKey
from .conversation_journal import Observation
from .review_workspace import _digest, _generation, _instant, _scope


def conversation_key(scope: dict, namespace: str) -> PRKey:
    scope = _scope(scope)
    return PRKey(
        scope["provider"],
        scope["endpoint"],
        json.dumps([scope["project_id"], scope["repository_id"]], separators=(",", ":")),
        scope["number"],
        scope["principal_id"],
        namespace,
    )


def observation_interval(sections: dict) -> tuple[int, int] | None:
    """Conservative bounds over the data-bearing sections of a validated generation."""
    intervals = []
    for section in sections.values():
        if section["data"] is None:
            continue
        start, end = section["observed_from"], section["observed_to"]
        if start is None or end is None:
            return None
        start, end = _instant(start).timestamp(), _instant(end).timestamp()
        if start < 0 or end < start:
            raise ValueError("invalid workspace observation interval")
        intervals.append((start, end))
    if not intervals:
        return None
    return (
        math.floor(min(start for start, _ in intervals)),
        math.ceil(max(end for _, end in intervals)),
    )


def project_generation(
    generation: dict, namespace: str, previous: Observation | None
) -> Observation | None:
    """Missing candidate/state/time is unavailable, never an invented empty PR."""
    scope = _scope(generation["scope"])
    _generation(generation, scope)
    key = conversation_key(scope, namespace)
    if previous is not None and previous.key != key:
        raise ValueError("previous conversation differs from workspace scope")
    sections, revision = generation["sections"], generation["revision"]
    pull = sections["pull_request"]["data"] or {}
    state = {
        "active": "open",
        "completed": "merged",
        "abandoned": "closed-unmerged",
    }.get(pull.get("status"))
    if state is None or not revision["head_sha"] or not revision["target_sha"]:
        return None
    interval = observation_interval(sections)
    if interval is None:
        return None

    def evidence(name, data=None):
        section = sections[name]
        return {
            "data": section["data"] if data is None else data,
            "complete": section["complete"],
            "errors": section["errors"],
        }

    discussions = sections["discussions"]["data"] or []
    reviewers = sections["reviewers"]["data"] or []
    comments = [
        {"thread": thread.get("id"), "comment": comment}
        for thread in discussions
        for comment in thread.get("comments", [])
    ]
    comments_complete = sections["discussions"]["complete"] and all(
        "comments" in thread for thread in discussions
    )
    threads = [
        {name: value for name, value in thread.items() if name != "comments"}
        for thread in discussions
    ]
    author = (pull.get("createdBy") or {}).get("id")
    if previous is not None and author is None:
        author = previous.author
    reviewer_ids = {row["id"] for row in reviewers if row.get("id")}
    participants = {
        row["comment"]["author"]["id"]
        for row in comments
        if (row["comment"].get("author") or {}).get("id")
    }
    participants.update(reviewer_ids)
    if author:
        participants.add(author)
    roles = set()
    if author == scope["principal_id"]:
        roles.add("author")
    if scope["principal_id"] in reviewer_ids:
        roles.add("reviewer")
    if state == "merged" and (pull.get("closedBy") or {}).get("id") == scope["principal_id"]:
        roles.add("merger")
    if previous is not None:
        reviewer_ids.update(previous.reviewers)
        roles.update(previous.our_roles)
    complete = generation["complete"] and all(
        section["complete"] and not section["errors"] for section in sections.values()
    )
    digests = {
        "commits": _digest(
            {
                "revision": revision,
                "pull_request": evidence("pull_request"),
                "iterations": evidence("iterations"),
                "changes": evidence("changes"),
            }
        ),
        "comments": _digest(
            evidence(
                "discussions",
                [
                    {"thread": thread.get("id"), "comments": thread.get("comments")}
                    for thread in discussions
                ],
            )
        ),
        "threads": _digest(evidence("discussions", threads)),
        "checks": _digest(evidence("policies")),
        "reviews": _digest(evidence("reviewers")),
        "participants": _digest(
            {
                "ids": sorted(participants),
                "complete": complete,
            }
        ),
    }
    return Observation(
        key,
        Candidate(revision["head_sha"], revision["target_sha"]),
        # The journal's v1 integer-second bounds must contain the native interval.
        # Rounding inward would invent ordering between overlapping observations.
        *interval,
        state,
        complete,
        author,
        tuple(sorted(reviewer_ids)),
        tuple(sorted(roles)),
        "unknown",  # A current reviewer vote does not identify a reviewed candidate.
        previous.review_rounds if previous is not None else None,
        (
            ("commits", None),
            ("reviews", None),
            ("comments", len(comments) if comments_complete else None),
            ("threads", len(threads) if sections["discussions"]["complete"] else None),
            (
                "checks",
                len(sections["policies"]["data"]) if sections["policies"]["complete"] else None,
            ),
            (
                "participants",
                len(participants)
                if (
                    complete
                    and comments_complete
                    and author is not None
                    and all(row.get("id") for row in reviewers)
                    and all((row["comment"].get("author") or {}).get("id") for row in comments)
                )
                else None,
            ),
        ),
        tuple(digests.items()),
    )
