"""Session worktree names, independent of prompt and transport identities."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .worktree_machine_identity import WorktreeMachineIdentity


def session_worktree_prefix(
    machine: str, platform: str, *, legacy: bool = False, physical_machine: bool = False,
) -> str:
    """Include the environment once, or preserve the historical prefix."""
    platform = platform.casefold()
    tag = "win" if platform == "windows" else platform
    environment_suffixes = (f"-{tag}", f"-{platform}")
    return (
        machine
        if not tag or (
            not legacy and not physical_machine
            and machine.casefold().endswith(environment_suffixes)
        )
        else f"{machine}-{tag}"
    )


def session_worktree_id(
    machine: str, platform: str, timestamp: str, suffix: str, *, legacy: bool = False,
    physical_machine: bool = False,
) -> str:
    """Include the platform once; retain exact historical allocations on replay."""
    prefix = session_worktree_prefix(
        machine, platform, legacy=legacy, physical_machine=physical_machine,
    )
    return f"{prefix}-{timestamp}-{suffix}"


def prospective_session_worktree_id(
    identity: WorktreeMachineIdentity, timestamp: str, suffix: str,
) -> str:
    """Name a new physical/environment tuple without stripping the physical name."""
    worktree_id = session_worktree_id(
        identity.machine, identity.environment, timestamp, suffix, physical_machine=True,
    )
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"
    }
    if (
        any(ord(char) <= 32 or ord(char) == 127 or char in '/\\~^:?*["<>|' for char in worktree_id)
        or worktree_id.startswith(".") or worktree_id.endswith((".", ".lock"))
        or ".." in worktree_id or "@{" in worktree_id
        or worktree_id.split(".", 1)[0].upper() in reserved
    ):
        raise ValueError("physical machine name cannot form a portable path/Git-ref component")
    return worktree_id


def parse_session_worktree_id(worktree_id: str) -> tuple[str, str, str] | None:
    """Read a complete allocation's prefix, timestamp and suffix."""
    match = re.fullmatch(
        r"(.+?)-(\d{8}-\d{6})-([0-9a-f]{4}|[0-9a-f]{32})", worktree_id,
    )
    return (match[1], match[2], match[3]) if match is not None else None


def session_worktree_allocation(
    worktree_id: str, machine: str, platform: str, *, physical_machine: bool = False,
) -> tuple[str, str] | None:
    """Recognize a complete native allocation, never a partial machine prefix."""
    allocation = parse_session_worktree_id(worktree_id)
    if allocation is None or allocation[0] not in {
        session_worktree_prefix(machine, platform, legacy=legacy, physical_machine=physical_machine)
        for legacy in (False, True)
    }:
        return None
    return allocation[1], allocation[2]
