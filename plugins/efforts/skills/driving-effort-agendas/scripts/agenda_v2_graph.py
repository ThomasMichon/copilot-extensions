"""Bounded display graph of the context-qualified agenda: tree, queue, stack, cursor, history.

The coarse snapshot flattens frames into text. This read-only projection keeps the
structure a renderer needs: every frame as a tree node linked to the item that
expanded it, each frame's items in queue order, the active stack and cursor, and
a recent tail of accepted immutable records. It carries no planning authority
and is derived only from validated store documents and records, so it is
deterministic for one accepted revision.
"""

from __future__ import annotations

from typing import Any, Callable

from agenda_v2_schema import definition, record

GRAPH_VERSION = 1
LABEL_LIMIT = 160
FRAME_LIMIT = 32
HISTORY_LIMIT = 24
ITEM_LIMITS = (96, 48, 24, 8)
BODY_LIMIT = 32768
_STATES = {"completed": "done", "pending": "queued", "running": "running",
           "replanning": "replanning", "waiting": "waiting"}
# One mutation stamps all of its records with the same instant; rank them causally.
_CAUSAL = {"decision": 0, "refinement": 0, "result": 1, "return": 2, "wait": 3, "deferred": 3,
           "expansion": 4, "recovery": 5, "assignment": 6, "attempt": 7}


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= LABEL_LIMIT else text[:LABEL_LIMIT - 1] + "…"


def _order(records: dict[str, Any], identity: str) -> tuple[str, int, str]:
    value = records[identity]
    return value["created_at"], _CAUSAL.get(value["kind"], 0), identity


def _started(records: dict[str, Any], item: dict[str, Any]) -> tuple[str, str, str]:
    origin = records[item["origin_id"]]
    begun = records[item["attempts"][0]]["created_at"] if item["attempts"] else origin["created_at"]
    return begun, origin["body"]["definition_id"], item["origin_id"]


def _history_text(kind: str, body: dict[str, Any]) -> str:
    if kind == "attempt":
        return "attempt: " + body["approach"]["action"]
    if kind == "result":
        return f"outcome {body['outcome']} -> {body['transition']}"
    if kind == "expansion":
        return "expanded: " + body["continuation"]["next_action"]
    if kind == "return":
        return "returned: " + body["acceptance_ref"]
    if kind == "wait":
        return f"wait ({body['reason_kind']}): {body['next_action']}"
    if kind == "deferred":
        return "deferred for decision " + body["decision_ref"]
    if kind == "recovery":
        return "recovered deferred branch"
    if kind == "assignment":
        return "assigned " + body["assignment_ref"]
    if kind == "decision":
        return f"decision {body['decision']}: {body['reason']}"
    if kind == "refinement":
        return f"refined board {body['board_ref']} r{body['revision']}"
    return kind


def _history_refs(kind: str, body: dict[str, Any], items: dict[str, Any],
                  records: dict[str, Any]) -> tuple[str | None, str | None]:
    item = body.get("item_id") or body.get("parent_item_id")
    frame = body.get("frame_id") or body.get("parent_frame_id")
    if frame is None and isinstance(item, str) and item in items:
        frame = record(records, items[item]["origin_id"], "item")["frame_id"]
    return (frame if isinstance(frame, str) else None, item if isinstance(item, str) else None)


