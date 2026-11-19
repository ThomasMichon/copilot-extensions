"""Continuing cache subscriptions under the existing ReplayStore transaction owner."""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .repository_contract import MAX_REGISTRATIONS, PROTOCOL, RepositoryIdentity

if TYPE_CHECKING:
    from .repository_projection import RepositoryProjection

RESPONSE_BYTES = 1_048_576
OPERATIONS: dict[str, tuple[set[str], set[str]]] = {
    "query": (set(), {"after", "limit", "number"}),
    "next-actions": ({"reviewer"}, {"after", "limit", "cursor", "max_age_seconds"}),
    "subscribe": ({"request_id", "ttl"}, {"after", "not_after"}),
    "inspect-request": ({"request_id"}, set()),
    "inspect": ({"registration_id"}, set()),
    "events": ({"registration_id"}, {"after", "limit"}),
    "acknowledge": ({"registration_id", "event_id"}, set()),
    "cancel": ({"registration_id"}, set()),
}


class RepositoryHistoryGap(ValueError):
    """The original requested cursor cannot be resumed; explicit baseline recovery is needed."""


class RepositoryRequestConflict(ValueError):
    """An original identity cannot be reused with changed intent or scope."""


class RepositoryRequestExpired(ValueError):
    """Creation crossed its original cutoff; inspect the original request."""


def _integer(value: object, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid bounded repository integer")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(
        ord(char) < 32 for char in value
    ):
        raise ValueError("invalid repository identity")
    return value


def _status(row: dict[str, Any], now: int) -> str:
    return "cancelled" if row["cancelled"] else (
        "expired" if row["expires_at"] <= now else "active"
    )


def _page(
    state: dict[str, Any], identity: RepositoryIdentity, after: int = 0, limit: int = 16,
    number: int | None = None, now: int = 0, *, budget: int | None = None,
) -> dict[str, Any]:
    budget = RESPONSE_BYTES - 8192 if budget is None else budget
    row = next((row for row in state["repositories"]
                if RepositoryIdentity.parse(row["identity"]).key == identity.key), None)
    if row is None:
        from .repository_projection import _new_repository
        row = _new_repository(identity)
    rows = sorted((pull for pull in row["pulls"] if pull["number"] > after
                   and (number is None or pull["number"] == number)), key=lambda pull: pull["number"])
    result: dict[str, Any] = {"identity": identity.document(), "inventory": row["inventory"],
              "pulls": [], "more": False, "cursor": state["next_sequence"] - 1, "now": now}
    for pull in rows[:limit]:
        candidate = {**result, "pulls": [*result["pulls"], pull]}
        if len(json.dumps(candidate, allow_nan=False).encode()) > budget:
            break
        result["pulls"].append(pull)
    result["more"] = len(rows) > len(result["pulls"])
    return result


