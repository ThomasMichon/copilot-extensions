"""Version-two board and version-four execution-state contracts."""

from __future__ import annotations

from typing import Any

from agenda import AgendaError, Snapshot, _number, _object, _text
from agenda_operations import fields

MODE = "context-qualified"
CAPS = {"max_depth", "max_pending_items", "max_deferred_frames", "max_no_parent_progress"}
STATUSES = {"working", "waiting", "replanning", "complete"}
ITEM_STATUSES = {"pending", "running", "replanning", "waiting", "completed"}


def board(value: Any) -> dict[str, Any]:
    fields(value, {"version", "source", "revision", "items", "order"})
    if type(value["version"]) is not int or value["version"] != 2:
        raise AgendaError("context-qualified boards require version 2")
    fields(value["source"], {"kind", "ref"})
    if value["source"]["kind"] not in ("effort", "task", "aside"):
        raise AgendaError("unsupported board source kind")
    _text(value["source"]["ref"], "source reference")
    _number(value["revision"], "definition revision")
    definitions = _object(value["items"], "board items")
    if not definitions:
        raise AgendaError("board items must not be empty")
    for identity, definition in definitions.items():
        _text(identity, "definition identity")
        fields(definition, {"source_ref", "scope_ref", "action", "exit", "on", "relevant_subjects"},
               {"label", "source_aliases"})
        for key in ("source_ref", "scope_ref", "action"):
            _text(definition[key], key)
        if "label" in definition:
            _text(definition["label"], "item label")
        for key in ("exit", "relevant_subjects"):
            values = definition[key]
            if not isinstance(values, list) or not values:
                raise AgendaError(f"{key} must be a nonempty list")
            for item in values:
                _text(item, key)
            if len(set(values)) != len(values):
                raise AgendaError(f"duplicate {key}")
        outcomes = _object(definition["on"], "item outcomes")
        if not outcomes:
            raise AgendaError("item outcomes must not be empty")
        for outcome, transition in outcomes.items():
            _text(outcome, "outcome")
            if transition not in ("advance", "wait", "replan"):
                raise AgendaError("item transitions must be advance, wait or replan")
        if "source_aliases" in definition:
            if not isinstance(definition["source_aliases"], list):
                raise AgendaError("source_aliases must be a list")
            for alias in definition["source_aliases"]:
                _text(alias, "source alias")
    order = value["order"]
    if not isinstance(order, list) or not order or not all(isinstance(item, str) for item in order):
        raise AgendaError("board order must be a nonempty definition queue")
    if len(set(order)) != len(order) or any(item not in definitions for item in order):
        raise AgendaError("board order has duplicate or unknown definitions")
    return value


def record(records: dict[str, Any], identity: Any, kind: str | None = None) -> dict[str, Any]:
    identity = _text(identity, "record identity")
    if identity not in records:
        raise AgendaError(f"missing immutable record: {identity}")
    value = _object(records[identity], "immutable record")
    fields(value, {"schema", "version", "id", "kind", "body", "created_at", "actor"})
    if value["schema"] != "efforts.agenda-record" or value["version"] != 2 or type(value["version"]) is not int:
        raise AgendaError("unsupported immutable record schema")
    if value["id"] != identity or (kind is not None and value["kind"] != kind):
        raise AgendaError("immutable record identity or kind mismatch")
    return _object(value["body"], "record body")


