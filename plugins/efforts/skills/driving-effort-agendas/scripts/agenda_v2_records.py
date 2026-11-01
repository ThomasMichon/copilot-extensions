"""Structural immutable-record crosslinks; no authenticity or relevance attestation."""

from __future__ import annotations

from typing import Any

from agenda import AgendaError, _text, _time
from agenda_context import facts
from agenda_operations import evidence, fields
from agenda_v2_schema import record

SHAPES = {
    "context": ({"facts", "semantic", "parent_context_id", "judgment"}, {"delta"}),
    "frame": ({"frame_id", "root_id", "parent_link", "source", "baseline_context_id"}, set()),
    "item": ({"item_id", "frame_id", "root_id", "definition_id", "definition",
              "baseline_context_id", "source", "semantic_key"}, set()),
    "attempt": ({"item_id", "context_id", "predecessor_attempt_id", "approach", "definition"}, set()),
    "result": ({"item_id", "attempt_id", "outcome", "transition", "evidence"}, set()),
    "expansion": ({"parent_frame_id", "parent_item_id", "parent_attempt_id", "continuation", "evidence"}, set()),
    "return": ({"frame_id", "expansion_id", "acceptance_ref", "evidence"}, set()),
    "assignment": ({"frame_id", "item_ids", "assignment_ref", "evidence"}, set()),
    "decision": ({"decision", "reason", "item_id"}, set()),
    "context-proposal": ({"item_id", "predecessor_attempt_id", "context", "judgment", "decision"}, set()),
    "wait": ({"item_id", "reason_kind", "reason_ref", "next_action", "evidence"}, set()),
    "deferred": ({"frames", "frame_state", "item_state", "resource_caps", "decision_ref",
                  "decision_packet_ref", "revisit_at", "saved_revision", "evidence"}, set()),
    "recovery": ({"deferred_id", "recheck_ref", "acceptance_ref", "attempt_policy", "evidence"}, set()),
    "refinement": ({"board_ref", "revision", "patches", "evidence"}, set()),
}


