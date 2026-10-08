"""The operator attention contract: items, ordering, dedupe and the aggregate.

Owner-neutral -- it imports nothing from agent-dispatch -- so another plugin
can produce or render attention items without depending on the aggregator.
See ``efforts/active/operator-attention-contract`` for the design.

An **item** is one thing that needs an operator, from one **source**. Sources
return a **source result** (``{items, status, error?, uncertain, read_at}``);
the aggregator orders and deduplicates their items into one queue and reports
an aggregate ``status`` that never reads a failed source as "all clear".
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Iterable

SCHEMA = 1

#: Worst first. ``severity`` is derived from ``display_state``.
DISPLAY_STATES = ("failed", "stalled", "awaiting_input", "blocked", "review")
SEVERITY = {state: rank for rank, state in enumerate(DISPLAY_STATES)}
CONFIDENCES = ("reported", "scanned", "heuristic")
SHARED_ENTITIES = ("task", "session", "pr", "queue")
VERBS = ("show", "resume", "open")
SOURCE_STATUSES = ("ok", "failed", "uncertain", "disabled")
COMMAND_STATUSES = ("ok", "failed", "uncertain")
SOURCE_NAME = re.compile(r"^[a-z0-9-]+$")
REASON_MAX = 200

_REQUIRED = ("schema", "id", "entity", "entity_ref", "lifecycle_state", "display_state",
             "severity", "reason", "created_at", "updated_at", "confidence", "actions",
             "source", "also")


class ContractError(ValueError):
    """An item or source result that doesn't satisfy the contract."""


def one_line(text: Any, limit: int = REASON_MAX) -> str:
    return " ".join(str(text or "").split())[:limit]


def is_schema(value: Any) -> bool:
    """Exactly the integer ``SCHEMA``: JSON ``true`` and ``1.0`` compare equal in
    Python but aren't the documented integer."""
    return type(value) is int and value == SCHEMA


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def for_terminal(text: str) -> str:
    """``text`` with control characters escaped, so a source's error or a
    registry name can't drive the terminal (JSON output keeps values intact)."""
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def make_id(source: str, entity: str, entity_ref: str) -> str:
    """``source`` and ``entity`` can't contain ``:``, so the id splits back
    unambiguously (only ``entity_ref``, last, may)."""
    return f"{source}:{entity}:{entity_ref}"


CUSTOM_KIND = re.compile(r"^[a-z0-9_-]+$")


def canonical_time(value: Any) -> str:
    """An ISO-8601 timestamp with an offset, normalized to UTC seconds
    (``YYYY-MM-DDTHH:MM:SS+00:00``) so the queue orders by time, not by spelling."""
    from datetime import datetime, timezone

    if not isinstance(value, str) or not value:
        raise ContractError("a timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{value!r} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{value!r} has no UTC offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def canonical_entity(entity: Any, source: str) -> str:
    """A shared kind as-is; a bare custom kind namespaced ``x.<source>.<kind>``;
    an already-namespaced kind kept only under the source's own name."""
    if not isinstance(entity, str) or not entity:
        raise ContractError("entity must be a non-empty string")
    if entity in SHARED_ENTITIES:
        return entity
    if entity.startswith("x."):
        prefix = f"x.{source}."
        if not entity.startswith(prefix) or not CUSTOM_KIND.match(entity[len(prefix):]):
            raise ContractError(f"entity {entity!r} is namespaced for another source, or malformed")
        return entity
    if not CUSTOM_KIND.match(entity):
        raise ContractError(f"entity {entity!r} is not a shared kind or a bare custom kind [a-z0-9_-]+")
    return f"x.{source}.{entity}"


def _check_action(action: Any, source: str) -> None:
    if not isinstance(action, dict):
        raise ContractError("each action must be an object")
    verb, argv = action.get("verb"), action.get("argv")
    if not (verb in VERBS or (isinstance(verb, str) and verb.startswith(f"x.{source}.")
                              and len(verb) > len(f"x.{source}."))):
        raise ContractError(f"action verb {verb!r} is not show, resume, open or x.{source}.<verb>")
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)):
        raise ContractError("action argv must be a non-empty list of strings")