class RepositorySubscriptions:
    """Read-only forge profile; only local subscription/delivery metadata mutates."""

    def __init__(self, projection: RepositoryProjection) -> None:
        if not projection.store.repository_continuing:
            raise ValueError("continuing repository storage requires explicit fresh provisioning")
        self.projection, self.store = projection, projection.store

    def execute(
        self, key: str, consumer: str, operation: str, payload: dict[str, Any], **authority: Any,
    ) -> dict[str, Any]:
        """Validate one admitted operation and reconcile its original identities."""
        if key not in self.projection.identities or operation not in OPERATIONS or (
            not isinstance(payload, dict)
        ):
            raise ValueError("unsupported repository request")
        required, optional = OPERATIONS[operation]
        if not required <= payload.keys() or payload.keys() - required - optional:
            raise ValueError("invalid repository request fields")
        consumer = _text(consumer)
        identity = self.projection.identities[key]
        if operation == "query":
            return self.projection.query(key, **payload, **authority)
        if operation == "next-actions":
            if not self.store.repository_activity:
                raise ValueError("reviewer actions require selected activity storage")
            from .repository_reviewer import page
            return self.projection._transaction(
                lambda state: page(
                    state, identity, consumer, now=int(self.store.clock()), **payload,
                ),
                **authority,
            )

        creation_cutoff: int | None = None
        checkpoint = authority.get("checkpoint")
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("invalid request checkpoint")

        def check_creation() -> None:
            if checkpoint is not None:
                checkpoint()
            if creation_cutoff is not None and self.store.clock() >= creation_cutoff:
                raise RepositoryRequestExpired("original subscription creation cutoff elapsed")

        def transact(document: dict[str, Any], commit: Callable[[], None]) -> dict[str, Any]:
            nonlocal creation_cutoff
            state = document["repository_cache"]
            now = int(self.store.clock())
            changed = False
            result: dict[str, Any]
            if operation == "subscribe":
                request_id = _text(payload["request_id"])
                request: dict[str, Any] = {"after": payload.get("after"),
                           "ttl": _integer(payload["ttl"], 1, 86400)}
                if request["after"] is not None:
                    _integer(request["after"])
                if "not_after" in payload:
                    request["not_after"] = _integer(payload["not_after"], 1)
                existing = next((row for row in state["registrations"]
                                 if row["consumer"] == consumer and row["request_id"] == request_id), None)
                if existing is not None:
                    if existing["target"] != key or existing["request"] != request:
                        raise RepositoryRequestConflict("original subscription intent differs")
                    row = existing
                else:
                    creation_cutoff = request.get("not_after")
                    if creation_cutoff is not None:
                        check_creation()
                    after = state["next_sequence"] - 1 if request["after"] is None else request["after"]
                    floor = state["events"][0]["sequence"] - 1 if state["events"] else state["next_sequence"] - 1
                    if after < floor:
                        raise RepositoryHistoryGap("query a new baseline before a new subscription")
                    if after >= state["next_sequence"]:
                        raise ValueError("future repository cursor")
                    state["registrations"] = [old for old in state["registrations"] if not (
                        old["expires_at"] + 86400 < now
                        and old["delivered"] <= old["acknowledged"]
                    )]
                    if len(state["registrations"]) >= MAX_REGISTRATIONS:
                        raise RepositoryRequestConflict("subscription capacity reached")
                    if not any(RepositoryIdentity.parse(item["identity"]).key == key
                               for item in state["repositories"]):
                        from .repository_projection import _new_repository
                        state["repositories"].append(_new_repository(identity))
                    row = {
                        "registration_id": uuid.uuid4().hex, "request_id": request_id,
                        "consumer": consumer, "target": key,
                        "expires_at": min(now + request["ttl"], creation_cutoff) if (
                            creation_cutoff is not None
                        ) else now + request["ttl"],
                        "acknowledged": after, "cancelled": False, "request": request,
                        "delivered": after, "acknowledged_event": None,
                    }
                    state["registrations"].append(row)
                    changed = True
                status = _status(row, now)
                result = {"status": status, "subscription": copy.deepcopy(row), "baseline": None}
                overhead = len(json.dumps(result, allow_nan=False).encode()) - len(b"null")
                result["baseline"] = _page(
                    state, identity, now=now, budget=RESPONSE_BYTES - 8192 - overhead,
                )
            elif operation == "inspect-request":
                request_id = _text(payload["request_id"])
                found = next((row for row in state["registrations"] if (
                    row["request_id"], row["consumer"], row["target"]
                ) == (request_id, consumer, key)), None)
                result = {
                    "status": _status(found, now) if found is not None else "not_found",
                    "request_id": request_id, "subscription": copy.deepcopy(found), "now": now,
                }
            else:
                registration_id = _text(payload["registration_id"])
                row = next((row for row in state["registrations"] if (
                    row["registration_id"], row["consumer"], row["target"]
                ) == (registration_id, consumer, key)), None)
                if row is None:
                    raise KeyError("subscription not found in admitted scope")
                status = _status(row, now)
                if operation == "inspect":
                    result = {"status": status, "subscription": copy.deepcopy(row)}
                elif operation == "cancel":
                    changed = not row["cancelled"]
                    row["cancelled"] = True
                    result = {"status": "cancelled", "registration_id": registration_id}
                elif operation == "events":
                    after = _integer(payload.get("after", row["acknowledged"]))
                    limit = _integer(payload.get("limit", 16), 1, 16)
                    if not row["acknowledged"] <= after <= row["delivered"]:
                        raise ValueError("event cursor must retain the admitted delivery position")
                    events = [event for event in state["events"] if (
                        event["target"] == key and event["sequence"] > after
                        and (status == "active" or event["sequence"] <= row["delivered"])
                    )]
                    batch: list[dict[str, Any]] = []
                    result = {"status": status, "registration_id": registration_id,
                              "events": batch, "more": False}
                    for event in events[:limit]:
                        candidate = {**result, "events": [*batch, event]}
                        if len(json.dumps(candidate, allow_nan=False).encode()) > RESPONSE_BYTES - 8192:
                            break
                        batch.append(event)
                    if batch and batch[-1]["sequence"] > row["delivered"]:
                        row["delivered"] = batch[-1]["sequence"]
                        changed = True
                    result["more"] = len(events) > len(batch)
                else:
                    event_id = _text(payload["event_id"])
                    if row["acknowledged_event"] == event_id:
                        result = {"status": "acknowledged", "event_id": event_id}
                    else:
                        event = next((event for event in state["events"]
                                      if event["event_id"] == event_id and event["target"] == key), None)
                        if event is None:
                            raise RepositoryHistoryGap("original event no longer retained")
                        if event["sequence"] > row["delivered"]:
                            raise ValueError("event was not admitted for delivery")
                        if event["sequence"] > row["acknowledged"]:
                            row["acknowledged"] = event["sequence"]
                            row["acknowledged_event"] = event_id
                            changed = True
                        result = {"status": "acknowledged", "event_id": event_id}
            if len(json.dumps(result, allow_nan=False).encode()) > RESPONSE_BYTES - 8192:
                raise ValueError("repository response exceeds its budget")
            if changed:
                commit()
            return result
        return self.store._review_transaction(
            transact, staged=True, persist=False, **{
                **authority, "checkpoint": check_creation,
            },
        )


def contract(*, reviewer_actions: bool = False) -> dict[str, object]:
    """Declare executing opt-in cache/continuing support, not package equality."""
    result = {"protocol": PROTOCOL, "storage": "repository_cache_version:2",
            "continuing": True, "effects": "local-subscription-state",
            "response_bytes": RESPONSE_BYTES, "forge_writes": False, "task_emissions": False}
    result["request_recovery"] = {
        "protocol": "agent-pull-requests.repository-request-recovery/v1",
        "inspection": "inspect-request", "cutoff": "not_after",
        "clock": "unix-seconds", "expiry": "min-ttl-cutoff",
    }
    if reviewer_actions:
        from .repository_reviewer import capability
        result["reviewer_actions"] = capability()
    return result
