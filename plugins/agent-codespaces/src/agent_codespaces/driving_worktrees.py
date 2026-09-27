"""Resolve CodeSpace rows back to their driving worktrees."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _choose_owner(owners: list[dict]) -> str:
    if len(owners) == 1:
        return str(owners[0].get("worktree_id") or "")
    active = [o for o in owners if o.get("status") == "active"]
    if len(active) == 1:
        return str(active[0].get("worktree_id") or "")
    return ""


def codespace_claim_owner_worktrees(codespace_names: Iterable[str]) -> dict[str, str]:
    """Resolve ``codespace`` claim refs to their owning local worktree ids.

    This is a soft cross-plugin integration: agent-codespaces has no hard
    dependency on agent-worktrees, so missing imports, old versions, and bad
    records all degrade to an empty map.
    """
    names = {str(name) for name in codespace_names if str(name)}
    if not names:
        return {}
    try:
        from agent_worktrees import claims_owner
    except ImportError:
        return {}
    try:
        by_ref = claims_owner.find_claim_owners_for_refs("codespace", names)
    except Exception:
        return {}
    return {
        name: worktree_id
        for name, owners in by_ref.items()
        if (worktree_id := _choose_owner(owners))
    }


def resolve_workstream_box(
    members: Iterable[Any],
    repo: str,
    owner: str | None,
) -> str | None:
    """The CodeSpace ``owner`` (a worktree id, or a bound effort ref) already
    claims for ``repo``, if any -- the box a Phase 2b venue request should
    resume rather than re-deriving from any idle/clean box in the pool.

    Looks up ``owner``'s own outbound ``codespace`` claims (active or at-rest
    -- an at-rest claim is exactly "mine, just not running right now"), then
    picks the one still present in ``members`` and matching ``repo``. Multiple
    live matches (should not normally happen -- one workstream, one box) picks
    the first deterministically (sorted by name) rather than raising. Soft
    cross-plugin integration + best-effort: no owner, no ``agent_worktrees``,
    or no matching claim all yield ``None`` (the caller then creates fresh,
    never silently borrowing an unrelated box).
    """
    if not owner:
        return None
    try:
        from agent_worktrees import claims_owner
    except ImportError:
        return None
    try:
        refs = claims_owner.claims_of_owner(owner, "codespace")
    except Exception:
        return None
    if not refs:
        return None
    from .config import _repo_matches_codespace

    by_name = {m.name: m for m in members}
    candidates = sorted(
        name for name in refs
        if (m := by_name.get(name)) is not None
        and _repo_matches_codespace(repo, m.repository)
    )
    return candidates[0] if candidates else None


def resolve_current_workstream_box(
    members: Iterable[Any],
    repo: str,
) -> str | None:
    """``resolve_workstream_box`` for THIS process's own calling worktree.

    Combines ``lease.resolve_owner_worktree()`` (who is asking) with
    :func:`resolve_workstream_box` (what they already claim) into the one call
    a venue request needs -- degrade-safe end to end: any resolution failure
    (no worktree context, no agent_worktrees, bad records) yields ``None``,
    the same "create fresh" fallback as an unresolvable owner.
    """
    try:
        from .lease import resolve_owner_worktree

        owner = resolve_owner_worktree()
    except Exception:
        return None
    return resolve_workstream_box(members, repo, owner)


def same_worktree_family(holder: str, owner: str) -> bool:
    """True when ``holder`` and ``owner`` are the same worktree, or one created
    the other (directly or transitively, via ``owner_ref``).

    A parent worktree that spawned a child to work on its behalf (e.g. a
    dedicated ``copilot-extensions`` worktree carved off a driving harness/task
    worktree) is not a foreign contender for a CodeSpace either of them already
    claimed -- it's the same task lineage. Recognizing that here means neither
    ``agent-codespaces``' own claim path nor ``agent-bridge``'s Session-Host
    dispatch (which shells out to it) needs an explicit force-takeover to keep
    operating on a box its own parent/child worktree already holds. Soft
    cross-plugin integration: no ``agent_worktrees`` / stale version / bad
    records all degrade to ``False`` (today's stricter behavior), never a false
    family match.
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
