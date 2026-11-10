"""Versioned hourly components; no collection, storage or service activation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from process_telemetry import hourly

from . import health_slots as slots

SCHEMA = "agent-machines.health.hourly"
MAX_BYTES = 768 * 1024
MAX_SERVICES = 8
COMPONENT_STATES = {"collected", "retained", "not-requested", "partial", "clock-skew", "hour-boundary", "timeout"}
SERVICE_STATES = {"observed", "partial", "unavailable", "unsupported", "invalid", "timeout", "duplicate"}


def _tag(value) -> bool:
    return type(value) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value) is not None


def validate_collection(value: dict) -> dict:
    """Validate local selections, never an executable or synchronized policy."""
    slots._fields(value, {"bridge_port", "services"})
    port, services = value["bridge_port"], value["services"]
    if (
        port is not None and (type(port) is not int or not 1 <= port <= 65535)
        or type(services) is not list or len(services) > MAX_SERVICES
    ):
        raise slots.HealthError("invalid-collection-policy")
    labels = set()
    for request in services:
        if type(request) is not str:
            raise slots.HealthError("invalid-service-selection")
        label, separator, service = request.partition("=")
        service = service if separator else label
        if not _tag(label) or not _tag(service) or label in labels:
            raise slots.HealthError("invalid-service-selection")
        labels.add(label)
    return value


def validate_record(value: dict) -> dict:
    """Validate a same-hour envelope without widening observation v1."""
    slots._fields(value, {
        "schema", "version", "publisher", "continuity", "hour", "revision", "parent",
        "observed_at_ms", "observation", "services", "coverage",
    })
    if (
        value["schema"] != SCHEMA or type(value["version"]) is not int or value["version"] != 1
        or not slots._identity(value["publisher"]) or not slots._identity(value["continuity"])
        or not slots._integer(value["hour"]) or not slots._integer(value["observed_at_ms"])
        or value["hour"] > value["observed_at_ms"] // slots.HOUR_MS
        or not slots._integer(value["revision"], minimum=1)
        or value["parent"] is not None and (
            not slots._integer(value["parent"], minimum=1) or value["parent"] >= value["revision"]
        )
    ):
        raise slots.HealthError("invalid-hourly-envelope")
    slots._fields(value["coverage"], {"observation", "services"})
    if any(type(state) is not str or state not in COMPONENT_STATES for state in value["coverage"].values()):
        raise slots.HealthError("invalid-collection-coverage")
    observation = value["observation"]
    if observation is not None:
        slots.validate_record(observation)
        if (
            not slots._owner(observation, value) or observation["hour"] != value["hour"]
            or observation["observed_at_ms"] > value["observed_at_ms"]
        ):
            raise slots.HealthError("hourly-observation-mismatch")
    services = value["services"]
    if type(services) is not list or len(services) > MAX_SERVICES:
        raise slots.HealthError("invalid-hourly-services")
    try:
        hour = datetime.fromtimestamp(value["hour"] * 3600, timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    except (ValueError, OverflowError, OSError):
        raise slots.HealthError("invalid-hourly-hour") from None
    labels = set()
    for service in services:
        slots._fields(service, {
            "instance", "service", "hour", "state",
            *({"contribution"} if type(service) is dict and "contribution" in service else set()),
        })
        if (
            not _tag(service["instance"]) or not _tag(service["service"])
            or service["instance"] in labels or service["hour"] != hour
            or type(service["state"]) is not str or service["state"] not in SERVICE_STATES
        ):
            raise slots.HealthError("invalid-hourly-service")
        labels.add(service["instance"])
        if "contribution" in service:
            try:
                contribution = hourly.decode(json.dumps(service["contribution"], allow_nan=False).encode())
                hourly.validate(
                    contribution, service=service["service"], hour=hour,
                    metrics=list(hourly.METRICS), now_ms=value["observed_at_ms"],
                )
            except (ValueError, TypeError, RecursionError):
                raise slots.HealthError("invalid-hourly-contribution") from None
            if contribution["coverage"]["state"] != service["state"]:
                raise slots.HealthError("hourly-coverage-mismatch")
        elif service["state"] in {"observed", "partial"}:
            raise slots.HealthError("missing-hourly-contribution")
    if len(json.dumps(value, separators=(",", ":"), allow_nan=False).encode()) > MAX_BYTES:
        raise slots.HealthError("output-oversize")
    return value


def sample(value: dict, now_ms: int) -> dict:
    """Retain component source ages, not freshness from envelope generation."""
    observation = value["observation"] or {
        "observed_at_ms": value["observed_at_ms"], "revision": 1, "domains": {},
    }
    return {
        **slots._sample(observation, now_ms), "revision": value["revision"],
        "services": value["services"], "collection_coverage": value["coverage"],
        "collected_at_ms": value["observed_at_ms"],
    }


def has_service_evidence(row: dict) -> bool:
    """A contribution wrapper alone is not retained observation evidence."""
    contribution = row.get("contribution", {})
    return bool(contribution.get("groups") or contribution.get("health") or contribution.get("diagnostics"))


def validate_replacement(value: dict, previous: dict | None, policy: dict) -> None:
    """Keep retained/unselected evidence intact at the actual write boundary."""
    previous = previous if previous is not None and previous["hour"] == value["hour"] else None
    observation = previous["observation"] if previous else None
    if value["observation"] != observation and (
        observation is not None and value["observation"] is None
        or policy["collection"]["bridge_port"] is None
        or value["coverage"]["observation"] != "collected"
    ):
        raise slots.HealthError("hourly-observation-replacement-refused")
    before_domains = observation["domains"] if observation else {}
    after_domains = value["observation"]["domains"] if value["observation"] else {}
    for domain in (before_domains.keys() | after_domains.keys()) - {"local_listener"}:
        if before_domains.get(domain) != after_domains.get(domain):
            raise slots.HealthError("hourly-observation-replacement-refused")
    selected = {}
    for request in policy["collection"]["services"]:
        label, separator, service = request.partition("=")
        selected[label] = service if separator else label
    before = {row["instance"]: row for row in previous["services"]} if previous else {}
    after = {row["instance"]: row for row in value["services"]}
    for label in before.keys() | after.keys():
        old, new = before.get(label), after.get(label)
        if old == new:
            continue
        if (
            new is None or new["service"] != selected.get(label)
            or value["coverage"]["services"] not in {"collected", "partial"}
            or old is not None and (
                old["service"] != new["service"]
                or has_service_evidence(old) and not has_service_evidence(new)
            )
        ):
            raise slots.HealthError("hourly-service-replacement-refused")
