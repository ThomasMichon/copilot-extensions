"""Owned immutable filter demand, using ReplayStore transaction and delivery custody."""

import copy
import json
import uuid

from pr_filters import Expression, PROTOCOL as FILTER_PROTOCOL

from .filtered_state import release_inactive, text, trim
from .repository_contract import MAX_REGISTRATIONS
from .repository_subscriptions import (
    RESPONSE_BYTES, RepositoryHistoryGap, RepositoryRequestConflict, _integer,
)

PROTOCOL = "agent-pull-requests.filtered-population/v1"
OPERATIONS = {
    "subscribe": ({"request_id", "ttl"}, {"filter", "definition"}),
    "query": ({"registration_id"}, {"after", "limit"}),
    "inspect": ({"registration_id"}, set()),
    "renew": ({"registration_id", "expires_at"}, set()),
    "cancel": ({"registration_id"}, set()),
    "events": ({"registration_id"}, {"after", "limit"}),
    "acknowledge": ({"registration_id", "event_id"}, set()),
}


def contract() -> dict[str, object]:
    return {
        "protocol": PROTOCOL, "filter_protocol": FILTER_PROTOCOL,
        "storage": "repository_cache_version:3", "continuing": True,
        "effects": "local-subscription-state", "forge_writes": False, "task_emissions": False,
        "response_bytes": RESPONSE_BYTES, "renewal": "absolute-expiry/v1",
        "receipt_retention_seconds": 86400,
    }


def status(row, now):
    return "cancelled" if row["cancelled"] else "expired" if row["expires_at"] <= now else "active"


def page(state, row, identity, *, after=0, limit=16):
    _integer(after)
    _integer(limit, 1, 16)
    repository = next(r for r in state["repositories"] if r["identity"] == identity.document())
    pulls = sorted((p for p in repository["pulls"] if p["number"] in row["members"] and p["number"] > after),
                   key=lambda p: p["number"])
    result = {
        "identity": identity.document(), "inventory": copy.deepcopy(row["coverage"]),
        "pulls": [], "more": False, "cursor": state["next_sequence"] - 1,
        "filter_revision": Expression.parse(row["request"]["filter"]).revision,
    }
    for pull in pulls[:limit]:
        if len(json.dumps({**result, "pulls": [*result["pulls"], pull]}).encode()) > RESPONSE_BYTES - 16384:
            break
        result["pulls"].append(copy.deepcopy(pull))
    result["more"] = len(pulls) > len(result["pulls"])
    return result


