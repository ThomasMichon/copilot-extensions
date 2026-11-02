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


def _retained_chains(cursor: dict[str, Any]) -> list[tuple[list[dict[str, Any]], bool]]:
    result = [(cursor["frames"], False)]
    for key in ("deferred", "child_results", "completed_roots", "restored_branches"):
        values = cursor.get(key, [])
        if not isinstance(values, list):
            raise AgendaError(f"recorded {key} is not a list; reconcile without discarding it")
        for value in values:
            value = _object(value, key)
            saved = _object(value.get("branch"), "restored branch") if key == "restored_branches" else value
            if "frames" not in saved:
                if key in ("child_results", "completed_roots"):
                    continue
                raise AgendaError("saved history has no frame chain")
            frames = saved["frames"]
            if not isinstance(frames, list):
                raise AgendaError("saved history frames must be a list")
            result.append(([_object(frame, "historical frame") for frame in frames],
                           key in ("completed_roots", "child_results")))
    return result


def _counts(value: Any) -> dict[str, int]:
    visits = _object(value, "recorded visits")
    for node, count in visits.items():
        _text(node, "visited node")
        _number(count, "recorded visit count")
    return visits


def _contains(sequence: list[Any], values: list[Any]) -> bool:
    position = 0
    for value in values:
        while position < len(sequence) and sequence[position] != value:
            position += 1
        if position == len(sequence):
            return False
        position += 1
    return True


def _merge_records(values: list[list[Any]]) -> list[Any]:
    merged: list[Any] = []
    for incoming in values:
        position = 0
        for value in incoming:
            while position < len(merged) and merged[position] != value:
                position += 1
            if position == len(merged):
                merged.append(deepcopy(value))
            position += 1
    return merged


def _initialize_frames(cursor: dict[str, Any]) -> None:
    """Correlate legacy copies only when a cumulative common baseline is provable."""
    retained = _retained_chains(cursor)
    identities: dict[str, tuple[tuple[str, ...], str]] = {}
    for depth in range(max((len(chain) for chain, _ in retained), default=0)):
        grouped: dict[tuple[tuple[str, ...], str], list[tuple[dict[str, Any], bool]]] = {}
        for chain, completed in retained:
            if len(chain) <= depth:
                continue
            frame = chain[depth]
            scope = (tuple(parent["frame_id"] for parent in chain[:depth]),
                     _text(frame.get("agenda"), "frame agenda"))
            grouped.setdefault(scope, []).append((frame, completed and depth == len(chain) - 1))
        for scope, members in grouped.items():
            known = {_text(frame["frame_id"], "frame identity") for frame, _ in members if "frame_id" in frame}
            if known and any("frame_id" not in frame for frame, _ in members):
                raise AgendaError("ambiguous legacy frame lineage overlaps identified roots")
            partitions: dict[str | None, list[tuple[dict[str, Any], bool]]] = {}
            for frame, completed in members:
                identity = _text(frame["frame_id"], "frame identity") if known else None
                partitions.setdefault(identity, []).append((frame, completed))
            for identity, copies in partitions.items():
                frames = [frame for frame, _ in copies]
                if identity is None:
                    opaque = any(
                        isinstance(value, dict) and value.get("agenda") == scope[1]
                        and "frames" not in value
                        for value in cursor.get("completed_roots", [])
                    )
                    if opaque or (any(done for _, done in copies) and len(copies) > 1):
                        raise AgendaError("ambiguous legacy completed frame lineage")
                    identity = uuid4().hex
                if identity in identities and identities[identity] != scope:
                    raise AgendaError("frame identity is shared by different roots or parents")
                identities[identity] = scope
                if any(("visit_origin" in frame) != ("visit_log" in frame) for frame in frames):
                    raise AgendaError("incomplete shared frame visit history")
                present = ["visit_origin" in frame for frame in frames]
                if any(present) and not all(present):
                    raise AgendaError("ambiguous shared frame history lacks occurrence records")
                if not any(present):
                    if any(frame.get("assignment_ref") != frames[0].get("assignment_ref") for frame in frames):
                        raise AgendaError("ambiguous legacy roots have different assignment references")
                    visits = [_counts(frame.get("visits")) for frame in frames]
                    dominant = next((value for value in visits if all(
                        all(value.get(node, 0) >= count for node, count in other.items())
                        for other in visits)), None)
                    if dominant is None:
                        raise AgendaError("ambiguous legacy shared-root visits are incomparable")
                    proof_lists = [frame.get("evidence", []) for frame in frames]
                    if not all(isinstance(value, list) for value in proof_lists):
                        raise AgendaError("recorded evidence is not a list; reconcile without discarding it")
                    if not any(all(_contains(value, other) for other in proof_lists) for value in proof_lists):
                        raise AgendaError("ambiguous legacy shared-root evidence has divergent histories")
                    entries: dict[tuple[str, int], str] = {}
                    for frame in frames:
                        occurrence = frame.get("occurrence_id")
                        if occurrence is not None:
                            occurrence = _text(occurrence, "entry occurrence")
                            ordinal = (frame["node"], frame["visits"][frame["node"]])
                            if ordinal in entries and entries[ordinal] != occurrence:
                                raise AgendaError("ambiguous legacy entry occurrences reuse a visit ordinal")
                            entries[ordinal] = occurrence
                    if any(frame.get("return_to") != frames[0].get("return_to") for frame in frames):
                        raise AgendaError("ambiguous shared frame has conflicting original returns")
                    for frame in frames:
                        frame["visit_origin"] = deepcopy(dominant)
                        frame["visit_log"] = []
                for frame in frames:
                    frame["frame_id"] = identity


