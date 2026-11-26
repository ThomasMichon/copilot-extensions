"""Existing Bridge continuation syntax and invocation-scoped observation config."""

from __future__ import annotations

import os


def continuation_arguments(
    session_id: str, *, wait: bool, communication: float, process: float = 300.0,
    caller: str | None = None,
) -> list[str]:
    """Keep continuation explicit without requiring a new selector flag on peers."""
    argv = ["send", session_id, "--require-agent-hosting", "--prompt-file", "-"]
    if caller is not None:
        argv += ["--caller", caller]
    if not wait:
        argv.append("--no-wait")
    return [
        *argv,
        "--startup-communication-timeout", str(communication),
        "--startup-process-timeout", str(process),
    ]


def observation_environment() -> dict[str, str]:
    """Retain the prior wait contract without new syntax on an older executable."""
    return {**os.environ, "AGENT_BRIDGE_LEGACY_OBSERVATION": "1"}


def resume_environment() -> dict[str, str]:
    """Keep retained resume compatible without new syntax on an older CLI."""
    return {**os.environ, "AGENT_BRIDGE_LEGACY_RESUME": "1"}
