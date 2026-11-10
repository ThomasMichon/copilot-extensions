"""Versioned current-state/event extension of the existing ReplayStore envelope."""

from __future__ import annotations

import json

from .repository_contract import (
    MAX_CACHE_BYTES, MAX_EVENTS, MAX_PULLS, MAX_REGISTRATIONS, MAX_TARGETS,
    RepositoryBackpressure, RepositoryIdentity,
)

METADATA_RESERVE_BYTES = 1_048_576

def empty() -> dict:
    return {"repositories": [], "registrations": [], "events": [], "next_sequence": 1}


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError("invalid repository cache storage fields")


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("invalid repository cache integer")


def facts(value):
    _fields(value, "provider pr_state merged head_sha base_ref base_sha mergeable checks_state "
            "checks_complete title draft author reviews check_failures fingerprints")
    if value["provider"] not in ("github", "gitea") or value["pr_state"] not in ("open", "closed"):
        raise ValueError("invalid normalized provider or lifecycle")
    if any(type(value[name]) is not bool for name in ("merged", "draft")) or any(
        not isinstance(value[name], str)
        for name in ("head_sha", "base_ref", "checks_state", "title")
    ):
        raise ValueError("invalid normalized current facts")
    for name in ("mergeable", "checks_complete"):
        if value[name] is not None and type(value[name]) is not bool:
            raise ValueError("invalid normalized nullable evidence")
    for name in ("base_sha", "author"):
        if value[name] is not None and not isinstance(value[name], str):
            raise ValueError("invalid normalized nullable identity")
    if not isinstance(value["reviews"], list) or len(value["reviews"]) > 1000:
        raise ValueError("invalid normalized reviews")
    for review in value["reviews"]:
        _fields(review, "id state user commit_id dismissed")
        if review["id"] is not None:
            _integer(review["id"])
        if any(review[name] is not None and not isinstance(review[name], str)
               for name in ("state", "user", "commit_id")) or (
            review["dismissed"] is not None and type(review["dismissed"]) is not bool
        ):
            raise ValueError("invalid normalized review")
    if not isinstance(value["check_failures"], list) or len(value["check_failures"]) > 1000:
        raise ValueError("invalid normalized checks")
    for failure in value["check_failures"]:
        _fields(failure, "name state source url")
        if any(part is not None and not isinstance(part, str) for part in failure.values()):
            raise ValueError("invalid normalized check")
    fingerprints = value["fingerprints"]
    if fingerprints is not None:
        _fields(fingerprints, "votes approvals discussions policies gates")
        if any(not isinstance(part, str) or not part for part in fingerprints.values()):
            raise ValueError("invalid normalized fingerprints")
    if len(json.dumps(value, allow_nan=False).encode()) > 65536:
        raise ValueError("normalized facts exceed byte bound")


