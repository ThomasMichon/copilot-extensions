"""Context-qualified semantic operations. No external effect is executed."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from agenda import AgendaError, _number, _object, _text, _time
from agenda_operations import fields, evidence
from agenda_context import (
    context_body, context_policy, qualification, validate_context_shape, validate_judgment_shape,
)
from agenda_queue import QueueState, validate_item_references
from agenda_v2_schema import definition, record, validate_definition


_CHANGE_FIELDS = {
    "assign-root": {"operation", "items", "assignment_ref", "baseline_context", "evidence"},
    "append": {"operation", "frame_id", "items", "assignment_ref", "evidence"},
    "outcome": {"operation", "frame_id", "item_id", "attempt_id", "outcome", "evidence"},
    "expand": {"operation", "frame_id", "item_id", "attempt_id", "items", "continuation", "evidence"},
    "return": {"operation", "frame_id", "expansion_id", "acceptance_ref", "evidence"},
    "revisit": {"operation", "frame_id", "item_id", "predecessor_attempt_id", "context", "judgment", "approach"},
    "wait": {"operation", "frame_id", "item_id", "reason_kind", "reason_ref", "next_action", "evidence"},
    "defer": {"operation", "frame_id", "decision_ref", "decision_packet_ref", "revisit_at", "evidence"},
    "recover": {"operation", "deferred_id", "recheck_ref", "acceptance_ref", "attempt_policy", "evidence"},
    "refine": {"operation", "board_ref", "expected_definition_revision", "patches", "evidence"},
}


def _text_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        raise AgendaError(f"{label} must be a nonempty list")
    for item in value:
        _text(item, label)
    if len(set(value)) != len(value):
        raise AgendaError(f"duplicate {label}")


def validate_change_shape(change: dict[str, Any], *, accepted_at: str | None = None) -> str:
    """Validate operation grammar without applying movement or asserting acceptance."""
    change = _object(change, "semantic change")
    kind = _text(change.get("operation"), "operation")
    if kind not in _CHANGE_FIELDS:
        raise AgendaError(f"unsupported context-qualified operation: {kind}")
    fields(change, _CHANGE_FIELDS[kind])
    structured = {
        "operation", "items", "baseline_context", "evidence", "context", "judgment",
        "approach", "continuation", "reason_kind", "revisit_at", "attempt_policy",
        "expected_definition_revision", "patches",
    }
    for key in _CHANGE_FIELDS[kind] - structured:
        _text(change[key], key)
    if "evidence" in change:
        evidence(change["evidence"])
    if "items" in change:
        validate_item_references(change["items"])
    if kind == "assign-root":
        validate_context_shape(change["baseline_context"])
    elif kind == "expand":
        fields(change["continuation"], {"parent_item_id", "next_action"})
        if change["continuation"]["parent_item_id"] != change["item_id"]:
            raise AgendaError("expansion continuation must preserve the current parent item")
        _text(change["continuation"]["next_action"], "parent continuation")
    elif kind == "revisit":
        fields(change["approach"], {"action"})
        _text(change["approach"]["action"], "attempt approach")
        validate_context_shape(change["context"])
        validate_judgment_shape(change["judgment"])
    elif kind == "wait":
        if change["reason_kind"] not in ("permission", "human-decision", "external", "bounded-replan"):
            raise AgendaError("unsupported wait reason")
    elif kind == "defer":
        revisit = _time(change["revisit_at"], "revisit time")
        if accepted_at is not None and revisit <= _time(accepted_at, "actual time"):
            raise AgendaError("deferred revisit must follow the actual attempt")
    elif kind == "recover":
        if change["attempt_policy"] != "resume-only":
            raise AgendaError("recovery requires an empty stack and resume-only policy")
    elif kind == "refine":
        if change["board_ref"] != "root":
            raise AgendaError("unknown board reference")
        _number(change["expected_definition_revision"], "definition revision")
        patches = change["patches"]
        if not isinstance(patches, list) or not patches:
            raise AgendaError("refinement patches must be a nonempty list")
        for patch in patches:
            fields(patch, {"definition_id"}, {"action", "label", "exit_append",
                   "relevant_subjects_append", "source_aliases_append", "add_definition"})
            _text(patch["definition_id"], "definition identity")
            if "add_definition" in patch:
                fields(patch, {"definition_id", "add_definition"})
                validate_definition(patch["add_definition"])
            else:
                if len(patch) == 1:
                    raise AgendaError("refinement requires a known definition and explicit changes")
                for key in ("action", "label"):
                    if key in patch:
                        _text(patch[key], key)
                for key in ("exit_append", "relevant_subjects_append", "source_aliases_append"):
                    if key in patch:
                        _text_list(patch[key], key)
    return kind


def apply(state: QueueState, change: dict[str, Any]) -> dict[str, Any]:
    kind = validate_change_shape(change, accepted_at=state.at)
    cursor = state.cursor
    if kind == "assign-root":
        if cursor["frames"]:
            raise AgendaError("assign-root requires an empty active stack; preserve the current stack first")
        _text(change["assignment_ref"], "root assignment reference")
        proof = evidence(change["evidence"])
        state.item_references(change["items"])
        subjects = {subject for value in change["items"]
                    for subject in state.documents["boards"]["root"]["items"][value["definition_ref"]]["relevant_subjects"]}
        baseline = context_body(change["baseline_context"], subjects)
        reason = state.bounds(pending_added=len(change["items"]), depth_added=1)
        if reason is not None:
            return state.replan(reason)
        context_id = state.add("context", {**baseline, "parent_context_id": None, "judgment": None})
        frame_id = state.make_frame(context_id)
        item_ids = state.append(frame_id, change["items"], context_id)
        cursor["frames"] = [frame_id]
        attempt = state.start_head(frame_id)
        assignment = state.add("assignment", {"frame_id": frame_id, "item_ids": item_ids,
            "assignment_ref": change["assignment_ref"], "evidence": proof})
        return {"decision": "root-assigned", "frame_id": frame_id, "item_ids": item_ids,
                "item_id": item_ids[0], "attempt_id": attempt, "context_id": context_id,
                "record_ids": [assignment]}
    if kind == "append":
        frame = state.frame(change["frame_id"])
        if frame["closed"]:
            raise AgendaError("completed frame cannot be reopened")
        if change["frame_id"] not in cursor["frames"]:
            raise AgendaError("append requires an active frame; recover deferred custody before changing its queue")
        _text(change["assignment_ref"], "assignment reference")
        proof = evidence(change["evidence"])
        values = change["items"]
        state.item_references(values)
        reason = state.bounds(pending_added=len(values))
        if reason is not None:
            return state.replan(reason)
        frame_origin = record(state.records, frame["origin_id"], "frame")
        ids = state.append(change["frame_id"], values, frame_origin["baseline_context_id"])
        event = state.add("assignment", {"frame_id": change["frame_id"], "item_ids": ids,
            "assignment_ref": change["assignment_ref"], "evidence": proof})
        attempt = None
        if cursor["frames"] and cursor["frames"][-1] == change["frame_id"]:
            attempt = state.start_head(change["frame_id"])
        return {"decision": "appended", "frame_id": change["frame_id"], "item_ids": ids,
                "attempt_id": attempt, "record_ids": [event]}
    if kind == "outcome":
        item = state.head(change["frame_id"], change["item_id"], change["attempt_id"])
        if state.results(change["attempt_id"]):
            raise AgendaError("attempt already has an immutable outcome")
        current = definition(state.documents, item)
        outcome = _text(change["outcome"], "outcome")
        if outcome not in current["on"]:
            raise AgendaError("outcome is not declared by this item")
        proof = evidence(change["evidence"])
        if not all(criterion in [entry["criterion"] for entry in proof] for criterion in current["exit"]):
            raise AgendaError("outcome evidence must cover every current exit criterion")
        result_id = state.add("result", {"item_id": change["item_id"], "attempt_id": change["attempt_id"],
            "outcome": outcome, "transition": current["on"][outcome], "evidence": proof})
        transition = current["on"][outcome]
        next_attempt = None
        if transition == "advance":
            item["status"] = "completed"
            frame = state.frame(change["frame_id"])
            frame["pending"].pop(0)
            if frame["pending"]:
                next_attempt = state.start_head(change["frame_id"])
            elif frame["parent_link"] is None:
                frame["closed"] = True
                cursor["frames"].pop()
                cursor["status"] = "waiting" if cursor["deferred"] else "complete"
        elif transition == "wait":
            item["status"], cursor["status"] = "waiting", "waiting"
            item["next_action"] = "Recheck current conditions and qualify changed context; do not replay the closed attempt."
        else:
            item["status"], cursor["status"] = "replanning", "replanning"
            item["next_action"] = "Bounded investigation/refinement or wait; no productive revisit admitted."
        return {"decision": transition, "frame_id": change["frame_id"], "item_id": change["item_id"],
                "attempt_id": change["attempt_id"], "next_attempt_id": next_attempt,
                "result_id": result_id, "record_ids": [result_id]}
    if kind == "expand":
        item = state.head(change["frame_id"], change["item_id"], change["attempt_id"])
        proof = evidence(change["evidence"])
        values = change["items"]
        state.item_references(values)
        reason = state.bounds(pending_added=len(values), depth_added=1, decomposing=item)
        if reason is not None:
            return state.replan(reason, change["item_id"])
        attempt = record(state.records, change["attempt_id"], "attempt")
        root = record(state.records, state.frame(change["frame_id"])["origin_id"], "frame")["root_id"]
        expansion_id = state.add("expansion", {"parent_frame_id": change["frame_id"],
            "parent_item_id": change["item_id"], "parent_attempt_id": change["attempt_id"],
            "continuation": change["continuation"], "evidence": proof})
        link = {"expansion_id": expansion_id, "parent_frame_id": change["frame_id"],
            "parent_item_id": change["item_id"], "parent_attempt_id": change["attempt_id"],
            "next_action": change["continuation"]["next_action"]}
        child = state.make_frame(attempt["context_id"], link, root)
        ids = state.append(child, values, attempt["context_id"])
        cursor["frames"].append(child)
        # Every still-pending ancestor counts decomposition until qualified parent progress.
        for frame_id in cursor["frames"][:-1]:
            pending = state.frame(frame_id)["pending"]
            if pending:
                cursor["item_state"][pending[0]]["decompositions"] += 1
        child_attempt = state.start_head(child)
        return {"decision": "expanded", "frame_id": child, "item_ids": ids,
                "attempt_id": child_attempt, "expansion_id": expansion_id,
                "parent_item_id": change["item_id"], "record_ids": [expansion_id]}
    if kind == "return":
        if not cursor["frames"] or cursor["frames"][-1] != change["frame_id"]:
            raise AgendaError("return requires the active child frame")
        frame = state.frame(change["frame_id"])
        if frame["pending"] or frame["closed"] or frame["parent_link"] is None:
            raise AgendaError("child queue is not accepted or has no parent")
        link = frame["parent_link"]
        if link["expansion_id"] != change["expansion_id"]:
            raise AgendaError("return does not match the original expansion")
        _text(change["acceptance_ref"], "child queue acceptance")
        proof = evidence(change["evidence"])
        event = state.add("return", {"frame_id": change["frame_id"], "expansion_id": change["expansion_id"],
            "acceptance_ref": change["acceptance_ref"], "evidence": proof})
        frame["closed"] = True
        cursor["frames"].pop()
        if not cursor["frames"] or cursor["frames"][-1] != link["parent_frame_id"]:
            raise AgendaError("saved continuation is not the immediate parent")
        parent = state.head(link["parent_frame_id"], link["parent_item_id"], link["parent_attempt_id"])
        if state.results(link["parent_attempt_id"]):
            parent["status"], cursor["status"] = "replanning", "replanning"
        else:
            parent["status"], cursor["status"] = "running", "working"
        parent["next_action"] = link["next_action"]
        cursor["notes"].append("Child accepted; original parent continuation restored; no effect replay or new attempt.")
        return {"decision": "returned", "frame_id": link["parent_frame_id"],
                "item_id": link["parent_item_id"], "attempt_id": link["parent_attempt_id"],
                "next_action": link["next_action"], "record_ids": [event]}
    if kind == "revisit":
        item = state.head(change["frame_id"], change["item_id"], change["predecessor_attempt_id"])
        previous = record(state.records, change["predecessor_attempt_id"], "attempt")
        if previous["item_id"] != change["item_id"] or not state.results(change["predecessor_attempt_id"]):
            raise AgendaError("productive revisit requires an unsuccessful recorded predecessor")
        if any(result["transition"] == "advance" for result in state.results(change["predecessor_attempt_id"])):
            raise AgendaError("successful attempts cannot be resurrected")
        subjects = set(definition(state.documents, item)["relevant_subjects"])
        prior = record(state.records, previous["context_id"], "context")
        policy = context_policy(state.records, previous["context_id"])
        proposed = context_body(change["context"], subjects, input_policy=policy)
        for fact in proposed["facts"]:
            policy.setdefault(fact["subject"], fact["kind"] == "input")
        used = [record(state.records, record(state.records, identity, "attempt")["context_id"], "context")["semantic"]
                for identity in item["attempts"]]
        baseline = record(state.records, item["baseline_context_id"], "context")
        admitted, reason, delta = qualification(proposed["semantic"], prior["semantic"],
            used + [baseline["semantic"]], subjects, change["judgment"], input_policy=policy)
        if not admitted:
            decision = state.replan(reason, change["item_id"])
            state.add("context-proposal", {"item_id": change["item_id"],
                "predecessor_attempt_id": change["predecessor_attempt_id"],
                "context": proposed, "judgment": change["judgment"], "decision": reason})
            return decision
        context_id = state.add("context", {**proposed, "parent_context_id": previous["context_id"],
            "judgment": change["judgment"], "delta": delta})
        attempt_id = state.start(change["item_id"], context_id, change["approach"],
                                 change["predecessor_attempt_id"])
        item["decompositions"] = 0
        return {"decision": "revisit-admitted", "frame_id": change["frame_id"], "item_id": change["item_id"],
                "attempt_id": attempt_id, "context_id": context_id, "record_ids": [context_id, attempt_id]}
    if kind == "wait":
        item = state.head(change["frame_id"], change["item_id"])
        _text(change["reason_ref"], "wait reason reference")
        _text(change["next_action"], "wait next action")
        event = state.add("wait", {"item_id": change["item_id"], "reason_kind": change["reason_kind"],
            "reason_ref": change["reason_ref"], "next_action": change["next_action"],
            "evidence": evidence(change["evidence"])})
        item["status"], cursor["status"] = "waiting", "waiting"
        item["next_action"] = change["next_action"]
        return {"decision": "waiting", "item_id": change["item_id"], "record_ids": [event]}
    if kind == "defer":
        if not cursor["frames"] or cursor["frames"][-1] != change["frame_id"]:
            raise AgendaError("defer requires the active top frame")
        _text(change["decision_ref"], "decision reference")
        _text(change["decision_packet_ref"], "decision packet")
        if len(cursor["deferred"]) >= cursor["resource_caps"]["max_deferred_frames"]:
            return state.replan("deferred-resource-bound")
        identity = state.add("deferred", {"frames": deepcopy(cursor["frames"]),
            "frame_state": deepcopy(cursor["frame_state"]), "item_state": deepcopy(cursor["item_state"]),
            "resource_caps": deepcopy(cursor["resource_caps"]),
            "decision_ref": change["decision_ref"], "decision_packet_ref": change["decision_packet_ref"],
            "revisit_at": change["revisit_at"], "saved_revision": cursor["revision"],
            "evidence": evidence(change["evidence"])})
        cursor["deferred"].append({"id": identity, "frames": list(cursor["frames"]),
            "decision_ref": change["decision_ref"], "decision_packet_ref": change["decision_packet_ref"],
            "revisit_at": change["revisit_at"], "saved_revision": cursor["revision"]})
        cursor["frames"] = []
        cursor["status"] = "waiting"
        return {"decision": "deferred", "deferred_id": identity, "record_ids": [identity]}
    if kind == "recover":
        if cursor["frames"]:
            raise AgendaError("recovery requires an empty stack and resume-only policy")
        _text(change["recheck_ref"], "actual recheck reference")
        _text(change["acceptance_ref"], "current acceptance reference")
        saved = next((item for item in cursor["deferred"] if item["id"] == change["deferred_id"]), None)
        if saved is None:
            raise AgendaError("unknown or already recovered deferred stack")
        cursor["frames"] = list(saved["frames"])
        cursor["deferred"].remove(saved)
        frame = state.frame(cursor["frames"][-1])
        if frame["closed"]:
            raise AgendaError("completed frames cannot be recovered")
        current_attempt = None
        if frame["pending"]:
            item = cursor["item_state"][frame["pending"][0]]
            current_attempt = item["current_attempt_id"]
            cursor["status"] = "replanning" if current_attempt and state.results(current_attempt) else "working"
        else:
            cursor["status"] = "working"
        event = state.add("recovery", {"deferred_id": change["deferred_id"],
            "recheck_ref": change["recheck_ref"], "acceptance_ref": change["acceptance_ref"],
            "attempt_policy": "resume-only", "evidence": evidence(change["evidence"])})
        return {"decision": "recovered", "frame_ids": list(cursor["frames"]),
                "attempt_id": current_attempt, "record_ids": [event]}
    if kind == "refine":
        board = state.documents["boards"]["root"]
        if board["revision"] != change["expected_definition_revision"]:
            raise AgendaError("stale definition revision")
        patches = change["patches"]
        for patch in patches:
            identity = _text(patch["definition_id"], "definition identity")
            if "add_definition" in patch:
                if identity in board["items"]:
                    raise AgendaError("original definitions cannot be replaced")
                board["items"][identity] = deepcopy(patch["add_definition"])
                continue
            if identity not in board["items"]:
                raise AgendaError("refinement requires a known definition and explicit changes")
            current = board["items"][identity]
            for key in ("action", "label"):
                if key in patch:
                    current[key] = _text(patch[key], key)
            for source, destination in (("exit_append", "exit"), ("relevant_subjects_append", "relevant_subjects"),
                                        ("source_aliases_append", "source_aliases")):
                if source in patch:
                    for value in patch[source]:
                        _text(value, source)
                        if value in current.get(destination, []):
                            raise AgendaError("duplicate refinement addition")
                        current.setdefault(destination, []).append(value)
        if board["revision"] >= (1 << 63) - 1:
            raise AgendaError("definition revision is exhausted")
        board["revision"] += 1
        event = state.add("refinement", {"board_ref": "root", "revision": board["revision"],
            "patches": patches, "evidence": evidence(change["evidence"])})
        return {"decision": "refined", "definition_revision": board["revision"], "record_ids": [event]}
    raise AgendaError(f"unsupported context-qualified operation: {kind}")
