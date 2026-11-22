"""Git state/result models, re-exported by :mod:`git_ops`."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypedDict


class _GitProbeOptions(TypedDict, total=False):
    timeout: float
    kill_tree: bool
    observation_deadline: float


class _GitHookOptions(TypedDict, total=False):
    no_hooks: bool


class _GitStateFields(TypedDict):
    current_branch: str | None
    branch_drift: bool
    fetch_requested: bool
    fetch_failed: bool


class WorktreeState(str, Enum):
    """Worktree state classification.

    Every value except :attr:`CONVO` is produced by :func:`classify_worktree`
    from pure git inspection.  ``CONVO`` is a *session-derived* refinement of
    ``UNUSED`` -- a clean, commit-less worktree whose Copilot session
    nonetheless held conversation turns -- layered on by
    :func:`refine_state_with_session`.  It lives in this enum (rather than as a
    private render flag) so the tmux status bar and the picker data contract
    (``list --json --classify``) share one display vocabulary.
    """

    ACTIVE = "active"
    WIP = "wip"
    DIRTY = "dirty"
    UNUSED = "unused"
    CONVO = "convo"
    COMPLETED = "completed"
    GONE = "gone"
    DEHYDRATED = "dehydrated"  # checkout reversibly removed; see checkout_hydration_catalog
    ORPHAN = "orphan"
    UNKNOWN = "unknown"


@dataclass
class WorktreeStateInfo:
    """Result of classifying a worktree's git state."""

    state: WorktreeState
    ahead: int = 0
    behind: int = 0
    dirty: int = 0
    title: str = ""
    current_branch: str | None = None
    """The worktree's actual HEAD branch (None if detached or unreadable)."""
    branch_drift: bool = False
    """True when the worktree's HEAD is on a different branch than tracked."""
    fetch_failed: bool = False
    """True when a requested fetch either failed outright (network down,
    remote unreachable -- classification proceeded on stale local refs) OR
    could not be confirmed after an actual fetch attempt (a timeout on the
    fetch itself or a later probe) -- both cases mean a `fetch=True`
    caller must not treat the result as refreshed evidence. Always False
    when ``fetch=False`` (no fetch was attempted) or classification short-
    circuited before reaching the fetch (GONE/zombie/ACTIVE)."""
    fetch_requested: bool = False
    """True only when ``fetch=True`` actually reached the point of
    attempting a fetch (i.e. classification proceeded past the GONE/zombie/
    ACTIVE short-circuits). A caller must derive "was this genuinely
    refreshed" as ``fetch_requested and not fetch_failed`` -- `fetch_failed`
    alone is insufficient, since it stays False when no fetch was ever
    attempted (e.g. the caller passed ``fetch=False``), which would
    otherwise let a fetch-free classification masquerade as refreshed."""


@dataclass
class FastForwardResult:
    """Outcome of attempting to fast-forward a worktree to its upstream.

    ``updated`` is True only when commits were actually advanced.  ``reason``
    is a stable token describing what happened (or why nothing did):

    - ``updated``     -- fast-forwarded ``behind`` commits onto the branch.
    - ``up-to-date``  -- clean and already level with upstream (no-op).
    - ``dirty``       -- working tree has uncommitted changes; skipped.
    - ``ahead``       -- branch has local commits, none behind; skipped.
    - ``diverged``    -- branch has both local commits and is behind; skipped.
    - ``detached``    -- HEAD is detached; skipped.
    - ``no-upstream`` -- the upstream ref does not exist; skipped.
    - ``orphan``      -- no merge base with upstream; skipped.
    - ``gone``        -- the worktree path/.git is missing; skipped.
    - ``ff-failed``   -- the fast-forward merge itself failed; skipped.

    ``fetch_error``: the requested fetch failed (compared to the last-known ref).
    """

    updated: bool
    reason: str
    behind: int = 0
    ahead: int = 0
    fetch_error: str | None = None


@dataclass
class WorktreeBaseResult:
    """Fresh-base resolution for worktree creation or local source binding."""

    start_point: str
    fetched: bool
    fetch_error: str | None
    anchor: FastForwardResult
