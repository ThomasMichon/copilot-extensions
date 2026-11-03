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


def retry_invocation(binding: dict, revision: int) -> dict:
    """Describe one work-bearing send without queueing or replacing its native."""
    return {
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
