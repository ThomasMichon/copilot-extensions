"""Bounded ephemeral waiter evidence, independent of retained timeline events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from .companion_timeline_contract import Availability, Completeness, TimelineContractError
from .companion_worktree_codec import encoded, object_fields, stamp

CAPABILITY = "bridge-session-current-requests-v1"
MAX_BYTES = 8 * 1024
FIELDS = frozenset(
    "version client_incarnation revision availability completeness reason observed_at requests".split()
)
UNKNOWN_REASONS = frozenset({
    "client-disconnected", "unsupported-transport", "producer-replaced", "unsupported-source",
    "no-current-client", "session-unavailable", "session-mismatch",
})


@dataclass(frozen=True, slots=True)
class CurrentRequestState:
    client_incarnation: str | None
    revision: int | None
    availability: Availability
    completeness: Completeness
    reason: str
    observed_at: datetime
    requests: tuple[tuple[str, str], ...]

    @property
    def content(self) -> tuple[Any, ...]:
        return (
            self.client_incarnation, self.revision, self.availability,
            self.completeness, self.reason, self.requests,
        )

    def summary(self) -> str:
        if self.availability is not Availability.OBSERVED:
            return f"Current input unknown ({self.reason}); answerability unknown."
        inputs = sum(kind == "input" for _identity, kind in self.requests)
        permissions = sum(kind == "permission" for _identity, kind in self.requests)
        if self.completeness is Completeness.PARTIAL:
            return (
                f"Listed pending waiters: {inputs} input, {permissions} permission; "
                "partial coverage, absence unknown; answerability unknown."
            )
        if not self.requests:
            return "No supported local waiters at this observation; answerability unknown."
        return (
            f"Observed pending waiters: {inputs} input, {permissions} permission; "
            "complete local ACP observation; answerability unknown."
        )


def _identity(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def decode_current_requests(value: Any) -> CurrentRequestState:
    body = object_fields(value, FIELDS)
    if len(encoded(body).encode("utf-8")) > MAX_BYTES:
        raise TimelineContractError("current request state exceeds its byte bound")
    availability, completeness, reason = (
        body["availability"], body["completeness"], body["reason"],
    )
    if (
        type(body["version"]) is not int or body["version"] != 1
        or not isinstance(reason, str)
        or not (
            (availability == "unknown" and completeness == "unknown" and reason in UNKNOWN_REASONS)
            or (availability == "observed" and completeness == "complete" and reason == "local-acp-waiters")
            or (availability == "observed" and completeness == "partial"
                and reason in {"waiter-budget", "waiter-coverage"})
        )
    ):
        raise TimelineContractError("invalid current request qualification")
    incarnation, revision = body["client_incarnation"], body["revision"]
    if not (
        (_identity(incarnation) and type(revision) is int and revision > 0)
        or (availability == "unknown" and incarnation is None and revision is None)
    ):
        raise TimelineContractError("invalid current request revision identity")
    observed = body["observed_at"]
    if not isinstance(observed, str) or len(observed) > 64:
        raise TimelineContractError("invalid current request observation time")
    observed_at = stamp(observed)
    if observed_at is None:
        raise TimelineContractError("current request observation time is missing")
    requests = body["requests"]
    if (
        not isinstance(requests, list) or len(requests) > 32
        or (availability == "unknown" and requests)
    ):
        raise TimelineContractError("invalid current request inventory")
    seen = set()
    result = []
    for item in requests:
        item = object_fields(item, {"occurrence_id", "kind"})
        if (
            not _identity(item["occurrence_id"]) or item["kind"] not in ("input", "permission")
            or item["occurrence_id"] in seen
        ):
            raise TimelineContractError("invalid current request occurrence")
        seen.add(item["occurrence_id"])
        result.append((item["occurrence_id"], item["kind"]))
    return CurrentRequestState(
        incarnation, revision, Availability(availability), Completeness(completeness),
        reason, observed_at, tuple(result),
    )


class CurrentRequestProjection:
    def __init__(self) -> None:
        self.current: CurrentRequestState | None = None
        self._last: CurrentRequestState | None = None

    def invalidate(self) -> None:
        self.current = None

    def accept(self, state: CurrentRequestState) -> bool:
        prior = self._last
        if prior is not None and prior.client_incarnation is not None and (
            prior.client_incarnation == state.client_incarnation
        ):
            if state.revision is None or prior.revision is None or state.revision < prior.revision or (
                state.revision == prior.revision and state.content != prior.content
            ):
                raise TimelineContractError("current request revision regressed or changed content")
        changed = self.current is None or self.current.content != state.content
        self.current = state
        if state.client_incarnation is not None:
            self._last = state
        return changed
