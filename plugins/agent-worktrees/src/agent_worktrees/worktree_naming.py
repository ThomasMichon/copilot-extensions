"""Session worktree names, independent of prompt and transport identities."""

from __future__ import annotations


def session_worktree_id(
    machine: str, platform: str, timestamp: str, suffix: str, *, legacy: bool = False,
) -> str:
    """Include the platform once; retain exact historical allocations on replay."""
    tag = "win" if platform == "windows" else platform
    environment_suffixes = (f"-{tag}", f"-{platform}")
    prefix = (
        machine
        if not legacy and machine.casefold().endswith(environment_suffixes)
        else f"{machine}-{tag}"
    )
    return f"{prefix}-{timestamp}-{suffix}"
