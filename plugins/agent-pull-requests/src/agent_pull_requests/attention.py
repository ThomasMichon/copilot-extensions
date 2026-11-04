"""Inert attention projections; acquisition, policy, storage and effects stay owned."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from urllib.parse import urlsplit


_ROLES = ("maintainer", "codeowner", "subject-matter-expert")
_PRIORITY = {
    "rebase": 60,
    "ci-rekick": 50,
    "follow-up": 40,
    "initial-review": 30,
    "merge-nudge": 20,
    "final-iteration": 10,
}


def _text(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("nonempty bounded identity text required")
    if len(value) > 2048:
        raise ValueError("identity exceeds its bound")


def _integer(value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid attention sequence or time")


@dataclass(frozen=True)
class PRKey:
    provider: str
    endpoint: str
    repository: str
    number: int
    account: str
    namespace: str

    def __post_init__(self) -> None:
        for value in (self.provider, self.endpoint, self.repository, self.account, self.namespace):
            _text(value)
        if self.provider not in ("github", "gitea", "azure-devops"):
            raise ValueError("unsupported attention provider")
        endpoint = urlsplit(self.endpoint)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or self.endpoint.endswith("/")
        ):
            raise ValueError("explicit canonical HTTPS endpoint required")
        _ = endpoint.port
        _integer(self.number, 1)


@dataclass(frozen=True)
class Candidate:
    head: str
    base: str

    def __post_init__(self) -> None:
        for value in (self.head, self.base):
            if not isinstance(value, str) or not re.fullmatch(
                r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value
            ):
                raise ValueError("full candidate object identities required")


@dataclass(frozen=True)
class Cursor:
    epoch: int
    sequence: int

    def __post_init__(self) -> None:
        _integer(self.epoch, 1)
        _integer(self.sequence)


@dataclass(frozen=True)
class Interest:
    role: str
    matches: bool | None
    consumer: str

    def __post_init__(self) -> None:
        _text(self.consumer)
        if self.role not in _ROLES or self.matches is not None and type(self.matches) is not bool:
            raise ValueError("explicit role and selector result required")


@dataclass(frozen=True)
class Facts:
    key: PRKey
    candidate: Candidate
    cursor: Cursor
    observed_at: int
    pending_since: int
    complete: bool
    state: str = "open"
    draft: bool = False
    conflict: bool = False
    ci_rekick: bool = False
    merge_nudge: bool = False
    final_iteration: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.key) is not PRKey
            or type(self.candidate) is not Candidate
            or type(self.cursor) is not Cursor
        ):
            raise ValueError("typed scoped attention evidence required")
        _integer(self.observed_at)
        _integer(self.pending_since)
        if self.pending_since > self.observed_at or self.state not in (
            "open",
            "merged",
            "closed-unmerged",
        ):
            raise ValueError("invalid attention observation")
        if any(
            type(value) is not bool
            for value in (
                self.complete,
                self.draft,
                self.conflict,
                self.ci_rekick,
                self.merge_nudge,
                self.final_iteration,
            )
        ):
            raise ValueError("explicit attention evidence flags required")


@dataclass(frozen=True)
class Handled:
    """Caller-supplied confirmed feedback baseline, not a receipt or authorization."""

    key: PRKey
    candidate: Candidate
    cursor: Cursor
    consumer: str
    role: str

    def __post_init__(self) -> None:
        _text(self.consumer)
        if self.role not in _ROLES:
            raise ValueError("invalid handled interest")
        if (
            type(self.key) is not PRKey
            or type(self.candidate) is not Candidate
            or type(self.cursor) is not Cursor
        ):
            raise ValueError("typed handled baseline required")


@dataclass(frozen=True)
class Attention:
    key: PRKey
    candidate: Candidate
    cursor: Cursor
    role: str
    consumer: str
    status: str
    reasons: tuple[str, ...]
    priority: int
    pending_since: int
    observed_at: int
    expires_at: int


def project(
    facts: Facts,
    interest: Interest,
    handled: Handled | None = None,
    *,
    now: int,
    max_age_seconds: int,
) -> Attention:
    """Project already admitted facts; no provider read, task or effect is performed."""
    if (
        type(facts) is not Facts
        or type(interest) is not Interest
        or (handled is not None and type(handled) is not Handled)
    ):
        raise ValueError("typed attention inputs required")
    _integer(now)
    _integer(max_age_seconds, 1)

    def result(status: str, *reasons: str) -> Attention:
        return Attention(
            facts.key,
            facts.candidate,
            facts.cursor,
            interest.role,
            interest.consumer,
            status,
            tuple(reasons),
            max((_PRIORITY.get(reason, 0) for reason in reasons), default=0),
            facts.pending_since,
            facts.observed_at,
            facts.observed_at + max_age_seconds,
        )

    if handled is not None and (
        handled.key != facts.key
        or handled.consumer != interest.consumer
        or handled.role != interest.role
    ):
        raise ValueError("handled baseline belongs to another PR/account/namespace/interest")
    if interest.matches is False:
        return result("excluded", "outside-selection")
    if interest.matches is None:
        return result("unavailable", "selection-unknown")
    if not facts.complete or not 0 <= now - facts.observed_at < max_age_seconds:
        return result("unavailable", "evidence-incomplete-or-stale")
    if handled is not None:
        if handled.cursor.epoch > facts.cursor.epoch or (
            handled.cursor.epoch == facts.cursor.epoch
            and handled.cursor.sequence > facts.cursor.sequence
        ):
            return result("unavailable", "observation-behind-feedback")
        if handled.cursor == facts.cursor and handled.candidate != facts.candidate:
            return result("unavailable", "candidate-cursor-mismatch")
    if facts.state != "open":
        return result("terminal", facts.state)
    if facts.draft:
        return result("waiting", "draft")
    if handled is not None and handled.cursor == facts.cursor:
        return result("waiting", "handled-through-cursor")
    reasons = []
    if facts.conflict:
        reasons.append("rebase")
    if facts.ci_rekick:
        reasons.append("ci-rekick")
    # These are recommendations from current policy evidence, never effect permits.
    if facts.merge_nudge:
        reasons.append("merge-nudge")
    if facts.final_iteration:
        reasons.append("final-iteration")
    if not reasons:
        reasons.append("initial-review" if handled is None else "follow-up")
    return result("actionable", *reasons)


def rank(
    items: Iterable[Attention],
    *,
    now: int,
    aging_seconds: int,
) -> tuple[Attention, ...]:
    """Age actionable items without mutating subscriptions, tasks or feedback."""
    _integer(now)
    _integer(aging_seconds, 1)
    selected = []
    seen = set()
    for item in items:
        if type(item) is not Attention:
            raise ValueError("typed attention projections required")
        if len(seen) >= 4096:
            raise ValueError("attention batch exceeds its bound")
        identity = (item.key, item.consumer, item.role)
        if identity in seen:
            raise ValueError("duplicate PR interest in attention batch")
        seen.add(identity)
        if item.status == "actionable":
            if not item.observed_at <= now < item.expires_at:
                continue
            if item.pending_since > now:
                raise ValueError("future attention baseline")
            selected.append(item)

    def order(item: Attention) -> tuple:
        age = (now - item.pending_since) // aging_seconds
        key = item.key
        return (
            -(item.priority + age),
            item.pending_since,
            key.provider,
            key.endpoint,
            key.repository,
            key.number,
            key.account,
            key.namespace,
            item.consumer,
            item.role,
        )

    return tuple(sorted(selected, key=order))
