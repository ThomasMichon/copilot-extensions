"""Semantic agenda movements; data never grants authority or executes effects."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4

from agenda import AgendaError, _number, _object, _text, _time, validate_board


def fields(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    data = _object(value, "operation")
    missing = required - data.keys()
    extra = data.keys() - required - (optional or set())
    if missing or extra:
        raise AgendaError(f"invalid fields: missing={sorted(missing)}, unknown={sorted(extra)}")
    return data


def evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise AgendaError("operation requires nonempty evidence")
    for item in value:
        fields(item, {"criterion", "ref", "inputs", "kind"})
        for key in ("criterion", "ref", "inputs"):
            _text(item[key], f"evidence {key}")
        if item["kind"] not in ("measured", "judgment"):
            raise AgendaError("evidence kind must be measured or judgment")
    return deepcopy(value)


def enter(frame: dict[str, Any], board: dict[str, Any], node: str, at: str,
          action: str | None = None) -> None:
    if node not in board["nodes"]:
        raise AgendaError("destination node does not exist")
    frame["node"] = node
    frame["visits"][node] = frame["visits"].get(node, 0) + 1
    frame["next_action"] = _text(action or board["nodes"][node]["action"], "next action")
    frame["entered_at"] = at
    frame["occurrence_id"] = str(uuid4())
    frame["timing_qualification"] = "generated"


def new_frame(name: str, board: dict[str, Any], at: str) -> dict[str, Any]:
    frame: dict[str, Any] = {"agenda": name, "visits": {}, "evidence": [], "return_to": None}
    if "revision" in board:
        frame["agenda_revision"] = board["revision"]
    enter(frame, board, board["entry"], at)
    return frame


def chains(cursor: dict[str, Any]) -> list[list[dict[str, Any]]]:
    return [cursor["frames"]] + [item["frames"] for item in cursor.get("deferred", [])]


def _top(cursor: dict[str, Any]) -> dict[str, Any]:
    if not cursor["frames"]:
        raise AgendaError("operation requires an active frame")
    return cursor["frames"][-1]


def _branch(cursor: dict[str, Any], identity: Any) -> dict[str, Any]:
    _text(identity, "branch id")
    for branch in cursor.get("deferred", []):
        if branch["id"] == identity:
            return branch
    raise AgendaError("unknown deferred branch")


def _append_evidence(frame: dict[str, Any], supplied: Any) -> None:
    original = frame.get("evidence", [])
    if not isinstance(original, list):
        raise AgendaError("recorded evidence is not a list; reconcile without discarding it")
    frame["evidence"] = original + evidence(supplied)


def _extend(container: dict[str, Any], key: str, values: list[Any]) -> None:
    original = container.get(key, [])
    if not isinstance(original, list):
        raise AgendaError(f"recorded {key} is not a list; reconcile without discarding it")
    container[key] = original + deepcopy(values)


def _add_board(documents: dict[str, Any], name: Any, board: Any) -> str:
    name = _text(name, "board name")
    if name in documents["boards"]:
        raise AgendaError("board already exists; refine it explicitly")
    documents["boards"][name] = deepcopy(validate_board(_object(board, "board")))
    return name


def _refine(documents: dict[str, Any], operation: dict[str, Any]) -> None:
    fields(operation, {"operation", "agenda", "board", "evidence"})
    name = _text(operation["agenda"], "board name")
    if name not in documents["boards"]:
        raise AgendaError("unknown board")
    old = documents["boards"][name]
    replacement = deepcopy(validate_board(_object(operation["board"], "board")))
    if replacement["source"] != old["source"] or replacement["entry"] != old["entry"]:
        raise AgendaError("refinement must preserve original source and root entry")
    # Retain all old nodes, metadata, exits and declared edges; refinements may add.
    for key, value in old.items():
        if key not in ("nodes", "revision") and replacement.get(key) != value:
            raise AgendaError("refinement must preserve original board metadata")
    for node_id, node in old["nodes"].items():
        new = replacement["nodes"].get(node_id)
        if not isinstance(new, dict):
            raise AgendaError("refinement cannot remove original nodes")
        if new["max_visits"] > node["max_visits"]:
            raise AgendaError("refinement cannot loosen visit limits")
        if not all(item in new["exit"] for item in node["exit"]):
            raise AgendaError("refinement cannot discard exit criteria")
        if any(new["on"].get(key) != target for key, target in node["on"].items()):
            raise AgendaError("refinement cannot discard declared edges")
        for key, value in node.items():
            if key not in ("action", "exit", "on", "max_visits") and new.get(key) != value:
                raise AgendaError("refinement cannot discard node metadata")
    documents["boards"][name] = replacement
    cursor = documents["cursor"]
    for chain in chains(cursor):
        for frame in chain:
            if frame["agenda"] == name:
                if "revision" in replacement:
                    frame["agenda_revision"] = replacement["revision"]
                else:
                    frame.pop("agenda_revision", None)
    _extend(cursor, "refinements", [
        {"agenda": name, "evidence": evidence(operation["evidence"])}])


def apply_operation(documents: dict[str, Any], operation: dict[str, Any],
                    at: str, now: datetime) -> None:
    """Mutate a private copy; the store validates the entire result before commit."""
    cursor, boards = documents["cursor"], documents["boards"]
    kind = _text(operation.get("operation"), "operation name")
    if kind == "refine":
        _refine(documents, operation)
    elif kind == "select":
        fields(operation, {"operation", "agenda", "assignment_ref"})
        name = _text(operation["agenda"], "board name")
        _text(operation["assignment_ref"], "assignment reference")
        if cursor["frames"] or not cursor["queue"] or cursor["queue"][0] != name:
            raise AgendaError("select requires an empty stack and the first assigned queued root")
        cursor["queue"].pop(0)
        cursor["frames"] = [new_frame(name, boards[name], at)]
        cursor["frames"][0]["assignment_ref"] = operation["assignment_ref"]
        cursor["status"] = "working"
    elif kind == "outcome":
        fields(operation, {"operation", "outcome", "evidence"})
        frame = _top(cursor)
        node = boards[frame["agenda"]]["nodes"][frame["node"]]
        outcome = _text(operation["outcome"], "outcome")
        if outcome not in node["on"]:
            raise AgendaError("outcome must follow a declared edge")
        proof = evidence(operation["evidence"])
        if not all(criterion in [item["criterion"] for item in proof] for criterion in node["exit"]):
            raise AgendaError("outcome evidence must cover every exit criterion")
        _append_evidence(frame, proof)
        enter(frame, boards[frame["agenda"]], node["on"][outcome], at)
        cursor["status"] = "working"
    elif kind == "link":
        fields(operation, {"operation", "executions", "evidence"})
        links = operation["executions"]
        if not isinstance(links, list) or not links:
            raise AgendaError("executions must be a nonempty list")
        for link in links:
            fields(link, {"kind", "ref"})
            _text(link["kind"], "execution kind")
            _text(link["ref"], "execution reference")
        frame = _top(cursor)
        _append_evidence(frame, operation["evidence"])
        _extend(frame, "executions", links)
    elif kind == "progress":
        fields(operation, {"operation", "reason", "evidence"})
        if len(cursor["frames"]) != 1:
            raise AgendaError("parent progress requires the original root active without a child")
        frame = _top(cursor)
        _append_evidence(frame, operation["evidence"])
        _extend(cursor, "progress_decisions", [{
            "reason": _text(operation["reason"], "demonstrated parent progress"),
            "evidence": evidence(operation["evidence"]), "recorded_at": at,
            "previous_detours": cursor["detours_without_progress"]}])
        cursor["detours_without_progress"] = 0
    elif kind in ("block", "wait"):
        fields(operation, {"operation", "reason", "next_action", "evidence"})
        frame = _top(cursor)
        _append_evidence(frame, operation["evidence"])
        frame["next_action"] = _text(operation["next_action"], "next action")
        _extend(frame, "holds", [{"reason": _text(operation["reason"], "reason"),
                                "recorded_at": at, "kind": kind}])
        cursor["status"] = "steering" if kind == "block" else "waiting"
    elif kind == "defer":
        fields(operation, {"operation", "id", "reason", "decision_ref", "decision_packet_ref",
                           "revisit_at", "recheck_action", "next_action", "evidence"})
        _append_evidence(_top(cursor), operation["evidence"])
        for key in ("id", "reason", "decision_ref", "decision_packet_ref", "recheck_action"):
            _text(operation[key], key)
        if _time(operation["revisit_at"], "revisit_at") <= now:
            raise AgendaError("revisit must be after the actual attempt")
        cursor["version"] = 2
        branch = {key: operation[key] for key in
                  ("id", "reason", "decision_ref", "decision_packet_ref", "revisit_at", "recheck_action")}
        branch.update(frames=deepcopy(cursor["frames"]), saved_revision=cursor["revision"],
                      attempted_at=at, max_depth=cursor["max_depth"],
                      max_detours_without_progress=cursor["max_detours_without_progress"],
                      detours_without_progress=cursor["detours_without_progress"])
        _extend(cursor, "deferred", [branch])
        cursor["frames"] = []
        cursor["status"] = "steering"
        cursor["next_action"] = _text(operation["next_action"], "next action")
    elif kind == "response":
        fields(operation, {"operation", "id", "response_ref"})
        branch = _branch(cursor, operation["id"])
        ref = _text(operation["response_ref"], "response reference")
        _extend(branch, "responses", [{"ref": ref, "recorded_at": at}])
        branch["response_ref"] = ref
    elif kind == "recheck":
        fields(operation, {"operation", "id", "revisit_at", "evidence"})
        branch = _branch(cursor, operation["id"])
        if now < _time(branch["revisit_at"], "revisit_at") and (
            branch.get("response_ref") is None or
            branch.get("response_ref") == branch.get("checked_response_ref")
        ):
            raise AgendaError("branch is not due and has no unchecked response")
        if _time(operation["revisit_at"], "revisit_at") <= now:
            raise AgendaError("next revisit must follow the actual check")
        _extend(branch, "checks", [{"checked_at": at,
            "response_ref": branch.get("response_ref"), "evidence": evidence(operation["evidence"])}])
        branch["last_checked_at"], branch["revisit_at"] = at, operation["revisit_at"]
        if branch.get("response_ref") is not None:
            branch["checked_response_ref"] = branch["response_ref"]
    elif kind == "restore":
        fields(operation, {"operation", "id", "acceptance_ref", "evidence"})
        branch = _branch(cursor, operation["id"])
        if cursor["frames"]:
            raise AgendaError("restore requires an empty active stack; defer current work first")
        if not branch.get("checks"):
            raise AgendaError("restore requires an explicit eligible recheck before acceptance")
        _text(operation["acceptance_ref"], "current acceptance reference")
        proof = evidence(operation["evidence"])
        cursor["frames"] = deepcopy(branch["frames"])
        _append_evidence(_top(cursor), proof)
        for key in ("max_depth", "max_detours_without_progress"):
            cursor[key] = min(cursor[key], branch[key])
        cursor["detours_without_progress"] = max(
            cursor["detours_without_progress"], branch["detours_without_progress"]) + 1
        _extend(cursor, "restored_branches", [
            {"branch": deepcopy(branch), "acceptance_ref": operation["acceptance_ref"],
             "evidence": proof, "restored_at": at}])
        cursor["deferred"].remove(branch)
        cursor["status"] = "working"
    elif kind == "push":
        fields(operation, {"operation", "agenda", "assignment_ref", "reason", "return_node",
                           "return_action", "evidence"})
        parent = _top(cursor)
        name = _text(operation["agenda"], "board name")
        if name not in boards:
            raise AgendaError("child board must already be queued as an assigned board")
        if name not in cursor["queue"]:
            raise AgendaError("child must be an assigned queued board")
        _text(operation["assignment_ref"], "assignment reference")
        _text(operation["reason"], "prerequisite or priority reason")
        child = new_frame(name, boards[name], at)
        child.update(return_to={"agenda": parent["agenda"],
                     "node": _text(operation["return_node"], "return node"),
                     "next_action": _text(operation["return_action"], "return action")},
                     assignment_ref=operation["assignment_ref"], reason=operation["reason"])
        _append_evidence(child, operation["evidence"])
        cursor["frames"].append(child)
        cursor["queue"].remove(name)
        cursor["detours_without_progress"] += 1
        cursor["status"] = "working"
    elif kind in ("return", "complete-root"):
        fields(operation, {"operation", "acceptance_ref", "evidence"})
        frame = _top(cursor)
        if boards[frame["agenda"]]["nodes"][frame["node"]].get("terminal") is not True:
            raise AgendaError("acceptance return requires a terminal node")
        _text(operation["acceptance_ref"], "canonical acceptance reference")
        proof = evidence(operation["evidence"])
        criteria = boards[frame["agenda"]]["nodes"][frame["node"]]["exit"]
        if not all(criterion in [item["criterion"] for item in proof] for criterion in criteria):
            raise AgendaError("acceptance evidence must cover every exit criterion")
        _append_evidence(frame, proof)
        if kind == "return" and len(cursor["frames"]) < 2:
            raise AgendaError("root cannot return to a parent")
        if kind == "complete-root" and len(cursor["frames"]) != 1:
            raise AgendaError("complete-root cannot discard children")
        _extend(cursor, "completed_roots" if kind == "complete-root" else "child_results", [
            {"frames": deepcopy(cursor["frames"]), "acceptance_ref": operation["acceptance_ref"],
             "accepted_at": at}])
        cursor["frames"].pop()
        if kind == "return":
            parent = _top(cursor)
            target = frame["return_to"]
            enter(parent, boards[parent["agenda"]], target["node"], at, target["next_action"])
            cursor["status"] = "working"
        else:
            cursor["status"] = ("working" if cursor["queue"] else
                                "steering" if cursor.get("deferred") else "complete")
            cursor["next_action"] = "Verify canonical acceptance and handoff obligations independently."
    elif kind == "queue":
        fields(operation, {"operation", "agenda", "board", "assignment_ref", "evidence"})
        _text(operation["assignment_ref"], "assignment reference")
        name = _add_board(documents, operation["agenda"], operation["board"])
        cursor["queue"].append(name)
        _extend(cursor, "assignments", [{"agenda": name,
            "assignment_ref": operation["assignment_ref"], "evidence": evidence(operation["evidence"])}])
        if cursor["status"] == "complete":
            cursor["status"] = "working"
    elif kind == "park":
        fields(operation, {"operation", "ref", "reason"})
        cursor["parked"].append({"ref": _text(operation["ref"], "discovery reference"),
            "reason": _text(operation["reason"], "nonblocking reason"), "recorded_at": at})
    elif kind == "limits":
        fields(operation, {"operation", "limits", "evidence"})
        values = fields(operation["limits"], set(),
                        {"max_depth", "max_detours_without_progress", "max_deferred"})
        if not values:
            raise AgendaError("limits must specify a finite tighter budget")
        for key, value in values.items():
            if _number(value, key, 1) > cursor.get(key, 16):
                raise AgendaError("cannot loosen an existing budget")
            cursor[key] = value
        _extend(cursor, "limit_decisions", [{"limits": deepcopy(values),
            "evidence": evidence(operation["evidence"])}])
    else:
        raise AgendaError(f"unsupported semantic operation: {kind}")
