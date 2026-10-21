"""Pure SDK event ownership and parent-turn folding."""

from __future__ import annotations

from typing import Any


def subagent_id(event: dict[str, Any]) -> str | None:
    """The sub-agent instance id that owns a raw SDK event, else None.

    The Copilot CLI stamps a sub-agent's events with ``agentId`` on the event
    envelope (absent for the main agent and session-level events); the legacy
    ``data.agentId`` placement is still honored.
    """
    agent_id = event.get("agentId")
    if not agent_id:
        data = event.get("data")
        agent_id = data.get("agentId") if isinstance(data, dict) else None
    return agent_id if isinstance(agent_id, str) and agent_id else None


def is_subagent_event(event: dict[str, Any]) -> bool:
    """Whether a raw SDK event belongs to a sub-agent, not the main agent.

    Recognizes the envelope ``agentId`` plus the legacy ``data.agentId`` and
    ``data.parentToolCallId`` markers (the same ownership rule the CLI applies).
    """
    if subagent_id(event):
        return True
    data = event.get("data")
    return isinstance(data, dict) and bool(data.get("parentToolCallId"))


_TURN_ACTIVITY_TYPES = frozenset({
    "user.message",
    "assistant.message",
    "assistant.reasoning",
    "tool.execution_start",
    "tool.execution_complete",
    "permission.requested",
})
_TURN_END_TYPE = "assistant.turn_end"


def derive_turn_state(
    raw_events: list[dict[str, Any]], *, prior_state: str | None = None
) -> tuple[str | None, bool]:
    """Fold a batch of raw SDK events into a coarse ``turn_state``.

    Returns ``(turn_state, saw_activity)`` where ``turn_state`` is ``"running"``
    (a turn is in progress), ``"idle"`` (the last turn ended), or ``prior_state``
    if the batch carried no turn signal. ``saw_activity`` is True when any
    activity event was seen (used to refresh ``last_activity_at``). Pure and
    order-sensitive: the *last* turn signal in the batch wins. This is the
    objective, token-free half of progress legibility (Phase 7 Channel A) --
    ``stalled`` is *not* decided here; it is computed on read from
    ``last_activity_at`` vs. a threshold.

    A sub-agent's events (``is_subagent_event``) are skipped: a background
    sub-agent busy in a long wait must neither mark the parent running nor feed
    its stall clock.
    """
    state = prior_state
    saw_activity = False
    for event in raw_events:
        if is_subagent_event(event):
            continue
        etype = event.get("type")
        if etype == _TURN_END_TYPE:
            state = "idle"
        elif etype in _TURN_ACTIVITY_TYPES:
            state = "running"
            saw_activity = True
    return state, saw_activity
