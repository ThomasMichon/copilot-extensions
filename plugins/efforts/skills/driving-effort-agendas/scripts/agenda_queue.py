"""Frame-local queues and append-only item/attempt/result identities."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import unicodedata
from typing import Any
from uuid import uuid4

from agenda import AgendaError, _text
from agenda_operations import fields
from agenda_v2_schema import definition, record


def source_aliases(value: dict[str, Any]) -> set[str]:
    return {unicodedata.normalize("NFC", _text(alias, "source alias")).strip()
            for alias in [value["source_ref"], *value.get("source_aliases", [])]}


@dataclass
class QueueState:
    documents: dict[str, Any]
    records: dict[str, Any]
    at: str
    actor: dict[str, Any]

    @property
    def cursor(self) -> dict[str, Any]:
        return self.documents["cursor"]

    def add(self, kind: str, body: dict[str, Any], identity: str | None = None) -> str:
        identity = identity if identity is not None else uuid4().hex
        if identity in self.records:
            raise AgendaError("immutable record identity already exists")
        self.records[identity] = {"schema": "efforts.agenda-record", "version": 2,
            "id": identity, "kind": kind, "body": deepcopy(body), "created_at": self.at,
            "actor": deepcopy(self.actor)}
        return identity

    def frame(self, identity: Any) -> dict[str, Any]:
        identity = _text(identity, "frame identity")
        if identity not in self.cursor["frame_state"]:
            raise AgendaError("unknown frame")
        return self.cursor["frame_state"][identity]

    def head(self, frame_id: str, item_id: str, attempt_id: str | None = None) -> dict[str, Any]:
        if not self.cursor["frames"] or self.cursor["frames"][-1] != frame_id:
            raise AgendaError("operation requires the active top frame")
        frame = self.frame(frame_id)
        if not frame["pending"] or frame["pending"][0] != item_id:
            raise AgendaError("operation requires the frame's pending head")
        item = self.cursor["item_state"][item_id]
        if item["status"] == "completed":
            raise AgendaError("completed items cannot be resurrected")
        if attempt_id is not None and item["current_attempt_id"] != attempt_id:
            raise AgendaError("attempt does not match the item's continuation")
        return item

    def results(self, attempt_id: str) -> list[dict[str, Any]]:
        return [value["body"] for value in self.records.values()
                if value["kind"] == "result" and value["body"]["attempt_id"] == attempt_id]

    def start(self, item_id: str, context_id: str, approach: Any,
              predecessor: str | None = None) -> str:
        item = self.cursor["item_state"][item_id]
        if item["status"] == "completed":
            raise AgendaError("completed item cannot start a new attempt")
        fields(approach, {"action"})
        _text(approach["action"], "attempt approach")
        record(self.records, context_id, "context")
        attempt = self.add("attempt", {"item_id": item_id, "context_id": context_id,
            "predecessor_attempt_id": predecessor, "approach": approach,
            "definition": deepcopy(definition(self.documents, item))})
        item["attempts"].append(attempt)
        item["visits"] += 1
        item["current_attempt_id"] = attempt
        item["status"] = "running"
        item["next_action"] = approach["action"]
        self.cursor["status"] = "working"
        return attempt

    def start_head(self, frame_id: str) -> str | None:
        frame = self.frame(frame_id)
        if not frame["pending"]:
            return None
        identity = frame["pending"][0]
        item = self.cursor["item_state"][identity]
        if item["current_attempt_id"] is not None:
            return item["current_attempt_id"]
        return self.start(identity, item["baseline_context_id"],
                          {"action": definition(self.documents, item)["action"]})

    def append(self, frame_id: str, values: Any, baseline: str) -> list[str]:
        frame = self.frame(frame_id)
        if frame["closed"]:
            raise AgendaError("closed frame cannot accept more work")
        if not isinstance(values, list) or not values:
            raise AgendaError("items must be a nonempty ordered list")
        root = record(self.records, frame["origin_id"], "frame")["root_id"]
        existing = {record(self.records, value["origin_id"], "item")["semantic_key"]
                    for value in self.cursor["item_state"].values()}
        aliases: set[str] = set()
        for value in self.cursor["item_state"].values():
            aliases.update(source_aliases(record(self.records, value["origin_id"], "item")["definition"]))
            aliases.update(source_aliases(definition(self.documents, value)))
        created: list[str] = []
        for value in values:
            fields(value, {"definition_ref"})
            ref = _text(value["definition_ref"], "definition reference")
            if ref not in self.documents["boards"]["root"]["items"]:
                raise AgendaError("unknown definition reference")
            current = self.documents["boards"]["root"]["items"][ref]
            semantic_key = json.dumps(
                {"source": self.documents["boards"]["root"]["source"],
                 "source_ref": current["source_ref"], "scope_ref": current["scope_ref"]},
                sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            # Refined presentation must not give an existing definition a new origin.
            if any(record(self.records, item["origin_id"], "item")["definition_id"] == ref
                   for item in self.cursor["item_state"].values()):
                raise AgendaError("definition already has an item in this root, including completed history")
            if semantic_key in existing or source_aliases(current) & aliases:
                raise AgendaError("same semantic item cannot be re-recorded under a new identity")
            existing.add(semantic_key)
            aliases.update(source_aliases(current))
            identity = uuid4().hex
            origin = self.add("item", {"item_id": identity, "frame_id": frame_id, "root_id": root,
                "definition_id": ref, "definition": deepcopy(current),
                "baseline_context_id": baseline,
                "source": deepcopy(self.documents["boards"]["root"]["source"]),
                "semantic_key": semantic_key})
            self.cursor["item_state"][identity] = {"origin_id": origin,
                "current_definition_ref": {"board": "root", "definition": ref},
                "current_attempt_id": None, "status": "pending", "attempts": [],
                "visits": 0, "decompositions": 0, "baseline_context_id": baseline,
                "next_action": current["action"]}
            frame["pending"].append(identity)
            created.append(identity)
        return created

    def item_references(self, values: Any) -> None:
        if not isinstance(values, list) or not values:
            raise AgendaError("items must be a nonempty ordered list")
        for value in values:
            fields(value, {"definition_ref"})
            ref = _text(value["definition_ref"], "definition reference")
            if ref not in self.documents["boards"]["root"]["items"]:
                raise AgendaError("unknown definition reference")

    def make_frame(self, baseline: str, parent_link: dict[str, Any] | None = None,
                   root_id: str | None = None) -> str:
        identity = uuid4().hex
        root = identity if root_id is None else root_id
        origin = self.add("frame", {"frame_id": identity, "root_id": root,
            "parent_link": deepcopy(parent_link),
            "baseline_context_id": baseline,
            "source": deepcopy(self.documents["boards"]["root"]["source"])})
        self.cursor["frame_state"][identity] = {"origin_id": origin,
            "parent_link": deepcopy(parent_link), "pending": [], "closed": False}
        return identity

    def bounds(self, pending_added: int = 0, depth_added: int = 0,
               decomposing: dict[str, Any] | None = None) -> str | None:
        caps = self.cursor["resource_caps"]
        pending = sum(len(frame["pending"]) for frame in self.cursor["frame_state"].values()
                      if not frame["closed"])
        if pending + pending_added > caps["max_pending_items"]:
            return "pending-item-resource-bound"
        if len(self.cursor["frames"]) + depth_added > caps["max_depth"]:
            return "depth-resource-bound"
        if decomposing is not None:
            for identity in self.cursor["frames"]:
                pending = self.frame(identity)["pending"]
                if pending and self.cursor["item_state"][pending[0]]["decompositions"] >= caps["max_no_parent_progress"]:
                    return "no-parent-progress-resource-bound"
        return None

    def replan(self, reason: str, item_id: str | None = None) -> dict[str, Any]:
        if item_id is not None:
            self.cursor["item_state"][item_id]["status"] = "replanning"
        self.cursor["status"] = "replanning"
        message = f"{reason}: bounded investigation/refinement/wait or other authorized work."
        if item_id is not None:
            self.cursor["item_state"][item_id]["next_action"] = message
        self.cursor["notes"].append(message)
        event = self.add("decision", {"decision": "not-admitted", "reason": reason, "item_id": item_id})
        return {"decision": "not-admitted", "reason_code": reason, "record_ids": [event],
                "attempt_id": None, "next_action": message}


def init_queue(board: dict[str, Any], worktree: str, caps: dict[str, Any],
               context: dict[str, Any], at: str, actor: dict[str, Any]) -> QueueState:
    cursor: dict[str, Any] = {"version": 4, "mode": "context-qualified", "worktree": worktree,
        "revision": 0, "status": "working", "frames": [], "frame_state": {}, "item_state": {},
        "deferred": [], "parked": [], "resource_caps": deepcopy(caps), "notes": [],
        "baseline_context_id": ""}
    state = QueueState({"cursor": cursor, "boards": {"root": deepcopy(board)}}, {}, at, actor)
    baseline = state.add("context", {**context, "parent_context_id": None, "judgment": None})
    cursor["baseline_context_id"] = baseline
    frame = state.make_frame(baseline)
    cursor["frames"] = [frame]
    if state.bounds(pending_added=len(board["order"])) is not None:
        raise AgendaError("initial queue exceeds explicit resource caps")
    state.append(frame, [{"definition_ref": ref} for ref in board["order"]], baseline)
    state.start_head(frame)
    return state
