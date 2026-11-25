"""The existing startup-bound Bridge continuation form for local and SSH calls."""

from __future__ import annotations


def continuation_arguments(
    session_id: str, *, wait: bool, communication: float, process: float = 300.0,
) -> list[str]:
    """Keep continuation explicit without requiring a new selector flag on peers."""
    argv = ["send", session_id, "--require-agent-hosting", "--prompt-file", "-"]
    if not wait:
        argv.append("--no-wait")
    return [
        *argv,
        "--startup-communication-timeout", str(communication),
        "--startup-process-timeout", str(process),
    ]