def _shared_state(
    cursor: dict[str, Any], frame: dict[str, Any], boards: dict[str, Any],
) -> tuple[dict[str, int], list[dict[str, Any]], dict[str, list[Any]]]:
    members = [item for chain, _ in _retained_chains(cursor) for item in chain
               if item.get("frame_id") == frame["frame_id"]]
    if not any(item is frame for item in members):
        members.append(frame)
    origin = _counts(frame.get("visit_origin"))
    entries: dict[str, dict[str, Any]] = {}
    collections: dict[str, list[list[Any]]] = {"evidence": [], "executions": [], "holds": []}
    for member in members:
        if (member["agenda"] != frame["agenda"] or
                member.get("return_to") != frame.get("return_to") or
                member.get("assignment_ref") != frame.get("assignment_ref")):
            raise AgendaError("ambiguous shared frame source, assignment or original return")
        if _counts(member.get("visit_origin")) != origin:
            raise AgendaError("ambiguous shared-root visit origins differ")
        log = member.get("visit_log")
        if not isinstance(log, list):
            raise AgendaError("shared frame visit log must be a list")
        local = deepcopy(origin)
        seen: set[str] = set()
        for event in log:
            fields(event, {"id", "node"})
            identity = _text(event["id"], "visit occurrence")
            node = _text(event["node"], "visit node")
            if identity in seen:
                raise AgendaError("duplicate visit occurrence in one frame")
            seen.add(identity)
            if identity in entries and entries[identity] != event:
                raise AgendaError("conflicting shared visit occurrence")
            entries[identity] = event
            local[node] = local.get(node, 0) + 1
        if any(local.get(node, 0) < count for node, count in _counts(member["visits"]).items()):
            raise AgendaError("unrecorded shared-root visits; explicit reconciliation required")
        for key in collections:
            values = member.get(key, [])
            if not isinstance(values, list):
                raise AgendaError(f"recorded {key} is not a list; reconcile without discarding it")
            collections[key].append(values)
    visits = deepcopy(origin)
    for event in entries.values():
        visits[event["node"]] = visits.get(event["node"], 0) + 1
    nodes = boards[frame["agenda"]]["nodes"]
    if any(node not in nodes or count > nodes[node]["max_visits"] for node, count in visits.items()):
        raise AgendaError("shared-root node visit budget exceeded; replan without discarding history")
    return visits, deepcopy(list(entries.values())), {
        key: _merge_records(values) for key, values in collections.items()}


def _refresh_frame(cursor: dict[str, Any], frame: dict[str, Any], boards: dict[str, Any]) -> None:
    visits, log, collections = _shared_state(cursor, frame, boards)
    frame["visits"], frame["visit_log"] = visits, log
    for key, values in collections.items():
        if values or key in frame:
            frame[key] = values


def validate_frame_histories(cursor: dict[str, Any], boards: dict[str, Any]) -> None:
    """Validate required occurrence metadata without interpreting old unknown visits."""
    retained = _retained_chains(cursor)
    frames = [frame for chain, _ in retained for frame in chain]
    if not any("visit_origin" in frame or "visit_log" in frame for frame in frames):
        return
    scopes: dict[str, tuple[tuple[str, ...], str]] = {}
    for chain, _ in retained:
        for index, frame in enumerate(chain):
            identity = _text(frame.get("frame_id"), "frame identity")
            scope = (tuple(parent["frame_id"] for parent in chain[:index]), frame["agenda"])
            if identity in scopes and scopes[identity] != scope:
                raise AgendaError("frame identity is shared by different roots or parents")
            scopes[identity] = scope
    checked: set[str] = set()
    for chain in chains(cursor):
        for frame in chain:
            if frame["frame_id"] not in checked:
                _shared_state(cursor, frame, boards)
                checked.add(frame["frame_id"])


