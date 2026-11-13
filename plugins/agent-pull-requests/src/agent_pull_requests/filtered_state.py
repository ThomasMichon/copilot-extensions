"""Explicit generation-three population state in the existing ReplayStore envelope."""

import json
import math
import re

from pr_filters import Expression

from .repository_contract import (
    MAX_CACHE_BYTES, MAX_EVENTS, MAX_PULLS, MAX_REGISTRATIONS, MAX_TARGETS,
    RepositoryBackpressure, RepositoryIdentity,
)
from .repository_state import _fields, _integer

METADATA_RESERVE_BYTES = 1_048_576
FACT_FIELDS = "repo from assigned created_at activity_at tags publication"

def release_inactive(state, now):
    for row in state["registrations"]:
        if row["cancelled"] or row["expires_at"] <= now:
            row["members"] = []
            row["projected"] = {}
    for repository in state["repositories"]:
        key = RepositoryIdentity.parse(repository["identity"]).key
        active = [row for row in state["registrations"] if row["target"] == key
                  and not row["cancelled"] and row["expires_at"] > now]
        if not active:
            repository["pulls"] = []


def text(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ValueError("invalid filtered identity")
    return value


def event_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise ValueError("invalid filtered event identity")


def facts(value, identity):
    _fields(value, FACT_FIELDS)
    if value["repo"] != identity.repo:
        raise ValueError("population facts differ from admitted repository")
    for name in ("from", "assigned", "tags"):
        items = value[name]
        if items is not None and (
            not isinstance(items, list) or len(items) > 1000
            or any(not isinstance(item, str) or not item or len(item) > 256 for item in items)
            or len(set(items)) != len(items)
        ):
            raise ValueError("invalid population identities/tags")
    for name in ("created_at", "activity_at"):
        value_at = value[name]
        try:
            valid = value_at is None or (
                not isinstance(value_at, bool) and isinstance(value_at, (int, float))
                and math.isfinite(value_at)
            )
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("invalid population instant")
    if value["publication"] not in (None, "draft", "published"):
        raise ValueError("invalid population publication state")
    if len(json.dumps(value, allow_nan=False).encode()) > 65536:
        raise ValueError("population facts exceed byte capacity")


def coverage(value):
    _fields(value, "outcome observed_at last_complete_at error pages")
    if value["outcome"] not in ("unknown", "complete", "partial", "unavailable"):
        raise ValueError("invalid population coverage")
    for name in ("observed_at", "last_complete_at"):
        if value[name] is not None:
            _integer(value[name])
    _integer(value["pages"])
    if value["pages"] > 4 or not isinstance(value["error"], str) or len(value["error"]) > 128:
        raise ValueError("invalid population coverage bounds")


def _members(value):
    if not isinstance(value, list) or len(value) > MAX_PULLS * 2 or any(
        type(number) is not int or number < 1 for number in value
    ) or len(set(value)) != len(value):
        raise ValueError("invalid filtered membership")


def validate(value, namespace, *, version=3):
    if version != 3:
        raise ValueError("filtered storage generation unsupported")
    _fields(value, "repositories registrations events next_sequence")
    _integer(value["next_sequence"], 1)
    for name, maximum in (
        ("repositories", MAX_TARGETS), ("registrations", MAX_REGISTRATIONS), ("events", MAX_EVENTS),
    ):
        if not isinstance(value[name], list) or len(value[name]) > maximum:
            raise RepositoryBackpressure("filtered state capacity exceeded")
    identities = {}
    for repository in value["repositories"]:
        _fields(repository, "identity inventory pulls")
        identity = RepositoryIdentity.parse(repository["identity"])
        if identity.namespace != namespace or identity.key in identities:
            raise ValueError("filtered namespace/repository differs")
        identities[identity.key] = identity
        coverage(repository["inventory"])
        if not isinstance(repository["pulls"], list) or len(repository["pulls"]) > MAX_PULLS * 2:
            raise RepositoryBackpressure("filtered current-fact capacity exceeded")
        numbers = []
        for pull in repository["pulls"]:
            _fields(pull, "number facts observed_at")
            _integer(pull["number"], 1)
            _integer(pull["observed_at"])
            facts(pull["facts"], identity)
            numbers.append(pull["number"])
        if len(set(numbers)) != len(numbers):
            raise ValueError("duplicate population identity")
    registrations, requests = {}, set()
    for row in value["registrations"]:
        _fields(row, "registration_id request_id consumer target expires_at acknowledged cancelled "
                "request delivered acknowledged_event members projected coverage generation baseline")
        for name in ("registration_id", "request_id", "consumer", "target"):
            text(row[name])
        request_key = row["consumer"], row["request_id"]
        if row["target"] not in identities or row["registration_id"] in registrations or request_key in requests:
            raise ValueError("duplicate or unbound filtered registration")
        registrations[row["registration_id"]] = row
        requests.add(request_key)
        _fields(row["request"], "filter definition ttl")
        Expression.parse(row["request"]["filter"])
        if row["request"]["definition"] is not None:
            text(row["request"]["definition"])
        _integer(row["request"]["ttl"], 1)
        if row["request"]["ttl"] > 86400 or type(row["cancelled"]) is not bool:
            raise ValueError("invalid filtered lifetime")
        for name in ("expires_at", "acknowledged", "delivered"):
            _integer(row[name])
        if not row["acknowledged"] <= row["delivered"] < value["next_sequence"]:
            raise ValueError("invalid filtered delivery position")
        if row["acknowledged_event"] is not None:
            event_id(row["acknowledged_event"])
        _members(row["members"])
        if not isinstance(row["projected"], dict) or set(row["projected"]) != {
            str(number) for number in row["members"]
        }:
            raise ValueError("invalid filtered per-registration projection")
        for number, pull in row["projected"].items():
            _fields(pull, "number facts observed_at")
            if type(pull["number"]) is not int or str(pull["number"]) != number:
                raise ValueError("invalid projected population identity")
            _integer(pull["observed_at"])
            facts(pull["facts"], identities[row["target"]])
        coverage(row["coverage"])
        _integer(row["generation"], 1)
        if not isinstance(row["baseline"], dict) or len(json.dumps(row["baseline"]).encode()) > 1_000_000:
            raise ValueError("invalid filtered baseline")
    previous = 0
    for event in value["events"]:
        _fields(event, "sequence event_id target registration_id number kind facts")
        _integer(event["sequence"], 1)
        event_id(event["event_id"])
        row = registrations.get(event["registration_id"])
        if row is None or event["target"] != row["target"] or not (
            previous < event["sequence"] < value["next_sequence"]
        ):
            raise ValueError("invalid filtered event scope/order")
        previous = event["sequence"]
        if event["number"] is not None:
            _integer(event["number"], 1)
        if event["kind"] not in ("entry", "change", "departure", "coverage"):
            raise ValueError("invalid filtered event kind")
        if event["kind"] == "coverage":
            coverage(event["facts"])
        elif event["facts"] is not None:
            facts(event["facts"], identities[event["target"]])
    trim(value, new_sequence=value["next_sequence"], now=0, validate_only=True)


def protected(event, registrations, now):
    row = next((r for r in registrations if r["registration_id"] == event["registration_id"]), None)
    return row is not None and row["acknowledged"] < event["sequence"] and (
        not row["cancelled"] and row["expires_at"] > now or event["sequence"] <= row["delivered"]
    )


def trim(value, *, new_sequence, now, validate_only=False):
    total = len(json.dumps(value, allow_nan=False).encode())
    payload = len(json.dumps({**value, "registrations": []}, allow_nan=False).encode())
    removed = 0
    events = value["events"]
    def fits():
        return total <= MAX_CACHE_BYTES and payload <= MAX_CACHE_BYTES - METADATA_RESERVE_BYTES and (
            len(events) - removed <= MAX_EVENTS
        )
    while not fits():
        if validate_only or removed == len(events):
            raise RepositoryBackpressure("filtered state byte capacity exceeded")
        first = events[removed]
        if first["sequence"] >= new_sequence or protected(first, value["registrations"], now):
            raise RepositoryBackpressure("unsettled filtered delivery retained")
        amount = len(json.dumps(first, allow_nan=False).encode()) + (2 if len(events) - removed > 1 else 0)
        total, payload = total - amount, payload - amount
        removed += 1
    if removed:
        value["events"] = events[removed:]
