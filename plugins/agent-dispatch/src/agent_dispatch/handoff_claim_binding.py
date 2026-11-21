"""Durable task metadata selecting, but never authorizing, creator settlement."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .handoff_claim_release import handoff_worktree, is_handoff_task
from .queue_common import Task
from .queue_records import TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue

CONTRACT = "context-handoff-creator-claim/v1"
FIELDS = ("machine", "project", "worktree_id", "creation_nonce", "claim_nonce", "kind", "ref")


def validated_binding(binding: object, task: dict[str, Any]) -> dict[str, str]:
    """Require a complete exact selector consistent with the named handoff."""
    if not isinstance(binding, dict) or binding.get("contract") != CONTRACT:
        raise TaskError("unsupported or missing creator claim binding contract")
    if any(
        not isinstance(binding.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,256}", binding[key])
        for key in FIELDS
    ):
        raise TaskError("invalid creator claim binding")
    if (
        not is_handoff_task(task) or binding["kind"] != "task"
        or binding["ref"] != task.get("id")
        or binding["worktree_id"] != handoff_worktree(task)
        or task.get("target_machine") and binding["machine"].casefold() != str(task["target_machine"]).casefold()
    ):
        raise TaskError("creator claim binding does not match the handoff target")
    return {"contract": CONTRACT, **{key: binding[key] for key in FIELDS}}


def bind_claim(queue: TaskQueue, task_id: str, binding: object) -> Task:
    """Bind once, including a terminal creation race, without changing task custody."""
    from dataclasses import asdict

    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = queue._fetch(conn, task_id)
        if task is None:
            raise TaskError(f"no such task: {task_id}")
        selected = validated_binding(binding, asdict(task))
        if task.handoff_claim is not None:
            if validated_binding(task.handoff_claim, asdict(task)) != selected:
                raise TaskError("creator claim is already bound to another incarnation")
            return task
        ts = queue._now(None)
        conn.execute(
            "UPDATE tasks SET handoff_claim=?,updated_at=? WHERE id=?",
            (json.dumps(selected, sort_keys=True), ts, task_id),
        )
        queue._audit(
            conn, task_id, ts=ts, from_status=task.status, to_status=task.status,
            note="exact creator claim binding recorded",
        )
        bound = queue._fetch(conn, task_id)
        assert bound is not None
        return bound
