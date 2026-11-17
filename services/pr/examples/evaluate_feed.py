"""Finite read-only comparison with an independently supplied native observation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from agent_pull_requests.repository_activity import review_evidence
from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.repository_projection import validate_identity
from agent_pull_requests.repository_reviewer import PROTOCOL
from agent_pull_requests.watch_contract import PRSnapshot

Status = Literal["agree", "different", "unavailable", "stale"]
FIELDS = ("head_sha", "base_sha", "pr_state", "merged", "draft", "author", "mergeable")


@dataclass(frozen=True)
class Reference:
    """The acquisition/accounting owner supplies the identity and observation time."""

    identity: RepositoryIdentity
    snapshot: PRSnapshot
    observed_at: int


@dataclass(frozen=True)
class Comparison:
    number: int
    facts: dict[str, Status]
    action: Status
    reason: str


def compare(
    pulls: dict[str, Any], feed: dict[str, Any], reference: Reference, *,
    identity: RepositoryIdentity, consumer: str, reviewer: str, number: int,
    now: int, max_age_seconds: int = 1800,
) -> Comparison:
    """Compare one selected PR, never fetch a provider or declare repository-wide agreement."""
    if (
        reference.identity != identity or pulls.get("identity") != identity.document()
        or feed.get("identity") != identity.document() or feed.get("protocol") != PROTOCOL
        or feed.get("consumer") != consumer or feed.get("reviewer") != reviewer
        or type(number) is not int or number < 1
        or type(now) is not int or now < 0 or type(max_age_seconds) is not int
        or not 1 <= max_age_seconds <= 86400
    ):
        raise ValueError("comparison scope differs")
    native = validate_identity(reference.snapshot, identity, number)

    def unavailable(reason: str, status: Status = "unavailable") -> Comparison:
        return Comparison(number, dict.fromkeys(FIELDS, status), status, reason)

    if type(reference.observed_at) is not int or not 0 <= reference.observed_at <= now:
        return unavailable("reference time unavailable")
    if not 0 <= now - reference.observed_at < max_age_seconds:
        return unavailable("reference expired", "stale")
    if pulls["cursor"] != feed["event_cursor"]:
        return unavailable("cache views moved; read a coherent selection")
    if any(page["inventory"]["outcome"] != "complete" for page in (pulls, feed)):
        return unavailable("cache inventory incomplete")
    selected = [row for row in pulls["pulls"] if row["number"] == number]
    if len(selected) != 1 or not selected[0]["member"] or selected[0]["error"]:
        return unavailable("selected cache member unavailable")
    row = selected[0]
    if row["facts"] is None or type(row["observed_at"]) is not int or row["observed_at"] > now:
        return unavailable("cached facts unavailable")
    if not 0 <= now - row["observed_at"] < max_age_seconds:
        return unavailable("cached facts expired", "stale")
    facts: dict[str, Status] = {}
    for field in FIELDS:
        cached, direct = row["facts"].get(field), native.get(field)
        if cached is None or direct is None or cached == "" or direct == "":
            facts[field] = "unavailable"
        else:
            facts[field] = "agree" if cached == direct else "different"
    if any(item["pr"] == number for item in feed["unavailable"]):
        return Comparison(number, facts, "unavailable", "feed declares unavailable evidence")
    if any(facts[field] != "agree" for field in ("head_sha", "base_sha")):
        return Comparison(number, facts, "unavailable", "candidate differs or is unavailable")
    actions = [item for item in feed["next_actions"] if item["pr"] == number]
    if len(actions) > 1:
        return Comparison(number, facts, "unavailable", "ambiguous action selection")
    if not actions and feed["more"]:
        return Comparison(number, facts, "unavailable", "action page incomplete")
    if actions and (
        actions[0]["candidate"] != {
            "head": native["head_sha"], "base": native.get("base_sha"),
        }
    ):
        return Comparison(number, facts, "unavailable", "action candidate differs")
    if actions and not actions[0]["observed_at"] <= now < actions[0]["expires_at"]:
        return Comparison(number, facts, "stale", "action evidence expired")
    author = native.get("author")
    if not isinstance(author, str) or not author.strip():
        return Comparison(number, facts, "unavailable", "reference author unavailable")
    ineligible = (
        native["pr_state"] != "open" or native["merged"] or native["draft"]
        or author.casefold() == reviewer.casefold()
    )
    if ineligible:
        return Comparison(number, facts, "different" if actions else "agree",
                          "reference is not an eligible reviewer target")
    if actions and actions[0]["action"] == "rebase":
        if native["mergeable"] is None:
            return Comparison(number, facts, "unavailable", "reference mergeability unavailable")
        matches = (
            native["mergeable"] is False and actions[0]["actor_role"] == "author"
            and actions[0]["actor"] == author and actions[0]["reason"] == "stale_branch"
        )
        return Comparison(number, facts, "agree" if matches else "different",
                          "author-owned conflict comparison")
    if native["mergeable"] is False:
        return Comparison(number, facts, "different", "reference requires an author-owned rebase")
    if native.get("reviews_complete") is not True or not isinstance(native.get("reviews"), list):
        return Comparison(number, facts, "unavailable", "reference review history incomplete")
    reviews = [item for item in native.get("reviews", [])
               if item.get("state") not in ("PENDING", "REQUEST_REVIEW")]
    if any(review_evidence(item, reference.observed_at) is None for item in reviews):
        return Comparison(number, facts, "unavailable", "reference review evidence unavailable")
    submitted = [item for item in reviews if item["user"].casefold() == reviewer.casefold()]
    if not submitted:
        matches = len(actions) == 1 and (
            actions[0]["action"], actions[0]["reason"],
            actions[0]["actor_role"], actions[0]["actor"],
        ) == ("initial_verdict", "author_create", "reviewer", reviewer)
        return Comparison(number, facts, "agree" if matches else "different",
                          "initial-review comparison")
    if actions and actions[0]["action"] == "initial_verdict":
        return Comparison(number, facts, "different", "reference contains a submitted review")
    return Comparison(number, facts, "unavailable",
                      "a current reference cannot establish causal follow-up ordering")
