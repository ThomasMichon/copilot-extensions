"""Bounded opt-in GitHub snapshot batches; scalar callers remain unchanged."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time
from typing import Any

from ..pr_snapshot_payload import snapshot_payload
from .base import run_cli
from .github_batch_decode import Member, Quota, response, timestamp
from .github_batch_http import Reply, read_query
from .github_batch_query import API_BASE, ENDPOINT, validate_selection
from .observation_credentials import (
    MAX_SNAPSHOT_REQUESTS, CredentialSource, ObservationCredentialError, ObservationCredentials,
)


@dataclass(frozen=True)
class Backoff:
    resource: str
    not_before: float
    reason: str


def _backoff(reply: Reply) -> Backoff | None:
    now = time.time()
    if reply.retry_at is not None:
        return Backoff("shared-secondary", max(reply.retry_at, now + 60), "retry-after")
    errors = reply.value.get("errors", []) if isinstance(reply.value, dict) else []
    limited = isinstance(errors, list) and any(
        isinstance(error, dict) and (
            error.get("type") == "RATE_LIMITED"
            or isinstance(error.get("extensions"), dict)
            and error["extensions"].get("code") in ("RATE_LIMITED", "RATE_LIMIT")
        )
        for error in errors
    )
    if reply.status == 200 and not limited:
        return None
    if reply.primary_exhausted and reply.primary_reset is not None and reply.primary_reset > now:
        return Backoff("graphql-primary", reply.primary_reset, "primary-exhausted")
    if reply.status in (403, 429) or limited:
        # Without valid primary evidence, conservatively retain the shared hold.
        return Backoff("shared-secondary", now + 60, "provider-throttled")
    return None


def snapshots(
    repository: str, numbers: tuple[int, ...] | list[int], *,
    credential_source: CredentialSource, api_base: str = API_BASE,
    deadline: float | None = None, max_requests: int = MAX_SNAPSHOT_REQUESTS,
) -> dict[str, Any]:
    """Return one typed outcome per selected PR, without retries or REST fallback."""
    numbers = validate_selection(repository, numbers, api_base)
    now = time.monotonic()
    deadline = now + 60 if deadline is None else deadline
    if (
        not callable(credential_source) or type(max_requests) is not int
        or not 1 <= max_requests <= MAX_SNAPSHOT_REQUESTS
        or type(deadline) not in (int, float) or not math.isfinite(deadline)
        or deadline <= now or deadline > now + 60
    ):
        raise ValueError("invalid snapshot batch operation budget")
    credentials = ObservationCredentials(
        credential_source, provider="github", api_base=API_BASE, repository=repository,
        max_requests=max_requests,
    )
    members = [Member(number) for number in numbers]
    quota: Quota | None = None
    backoff: Backoff | None = None
    stop_reason = ""
    attempts = points = 0
    while active := [member for member in members if not member.reason and not member.complete]:
        if attempts >= max_requests or time.monotonic() >= deadline:
            stop_reason = "operation-budget-exhausted"
            break
        try:
            attempts += 1
            reply = read_query(
                repository, tuple(member.request() for member in active),
                credentials=credentials, deadline=deadline, invoke=run_cli,
            )
            backoff = _backoff(reply)
            if backoff is not None:
                stop_reason = backoff.reason
                break
            if reply.status != 200:
                stop_reason = "provider-unavailable"
                break
            selected, failed, quota = response(reply.value, repository, len(active))
            points += quota.cost
            reset = timestamp(quota.reset_at)
            if reset <= time.time():
                raise ValueError("stale batch quota reset")
            for index, member in enumerate(active):
                if index in failed:
                    member.reason = "provider-member-error"
                    continue
                try:
                    member.append(selected[f"p{index}"])
                except (ValueError, TypeError, AttributeError, KeyError):
                    member.reason = "invalid-member-evidence"
            if quota.remaining == 0:
                backoff = Backoff("graphql-primary", reset, "primary-exhausted")
                stop_reason = backoff.reason
                break
        except ObservationCredentialError:
            stop_reason = "credential-or-transport-unavailable"
            break
        except (ValueError, TypeError, AttributeError, KeyError):
            stop_reason = "invalid-provider-evidence"
            break
    results = []
    for member in members:
        value = None
        if time.monotonic() >= deadline:
            member.reason = "operation-budget-exhausted"
        elif member.complete:
            try:
                snapshot = member.snapshot()
                value = snapshot_payload(snapshot, repository, member.number, "github", API_BASE)
                value.update(labels=list(snapshot.labels), check_failures_omitted=snapshot.check_failures_omitted)
            except (ValueError, TypeError, AttributeError, KeyError):
                member.reason = "invalid-member-evidence"
        results.append({
            "pr": member.number, "outcome": "complete" if value is not None else "unavailable",
            "reason": "" if value is not None else member.reason or stop_reason or "incomplete-member",
            "snapshot": value,
        })
    return {
        "schema": "agent-worktrees.pr-snapshot-batch", "version": 1,
        "provider": "github", "api_base": API_BASE, "endpoint": ENDPOINT, "repo": repository,
        "results": results, "quota": asdict(quota) if quota is not None else None,
        "backoff": asdict(backoff) if backoff is not None else None,
        "credential_acquisitions": credentials.requests, "query_attempts": attempts,
        "validated_primary_points": points, "stop_reason": stop_reason,
    }
