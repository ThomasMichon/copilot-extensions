"""Continuing cache subscriptions under the existing ReplayStore transaction owner."""

import copy
import json
import uuid

from .repository_contract import MAX_REGISTRATIONS, PROTOCOL, RepositoryIdentity

RESPONSE_BYTES = 1_048_576
OPERATIONS = {
    "query": (set(), {"after", "limit", "number"}),
    "subscribe": ({"request_id", "ttl"}, {"after"}),
    "inspect": ({"registration_id"}, set()),
    "events": ({"registration_id"}, {"after", "limit"}),
    "acknowledge": ({"registration_id", "event_id"}, set()),
    "cancel": ({"registration_id"}, set()),
}


class RepositoryHistoryGap(ValueError):
    """The original requested cursor cannot be resumed; explicit baseline recovery is needed."""


class RepositoryRequestConflict(ValueError):
    """An original identity cannot be reused with changed intent or scope."""


def _integer(value, minimum=0, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid bounded repository integer")
    return value


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(
        ord(char) < 32 for char in value
    ):
        raise ValueError("invalid repository identity")
    return value


def _page(state, identity, after=0, limit=16, number=None, now=0):
    row = next((row for row in state["repositories"]
                if RepositoryIdentity.parse(row["identity"]).key == identity.key), None)
    if row is None:
        from .repository_projection import _new_repository
        row = _new_repository(identity)
    rows = sorted((pull for pull in row["pulls"] if pull["number"] > after
                   and (number is None or pull["number"] == number)), key=lambda pull: pull["number"])
    result = {"identity": identity.document(), "inventory": row["inventory"],
              "pulls": [], "more": False, "cursor": state["next_sequence"] - 1, "now": now}
    for pull in rows[:limit]:
        candidate = {**result, "pulls": [*result["pulls"], pull]}
        if len(json.dumps(candidate).encode()) > RESPONSE_BYTES - 8192:
            break
        result["pulls"].append(pull)
    result["more"] = len(rows) > len(result["pulls"])
    return result


class RepositorySubscriptions:
    """Read-only forge profile; only local subscription/delivery metadata mutates."""

    def __init__(self, projection):
        if not projection.store.repository_continuing:
            raise ValueError("continuing repository storage requires explicit fresh provisioning")
        self.projection, self.store = projection, projection.store

    def execute(self, key, consumer, operation, payload, **authority):
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

        def transact(document, commit):
            state = document["repository_cache"]
            now = int(self.store.clock())
            changed = False
            if operation == "subscribe":
                request_id = _text(payload["request_id"])
                request = {"after": payload.get("after"),
                           "ttl": _integer(payload["ttl"], 1, 86400)}
                if request["after"] is not None:
                    _integer(request["after"])
                existing = next((row for row in state["registrations"]
                                 if row["consumer"] == consumer and row["request_id"] == request_id), None)
                if existing is not None:
                    if existing["target"] != key or existing["request"] != request:
                        raise RepositoryRequestConflict("original subscription intent differs")
                    row = existing
                else:
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
                        "consumer": consumer, "target": key, "expires_at": now + request["ttl"],
                        "acknowledged": after, "cancelled": False, "request": request,
                        "delivered": after, "acknowledged_event": None,
                    }
                    state["registrations"].append(row)
                    changed = True
                status = "cancelled" if row["cancelled"] else (
                    "expired" if row["expires_at"] <= now else "active"
                )
                result = {"status": status, "subscription": copy.deepcopy(row),
                          "baseline": _page(state, identity, now=now)}
            else:
                registration_id = _text(payload["registration_id"])
                row = next((row for row in state["registrations"] if (
                    row["registration_id"], row["consumer"], row["target"]
                ) == (registration_id, consumer, key)), None)
                if row is None:
                    raise KeyError("subscription not found in admitted scope")
                status = "cancelled" if row["cancelled"] else (
                    "expired" if row["expires_at"] <= now else "active"
                )
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
                    batch = []
                    for event in events[:limit]:
                        candidate = {"status": status, "events": [*batch, event], "more": True}
                        if len(json.dumps(candidate).encode()) > RESPONSE_BYTES - 8192:
                            break
                        batch.append(event)
                    if batch and batch[-1]["sequence"] > row["delivered"]:
                        row["delivered"] = batch[-1]["sequence"]
                        changed = True
                    result = {"status": status, "registration_id": registration_id,
                              "events": batch, "more": len(events) > len(batch)}
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
            transact, staged=True, persist=False, **authority,
        )


def contract() -> dict[str, object]:
    """Declare executing opt-in cache/continuing support, not package equality."""
    return {"protocol": PROTOCOL, "storage": "repository_cache_version:2",
            "continuing": True, "effects": "local-subscription-state",
            "response_bytes": RESPONSE_BYTES, "forge_writes": False, "task_emissions": False}
