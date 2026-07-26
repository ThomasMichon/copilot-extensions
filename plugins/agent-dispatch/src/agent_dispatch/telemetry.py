"""Generic telemetry emission seam (pluggable, no-op by default).

A small, dependency-free hook for surfacing the coordinator's lifecycle events
as structured telemetry, so a downstream consumer can observe the task state
machine **without this plugin depending on any specific telemetry backend**.

By default emission is a **no-op**: nothing happens until a consumer registers a
sink via :func:`set_telemetry_sink`. A sink is any callable taking one ``dict``
event; :func:`emit` is **fail-open** -- a sink that raises never perturbs the
coordinator. This keeps agent-dispatch generic: it *declares* its telemetry
surface and ships the hook; the publisher (and its transport) live in whatever
consumer registers a sink.

Vocabulary
----------
The coordinator publishes task-lifecycle events of type ``task.<verb>``
(``proposed`` / ``created`` / ``claimed`` / ``started`` / ``yielded`` /
``completed`` / ``abandoned`` / ``detached``). :func:`task_lifecycle_event`
shapes one of those into a generic **state-transition** record carrying only
lifecycle *state and structure* -- never the task prompt, payload, or any
secret. A consumer maps that record onto whatever telemetry schema it uses.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("agent-dispatch.telemetry")

#: A telemetry sink: a callable receiving one structured event dict.
TelemetrySink = Callable[[dict[str, Any]], None]

_sink: TelemetrySink | None = None


def set_telemetry_sink(sink: TelemetrySink | None) -> None:
    """Register (or clear, with ``None``) the process-wide telemetry sink."""
    global _sink
    _sink = sink


def clear_telemetry_sink() -> None:
    """Remove any registered sink -- emission returns to a no-op."""
    set_telemetry_sink(None)


def has_sink() -> bool:
    """True when a sink is registered (emission will be delivered)."""
    return _sink is not None


def emit(event: dict[str, Any]) -> None:
    """Emit one telemetry event to the registered sink (fail-open).

    No sink registered -> a no-op. A sink that raises is swallowed (logged at
    debug): telemetry is best-effort and must never perturb the coordinator.
    """
    sink = _sink
    if sink is None:
        return
    try:
        sink(event)
    except Exception:  # noqa: BLE001 - telemetry is best-effort, never fatal
        log.debug("telemetry sink raised; dropping event", exc_info=True)


# Lifecycle fields that are safe to surface: state and structure only. The task
# prompt/payload are deliberately excluded so telemetry can never leak a secret.
_SAFE_TASK_FIELDS = (
    "id",
    "status",
    "repo",
    "source",
    "target_machine",
    "target_worktree",
    "owner",
    "attempts",
)


def task_lifecycle_event(event_type: str, task: dict[str, Any]) -> dict[str, Any]:
    """Shape a coordinator ``task.<verb>`` event into a generic state-transition
    telemetry record.

    Carries only lifecycle **state and structure** (id, status, repo, routing,
    owner, attempts) -- never the task's prompt, payload, or any secret.
    """
    record: dict[str, Any] = {
        "kind": "state_transition",
        "name": "task",
        "event": event_type,
        "to": task.get("status"),
    }
    for field in _SAFE_TASK_FIELDS:
        value = task.get(field)
        if value is not None:
            record[field] = value
    return record
