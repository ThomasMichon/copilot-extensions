"""Data-only same-native adoption, namespace exclusion and receipt lineage."""

from __future__ import annotations

import json
from typing import Any, cast

from .acquisition_contract import AcquisitionRefused

CAPABILITY = "retained_native_metadata_adoption"
COLUMN = "creation_adoption_json"
CONFIG_KEY = "retained_metadata_adoption"
EPOCH_KEY = "_agent_bridge_metadata_epoch"


def _refuse(message: str) -> AcquisitionRefused:
    return AcquisitionRefused("retained_metadata_held", message)


def adoption(row: dict[str, Any]) -> dict[str, Any] | None:
    """Read the immutable epoch boundary without interpreting lost receipts."""
    raw = row.get(COLUMN)
    if raw is None:
        return None
    from .models import RetainedRecoveryRequest

    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {
            "version", "request", "predecessor_id", "session_id", "history", "prior_operations",
        }:
            raise ValueError("invalid adoption")
        request = RetainedRecoveryRequest.model_validate(value["request"])
        if (
            type(value["version"]) is not int or value["version"] != 1
            or request.journal_key != row["key_digest"]
            or request.native_session_id != row["native_session_id"]
            or value["predecessor_id"] != row["session_id"]
            or not isinstance(value["session_id"], str) or not value["session_id"]
            or value["session_id"] == value["predecessor_id"]
            or value["history"] != history(value["predecessor_id"], value["session_id"])
            or not isinstance(value["prior_operations"], list)
            or any(not isinstance(item, str) for item in value["prior_operations"])
            or len(set(value["prior_operations"])) != len(value["prior_operations"])
        ):
            raise ValueError("changed adoption")
        return value
    except (ValueError, KeyError, TypeError) as exc:
        raise _refuse("Original metadata adoption contract is unconfirmed") from exc


def history(predecessor_id: str, session_id: str) -> dict[str, str]:
    return {
        "status": "unavailable", "predecessor_id": predecessor_id,
        "event_epoch": session_id, "turn_epoch": session_id,
    }


def native_for(row: dict[str, Any], session_id: str) -> str | None:
    """Resolve birth, handoff and explicit same-native metadata lineage together."""
    boundary = adoption(row)
    if boundary is not None and boundary["session_id"] == session_id:
        return str(boundary["request"]["native_session_id"])
    if row["session_id"] == session_id:
        if boundary is not None:
            raise _refuse("Original control namespace is excluded by its metadata adoption")
        return cast("str | None", row["native_session_id"])
    try:
        handoffs = json.loads(row["creation_handoffs_json"] or "{}")
        if not isinstance(handoffs, dict):
            raise ValueError("invalid handoffs")
        matches = [item for item in handoffs.values() if item["successor_id"] == session_id]
        if len(matches) != 1:
            raise ValueError("unretained successor")
        return cast("str | None", matches[0].get("native_session_id"))
    except (ValueError, TypeError, KeyError) as exc:
        raise _refuse("Native continuation lost its original keyed lineage") from exc


def require_operation_epoch(row: dict[str, Any], session_id: str, operation_id: str) -> None:
    boundary = adoption(row)
    if boundary is not None and boundary["session_id"] == session_id:
        if operation_id in boundary["prior_operations"]:
            raise _refuse("Original recovery operation belongs to the missing predecessor epoch")