def definition(documents: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    reference = item["current_definition_ref"]
    fields(reference, {"board", "definition"})
    boards = documents["boards"]
    if reference["board"] not in boards or reference["definition"] not in boards[reference["board"]]["items"]:
        raise AgendaError("item definition reference does not exist")
    return boards[reference["board"]]["items"][reference["definition"]]


def validate(documents: Any, records: dict[str, Any]) -> None:
    fields(documents, {"cursor", "boards"})
    for value in _object(documents["boards"], "boards").values():
        board(value)
    cursor = documents["cursor"]
    fields(cursor, {"version", "mode", "worktree", "revision", "status", "frames", "frame_state",
                   "item_state", "deferred", "parked", "resource_caps", "notes", "baseline_context_id"})
    if type(cursor["version"]) is not int or cursor["version"] != 4 or cursor["mode"] != MODE:
        raise AgendaError("context-qualified execution requires cursor version 4")
    _text(cursor["worktree"], "worktree")
    _number(cursor["revision"], "cursor revision")
    if not isinstance(cursor["status"], str) or cursor["status"] not in STATUSES:
        raise AgendaError("unsupported context-qualified status")
    caps = fields(cursor["resource_caps"], CAPS)
    for key, value in caps.items():
        if not 1 <= _number(value, key, 1) <= (1 << 63) - 1:
            raise AgendaError("resource cap exceeds signed integer bounds")
    frames = _object(cursor["frame_state"], "frame state")
    items = _object(cursor["item_state"], "item state")
    active = cursor["frames"]
    if not isinstance(active, list) or len(set(active)) != len(active):
        raise AgendaError("active frames must be a unique ordered stack")
    if len(active) > caps["max_depth"]:
        raise AgendaError("depth resource bound exceeded")
    for identity, frame in frames.items():
        fields(frame, {"origin_id", "parent_link", "pending", "closed"})
        original_frame = record(records, frame["origin_id"], "frame")
        if original_frame["frame_id"] != identity or original_frame["parent_link"] != frame["parent_link"]:
            raise AgendaError("frame origin or parent continuation was rewritten")
        if type(frame["closed"]) is not bool or not isinstance(frame["pending"], list):
            raise AgendaError("invalid frame closure or pending queue")
        if len(set(frame["pending"])) != len(frame["pending"]):
            raise AgendaError("duplicate pending item")
        for item_id in frame["pending"]:
            if item_id not in items or record(records, items[item_id]["origin_id"], "item")["frame_id"] != identity:
                raise AgendaError("pending item belongs to another frame")
            if items[item_id]["status"] == "completed":
                raise AgendaError("completed item cannot reenter a pending queue")
        if frame["closed"] and frame["pending"]:
            raise AgendaError("closed frame retains pending work")
        link = frame["parent_link"]
        if link is not None:
            fields(link, {"expansion_id", "parent_frame_id", "parent_item_id", "parent_attempt_id", "next_action"})
            expansion = record(records, link["expansion_id"], "expansion")
            if link["parent_frame_id"] not in frames or link["parent_item_id"] not in items:
                raise AgendaError("parent continuation does not exist")
            if any(expansion[key] != link[key] for key in
                   ("parent_frame_id", "parent_item_id", "parent_attempt_id")):
                raise AgendaError("parent attempt or item link was rewritten")
            if expansion["continuation"]["next_action"] != link["next_action"]:
                raise AgendaError("parent continuation action was rewritten")
            parent = frames[link["parent_frame_id"]]
            if not frame["closed"] and (not parent["pending"] or parent["pending"][0] != link["parent_item_id"]):
                raise AgendaError("expanded parent item cannot advance before its child returns")
    for index, identity in enumerate(active):
        if identity not in frames or frames[identity]["closed"]:
            raise AgendaError("active stack references an unknown or closed frame")
        link = frames[identity]["parent_link"]
        if (index == 0 and link is not None) or (index > 0 and
                (link is None or link["parent_frame_id"] != active[index - 1])):
            raise AgendaError("active frame continuation does not name its immediate parent")
    for identity, item in items.items():
        fields(item, {"origin_id", "current_definition_ref", "current_attempt_id", "status",
                      "attempts", "visits", "decompositions", "baseline_context_id", "next_action"})
        _text(item["next_action"], "current item action")
        origin = record(records, item["origin_id"], "item")
        if origin["item_id"] != identity:
            raise AgendaError("item origin identity mismatch")
        definition(documents, item)
        if not isinstance(item["status"], str) or item["status"] not in ITEM_STATUSES:
            raise AgendaError("unsupported item status")
        _number(item["visits"], "cumulative item visits")
        _number(item["decompositions"], "decomposition count")
        if not isinstance(item["attempts"], list) or len(set(item["attempts"])) != len(item["attempts"]):
            raise AgendaError("invalid attempt lineage")
        if item["visits"] != len(item["attempts"]):
            raise AgendaError("cumulative visits and attempt lineage disagree")
        for index, attempt_id in enumerate(item["attempts"]):
            attempt = record(records, attempt_id, "attempt")
            if attempt["item_id"] != identity:
                raise AgendaError("attempt belongs to another item")
            predecessor = item["attempts"][index - 1] if index else None
            if attempt["predecessor_attempt_id"] != predecessor:
                raise AgendaError("attempt predecessor lineage was rewritten")
            record(records, attempt["context_id"], "context")
        if item["current_attempt_id"] is not None and item["current_attempt_id"] not in item["attempts"]:
            raise AgendaError("current attempt is outside item history")
        if item["attempts"] and item["current_attempt_id"] != item["attempts"][-1]:
            raise AgendaError("current attempt cannot rewind its immutable predecessor lineage")
        record(records, item["baseline_context_id"], "context")
    record(records, cursor["baseline_context_id"], "context")
    all_subjects = {subject for current in documents["boards"].values()
                    for value in current["items"].values() for subject in value["relevant_subjects"]}
    from agenda_context import canonical_equal, context_policy, facts
    for identity in records:
        value = records[identity]
        body = record(records, identity)
        if value["kind"] == "context":
            _, expected = facts(body["facts"], all_subjects, input_policy=context_policy(records, identity))
            if not canonical_equal(body["semantic"], expected):
                raise AgendaError("immutable context semantic facts were rewritten")
            parent = body["parent_context_id"]
            visited = {identity}
            while parent is not None:
                if parent in visited:
                    raise AgendaError("context predecessor lineage is cyclic")
                visited.add(parent)
                parent = record(records, parent, "context")["parent_context_id"]
        elif value["kind"] == "result":
            attempt = record(records, body["attempt_id"], "attempt")
            if body["item_id"] != attempt["item_id"]:
                raise AgendaError("outcome result is attached to another item")
    if sum(len(frame["pending"]) for frame in frames.values() if not frame["closed"]) > caps["max_pending_items"]:
        raise AgendaError("pending-item resource bound exceeded")
    if not isinstance(cursor["deferred"], list) or len(cursor["deferred"]) > caps["max_deferred_frames"]:
        raise AgendaError("deferred resource bound exceeded")
    occupied = set(active)
    for deferred in cursor["deferred"]:
        fields(deferred, {"id", "frames", "decision_ref", "decision_packet_ref", "revisit_at", "saved_revision"})
        record(records, deferred["id"], "deferred")
        if not isinstance(deferred["frames"], list) or not deferred["frames"]:
            raise AgendaError("deferred stack must not be empty")
        if any(identity not in frames or identity in occupied for identity in deferred["frames"]):
            raise AgendaError("a frame cannot be active or deferred twice")
        occupied.update(deferred["frames"])
        for index, frame_id in enumerate(deferred["frames"]):
            link = frames[frame_id]["parent_link"]
            if (index == 0 and link is not None) or (index and (
                    link is None or link["parent_frame_id"] != deferred["frames"][index - 1])):
                raise AgendaError("deferred frame ancestry or original return was rewritten")
            if frames[frame_id]["closed"]:
                raise AgendaError("completed frame remains deferred")
    if cursor["status"] == "complete" and (active or cursor["deferred"]):
        raise AgendaError("complete cursor retains open stack or deferred work")
    if not isinstance(cursor["parked"], list) or not isinstance(cursor["notes"], list):
        raise AgendaError("parked and notes must be lists")
    from agenda_v2_records import validate as validate_records
    validate_records(documents, records)


def project(documents: dict[str, Any], records: dict[str, Any]) -> Snapshot:
    from agenda_v2_projection import project as project_snapshot
    return project_snapshot(documents, records)
