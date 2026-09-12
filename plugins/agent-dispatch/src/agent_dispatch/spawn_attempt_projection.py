"""Pure projection of a task's spawn-attempt/dead-letter status.

Shared by both the reviewer-loop and repository-issue-loop ``status``/
``doctor`` command implementations in ``loop_commands.py``: each active task
is projected through this to decide whether it has exhausted its declared
spawn-attempt budget (label-specific caps take priority over the loop's
default) and, if so, whether it qualifies for the atomic dead-letter rearm
command (which requires at least 3 observed failures) or only a manual
recovery note.
"""

from __future__ import annotations

from collections.abc import Mapping


def spawn_attempt_projection(
    task: dict,
    *,
    failures: int,
    default_max_attempts: int,
    label_max_attempts: Mapping[str, int],
) -> dict:
    label_caps = [
        int(label_max_attempts[label])
        for label in (task.get("labels") or [])
        if label in label_max_attempts
    ]
    max_attempts = max(label_caps) if label_caps else int(default_max_attempts)
    dead_lettered = bool(
        task.get("status") == "queued"
        and not task.get("owner")
        and max_attempts
        and failures >= max_attempts
    )
    result = {
        "failed_spawns": failures,
        "max_attempts": max_attempts,
        "dead_lettered": dead_lettered,
    }
    if dead_lettered and failures >= 3:
        result["rearm"] = (
            f"agent-dispatch reservations rearm {task['id']} --permit --reason <reason>"
        )
    elif dead_lettered:
        result["recovery"] = (
            "the atomic rearm command requires at least 3 failed spawns; "
            "raise this loop's attempt bound or resolve the task explicitly"
        )
    return result
