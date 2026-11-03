"""Shared immutable bytes for one negotiated pool-startup retry."""

from __future__ import annotations


def driver_binding(reservation: dict, native: str | None) -> dict:
    """Keep the original CLI wire form; ACP also binds its distinct Bridge ID."""
    if reservation.get("driver") == "cli":
        return {}
    from .ordinary_agent_facts import identity
    from .queue_records import TaskError

    info = reservation.get("agent_info")
    if reservation.get("driver") != "ordinary-bridge" or not isinstance(info, dict):
        raise TaskError("pool retry requires retained ordinary Bridge facts")
    actual = identity(info)
    if (
        actual.native != native or actual.worktree != reservation.get("worktree")
        or reservation.get("session_handle") != f"local-body:{actual.bridge}"
    ):
        raise TaskError("pool retry Bridge, native or workspace identity changed")
    return {"driver": "ordinary-bridge", "bridge_session_id": actual.bridge}


def session_handle(binding: dict) -> str:
    """Never publish an ACP native UUID as a Bridge or live-CLI handle."""
    if binding.get("driver") == "ordinary-bridge":
        from .queue_records import TaskError
        bridge = binding.get("bridge_session_id")
        if not isinstance(bridge, str) or not bridge.strip():
            raise TaskError("ordinary retry requires its retained Bridge session")
        return f"local-body:{bridge}"
    return binding["native_id"]


def ordinary_prompt(binding: dict, reservation: dict | None) -> str:
    """A refused first send cannot supply routing or charters to a later retry."""
    from .ordinary_invocation import historical_intent
    from .queue_records import TaskError

    info = reservation.get("agent_info") if isinstance(reservation, dict) else None
    if not isinstance(info, dict):
        raise TaskError("ordinary retry requires its retained initial startup intent")
    original = historical_intent(info.get("startup_invocation"))
    intent = original["binding"]
    if (
        intent.get("task_id") != binding["task_id"]
        or intent.get("admission_generation") != binding["generation"]
        or intent.get("worktree_id") != binding["worktree"]
        or original.get("expected_native_session_id") != binding["native_id"]
        or not isinstance(original.get("prompt"), str) or not original["prompt"].strip()
    ):
        raise TaskError("ordinary retry initial startup intent changed")
    return original["prompt"]


def retry_invocation(binding: dict, revision: int, *, reservation: dict | None = None) -> dict:
    """Describe one work-bearing send without queueing or replacing its native."""
    result = {
        **binding, "startup_revision": revision,
        "idempotency_key": (
            f"pool-startup:{binding['reservation_key']}:{binding['generation']}:{revision}:retry"
        ),
        "prompt": (
            f"Re-read your allocator-assigned task {binding['task_id']} in this same native conversation "
            "and worktree. Use its original coordinator routing flags for every command. "
            f"If it is still CLAIMED, unowned and at generation {binding['generation']}, "
            "evaluate the assigned worker contract and explicitly decide whether to accept. "
            f"Acceptance uses `agent-dispatch start {binding['task_id']} "
            f"--pool-generation {binding['generation']} --owner-session-id {binding['native_id']}`. "
            "Do not run claim --evaluation, acquire another claim, create a replacement or "
            "release another owner's assignment. If it changed or you cannot decide, report a blocker "
            "under the existing task contract. Prompt delivery is not acceptance."
        ),
    }
    if binding.get("driver") == "ordinary-bridge":
        result["prompt"] = (
            f"Retry the allocator's current assignment {binding['task_id']} in this same native "
            f"and worktree only if it remains unowned CLAIMED at generation {binding['generation']}. "
            "Do not acquire another claim or replace this conversation. "
            "The original self-contained assignment instructions follow; prompt delivery is not acceptance.\n\n"
            + ordinary_prompt(binding, reservation)
        )
    return result
