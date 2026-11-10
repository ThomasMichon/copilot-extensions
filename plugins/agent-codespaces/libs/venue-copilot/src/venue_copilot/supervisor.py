"""The supervising worktree of a venue session a caller launches.

A detached venue session records which worktree launched it
(``LiveSessionVenue.supervisor_ref``), so a successor session in that worktree
-- after a context handoff, say -- can find the workers it supervises with
``agent-bridge live-sessions list --supervisor <ref>``. The ref names the
worktree (``machine/project/worktree_id``), never the launching session, so it
survives the handoff by construction.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from typing import Any, Protocol


class _OwnerRefResult(Protocol):
    stdout: str
    returncode: int

def worktree_ref(ref: str | None) -> str:
    """``ref`` without a ``#session`` suffix, or ``""`` unless it is qualified
    (``machine/project/worktree_id``)."""
    base = str(ref or "").split("#", 1)[0].strip()
    return base if base.count("/") >= 2 else ""


def supervisor_ref(
    *, explicit: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., _OwnerRefResult] = subprocess.run,
) -> str:
    """Resolve an explicit supervisor or the CWD's qualified worktree reference.

    Supervisor metadata identifies the launcher, not the detached child's owner.
    Best-effort; never raises.
    """
    ref = worktree_ref(explicit)
    if ref:
        return ref
    binstub = which("agent-worktrees")
    if not binstub:
        return ""
    try:
        proc = run([binstub, "get", "owner-ref"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return worktree_ref(proc.stdout) if proc.returncode == 0 else ""


def with_supervisor(venue: dict[str, Any], ref: str | None = None) -> dict[str, Any]:
    """``venue`` plus ``supervisor_ref`` when one resolves (unchanged if not)."""
    ref = supervisor_ref() if ref is None else worktree_ref(ref)
    return {**venue, "supervisor_ref": ref} if ref else dict(venue)