def validate(value: object, namespace: str, *, version: int = 1) -> None:
    """Refuse foreign/newer state rather than silently rebuilding a projection."""
    _fields(value, "repositories registrations events next_sequence")
    _integer(value["next_sequence"], 1)
    limits = {"repositories": MAX_TARGETS, "registrations": MAX_REGISTRATIONS, "events": MAX_EVENTS}
    for name, limit in limits.items():
        if not isinstance(value[name], list) or len(value[name]) > limit:
            raise ValueError("repository cache capacity exceeded")
    targets = set()
    for row in value["repositories"]:
        _fields(row, "identity inventory pulls")
        identity = RepositoryIdentity.parse(row["identity"])
        if identity.namespace != namespace or identity.key in targets:
            raise ValueError("repository namespace mismatch or repeated target")
        targets.add(identity.key)
        coverage = row["inventory"]
        _fields(coverage, "outcome observed_at last_complete_at error pages")
        if coverage["outcome"] not in ("complete", "partial", "unavailable", "unknown"):
            raise ValueError("invalid inventory coverage")
        for field in ("observed_at", "last_complete_at"):
            if coverage[field] is not None:
                _integer(coverage[field])
        _integer(coverage["pages"])
        if not isinstance(coverage["error"], str) or len(coverage["error"]) > 128:
            raise ValueError("invalid inventory error")
        if not isinstance(row["pulls"], list) or len(row["pulls"]) > MAX_PULLS * 2:
            raise ValueError("repository current-state bound exceeded")
        numbers = set()
        for pull in row["pulls"]:
            _fields(pull, "number member departed_at observed_at error facts")
            _integer(pull["number"], 1)
            if pull["number"] in numbers or type(pull["member"]) is not bool:
                raise ValueError("invalid current-state membership")
            numbers.add(pull["number"])
            for field in ("departed_at", "observed_at"):
                if pull[field] is not None:
                    _integer(pull[field])
            if not isinstance(pull["error"], str) or len(pull["error"]) > 128:
                raise ValueError("invalid current-state error")
            if pull["facts"] is not None:
                facts(pull["facts"])
    registrations = set()
    for row in value["registrations"]:
        _fields(row, "registration_id request_id consumer target expires_at acknowledged cancelled" + (
            " request delivered acknowledged_event" if version == 2 else ""
        ))
        if any(not isinstance(row[name], str) or not 1 <= len(row[name]) <= 128 for name in (
            "registration_id", "request_id", "consumer", "target",
        )) or row["target"] not in targets or row["registration_id"] in registrations:
            raise ValueError("invalid continuing registration")
        registrations.add(row["registration_id"])
        _integer(row["expires_at"], 1)
        _integer(row["acknowledged"])
        if row["acknowledged"] >= value["next_sequence"] or type(row["cancelled"]) is not bool:
            raise ValueError("invalid continuing cursor")
        if version == 2:
            _fields(row["request"], "after ttl")
            after = row["request"]["after"]
            if after is not None:
                _integer(after)
            _integer(row["request"]["ttl"], 1)
            if row["request"]["ttl"] > 86400:
                raise ValueError("invalid continuing request lifetime")
            _integer(row["delivered"])
            if not row["acknowledged"] <= row["delivered"] < value["next_sequence"] or (
                row["acknowledged_event"] is not None and (
                    not isinstance(row["acknowledged_event"], str) or len(row["acknowledged_event"]) != 32
                )
            ):
                raise ValueError("invalid continuing delivery position")
    previous = 0
    for row in value["events"]:
        _fields(row, "sequence event_id target number kind facts")
        _integer(row["sequence"], 1)
        if row["sequence"] <= previous or row["sequence"] >= value["next_sequence"]:
            raise ValueError("invalid durable event ordering")
        previous = row["sequence"]
        if (
            row["target"] not in targets or row["kind"] not in (
                "baseline", "entry", "change", "departure", "terminal", "coverage",
            ) or not isinstance(row["event_id"], str) or len(row["event_id"]) != 32
            or row["facts"] is not None and not isinstance(row["facts"], dict)
        ):
            raise ValueError("invalid durable repository event")
        if row["number"] is not None:
            _integer(row["number"], 1)
        if row["facts"] is not None:
            if row["kind"] == "coverage":
                _fields(row["facts"], "outcome error")
                if row["facts"]["outcome"] not in ("complete", "partial", "unavailable") or (
                    not isinstance(row["facts"]["error"], str)
                ):
                    raise ValueError("invalid inventory status event")
            else:
                facts(row["facts"])
    if not fits(value, version=version):
        raise RepositoryBackpressure("repository cache byte capacity exceeded")


def fits(value, *, version=1):
    total = len(json.dumps(value, allow_nan=False).encode())
    payload = len(json.dumps({**value, "registrations": []}, allow_nan=False).encode())
    return total <= MAX_CACHE_BYTES and payload <= MAX_CACHE_BYTES - (
        METADATA_RESERVE_BYTES if version == 2 else 0
    )
