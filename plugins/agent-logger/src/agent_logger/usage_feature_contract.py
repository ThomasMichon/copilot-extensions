"""Admission for count-only feature documents supplied to the estimator."""

from __future__ import annotations

import re

from agent_logger.usage_features import FEATURE_METHOD, FEATURE_SCHEMA, FEATURE_VERSION, _identifier, _tokens
from agent_logger.usage_ledger import MAX_RECORDS, UsageEvidenceError

MAX_SESSIONS = 1000
MAX_COUNT = 10**15


def validate_feature_session(document: object) -> dict:
    if (
        not isinstance(document, dict) or document.get("schema") != FEATURE_SCHEMA
        or type(document.get("version")) is not int or document["version"] != FEATURE_VERSION
        or document.get("method") != FEATURE_METHOD
    ):
        raise UsageEvidenceError("incompatible transcript feature schema")
    identity = _identifier(document.get("session_id"))
    digest = document.get("source_digest")
    if not identity or not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise UsageEvidenceError("feature source identity is unavailable")
    calls = document.get("calls")
    if not isinstance(calls, list) or len(calls) > MAX_RECORDS:
        raise UsageEvidenceError("invalid feature call collection")
    identities: set[tuple[int, str]] = set()
    for call in calls:
        if not isinstance(call, dict) or set(call) != {
            "turn", "api_call_id", "model", "input_chars", "output_chars",
            "input_tokens", "output_tokens", "emissions",
        }:
            raise UsageEvidenceError("invalid feature call schema")
        for key in ("turn", "input_chars", "output_chars", "emissions"):
            if type(call[key]) is not int or not 0 <= call[key] <= MAX_COUNT:
                raise UsageEvidenceError("invalid feature count")
        for key in ("model", "api_call_id"):
            _identifier(call[key])
        if call["turn"] == 0:
            raise UsageEvidenceError("invalid root turn index")
        if call["api_call_id"]:
            key = (call["turn"], call["api_call_id"])
            if key in identities:
                raise UsageEvidenceError("duplicate root-call feature identity")
            identities.add(key)
        for key in ("input_tokens", "output_tokens"):
            _tokens(call[key])
    return document


def admit_sessions(documents: list[dict]) -> list[dict]:
    """Deduplicate identical originals, refuse divergent copies and renamed copies."""
    if not isinstance(documents, list) or len(documents) > MAX_SESSIONS:
        raise UsageEvidenceError("feature session limit exceeded")
    admitted: dict[str, dict] = {}
    digests: dict[str, str] = {}
    total_calls = 0
    for document in documents:
        document = validate_feature_session(document)
        identity = document["session_id"].lower()
        document = dict(document, session_id=identity)
        digest = document["source_digest"]
        has_calls = bool(document["calls"])
        if has_calls:
            # Two independent empty transcripts both hash to SHA-256(empty);
            # exempt call-free documents (no calibration samples) from the
            # cross-identity digest check, while still catching divergent
            # same-identity resubmissions below.
            if digest in digests and digests[digest] != identity:
                raise UsageEvidenceError(
                    "transcript copy has conflicting original session identity",
                )
            digests[digest] = identity
        if identity in admitted:
            previous = admitted[identity]
            if previous["source_digest"] != digest or previous["calls"] != document["calls"]:
                raise UsageEvidenceError("divergent copies of original session")
            continue
        total_calls += len(document["calls"])
        if total_calls > MAX_RECORDS:
            raise UsageEvidenceError("calibration total call limit exceeded")
        admitted[identity] = document
    return [admitted[key] for key in sorted(admitted)]
