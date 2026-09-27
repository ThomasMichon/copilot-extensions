"""Cross-plugin worktree-lineage lookups for the container lease broker.

Mirrors ``agent_codespaces.driving_worktrees``'s ``same_worktree_family`` --
kept as its own small module (rather than a hard dependency) so
agent-containers never requires agent-worktrees to be installed.
"""

from __future__ import annotations


def same_worktree_family(holder: str, owner: str) -> bool:
    """True when ``holder`` and ``owner`` are the same worktree, or one created
    the other (directly or transitively, via ``owner_ref``).

    A parent worktree that spawned a child to work on its behalf is not a
    foreign contender for a container either of them already leased -- it's
    the same task lineage. Recognizing that here means a container lease
    conflict doesn't need an explicit force-takeover between them. Soft
    cross-plugin integration: no ``agent_worktrees`` / stale version / bad
    records / a lease-holder string that isn't actually a worktree id all
    degrade to ``False`` (today's stricter behavior), never a false family
    match.
    """
    if not holder or not owner:
        return False
    try:
        from agent_worktrees import claims_owner
    except ImportError:
        return False
    try:
        return bool(claims_owner.same_worktree_family(holder, owner))
    except Exception:
        return False
