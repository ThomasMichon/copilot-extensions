"""Session worktree names, independent of prompt and transport identities."""

from __future__ import annotations

import re


def session_worktree_prefix(
    machine: str, platform: str, *, legacy: bool = False,
) -> str:
    """Include the environment once, or preserve the historical prefix."""
    tag = "win" if platform == "windows" else platform
    environment_suffixes = (f"-{tag}", f"-{platform}")
    return (
        machine
        if not tag or (not legacy and machine.casefold().endswith(environment_suffixes))
        else f"{machine}-{tag}"
    )


def session_worktree_id(
    machine: str, platform: str, timestamp: str, suffix: str, *, legacy: bool = False,
) -> str:
    """Include the platform once; retain exact historical allocations on replay."""
    prefix = session_worktree_prefix(machine, platform, legacy=legacy)
    return f"{prefix}-{timestamp}-{suffix}"


def session_worktree_allocation(
    worktree_id: str, machine: str, platform: str,
) -> tuple[str, str] | None:
    """Recognize a complete native allocation, never a partial machine prefix."""
    match = re.fullmatch(
        r"(.+)-(\d{8}-\d{6})-([0-9a-f]{4}|[0-9a-f]{32})", worktree_id,
    )
    if match is None or match[1] not in {
        session_worktree_prefix(machine, platform, legacy=legacy)
        for legacy in (False, True)
    }:
        return None
    return match[2], match[3]