class FilteredSubscriptions:
    def __init__(self, projection):
        if not projection.store.repository_filtered:
            raise ValueError("filtered subscriptions require explicit generation-three storage")
        self.projection, self.store = projection, projection.store

    def execute(self, key, consumer, operation, payload, **authority):
        if key not in self.projection.identities or operation not in OPERATIONS or not isinstance(payload, dict):
            raise ValueError("unsupported filtered request")
        required, optional = OPERATIONS[operation]
        if not required <= payload.keys() or payload.keys() - required - optional:
            raise ValueError("invalid filtered request fields")
        text(consumer)
        identity = self.projection.identities[key]
        if operation == "subscribe":
            text(payload["request_id"])
            ttl = _integer(payload["ttl"], 1, 86400)
            if ("filter" in payload) == ("definition" in payload):
                raise ValueError("select exactly one expression or named definition")
            definition = payload.get("definition")
            if definition is not None:
                text(definition)

        def transact(document, commit):
            state = document["repository_cache"]
            now = int(self.store.clock())
            changed = False
            if operation == "subscribe":
                row = next((r for r in state["registrations"] if (
                    r["consumer"], r["request_id"]
                ) == (consumer, payload["request_id"])), None)
                if row is not None:
                    # A retry of a named request retains its admitted immutable
                    # definition even if the configured definition has changed.
                    original = row["request"]
                    same = original["ttl"] == ttl and (
                        definition is not None and original["definition"] == definition
                        or definition is None and original["definition"] is None
                        and original["filter"] == Expression.parse(payload["filter"]).document()
                    )
                    if row["target"] != key or not same:
                        raise RepositoryRequestConflict("original filtered request differs")
                else:
                    selected = (
                        self.projection.definitions.resolve(definition) if definition is not None
                        else Expression.parse(payload["filter"])
                    )
                    self.projection.require(key, selected)
                    request = {"filter": selected.document(), "definition": definition, "ttl": ttl}
                    retired = {r["registration_id"] for r in state["registrations"]
                               if r["expires_at"] + 86400 < now and r["delivered"] <= r["acknowledged"]}
                    state["registrations"] = [r for r in state["registrations"] if r["registration_id"] not in retired]
                    state["events"] = [e for e in state["events"] if e["registration_id"] not in retired]
                    if len(state["registrations"]) >= MAX_REGISTRATIONS:
                        raise RepositoryRequestConflict("filtered subscription capacity reached")
                    repository = self.projection.repository(state, key)
                    row = {
                        "registration_id": uuid.uuid4().hex, "request_id": payload["request_id"],
                        "consumer": consumer, "target": key, "expires_at": now + request["ttl"],
                        "acknowledged": state["next_sequence"] - 1, "delivered": state["next_sequence"] - 1,
                        "acknowledged_event": None, "cancelled": False, "request": request,
                        "members": [], "revisions": {}, "coverage": copy.deepcopy(repository["inventory"]),
                        "generation": 1, "baseline": {},
                    }
                    self.projection.seed(state, row, now)
                    self.projection.require(key, self.projection.union([*(
                        r for r in state["registrations"] if r["target"] == key
                        and not r["cancelled"] and r["expires_at"] > now
                    ), row]))
                    row["baseline"] = page(state, row, identity)
                    state["registrations"].append(row)
                    changed = True
                result = {
                    "status": status(row, now), "subscription": self.receipt(row),
                    "baseline": copy.deepcopy(row["baseline"]),
                }
            else:
                registration_id = text(payload["registration_id"])
                row = next((r for r in state["registrations"] if (
                    r["registration_id"], r["consumer"], r["target"]
                ) == (registration_id, consumer, key)), None)
                if row is None:
                    raise KeyError("filtered registration not found")
                current = status(row, now)
                result = {"status": current, "registration_id": registration_id}
                if operation == "query":
                    if current != "active":
                        raise RepositoryRequestConflict("inactive membership cannot publish newer shared facts")
                    result["baseline"] = page(
                        state, row, identity, after=payload.get("after", 0), limit=payload.get("limit", 16),
                    )
                elif operation == "inspect":
                    result["subscription"] = self.receipt(row)
                elif operation == "cancel":
                    changed = not row["cancelled"]
                    row["cancelled"] = True
                    result["status"] = "cancelled"
                elif operation == "renew":
                    until = _integer(payload["expires_at"], 1)
                    if row["cancelled"]:
                        raise RepositoryRequestConflict("cancelled demand cannot be renewed")
                    if until > now + 86400:
                        raise ValueError("renewal exceeds finite horizon")
                    if until > row["expires_at"] and until > now:
                        self.projection.require(key, self.projection.union([*(
                            r for r in state["registrations"] if r["target"] == key
                            and r is not row and not r["cancelled"] and r["expires_at"] > now
                        ), row]))
                        if row["expires_at"] <= now:
                            row["generation"] += 1
                            row["members"] = []
                            row["revisions"] = {}
                            row["coverage"] = {
                                "outcome": "unknown", "observed_at": None, "last_complete_at": None,
                                "pages": 0, "error": "",
                            }
                        row["expires_at"] = until
                        changed = True
                    result.update(status=status(row, now), expires_at=row["expires_at"])
                elif operation == "events":
                    after = _integer(payload.get("after", row["acknowledged"]))
                    limit = _integer(payload.get("limit", 16), 1, 16)
                    if not row["acknowledged"] <= after <= row["delivered"]:
                        raise ValueError("filtered cursor differs from delivered position")
                    available = [e for e in state["events"] if e["registration_id"] == registration_id
                                 and e["sequence"] > after and (
                                     current == "active" or e["sequence"] <= row["delivered"]
                                 )]
                    batch = []
                    for event in available[:limit]:
                        if len(json.dumps([*batch, event]).encode()) > RESPONSE_BYTES - 16384:
                            break
                        batch.append(copy.deepcopy(event))
                    if batch and batch[-1]["sequence"] > row["delivered"]:
                        row["delivered"] = batch[-1]["sequence"]
                        changed = True
                    result.update(events=batch, more=len(available) > len(batch))
                else:
                    event_id = text(payload["event_id"])
                    if row["acknowledged_event"] != event_id:
                        event = next((e for e in state["events"] if e["registration_id"] == registration_id
                                      and e["event_id"] == event_id), None)
                        if event is None:
                            raise RepositoryHistoryGap("original filtered event no longer retained")
                        if event["sequence"] > row["delivered"]:
                            raise ValueError("filtered event was not delivered")
                        if event["sequence"] > row["acknowledged"]:
                            row["acknowledged"] = event["sequence"]
                            row["acknowledged_event"] = event_id
                            changed = True
                    result.update(status="acknowledged", event_id=event_id)
            if len(json.dumps(result, allow_nan=False).encode()) > RESPONSE_BYTES - 8192:
                raise ValueError("filtered response exceeds its budget")
            if changed:
                release_inactive(state, now)
                trim(state, new_sequence=state["next_sequence"], now=now)
                commit()
            return result
        return self.store._review_transaction(transact, staged=True, persist=False, **authority)

    @staticmethod
    def receipt(row):
        return copy.deepcopy({name: value for name, value in row.items()
                              if name not in ("members", "revisions", "baseline")})
