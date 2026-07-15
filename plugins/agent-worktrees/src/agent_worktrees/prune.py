"""Prune-safety triage for worktrees.

Answers one question per worktree, with evidence: **is it safe to prune?**
i.e. is all of its work already on the default branch (or does it hold nothing
worth keeping), so removing the worktree + branch loses no data?

The verdict reconciles three signal sources, in descending order of trust:

1. **Live PR merged-state** (authoritative) -- a ``merged`` PR's content is on
   the default branch by definition.  The local tracking record can go *stale*
   (e.g. an external squash-merge by an automated reviewer leaves a recorded PR
   reading ``open``), so :func:`reconcile_pr_states` refreshes it from the
   provider before assessment.
2. **Git content-on-master** (squash-merge aware) -- ``classify_worktree``
   already proves content landed via ``git cherry``/blob comparison for
   branches that still carry commits.
3. **Session activity** -- a worktree with no commits is only *truly* unused
   when its session held **zero** conversation turns; one that asked a question
   or captured an idea is preserved by default.

This module is intentionally free of I/O for the core assessment
(:func:`assess` is pure); the live lookup is injected as a callable so callers
wire the concrete provider and the assessment stays unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import git_ops, tracking

# --- Verdict categories -----------------------------------------------------
#
# safe == True  (pruning loses nothing):
#   "merged"            -- a tracked PR is merged; work is on the default branch
#   "completed-local"   -- git verified content-on-master (no PRs / direct path)
#   "empty"             -- no commits and no conversation turns
#
# safe == False (pruning would lose work or interrupt a flow):
#   "open-pr"           -- a tracked PR is still live (in review / recoverable)
#   "conversation-only" -- no commits, but the session held >0 turns
#   "unmerged"          -- WIP/dirty/orphan: content not on the default branch
#   "active"            -- a live Copilot session owns the worktree
#
# needs a human look (safe == False, but distinct from "unmerged"):
#   "closed-unmerged"   -- every tracked PR is terminal and none merged; the
#                          content is not confirmed on the default branch
#   "gone"              -- worktree directory is missing (caller verifies the
#                          branch is merged before deleting)

CATEGORY_SAFE = {"merged", "completed-local", "empty"}


@dataclass
class PruneVerdict:
    """The prune-safety assessment for one worktree."""

    safe: bool
    category: str
    reason: str
    turn_count: int = 0


def assess(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    *,
    turn_count: int = 0,
) -> PruneVerdict:
    """Classify a worktree's prune-safety from its record, git state, and turns.

    ``rec`` should already be reconciled against the provider (see
    :func:`reconcile_pr_states`) so that ``rec.prs`` reflects *live* PR state;
    a stale ``open`` here yields a (false) ``open-pr`` verdict, which is the
    safe failure direction.
    """
    state = info.state
    S = git_ops.WorktreeState

    # A live session owns it -- never prune, regardless of anything else.
    if state == S.ACTIVE:
        return PruneVerdict(False, "active", "live Copilot session in use",
                            turn_count)

    # Directory missing -- the caller must verify the branch is merged before
    # deleting; surface it distinctly rather than guessing.
    if state == S.GONE:
        return PruneVerdict(False, "gone", "worktree directory missing",
                            turn_count)

    # Uncommitted changes in the working tree -- unsafe to remove.
    if state == S.DIRTY:
        return PruneVerdict(False, "unmerged",
                            f"{info.dirty} uncommitted change(s)", turn_count)

    # No merge base with upstream -- cannot prove anything landed.
    if state == S.ORPHAN:
        return PruneVerdict(False, "unmerged", "no merge base with upstream",
                            turn_count)

    # --- PR-aware path (PR mode) --------------------------------------------
    # In PR mode the worktree/ branch is reset to the upstream tip, so git sees
    # no unique commits and the *real* merge state lives on the PR(s).  Trust
    # the (reconciled) PR records over git heuristics here.
    if rec.prs:
        if rec.has_live_pr():
            live = [p.number for p in rec.prs
                    if not tracking._pr_is_terminal(p)]
            nums = ", ".join(f"#{n}" for n in live if n is not None) or "?"
            return PruneVerdict(False, "open-pr",
                                f"PR {nums} still open/in review", turn_count)
        merged = [p.number for p in rec.prs if p.state == "merged"]
        if merged:
            nums = ", ".join(f"#{n}" for n in merged if n is not None) or "?"
            return PruneVerdict(True, "merged", f"PR {nums} merged", turn_count)
        # All PRs terminal, none merged.  Content may still have landed via a
        # sibling/duplicate PR or a direct path -- trust git if it proved so.
        if state == S.COMPLETED:
            return PruneVerdict(True, "completed-local",
                                "PR closed unmerged, but content is on the "
                                "default branch", turn_count)
        closed = ", ".join(f"#{p.number}" for p in rec.prs
                           if p.number is not None) or "?"
        return PruneVerdict(False, "closed-unmerged",
                            f"PR {closed} closed without merging; content not "
                            "confirmed on the default branch", turn_count)

    # --- No PRs: rely on git state + session activity -----------------------
    if state == S.COMPLETED:
        return PruneVerdict(True, "completed-local",
                            "content is on the default branch", turn_count)

    if state == S.WIP:
        return PruneVerdict(False, "unmerged",
                            "branch has content not on the default branch",
                            turn_count)

    if state == S.UNUSED:
        if turn_count > 0:
            return PruneVerdict(False, "conversation-only",
                                f"no commits, but the session held "
                                f"{turn_count} turn(s)", turn_count)
        return PruneVerdict(True, "empty",
                            "no commits and no conversation", turn_count)

    return PruneVerdict(False, "unmerged",
                        f"unclassified git state: {state.value}", turn_count)


@dataclass
class CleanupDisposition:
    """How cleanup should treat a worktree, derived from its verdict."""

    cleanable: bool
    bucket: str   # clean | active | unused | conversation | follow-up |
    #               open-pr | closed-unmerged | dirty | wip | unmerged
    reason: str


def cleanup_disposition(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    *,
    turn_count: int = 0,
    include_unused: bool = False,
    include_conversations: bool = False,
) -> CleanupDisposition:
    """Map a prune verdict onto a cleanup action + bucket.

    The GONE state is intentionally **not** handled here: a missing worktree
    needs a git branch-merged check the caller owns.  Everything else flows
    from :func:`assess`.

    Safety invariant: a ``finalized`` worktree (or one git proves COMPLETED) is
    always cleanable -- its work is at minimum pushed to the remote feature
    branch, so removing the local copy loses nothing.  This preserves the
    long-standing default and avoids over-preserving on a *stale* local PR
    state (use ``--reconcile-prs`` / live reconcile to refine those).
    """
    v = assess(rec, info, turn_count=turn_count)
    S = git_ops.WorktreeState

    if info.state == S.ACTIVE:
        return CleanupDisposition(False, "active", v.reason)

    # worktree-status-core: an agent-asserted follow-up overrides a would-be
    # SAFE verdict. A finalized/merged/completed worktree the agent flagged as
    # having actionable follow-ups (un-pushed change, undeployed merge, leftover
    # temp state) is REVIEW -- never auto-pruned SAFE. Only downgrades the
    # clean/SAFE path; a dirty/wip/open-pr worktree is already non-cleanable, so
    # the flag adds nothing there.
    if rec.follow_up and (
        rec.status == "finalized" or info.state == S.COMPLETED
        or v.category == "merged"
    ):
        return CleanupDisposition(
            False, "follow-up",
            f"{v.reason} · agent flagged follow-ups pending")

    if rec.status == "finalized" or info.state == S.COMPLETED:
        return CleanupDisposition(True, "clean", v.reason)

    if v.category == "open-pr":
        return CleanupDisposition(False, "open-pr", v.reason)
    if v.category == "closed-unmerged":
        return CleanupDisposition(False, "closed-unmerged", v.reason)
    if v.category == "merged":
        return CleanupDisposition(True, "clean", v.reason)
    if v.category == "empty":
        return CleanupDisposition(
            include_unused or include_conversations, "unused", v.reason)
    if v.category == "conversation-only":
        return CleanupDisposition(include_conversations, "conversation", v.reason)
    if info.state == S.DIRTY:
        return CleanupDisposition(False, "dirty", v.reason)
    if info.state == S.WIP:
        return CleanupDisposition(False, "wip", v.reason)
    return CleanupDisposition(False, "unmerged", v.reason)


def reconcile_pr_states(
    rec: tracking.WorktreeRecord,
    lookup: Callable[[str, int], "object | None"],
    *,
    only_live: bool = True,
) -> list[tuple[int, str, str]]:
    """Refresh tracked PR states from the provider; return the changes.

    ``lookup(repo, number)`` returns a provider ``PullResult`` (or None when the
    PR can't be looked up).  For each candidate PR, if the live result reports
    ``merged`` the local state becomes ``"merged"``; otherwise a live state of
    ``"closed"`` becomes ``"closed"``.  A live ``open`` is left as-is.

    With ``only_live`` (the default) only non-terminal records are refreshed --
    that is the stale case (local ``open`` while the PR merged externally).
    Pass ``only_live=False`` to re-verify terminal records too.

    Mutates ``rec.prs`` in place and returns ``(number, old_state, new_state)``
    tuples for every record that changed.  The caller persists ``rec`` if it
    wants the healed state on disk.
    """
    changes: list[tuple[int, str, str]] = []
    for pr in rec.prs:
        if pr.number is None:
            continue
        if only_live and tracking._pr_is_terminal(pr):
            continue
        repo = pr.repo or rec.repo
        try:
            result = lookup(repo, pr.number)
        except Exception:
            result = None
        if result is None:
            continue
        new_state = pr.state
        if getattr(result, "merged", False):
            new_state = "merged"
        elif str(getattr(result, "state", "")) == "closed":
            new_state = "closed"
        if new_state != pr.state:
            changes.append((pr.number, pr.state, new_state))
            pr.state = new_state
    return changes