def validate(documents: dict[str, Any], records: dict[str, Any]) -> None:
    cursor = documents["cursor"]
    frames, items = cursor["frame_state"], cursor["item_state"]
    subjects = {subject for board in documents["boards"].values()
                for definition in board["items"].values() for subject in definition["relevant_subjects"]}
    results: dict[str, dict[str, Any]] = {}
    for identity, value in records.items():
        body = record(records, identity)
        kind = value["kind"]
        if not isinstance(kind, str) or kind not in SHAPES:
            raise AgendaError("unsupported immutable record kind")
        fields(body, *SHAPES[kind])
        _time(value["created_at"], "record creation time")
        if "evidence" in body:
            evidence(body["evidence"])
        if kind == "context":
            _, normalized = facts(body["facts"], subjects)
            if normalized != body["semantic"]:
                raise AgendaError("immutable context semantic facts were rewritten")
            visited = {identity}
            parent_id = body["parent_context_id"]
            if parent_id is None:
                if body["judgment"] is not None or "delta" in body:
                    raise AgendaError("baseline context cannot claim a qualified predecessor")
            else:
                fields(body["judgment"], {"qualification", "changed_subjects", "causal_change", "evidence_refs"})
                if body["judgment"]["qualification"] != "agent-judgment":
                    raise AgendaError("context lacks qualified causal judgment")
                _text(body["judgment"]["causal_change"], "causal change")
                while parent_id is not None:
                    if parent_id in visited:
                        raise AgendaError("context predecessor lineage is cyclic")
                    visited.add(parent_id)
                    parent_id = record(records, parent_id, "context")["parent_context_id"]
        elif kind == "frame":
            frame_id = body["frame_id"]
            if frame_id not in frames or frames[frame_id]["origin_id"] != identity:
                raise AgendaError("frame origin is not linked by its current frame")
            root_id = body["root_id"]
            if root_id not in frames:
                raise AgendaError("frame root does not exist")
            root = record(records, frames[root_id]["origin_id"], "frame")
            if root["root_id"] != root_id or root["parent_link"] is not None:
                raise AgendaError("frame root does not identify an original root")
            if frames[frame_id]["parent_link"] != body["parent_link"]:
                raise AgendaError("mutable parent continuation differs from original frame")
            visited_frames = {frame_id}
            ancestor = body["parent_link"]
            while ancestor is not None:
                parent_id = ancestor["parent_frame_id"]
                if parent_id in visited_frames or parent_id not in frames:
                    raise AgendaError("original parent frame lineage is cyclic or missing")
                visited_frames.add(parent_id)
                ancestor = record(records, frames[parent_id]["origin_id"], "frame")["parent_link"]
            record(records, body["baseline_context_id"], "context")
            link = body["parent_link"]
            if link is not None:
                expansion = record(records, link["expansion_id"], "expansion")
                if any(link[key] != expansion[key] for key in
                       ("parent_frame_id", "parent_item_id", "parent_attempt_id")):
                    raise AgendaError("frame parent links differ from original expansion")
                if expansion["continuation"]["next_action"] != link["next_action"]:
                    raise AgendaError("frame continuation differs from original expansion")
                parent = record(records, frames[link["parent_frame_id"]]["origin_id"], "frame")
                if parent["root_id"] != root_id:
                    raise AgendaError("child frame crossed root custody")
        elif kind == "item":
            item_id, frame_id = body["item_id"], body["frame_id"]
            if item_id not in items or frame_id not in frames or items[item_id]["origin_id"] != identity:
                raise AgendaError("item origin or owning frame does not exist")
            if items[item_id]["baseline_context_id"] != body["baseline_context_id"]:
                raise AgendaError("item baseline context was rewritten")
            if items[item_id]["current_definition_ref"] != {"board": "root", "definition": body["definition_id"]}:
                raise AgendaError("item definition identity was rewritten")
            parent = record(records, frames[frame_id]["origin_id"], "frame")
            if body["root_id"] != parent["root_id"]:
                raise AgendaError("item belongs to a different root")
            record(records, body["baseline_context_id"], "context")
        elif kind == "attempt":
            item_id = body["item_id"]
            if item_id not in items or identity not in items[item_id]["attempts"]:
                raise AgendaError("attempt is not retained by its original item")
            item = items[item_id]
            position = item["attempts"].index(identity)
            expected = item["attempts"][position - 1] if position else None
            predecessor = body["predecessor_attempt_id"]
            if predecessor != expected:
                raise AgendaError("attempt predecessor lineage was rewritten")
            context = record(records, body["context_id"], "context")
            fields(body["approach"], {"action"})
            _text(body["approach"]["action"], "recorded attempt action")
            if predecessor is None:
                if body["context_id"] != item["baseline_context_id"]:
                    raise AgendaError("first attempt did not retain its original baseline")
            else:
                previous = record(records, predecessor, "attempt")
                if previous["item_id"] != item_id:
                    raise AgendaError("attempt predecessor crosses item lineage")
                if context["parent_context_id"] != previous["context_id"]:
                    raise AgendaError("attempt context does not extend its predecessor context")
        elif kind == "result":
            attempt_id = body["attempt_id"]
            attempt = record(records, attempt_id, "attempt")
            if body["item_id"] != attempt["item_id"]:
                raise AgendaError("outcome result crosses item lineage")
            if attempt_id in results:
                raise AgendaError("attempt has multiple immutable outcomes")
            if attempt["definition"]["on"].get(body["outcome"]) != body["transition"]:
                raise AgendaError("result does not follow its attempt's declared outcome")
            if not all(criterion in [entry["criterion"] for entry in body["evidence"]]
                       for criterion in attempt["definition"]["exit"]):
                raise AgendaError("result evidence omits the original attempt exit")
            results[attempt_id] = body
        elif kind == "expansion":
            parent_id = body["parent_item_id"]
            if parent_id not in items or body["parent_frame_id"] not in frames:
                raise AgendaError("expansion parent no longer exists")
            attempt = record(records, body["parent_attempt_id"], "attempt")
            if attempt["item_id"] != parent_id:
                raise AgendaError("expansion parent attempt crosses item lineage")
            item_origin = record(records, items[parent_id]["origin_id"], "item")
            if item_origin["frame_id"] != body["parent_frame_id"]:
                raise AgendaError("expansion parent item belongs to another frame")
            fields(body["continuation"], {"parent_item_id", "next_action"})
            if body["continuation"]["parent_item_id"] != parent_id:
                raise AgendaError("expansion continuation crosses parent lineage")
        elif kind == "return":
            if body["frame_id"] not in frames:
                raise AgendaError("return child frame is missing")
            link = frames[body["frame_id"]]["parent_link"]
            if link is None or link["expansion_id"] != body["expansion_id"]:
                raise AgendaError("return does not match the child's original expansion")
        elif kind == "deferred":
            if not isinstance(body["frames"], list) or not body["frames"]:
                raise AgendaError("deferred record lacks its full original stack")
            for frame_id in body["frames"]:
                if frame_id not in frames or frame_id not in body["frame_state"]:
                    raise AgendaError("deferred frame custody or saved state was lost")
                origin = frames[frame_id]["origin_id"]
                if body["frame_state"][frame_id]["origin_id"] != origin:
                    raise AgendaError("deferred frame changed its original custody")
            _time(body["revisit_at"], "deferred revisit")
        elif kind == "recovery":
            record(records, body["deferred_id"], "deferred")
            if body["attempt_policy"] != "resume-only":
                raise AgendaError("recovery cannot fork or replay attempts")
    for identity, item in items.items():
        successes = [results[attempt] for attempt in item["attempts"] if attempt in results
                     and results[attempt]["transition"] == "advance"]
        if bool(successes) != (item["status"] == "completed") or len(successes) > 1:
            raise AgendaError("item completion and immutable successful outcomes disagree")
        for attempt in item["attempts"][1:]:
            predecessor = record(records, attempt, "attempt")["predecessor_attempt_id"]
            if predecessor not in results or results[predecessor]["transition"] == "advance":
                raise AgendaError("revisit lacks an unsuccessful predecessor outcome")
    for saved in cursor["deferred"]:
        original = record(records, saved["id"], "deferred")
        if any(saved[key] != original[key] for key in
               ("frames", "decision_ref", "decision_packet_ref", "revisit_at", "saved_revision")):
            raise AgendaError("deferred decision, revisit or original stack was rewritten")
        if any(cursor["resource_caps"][key] > value for key, value in original["resource_caps"].items()):
            raise AgendaError("deferred original resource caps cannot be loosened")
        for frame_id in saved["frames"]:
            captured = original["frame_state"][frame_id]
            current = frames[frame_id]
            if current != captured:
                raise AgendaError("deferred owned frame or pending queue drifted before recovery")
            for item_id, captured_item in original["item_state"].items():
                item_origin = record(records, captured_item["origin_id"], "item")
                if item_origin["frame_id"] != frame_id:
                    continue
                if item_id not in items or items[item_id] != captured_item:
                    raise AgendaError("deferred owned item/attempt/continuation drifted before recovery")
            captured_items = {item_id for item_id, value in original["item_state"].items()
                              if record(records, value["origin_id"], "item")["frame_id"] == frame_id}
            current_items = {item_id for item_id, value in items.items()
                            if record(records, value["origin_id"], "item")["frame_id"] == frame_id}
            if captured_items != current_items:
                raise AgendaError("deferred frame gained or lost owned item membership")