def check_input(fields: Any) -> None:
    """``input`` is exactly the steering card's ``request_input`` field list (what
    ``steering.parse_request_input`` produces and ``steer submit`` answers), so
    every client renders and answers it the same way."""
    from .steering import FIELD_CHOICE_TYPES, FIELD_TYPES

    if not isinstance(fields, list) or not fields:
        raise ContractError("input must be a non-empty list of steering fields")
    for field in fields:
        if not isinstance(field, dict) or set(field) - {"name", "type", "options", "allow_other", "show_when"}:
            raise ContractError("an input field has keys name, type, options?, allow_other?, show_when? only")
        if not isinstance(field.get("name"), str) or not field["name"] or field.get("type") not in FIELD_TYPES:
            raise ContractError(f"input field {field.get('name')!r} needs a name and a type in {sorted(FIELD_TYPES)}")
        choice = field["type"] in FIELD_CHOICE_TYPES
        options = field.get("options")
        if choice != (options is not None) or (choice and not (
                isinstance(options, list) and options and all(isinstance(o, str) for o in options))):
            raise ContractError(f"input field {field['name']!r}: options are a non-empty string list, "
                                "for a choice or multichoice only")
        if "allow_other" in field and (not choice or not isinstance(field["allow_other"], bool)):
            raise ContractError(f"input field {field['name']!r}: allow_other is a boolean on a choice only")
        when = field.get("show_when")
        if when is not None and not (isinstance(when, dict) and set(when) == {"field", "equals"}
                                     and all(isinstance(v, str) for v in when.values())):
            raise ContractError(f"input field {field['name']!r}: show_when is {{field, equals}} strings")


def validate_item(item: Any) -> None:
    """Raise :class:`ContractError` unless ``item`` is a complete, valid item."""
    if not isinstance(item, dict):
        raise ContractError("an item must be an object")
    missing = [k for k in _REQUIRED if k not in item]
    if missing:
        raise ContractError(f"item is missing {', '.join(missing)}")
    if not is_schema(item["schema"]):
        raise ContractError(f"item schema {item['schema']!r} is not {SCHEMA}")
    source = item["source"]
    if not isinstance(source, str) or not SOURCE_NAME.match(source):
        raise ContractError("item source must match [a-z0-9-]+")
    if canonical_entity(item["entity"], source) != item["entity"]:
        raise ContractError(f"entity {item['entity']!r} is not canonical")
    if not isinstance(item["entity_ref"], str) or not item["entity_ref"]:
        raise ContractError("entity_ref must be a non-empty string")
    if item["id"] != make_id(source, item["entity"], item["entity_ref"]):
        raise ContractError("id is not <source>:<entity>:<entity_ref>")
    if item["lifecycle_state"] is not None and not isinstance(item["lifecycle_state"], str):
        raise ContractError("lifecycle_state must be a string or null")
    if item["display_state"] not in SEVERITY:
        raise ContractError(f"display_state {item['display_state']!r} is unknown")
    if type(item["severity"]) is not int or item["severity"] != SEVERITY[item["display_state"]]:
        raise ContractError("severity must be the integer that matches display_state")
    if not isinstance(item["reason"], str) or not item["reason"] or len(item["reason"]) > REASON_MAX \
            or "\n" in item["reason"]:
        raise ContractError(f"reason must be one non-empty line of at most {REASON_MAX} characters")
    for key in ("created_at", "updated_at"):
        if canonical_time(item[key]) != item[key]:
            raise ContractError(f"{key} is not canonical UTC (YYYY-MM-DDTHH:MM:SS+00:00)")
    if item["confidence"] not in CONFIDENCES:
        raise ContractError(f"confidence {item['confidence']!r} is unknown")
    if not isinstance(item["actions"], list):
        raise ContractError("actions must be a list")
    for action in item["actions"]:
        _check_action(action, source)
    if "input" in item and item["input"] is not None:
        check_input(item["input"])
    if not isinstance(item["also"], list):
        raise ContractError("also must be a list")


def new_item(*, source: str, entity: str, entity_ref: str, lifecycle_state: str | None,
             display_state: str, reason: str, created_at: str, updated_at: str,
             confidence: str = "reported", actions: Iterable[dict] = (),
             input: list | None = None) -> dict[str, Any]:
    """Build a valid item for a built-in source."""
    entity = canonical_entity(entity, source)
    item: dict[str, Any] = {
        "schema": SCHEMA, "id": make_id(source, entity, entity_ref), "entity": entity,
        "entity_ref": entity_ref, "lifecycle_state": lifecycle_state,
        "display_state": display_state, "severity": SEVERITY[display_state],
        "reason": one_line(reason), "created_at": created_at, "updated_at": updated_at,
        "confidence": confidence, "actions": list(actions), "source": source, "also": [],
    }
    if input is not None:
        item["input"] = input
    validate_item(item)
    return item


def sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    """Severity (worst first), then ``created_at`` (oldest first), then ``id``."""
    return (item["severity"], item["created_at"], item["id"])


