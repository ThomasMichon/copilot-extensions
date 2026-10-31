"""Review intent/receipt data validation; ReplayStore remains the only owner."""

from __future__ import annotations

import copy
import json
import math
import re

from .review_workspace import _digest, _scope

SCHEMA = "agent-pull-requests.review-intent"
MAX_ACTIONS = 16
MAX_OPERATIONS = 1024
MAX_INTENT_BYTES = 256 * 1024


def encoded(value) -> bytes:
    return json.dumps(value, allow_nan=False).encode("utf-8")


def text(value, limit=256):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("invalid review identity")
    return value


def identifier(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("review requires a full content identity")
    return value


def revision(value):
    if not isinstance(value, dict) or set(value) != {"head_sha", "common_sha", "iteration"}:
        raise ValueError("invalid review revision")
    for name in ("head_sha", "common_sha"):
        if not isinstance(value[name], str) or re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value[name]) is None:
            raise ValueError("review requires exact source objects")
    if len(value["head_sha"]) != len(value["common_sha"]) or type(value["iteration"]) is not int or value["iteration"] <= 0:
        raise ValueError("invalid review source iteration")
    return copy.deepcopy(value)


def intent(value):
    """Validate selected data, not authority or evidence that it was reviewed."""
    if not isinstance(value, dict) or set(value) != {
        "schema", "version", "operation_id", "scope", "generation_id", "source_id", "revision", "actions",
    } or value["schema"] != SCHEMA or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("invalid review intent")
    result = copy.deepcopy(value)
    text(result["operation_id"], 128)
    result["scope"] = _scope(result["scope"])
    identifier(result["generation_id"])
    identifier(result["source_id"])
    result["revision"] = revision(result["revision"])
    actions = result["actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
        raise ValueError("review requires bounded selected actions")
    identities = set()
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("invalid selected review action")
        identity = text(action.get("id"), 128)
        if identity in identities:
            raise ValueError("duplicate review action identity")
        identities.add(identity)
        kind = action.get("kind")
        fields = {
            "comment": {"id", "kind", "body"},
            "reply": {"id", "kind", "body", "thread_id"},
            "edit_owned_comment": {"id", "kind", "body", "thread_id", "comment_id", "expected_content_id"},
            "verdict": {"id", "kind", "verdict"},
        }
        if not isinstance(kind, str) or kind not in fields or set(action) != fields[kind]:
            raise ValueError("unsupported or malformed review action")
        if "body" in action and (
            not isinstance(action["body"], str) or not action["body"].strip()
            or len(action["body"].encode("utf-8")) > 64 * 1024 or "\0" in action["body"]
        ):
            raise ValueError("invalid selected review body")
        for name in ("thread_id", "comment_id"):
            if name in action and (type(action[name]) is not int or action[name] <= 0):
                raise ValueError("review requires a positive native identity")
        if "expected_content_id" in action:
            identifier(action["expected_content_id"])
        if kind == "verdict" and action["verdict"] not in ("APPROVED", "CHANGES_REQUESTED"):
            raise ValueError("review requires a supported explicit vote; COMMENTED has no vote action")
    if len(encoded(result)) > MAX_INTENT_BYTES:
        raise ValueError("review intent exceeds its byte limit")
    return result


def receipt(value, *, operation, action, limit):
    if not isinstance(value, dict) or set(value) != {"origin", "platform_ids", "proof"}:
        raise ValueError("invalid action receipt")
    if value["origin"] not in ("response", "attributable_readback", "no_send"):
        raise ValueError("unsupported receipt origin")
    ids = value["platform_ids"]
    if not isinstance(ids, dict) or len(ids) > 8:
        raise ValueError("invalid native receipt identities")
    for name, identity in ids.items():
        text(name, 64)
        if not (type(identity) is int and 0 < identity <= 2**63 - 1):
            text(identity)
    if value["origin"] != "no_send" and not ids:
        raise ValueError("confirmation requires native identities")
    if value["origin"] == "no_send" and ids:
        raise ValueError("no-send evidence cannot claim native effects")
    if value["origin"] == "attributable_readback":
        if not ({"event_id", "action_marker"} & set(ids)):
            raise ValueError("current value or reviewer identity is not effect attribution")
        if value["proof"] != {
            "operation_id": operation["intent"]["operation_id"], "action_id": action["id"],
            "scope_id": _digest(operation["intent"]["scope"]), "intent_id": operation["intent_id"],
        }:
            raise ValueError("receipt attribution does not match the selected intent")
    elif value["proof"] is not None:
        raise ValueError("unexpected receipt proof")
    if len(encoded(value)) > limit:
        raise ValueError("native receipt exceeds its reserved shape")
    return copy.deepcopy(value)


def confirms(value, selected, scope):
    """Native target consistency, not authentication of the effect owner."""
    if value["origin"] == "no_send":
        return False
    ids = value["platform_ids"]
    if selected["kind"] in ("comment", "reply", "edit_owned_comment"):
        if not all(type(ids.get(name)) is int and ids[name] > 0 for name in ("thread_id", "comment_id")):
            return False
        return all(ids[name] == selected[name] for name in ("thread_id", "comment_id") if name in selected)
    return selected["verdict"] != "COMMENTED" and ids.get("reviewer_id") == scope["principal_id"]


def validate_operations(operations, *, receipt_bytes, review_bytes):
    if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
        raise ValueError("invalid persisted review operations")
    identities = set()
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) != {
            "intent", "intent_id", "reserved_bytes", "created_at", "actions",
        }:
            raise ValueError("invalid persisted review operation")
        selected = intent(operation["intent"])
        identity = selected["operation_id"]
        if identity in identities or operation["intent_id"] != _digest(selected):
            raise ValueError("persisted review intent identity mismatch")
        identities.add(identity)
        if type(operation["created_at"]) not in (int, float) or not math.isfinite(operation["created_at"]):
            raise ValueError("invalid review creation time")
        actions = operation["actions"]
        if not isinstance(actions, list) or len(actions) != len(selected["actions"]):
            raise ValueError("persisted review actions are incomplete")
        for state, chosen in zip(actions, selected["actions"]):
            if not isinstance(state, dict) or set(state) != {"id", "state", "attempt_id", "receipt"}:
                raise ValueError("invalid persisted action state")
            if state["id"] != chosen["id"] or state["state"] not in ("unattempted", "unknown", "confirmed"):
                raise ValueError("persisted review action mismatch")
            if state["state"] == "unattempted":
                if state["attempt_id"] is not None or state["receipt"] is not None:
                    raise ValueError("unattempted review action has an effect")
            else:
                text(state["attempt_id"], 128)
                if state["receipt"] is not None:
                    receipt(state["receipt"], operation=operation, action=state, limit=receipt_bytes)
                if state["state"] == "confirmed" and (
                    state["receipt"] is None or not confirms(state["receipt"], chosen, selected["scope"])
                ):
                    raise ValueError("confirmed review action lacks a native receipt")
        required = len(encoded({"intent": selected, "intent_id": _digest(selected), "created_at": operation["created_at"],
                                "actions": [{"id": a["id"], "state": "unattempted", "attempt_id": None, "receipt": None}
                                            for a in selected["actions"]]})) + len(actions) * (receipt_bytes + 512) + 1024
        if type(operation["reserved_bytes"]) is not int or operation["reserved_bytes"] < required:
            raise ValueError("review operation lost its receipt reservation")
        if len(encoded(operation)) > operation["reserved_bytes"]:
            raise ValueError("review outcome exceeds its admitted reservation")
    if 1024 + sum(operation["reserved_bytes"] for operation in operations) > review_bytes:
        raise ValueError("review reservations exceed owner capacity")
