"""A current control operation is not a replay of the retained birth send."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .ordinary_agent_facts import identity
from .ordinary_control_adoption import ADOPTION, HISTORY
from .ordinary_invocation import historical_intent, same_facts
from .queue_records import TaskError

CONTROL = "current_control_invocation"


def prepare(
    task: Any, reservation: Any, info: dict[str, Any], *, turn: int | None = None,
) -> dict[str, Any]:
    """Bind the new operation to the admitted attempt and unchanged birth intent."""
    original = historical_intent(info.get("startup_invocation"))
    actual = identity(info)
    if (
        not isinstance(info.get(ADOPTION), dict)
        or original["binding"]["admission_generation"] != task["generation"]
        or type(reservation["startup_revision"]) is not int
        or reservation["startup_revision"] < 1
    ):
        raise TaskError("ordinary current control lacks its admitted original intent")
    turn = info.get("turn_count") if turn is None else turn
    if type(turn) is not int or turn < 0:
        raise TaskError("ordinary current control turn is unproven")
    binding = {
        "version": 1, "task_generation": task["generation"],
        "reservation_key": reservation["key"],
        "startup_revision": reservation["startup_revision"],
        "bridge_session_id": actual.bridge,
    }
    key = hashlib.sha256(json.dumps([
        "ordinary-current-control/v1", binding, original["invocation_key"],
    ], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if key == original["invocation_key"]:
        raise TaskError("ordinary current control cannot reuse the original invocation")
    invocation = {
        **copy.deepcopy(original), "invocation_key": key,
        "prompt": (
            "The original native conversation now has a restored controller. "
            "This is a new current-assignment decision, not evidence that an earlier prompt "
            "was delivered or that the task was accepted. Re-read the current task before "
            "acting; preserve its generation, holds and existing ownership. "
            "Only the worker may explicitly accept it under the following retained contract.\n\n"
            + original["prompt"]
        ),
        "binding": {
            **original["binding"], "origin_kind": "resumed", "expected_turn_index": turn,
        },
    }
    return {**binding, "invocation": invocation}


def retain(
    previous: dict[str, Any] | None, incoming: dict[str, Any],
    reservation: dict[str, Any], generation: int,
) -> None:
    """Retain immutable operation bytes before a caller can submit a new prompt."""
    prior = previous.get(CONTROL) if previous is not None else None
    current = incoming.get(CONTROL, prior)
    if current is None and CONTROL not in incoming and prior is None:
        return
    if not isinstance(current, dict) or HISTORY not in incoming:
        raise TaskError("ordinary current control requires retained predecessor history")
    invocation = current.get("invocation")
    binding = invocation.get("binding") if isinstance(invocation, dict) else None
    if not isinstance(binding, dict):
        raise TaskError("ordinary current control invocation is unavailable")
    expected = prepare(
        {"generation": generation}, reservation, incoming, turn=binding.get("expected_turn_index"),
    )
    if (
        not same_facts(current, expected)
        or prior is not None and not same_facts(current, prior)
        or prior is None and binding["expected_turn_index"] != incoming.get("turn_count")
    ):
        raise TaskError("ordinary current control invocation changed")
    incoming[CONTROL] = copy.deepcopy(current)


def protect(conn: Any) -> None:
    """Older recorders cannot erase an admitted current-controller operation."""
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS spawn_current_control_retained"
        " BEFORE UPDATE OF agent_info ON spawn_reservations"
        " WHEN CASE WHEN json_valid(OLD.agent_info)"
        f" THEN json_type(OLD.agent_info, '$.{CONTROL}') IS NOT NULL ELSE 0 END"
        " AND CASE WHEN json_valid(NEW.agent_info)"
        f" THEN json_extract(NEW.agent_info, '$.{CONTROL}')"
        f" IS NOT json_extract(OLD.agent_info, '$.{CONTROL}') ELSE 1 END"
        " BEGIN SELECT RAISE(ABORT, 'ordinary current control invocation is immutable'); END"
    )