def enter(frame: dict[str, Any], board: dict[str, Any], node: str, at: str,
          cursor: dict[str, Any], boards: dict[str, Any], action: str | None = None) -> None:
    if node not in board["nodes"]:
        raise AgendaError("destination node does not exist")
    _refresh_frame(cursor, frame, boards)
    if frame["visits"].get(node, 0) >= board["nodes"][node]["max_visits"]:
        raise AgendaError("shared-root node visit budget exceeded")
    frame["node"] = node
    frame["visits"][node] = frame["visits"].get(node, 0) + 1
    frame["next_action"] = _text(action or board["nodes"][node]["action"], "next action")
    frame["entered_at"] = at
    frame["occurrence_id"] = str(uuid4())
    frame["visit_log"].append({"id": frame["occurrence_id"], "node": node})
    frame["timing_qualification"] = "generated"


def new_frame(name: str, board: dict[str, Any], at: str, cursor: dict[str, Any],
              boards: dict[str, Any]) -> dict[str, Any]:
    frame: dict[str, Any] = {"agenda": name, "visits": {}, "evidence": [], "return_to": None,
                             "frame_id": uuid4().hex, "visit_origin": {}, "visit_log": []}
    if "revision" in board:
        frame["agenda_revision"] = board["revision"]
    enter(frame, board, board["entry"], at, cursor, boards)
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


def _append_evidence(frame: dict[str, Any], supplied: Any, cursor: dict[str, Any],
                     boards: dict[str, Any]) -> None:
    _refresh_frame(cursor, frame, boards)
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
    if kind in {"select", "outcome", "link", "progress", "block", "wait", "defer",
                "restore", "push", "return", "complete-root"}:
        _initialize_frames(cursor)
    if kind == "refine":
        _refine(documents, operation)
    elif kind == "select":
        fields(operation, {"operation", "agenda", "assignment_ref"})
        name = _text(operation["agenda"], "board name")
        _text(operation["assignment_ref"], "assignment reference")
        if cursor["frames"] or not cursor["queue"] or cursor["queue"][0] != name:
            raise AgendaError("select requires an empty stack and the first assigned queued root")
        cursor["queue"].pop(0)
        cursor["frames"] = [new_frame(name, boards[name], at, cursor, boards)]
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
        _append_evidence(frame, proof, cursor, boards)
        enter(frame, boards[frame["agenda"]], node["on"][outcome], at, cursor, boards)
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
        _append_evidence(frame, operation["evidence"], cursor, boards)
        _extend(frame, "executions", links)
    elif kind == "progress":
        fields(operation, {"operation", "reason", "evidence"})
        if len(cursor["frames"]) != 1:
            raise AgendaError("parent progress requires the original root active without a child")
        frame = _top(cursor)
        _append_evidence(frame, operation["evidence"], cursor, boards)
        _extend(cursor, "progress_decisions", [{
            "reason": _text(operation["reason"], "demonstrated parent progress"),
            "evidence": evidence(operation["evidence"]), "recorded_at": at,
            "previous_detours": cursor["detours_without_progress"]}])
        cursor["detours_without_progress"] = 0
    elif kind in ("block", "wait"):
        fields(operation, {"operation", "reason", "next_action", "evidence"})
        frame = _top(cursor)
        _append_evidence(frame, operation["evidence"], cursor, boards)
        frame["next_action"] = _text(operation["next_action"], "next action")
        _extend(frame, "holds", [{"reason": _text(operation["reason"], "reason"),
                                "recorded_at": at, "kind": kind}])
        cursor["status"] = "steering" if kind == "block" else "waiting"
    elif kind == "defer":
        fields(operation, {"operation", "id", "reason", "decision_ref", "decision_packet_ref",
                           "revisit_at", "recheck_action", "next_action", "evidence"})
        _append_evidence(_top(cursor), operation["evidence"], cursor, boards)
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
        for frame in cursor["frames"]:
            _refresh_frame(cursor, frame, boards)
        _append_evidence(_top(cursor), proof, cursor, boards)
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
        child = new_frame(name, boards[name], at, cursor, boards)
        child.update(return_to={"agenda": parent["agenda"],
                     "node": _text(operation["return_node"], "return node"),
                     "next_action": _text(operation["return_action"], "return action")},
                     assignment_ref=operation["assignment_ref"], reason=operation["reason"])
        _append_evidence(child, operation["evidence"], cursor, boards)
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
        _append_evidence(frame, proof, cursor, boards)
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
            enter(parent, boards[parent["agenda"]], target["node"], at, cursor, boards,
                  target["next_action"])
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