def graph(documents: dict[str, Any], records: dict[str, Any], *,
          item_limit: int = ITEM_LIMITS[0]) -> dict[str, Any]:
    cursor = documents["cursor"]
    frames, items = cursor["frame_state"], cursor["item_state"]
    active = list(cursor["frames"])
    deferred = {frame_id for saved in cursor["deferred"] for frame_id in saved["frames"]}
    origins = {frame_id: record(records, frame["origin_id"], "frame") for frame_id, frame in frames.items()}

    children: dict[str | None, list[str]] = {}
    for frame_id, origin in origins.items():
        link = origin["parent_link"]
        children.setdefault(None if link is None else link["parent_frame_id"], []).append(frame_id)
    for siblings in children.values():
        siblings.sort(key=lambda frame_id: _order(records, frames[frame_id]["origin_id"]))
    ordered: list[str] = []
    pending_walk = list(reversed(children.get(None, [])))
    while pending_walk:
        frame_id = pending_walk.pop()
        ordered.append(frame_id)
        pending_walk.extend(reversed(children.get(frame_id, [])))

    owned: dict[str, list[str]] = {frame_id: [] for frame_id in frames}
    for item_id, item in items.items():
        owned[record(records, item["origin_id"], "item")["frame_id"]].append(item_id)

    def item_order(frame_id: str) -> list[str]:
        pending = frames[frame_id]["pending"]
        rest = sorted((item_id for item_id in owned[frame_id] if item_id not in pending),
                      key=lambda item_id: _started(records, items[item_id]))
        return rest + list(pending)

    cursor_frame = active[-1] if active else None
    cursor_item = (frames[cursor_frame]["pending"][0]
                   if cursor_frame is not None and frames[cursor_frame]["pending"] else None)

    # Keep the stack, cursor and queued work visible first when the item bound bites.
    candidates = [(frame_id, item_id) for frame_id in ordered for item_id in item_order(frame_id)]
    live = {frame_id for frame_id in active} | deferred

    position = {entry: index for index, entry in enumerate(candidates)}

    def priority(entry: tuple[str, str]) -> tuple[int, int]:
        frame_id, item_id = entry
        if item_id == cursor_item:
            return 0, 0
        if item_id in frames[frame_id]["pending"] and frame_id in live:
            return 1, position[entry]
        return 2, -position[entry]

    kept = set(sorted(candidates, key=priority)[:max(item_limit, 0)])
    kept_frames = [frame_id for frame_id in ordered
                   if frame_id in live or any(entry[0] == frame_id for entry in kept)][:FRAME_LIMIT]

    frame_nodes = []
    for frame_id in kept_frames:
        origin, frame = origins[frame_id], frames[frame_id]
        link = origin["parent_link"]
        if link is None:
            label = f"{origin['source']['kind']}:{origin['source']['ref']}"
        else:
            parent = definition(documents, items[link["parent_item_id"]])
            label = "subqueue: " + (parent.get("label") or parent["action"])
        state = ("active" if frame_id in active else "deferred" if frame_id in deferred
                 else "closed" if frame["closed"] else "idle")
        frame_nodes.append({"id": frame_id, "parent": None if link is None else link["parent_frame_id"],
                            "via": None if link is None else link["parent_item_id"],
                            "state": state, "label": _clip(label),
                            "returns": None if link is None else _clip(link["next_action"])})

    item_nodes = []
    for frame_id in kept_frames:
        for item_id in item_order(frame_id):
            if (frame_id, item_id) not in kept:
                continue
            item = items[item_id]
            current = definition(documents, item)
            state = _STATES[item["status"]]
            if state != "done" and item_id not in frames[frame_id]["pending"]:
                state = "closed"
            item_nodes.append({"id": item_id, "frame": frame_id,
                               "label": _clip(current.get("label") or current["action"]),
                               "state": state, "attempts": len(item["attempts"])})

    shown = {"item", "frame", "context", "context-proposal"}
    events = sorted((identity for identity, value in records.items() if value["kind"] not in shown),
                    key=lambda identity: _order(records, identity))
    history = []
    for identity in events[-HISTORY_LIMIT:]:
        value = records[identity]
        body = record(records, identity)
        frame_id, item_id = _history_refs(value["kind"], body, items, records)
        history.append({"at": value["created_at"], "kind": value["kind"], "frame": frame_id,
                        "item": item_id, "text": _clip(_history_text(value["kind"], body))})

    return {"version": GRAPH_VERSION, "revision": cursor["revision"],
            "cursor": {"frame": cursor_frame, "item": cursor_item, "stack": active[-FRAME_LIMIT:]},
            "frames": frame_nodes, "items": item_nodes, "history": history,
            "omitted": {"frames": len(frames) - len(frame_nodes), "items": len(items) - len(item_nodes),
                        "history": len(events) - len(history)}}


def attach(observation: dict[str, Any], documents: dict[str, Any], records: dict[str, Any],
           encode: Callable[[Any], str]) -> dict[str, Any]:
    """Add the largest bounded graph that keeps the observation within its body limit.

    The graph is optional display data: when even the smallest bound does not fit, the
    observation is returned unchanged rather than becoming undeliverable because of it.
    """
    for limit in ITEM_LIMITS:
        candidate = {**observation, "graph": graph(documents, records, item_limit=limit)}
        if len(encode(candidate).encode()) + 1 <= BODY_LIMIT:
            return candidate
    return observation
