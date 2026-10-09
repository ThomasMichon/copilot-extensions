"""What makes a task a context-handoff baton."""

from __future__ import annotations

from typing import Any


def is_handoff_task(task: dict[str, Any]) -> bool:
    """A task is a *handoff* baton (exactly-once, spent-aware) iff it carries
    the ``handoff`` label or originates from ``context-handoff``. Consuming one
    is its completion: no operator review follows a pickup."""
    return ("handoff" in (task.get("labels") or [])) or (
        task.get("source") == "context-handoff"
    )
