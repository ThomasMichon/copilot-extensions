"""Resolve the calling agent's identity (machine + worktree) from context.

The only durable agent id the facility has is the ``machine`` + ``worktree``
pair, and the authority on "which worktree am I in" is **agent-worktrees**, which
resolves it from the current directory (the way git resolves its repo). So
agent-dispatch *delegates* to the ``agent-worktrees`` CLI when present -- a soft
dependency, like the agent-bridge integration -- rather than reading an env var
or re-implementing the resolution. It degrades to explicit ``--machine`` /
``--worktree`` when agent-worktrees is absent (e.g. outside the facility) or the
caller isn't inside a worktree.
"""

from __future__ import annotations

import os
import shutil
import subprocess


def _aw_get(key: str) -> str | None:
    """Return `agent-worktrees get <key>` (CWD-resolved), or None if unavailable."""
    exe = shutil.which("agent-worktrees")
    if exe is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "get", key], check=False, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def resolve_identity() -> tuple[str | None, str | None]:
    """Resolve the caller's ``(machine, worktree)`` from CWD via agent-worktrees.

    Either element may be ``None`` when agent-worktrees is absent or the caller
    isn't inside a worktree -- callers then supply the value explicitly.
    """
    machine = _aw_get("machine")
    wt_dir = _aw_get("worktree-dir")
    worktree = os.path.basename(wt_dir.rstrip("/\\")) if wt_dir else None
    return (machine, worktree)
