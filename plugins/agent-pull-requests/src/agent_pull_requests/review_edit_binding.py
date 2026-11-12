"""Selected native edit correlation inside the existing replay transaction."""

from __future__ import annotations

import copy

from .review_preparation import _outer_markers


def _could_target(selected: dict, receipt: dict | None, target: dict) -> bool:
    ids = receipt["platform_ids"] if receipt is not None else {}
    for name in ("thread_id", "comment_id"):
        known = [row[name] for row in (selected, ids)
                 if type(row.get(name)) is int and row[name] > 0]
        if known and target[name] not in known:
            return False
    return True


def previous_preparation(document: dict, original: dict, action: dict, baseline: dict) -> dict | None:
    """Correlate protected baseline data with this owner's original native receipts.

    The caller must first use the existing protected selection/source and native
    ownership checks. This lookup neither authenticates the source nor sends IO.
    """
    comment = next(row for row in baseline[action["thread_id"]]["comments"]
                   if row["id"] == action["comment_id"])
    body = comment.get("content")
    if not isinstance(body, str):
        raise ValueError("selected owned baseline requires native text")
    for operation in document.get("terminal_recovery", {}).get("operations", []):
        if operation["scope"] != original["scope"]:
            continue
        for state in operation["actions"]:
            if state["kind"] == "verdict" or state["state"] == "retired":
                continue
            if not _could_target(state, state["receipt"], action):
                continue
            if state["state"] == "unknown":
                raise ValueError("unresolved native effect may refer to the selected owned comment")
            if "prepared" in state:
                raise ValueError("selected owned comment requires its retired original preparation")
    identities: set[str] = set()
    matching: list[dict] = []
    for operation in document["reviews"]:
        if operation["intent"]["scope"] != original["scope"]:
            continue
        for selected, state in zip(operation["intent"]["actions"], operation["actions"]):
            if selected["kind"] == "verdict" or state["state"] == "unattempted":
                continue
            if not _could_target(selected, state["receipt"], action):
                continue
            if state["state"] == "unknown":
                raise ValueError("unresolved native effect may refer to the selected owned comment")
            binding = selected.get("preparation")
            if binding is None:
                continue
            identities.add(binding["identity"]["comment_id"])
            if selected["body"] == body:
                matching.append({
                    "protocol": "agent-forge-comment.preparation/v1",
                    **copy.deepcopy(binding), "body": body, "effects": "none",
                })
    if identities:
        if len(identities) != 1 or len(matching) != 1:
            raise ValueError("selected owned comment lacks an unambiguous original preparation")
        return matching[0]
    if _outer_markers(body):
        raise ValueError("selected owned comment has unbound outer identity metadata")
    return None
