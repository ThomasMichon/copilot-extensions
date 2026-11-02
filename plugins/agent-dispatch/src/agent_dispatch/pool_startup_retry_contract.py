"""Shared immutable bytes for one negotiated pool-startup retry."""

from __future__ import annotations


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
