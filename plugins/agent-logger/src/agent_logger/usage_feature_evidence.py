"""Join explicitly identified measured calls without summing competing sources."""

from __future__ import annotations

import json
from pathlib import Path

from agent_logger.sessions import SessionRef
from agent_logger.usage_features import _identifier, _tokens
from agent_logger.usage_ledger import MAX_BYTES, MAX_RECORDS, SCHEMA, UsageEvidenceError, validate_values


def enrich_features(
    features: dict, ref: SessionRef, *, usage_store: Path | None = None,
    additional_records: list[dict] | None = None,
) -> dict:
    from agent_logger.usage_reader import read_session_member
    from agent_logger.usage_sqlite import read_sqlite_usage

    records: list[dict] = list(additional_records or [])
    saved = read_session_member(ref, "usage-evidence.json", max_bytes=MAX_BYTES)
    if saved is not None:
        try:
            artifact = json.loads(saved)
        except (ValueError, UnicodeError) as exc:
            raise UsageEvidenceError("invalid usage evidence artifact") from exc
        if (
            not isinstance(artifact, dict) or artifact.get("schema") != SCHEMA
            or artifact.get("version") != 1
            or str(artifact.get("session_id", "")).lower() not in (ref.id.lower(), features["session_id"])
            or not isinstance(artifact.get("records"), list)
            or len(artifact["records"]) > MAX_RECORDS
        ):
            raise UsageEvidenceError("incompatible usage evidence artifact")
        records.extend(artifact["records"])
    if usage_store is not None:
        rows = read_sqlite_usage(usage_store, {ref.id, features["session_id"]})
        records.extend(row for batch in rows.values() for row in batch)
    return pair_measured_features(features, records)


def pair_measured_features(features: dict, records: list[dict]) -> dict:
    """Only a unique API ID match is defensible for external scalar evidence."""
    if len(records) > MAX_RECORDS:
        raise UsageEvidenceError("calibration evidence record limit exceeded")
    index: dict[str, list[dict]] = {}
    for call in features["calls"]:
        if call["api_call_id"]:
            index.setdefault(call["api_call_id"], []).append(call)
    paired: dict[str, set[str]] = {}
    unmatched = ambiguous = 0
    for record in records:
        if not isinstance(record, dict) or record.get("source") not in ("session-store", "transcript"):
            raise UsageEvidenceError("invalid measured feature source")
        values = validate_values(record.get("values"))
        if values.get("parent_tool_call_id") or values.get("agent_id"):
            # Child-owned records are excluded before root-identity checks so a
            # child's distinct source_session_id cannot fail extraction.
            continue
        for identity in (record.get("session_id"), values.get("source_session_id")):
            normalized = _identifier(identity)
            if identity is not None and (not normalized or normalized.lower() != features["session_id"]):
                raise UsageEvidenceError("measured feature session identity mismatch")
        api = _identifier(values.get("api_call_id"))
        candidates = index.get(api, []) if api else []
        if len(candidates) != 1:
            unmatched += not candidates
            ambiguous += bool(candidates)
            continue
        if record.get("multiplicity", 1) != 1:
            ambiguous += 1
            continue
        call = candidates[0]
        observed_model = _identifier(values.get("model"))
        if observed_model and call["model"] and observed_model != call["model"]:
            raise UsageEvidenceError("conflicting measured feature model")
        call["model"] = observed_model or call["model"]
        for source, target in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
            value = _tokens(values.get(source))
            if value is not None:
                if call[target] is not None and call[target] != value:
                    raise UsageEvidenceError("conflicting measured feature tokens")
                call[target] = value
        paired.setdefault(record["source"], set()).add(api)
    features["evidence_join"] = {
        "paired_calls_by_source": {source: len(ids) for source, ids in sorted(paired.items())},
        "unmatched_records": unmatched, "ambiguous_records": ambiguous,
        "policy": "unique API ID only; competing sources are not additive",
    }
    if all(call["model"] for call in features["calls"]):
        features["warnings"] = [value for value in features["warnings"] if value != "unknown-model"]
    return features
