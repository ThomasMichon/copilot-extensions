"""Closed Dispatch projection; legacy lifecycle and producer-fence records stay intact."""

from typing import Any

_TASK_EVENTS = frozenset({
    "task.proposed", "task.created", "task.approved", "task.claimed", "task.started",
    "task.yielded", "task.suspended", "task.resumed", "task.released", "task.held",
    "task.unheld", "task.unexcluded", "task.reset", "task.submitted", "task.completed",
    "task.reopened", "task.abandoned", "task.detached", "task.recovered",
    "task.result_recorded", "task.rejected",
    "task.owner_session_bound", "task.progress", "task.card", "task.steer",
    "task.card_draft", "task.card_draft_cleared", "task.steer_taken",
})
_FENCE_EVENTS = frozenset({
    "task.create_rejected", "producer.claim_rejected",
    "producer_scope.transitioned", "producer_scope.transition_rejected",
})
_STATES = frozenset({
    "proposed", "queued", "claimed", "started", "suspended", "submitted",
    "completed", "abandoned", "dead_letter",
})
_FENCE_INTS = frozenset({
    "requested_generation", "current_generation", "from_generation", "to_generation",
    "blocking_task_count",
})
_FENCE_BOOLS = frozenset({"retryable", "replayed", "blocking_ids_truncated"})
_REQUIRED = frozenset({"kind", "name", "event"})
_TASK_FIELDS = _REQUIRED | {"to", "attempts"}
_FENCE_FIELDS = _REQUIRED | _FENCE_INTS | _FENCE_BOOLS


def offered_fields(event: dict[str, Any]) -> dict[str, Any]:
    """Omit identities and content before the bounded queue snapshots values."""
    if type(event) is not dict or len(event) > 64:
        raise ValueError("unbounded source")
    name = event.get("name")
    fields = (
        _TASK_FIELDS if type(name) is str and name == "task"
        else _FENCE_FIELDS if type(name) is str and name == "producer_fence"
        else _REQUIRED
    )
    result: dict[str, Any] = {}
    for key, value in event.items():
        if type(key) is not str:
            raise ValueError("nonprimitive key")
        if key in fields and (value is not None or key in _REQUIRED):
            result[key] = value
    return result


def project(event: dict[str, Any]) -> dict[str, Any]:
    """Admit structural state, actual attempt counts and fence replay semantics only."""
    name = event.get("name")
    kind = event.get("kind")
    verb = event.get("event")
    if (
        type(kind) is not str or kind != "state_transition"
        or type(name) is not str or name not in ("task", "producer_fence")
        or type(verb) is not str
        or verb not in (_TASK_EVENTS if name == "task" else _FENCE_EVENTS)
    ):
        raise ValueError("unadmitted record")
    result: dict[str, Any] = {"kind": kind, "name": name, "event": verb}
    if name == "task" and event.get("to") is not None:
        value = event["to"]
        if type(value) is not str or value not in _STATES:
            raise ValueError("unadmitted state")
        result["to"] = value
    integers = {"attempts"} if name == "task" else _FENCE_INTS
    for key in integers:
        value = event.get(key)
        if value is not None:
            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                raise ValueError("unadmitted count")
            result[key] = value
    if name == "producer_fence":
        for key in _FENCE_BOOLS:
            value = event.get(key)
            if value is not None:
                if type(value) is not bool:
                    raise ValueError("unadmitted flag")
                result[key] = value
    return result
