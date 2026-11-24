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


_CODENAME_ID_RE = re.compile(r"([a-z0-9]+(?:-[a-z0-9]+)*)-([0-9a-f]{4})")
# A prefixed ``<date>-<time>`` pair marks a timestamped allocation (possibly
# another machine's), never a codename. A bare numeric pair stays a codename.
_TIMESTAMP_PAIR_RE = re.compile(r"-\d{8}-\d{6}(?:-|$)")


def codename_session_worktree_id(codename: str, suffix: str) -> str:
    """Name a new session worktree ``<codename>-<4hex>`` to keep checkout paths short."""
    worktree_id = f"{codename}-{suffix}"
    if parse_codename_session_worktree_id(worktree_id) != (codename, suffix):
        raise ValueError(f"invalid codename-based worktree id {worktree_id!r}")
    return worktree_id


def parse_codename_session_worktree_id(worktree_id: str) -> tuple[str, str] | None:
    """Read the codename and suffix of a ``<codename>-<4hex>`` ID.

    A timestamped ID (``<machine>-<platform>-<ts>-<suffix>`` or ``sys-...``)
    is never read as a codename-based ID.
    """
    if parse_session_worktree_id(worktree_id) is not None:
        return None
    match = _CODENAME_ID_RE.fullmatch(worktree_id)
    if match is None or _TIMESTAMP_PAIR_RE.search(match[1]):
        return None
    return match[1], match[2]


def allocation_matches(worktree_id: str, timestamp: str, suffix: str) -> bool:
    """True when an ID can come from this timestamp/suffix allocation, in either format."""
    timestamped = parse_session_worktree_id(worktree_id)
    if timestamped is not None:
        return timestamped[1:] == (timestamp, suffix)
    named = parse_codename_session_worktree_id(worktree_id)
    return named is not None and named[1] == suffix


def pair_stub(harness_id: str, timestamp: str | None, suffix: str) -> str:
    """The shared pair id of a harness/knowledge pair.

    Timestamped IDs keep the historical ``<ts>-<suffix>`` stub. A codename-based
    ID has no timestamp, so the harness ID itself is the stub; repair can
    recover it from the record alone.
    """
    if parse_codename_session_worktree_id(harness_id) is not None:
        return harness_id
    if not timestamp:
        raise ValueError(f"timestamped worktree id {harness_id!r} needs its creation timestamp")
    return f"{timestamp}-{suffix}"


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
