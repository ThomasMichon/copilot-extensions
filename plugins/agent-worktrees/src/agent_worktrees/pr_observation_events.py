"""Stable ADO observation cursors; current votes are not chronological reviews."""

from __future__ import annotations

import base64
import json

from .pr_observation import Observation, fingerprint
from .pr_snapshot import REVIEW_STATE_EVENT


MAX_NEGATIVE_VOTES = 16


def historical_review_events(max_review_id, reviews, author, wanted):
    return [
        {"event": REVIEW_STATE_EVENT[review.state.upper()], "review": {
            "id": review.id, "state": review.state, "user": review.user,
            "submitted_at": review.submitted_at, "commit_id": review.commit_id,
        }}
        for review in sorted(reviews, key=lambda item: item.id)
        if review.id > max_review_id and (not author or review.user != author)
        and REVIEW_STATE_EVENT.get(review.state.upper()) in wanted
    ]


def cursor_for(observation: Observation, previous_cursor: str = "") -> str:
    """Capture current state while retaining a validated target's event high-water."""
    report = observation.to_dict()
    negative_votes = sorted({
        fingerprint((vote.actor_id, vote.vote)) for vote in observation.votes if vote.vote < 0
    })
    payload = {
        "version": 1,
        "target": [observation.provider, observation.api_base.rstrip("/"),
                   observation.repo, observation.number],
        "fingerprints": report["fingerprints"],
        "last_event": max((int(event.id) for event in observation.approval_events), default=0),
        "ready_for_human": report["ready_for_human"],
        # None means unknown, not empty: never truncate a set used for membership.
        "negative_votes": (negative_votes
                           if observation.complete and observation.votes_known
                           and len(negative_votes) <= MAX_NEGATIVE_VOTES else None),
    }
    if previous_cursor:
        previous = read_cursor(previous_cursor)
        if previous["target"] != payload["target"]:
            raise ValueError("PR observation cursor belongs to another target; capture a new cursor")
        # Missing or emptied history cannot revoke an event already observed.
        payload["last_event"] = max(payload["last_event"], previous["last_event"])
    def encode():
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode(),
        ).decode().rstrip("=")

    value = encode()
    if len(value) > 4096:
        # Actor-bound fallback evidence is optional; keep old large targets usable.
        payload.pop("negative_votes")
        value = encode()
    if len(value) > 4096:
        raise ValueError("invalid PR observation cursor: oversized")
    return value


def read_cursor(value: str) -> dict:
    if len(value) > 4096:
        raise ValueError("invalid PR observation cursor: oversized")
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid PR observation cursor") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("target"), list) or len(data["target"]) != 4
            or not isinstance(data.get("fingerprints"), dict)
            or type(data.get("last_event")) is not int or data["last_event"] < 0
            or type(data.get("ready_for_human")) is not bool):
        raise ValueError("invalid PR observation cursor")
    expected = {"votes", "approvals", "discussions", "policies", "gates"}
    if (set(data["fingerprints"]) != expected
            or any(not isinstance(item, str) or len(item) != 64
                   for item in data["fingerprints"].values())):
        raise ValueError("invalid PR observation cursor fingerprints")
    negative_votes = data.get("negative_votes")
    if negative_votes is not None and (
        not isinstance(negative_votes, list) or len(negative_votes) > MAX_NEGATIVE_VOTES
        or any(not isinstance(item, str) or len(item) != 64
               or any(char not in "0123456789abcdef" for char in item)
               for item in negative_votes)
        or len(set(negative_votes)) != len(negative_votes)
    ):
        raise ValueError("invalid PR observation cursor negative votes")
    return data


def events_since(value: str, observation: Observation, wanted: set[str]) -> list[dict]:
    """Prefer native history; infer rejection only from new actor/vote evidence.

    Legacy or over-budget cursors lack that evidence, so current votes can only
    signal review_changed, never an inferred chronological rejection.
    """
    if not value:
        return []
    previous = read_cursor(value)
    current = read_cursor(cursor_for(observation))
    if previous["target"] != current["target"]:
        raise ValueError("PR observation cursor belongs to another target; capture a new cursor")
    events = []
    for category, name in (
        ("votes", "review_changed"), ("approvals", "review_changed"), ("discussions", "threads_changed"),
        ("policies", "policies_changed"), ("gates", "gates_changed"),
    ):
        if (previous["fingerprints"][category] != current["fingerprints"][category] and name in wanted
                and not any(event["event"] == name for event in events)):
            events.append({"event": name})
    new_votes = [event for event in observation.approval_events if int(event.id) > previous["last_event"]]
    for event in new_votes:
        name = "approved" if event.vote >= 5 else "changes_requested" if event.vote < 0 else "review_changed"
        if name in wanted and (name != "review_changed"
                               or not any(event["event"] == name for event in events)):
            events.append({"event": name, "review": {
                "id": event.id, "user": event.actor_id, "state": name.upper(),
                "submitted_at": event.at, "commit_id": "",
            }})
    previous_negative = previous.get("negative_votes")
    current_negative = current.get("negative_votes")
    if (previous_negative is not None and current_negative is not None
            and set(current_negative) - set(previous_negative) and "changes_requested" in wanted
            and not any(event["event"] == "changes_requested" for event in events)):
        events.append({"event": "changes_requested"})
    if (current["ready_for_human"] and not previous["ready_for_human"]
            and "ready_for_human" in wanted):
        events.append({"event": "ready_for_human"})
    return events
