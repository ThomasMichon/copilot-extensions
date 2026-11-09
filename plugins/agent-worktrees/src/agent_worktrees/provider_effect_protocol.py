"""Durable provider-effect responsibility in the canonical resource CAS slot."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields, replace
from datetime import timedelta

from .lease_protocol import (
    LeaseRecord, ProtocolError, Resource, canonical_json, parse_timestamp, resource,
    validate_holder, validate_oid,
)

SENTINEL = "agent-provider-effect-envelope-v1"
ACTIONS = frozenset({
    "borrow", "start", "stop", "delete", "provision", "reprovision",
    "workspace", "connection-cleanup",
})
OUTCOMES = frozenset({"succeeded", "failed", "not-started"})
_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ProviderEffectRecord:
    """An authenticated provider's assertion, never native settlement proof itself."""

    schema_version: int
    resource: dict[str, str]
    state: str
    event: str
    effect_id: str
    owner_id: str
    provider_id: str
    provider_resource_id: str
    action: str
    request_digest: str
    task_token: str
    predecessor: str
    started_at: str
    admission_margin_seconds: int
    settled_at: str | None = None
    outcome: str | None = None
    settlement_digest: str | None = None


def serialize_effect(record: ProviderEffectRecord) -> str:
    return f"{SENTINEL}\n{canonical_json(asdict(record))}"


def parse_effect(message: str, expected: Resource | None = None) -> ProviderEffectRecord:
    prefix = SENTINEL + "\n"
    if not message.startswith(prefix) or message.count("\n") != 1:
        raise ProtocolError("provider effect commit message has an invalid envelope")
    body = message[len(prefix):]
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProtocolError("provider effect payload is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {field.name for field in fields(ProviderEffectRecord)}:
        raise ProtocolError("provider effect payload has missing or unknown fields")
    if canonical_json(raw) != body:
        raise ProtocolError("provider effect payload JSON is not canonical")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ProtocolError("unsupported provider effect schema_version")
    value = raw["resource"]
    if not isinstance(value, dict) or set(value) != {"identity", "kind", "key"}:
        raise ProtocolError("provider effect resource identity is malformed")
    item = resource(value["kind"], value["key"])
    if value["identity"] != item.identity or (expected is not None and item != expected):
        raise ProtocolError("provider effect resource identity does not match its ref")
    if type(raw["effect_id"]) is not str or not _ID.fullmatch(raw["effect_id"]):
        raise ProtocolError("provider effect_id must be a lowercase UUID hex value")
    for name in ("owner_id", "provider_id", "provider_resource_id"):
        validate_holder(raw[name])
    if type(raw["action"]) is not str or raw["action"] not in ACTIONS:
        raise ProtocolError("unsupported provider effect action")
    if type(raw["request_digest"]) is not str or not _DIGEST.fullmatch(raw["request_digest"]):
        raise ProtocolError("provider effect request_digest must be a SHA-256 digest")
    for name in ("task_token", "predecessor"):
        if type(raw[name]) is not str:
            raise ProtocolError("provider effect requires full Git object IDs")
        validate_oid(raw[name])
    started = parse_timestamp(raw["started_at"])
    margin = raw["admission_margin_seconds"]
    if type(margin) is not int or not 0 <= margin <= 604800:
        raise ProtocolError("provider effect admission margin is outside the protocol bound")
    if (raw["state"], raw["event"]) == ("effect-unsettled", "begin"):
        if any(raw[name] is not None for name in ("settled_at", "outcome", "settlement_digest")):
            raise ProtocolError("unsettled provider effect cannot assert settlement")
    elif (raw["state"], raw["event"]) == ("effect-settled", "settle"):
        if type(raw["outcome"]) is not str or raw["outcome"] not in OUTCOMES:
            raise ProtocolError("provider effect requires explicit terminal outcome")
        if (type(raw["settlement_digest"]) is not str
                or not _DIGEST.fullmatch(raw["settlement_digest"])):
            raise ProtocolError("provider effect settlement_digest must be a SHA-256 digest")
        if parse_timestamp(raw["settled_at"]) < started:
            raise ProtocolError("provider effect settlement precedes admission")
    else:
        raise ProtocolError("unsupported provider effect state or event")
    return ProviderEffectRecord(**raw)


def validate_begin(task: LeaseRecord | None, effect: ProviderEffectRecord, *, parent_oid: str) -> None:
    if not isinstance(task, LeaseRecord) or task.state != "leased":
        raise ProtocolError("provider effect requires a live task lease")
    if effect.task_token != parent_oid or effect.predecessor != parent_oid:
        raise ProtocolError("provider effect task fence differs from its Git parent")
    started = parse_timestamp(effect.started_at)
    if (started < parse_timestamp(task.renewed_at)
            or started > task.expires() - timedelta(seconds=effect.admission_margin_seconds)):
        raise ProtocolError("provider effect admission is outside the task safe interval")


def validate_settle(previous: ProviderEffectRecord, current: ProviderEffectRecord,
                    *, parent_oid: str) -> None:
    if current.predecessor != parent_oid:
        raise ProtocolError("provider effect settlement differs from its Git parent")
    expected = replace(
        previous, state=current.state, event=current.event, predecessor=parent_oid,
        settled_at=current.settled_at, outcome=current.outcome,
        settlement_digest=current.settlement_digest,
    )
    if expected != current:
        raise ProtocolError("provider effect settlement changed original responsibility")