def dedupe(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One item per ``(entity, entity_ref)``: the first in queue order wins and the
    rest become its ``also[]`` (in queue order); the winner's ``created_at`` is the
    earliest of the group's. Returns the queue, ordered."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in sorted(items, key=sort_key):
        groups.setdefault((item["entity"], item["entity_ref"]), []).append(item)
    queue = []
    for members in groups.values():
        head = dict(members[0])
        head["also"] = [dict(m) for m in members[1:]]
        head["created_at"] = min(m["created_at"] for m in members)
        queue.append(head)
    return sorted(queue, key=sort_key)


def aggregate_status(sources: list[dict[str, Any]], config_errors: list[dict[str, Any]],
                     items: list[dict[str, Any]]) -> str:
    enabled = [s for s in sources if s["status"] != "disabled"]
    if config_errors or any(s["status"] == "failed" for s in enabled):
        return "degraded"
    if any(s["status"] == "uncertain" for s in enabled):
        return "partial"
    return "attention" if items else "clear"


# -- the next cursor -----------------------------------------------------------


def encode_cursor(item: dict[str, Any]) -> str:
    raw = json.dumps([item["severity"], item["created_at"], item["id"]], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        severity, created_at, item_id = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not (isinstance(severity, int) and isinstance(created_at, str) and isinstance(item_id, str)):
            raise ValueError
        return severity, created_at, item_id
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeEncodeError) as exc:
        raise ContractError("the cursor is not one this command issued") from exc


def next_item(queue: list[dict[str, Any]], cursor: str | None) -> dict[str, Any] | None:
    """The first item strictly after the cursor's queue position (not its id, so a
    resolved or deduped item still advances the walk); wraps to the top. A cursor
    this command didn't issue is an error even when the queue is empty."""
    position = decode_cursor(cursor) if cursor is not None else None
    if not queue:
        return None
    if position is None:
        return queue[0]
    for item in queue:
        if sort_key(item) > position:
            return item
    return queue[0]


# -- command (external) sources --------------------------------------------------


def command_failure(error: str) -> dict[str, Any]:
    return {"items": [], "status": "failed", "error": one_line(error) or "source failed", "uncertain": 0}


def normalize_command_result(raw: Any, *, name: str) -> dict[str, Any]:
    """Validate a command source's envelope and translate omitted fields, never
    guessing. Returns a partial source result whose ``items`` still need
    :func:`stamp_command_item`; a contract violation becomes ``failed``."""
    if not isinstance(raw, dict):
        return command_failure("output is not a JSON object")
    if not is_schema(raw.get("schema")):
        return command_failure(f"unsupported schema {raw.get('schema')!r}")
    items = raw.get("items")
    if not isinstance(items, list):
        return command_failure("response has no items[]")
    uncertain = raw.get("uncertain", 0)
    if not isinstance(uncertain, int) or isinstance(uncertain, bool) or uncertain < 0:
        return command_failure("uncertain must be a non-negative integer")
    status = raw.get("status")
    if status is None:
        status = "uncertain" if uncertain else "ok"
    if status not in COMMAND_STATUSES:
        return command_failure(f"status {status!r} is not ok, failed or uncertain")
    if (status == "ok" and uncertain) or (status == "uncertain" and not uncertain):
        return command_failure(f"status {status!r} contradicts uncertain={uncertain}")
    if status == "failed":
        error = one_line(raw.get("error"))
        return {"items": [], "status": "failed", "uncertain": 0,
                "error": error or "source reported failed without an error"}
    result = {"items": items, "status": status, "uncertain": uncertain}
    if raw.get("read_at") is not None:
        try:
            result["read_at"] = canonical_time(raw["read_at"])
        except ContractError as exc:
            return command_failure(f"read_at: {exc}")
    return result


def stamp_command_item(item: Any, *, name: str) -> dict[str, Any]:
    """Steps (0)-(2) of the boundary: canonicalize ``entity``, reject a foreign
    ``source``/``id``, then set ``source`` and derive ``id``. Time stamping and the
    final :func:`validate_item` are the caller's."""
    if not isinstance(item, dict):
        raise ContractError("an item must be an object")
    item = dict(item)
    item["entity"] = canonical_entity(item.get("entity"), name)
    if "source" in item and item["source"] != name:
        raise ContractError(f"item claims source {item['source']!r}")
    ref = item.get("entity_ref")
    if not isinstance(ref, str) or not ref:
        raise ContractError("entity_ref must be a non-empty string")
    derived = make_id(name, item["entity"], ref)
    if "id" in item and item["id"] != derived:
        raise ContractError(f"item id {item['id']!r} is not {derived!r}")
    if "also" in item and item["also"] != []:  # {}, null and "" are malformed too, not "empty"
        raise ContractError("also[] is aggregator-owned; a source must not fill it")
    if "input" in item:  # its only submission path is `steer submit`, for a dispatch card
        raise ContractError("input is reserved for dispatch steering items; a command source can't set it")
    item.update(source=name, id=derived, also=[])
    for key in ("created_at", "updated_at"):  # a command may send any ISO-8601 spelling
        if item.get(key) is not None:
            item[key] = canonical_time(item[key])
    if item.get("display_state") in SEVERITY:
        item.setdefault("severity", SEVERITY[item["display_state"]])
    return item
