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
from typing import Callable, Optional

from . import git_ops, tracking

# A claimant-liveness probe: given a resource's qualified ``owner_ref``, report
# whether the owning worktree is still alive. Tri-state on purpose:
#   * True  -- claimant confirmed alive           -> spare an IN-FLIGHT resource
#   * None  -- claimant liveness UNCONFIRMED       -> spare an IN-FLIGHT resource
#              (absence of a *local* owner is NOT proof of no owner)
#   * False -- claimant confirmed GONE             -> fall through to git/PR state
# A FINISHED resource (its status ``finalized``, or content on the default
# branch via a merged PR / git-COMPLETED) is collectable even when this probe
# reports alive/unconfirmed; the gate only protects a live
# owner's still-in-flight resources.
# Injected (not called inline) so the pure assessment stays unit-testable and a
# caller wires the concrete same-machine / cross-fabric resolver.
ClaimantAliveProbe = Callable[[str], Optional[bool]]

# A paired-sibling-final probe (citadel #957): given a PAIRED worktree record,
# report whether its -harness/-knowledge sibling is finalized so the pair is safe
# to prune. Tri-state, mirroring ClaimantAliveProbe:
#   * True  -- nothing to wait on (anchor pairing, or the sibling is finalized)
#              -> the BOTH-finalized gate is satisfied
#   * False -- the sibling worktree exists but is NOT yet finalized -> hold
#   * None  -- the sibling has no local record (cross-machine / not yet carved)
#              -> unknown; the caller spares the worktree (safe direction)
# Injected (not called inline) so the pure assessment stays unit-testable.
PairedSiblingFinalProbe = Callable[[tracking.WorktreeRecord], bool | None]

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
#   "claimed"           -- an IN-FLIGHT outbound resource of another worktree
#                          whose claimant is alive or not-confirmed-gone
#                          (agent-fabric `claimed-resource-not-reclaimed`). A
#                          FINISHED resource is NOT "claimed" -- it stays
#                          merged/completed-local and is collectable even under a
#                          live claimant.

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
    claimant_alive: ClaimantAliveProbe | None = None,
) -> PruneVerdict:
    """Classify a worktree's prune-safety from its record, git state, and turns.

    ``rec`` should already be reconciled against the provider (see
    :func:`reconcile_pr_states`) so that ``rec.prs`` reflects *live* PR state;
    a stale ``open`` here yields a (false) ``open-pr`` verdict, which is the
    safe failure direction.

    When ``claimant_alive`` is injected and this worktree carries an
    ``owner_ref`` (it was created as another worktree's outbound resource), the
    claimant-safety rule spares it while a live -- or not-confirmed-gone --
    claimant may still be using it, so a machine-local sweep can't mistake an
    actively-owned cross-repo resource for an orphan. NARROWED so that the
    spare applies only while the resource is still
    IN-FLIGHT; a FINISHED resource (its own status ``finalized``, or content on
    the default branch via a merged PR / git-COMPLETED) is collectable even
    under a live claimant, because a finished child of a long-open host is
    garbage, not in-use. A claimant *confirmed gone* frees even an in-flight one.
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

    # Claimed-resource safety (agent-fabric `claimed-resource-not-reclaimed`),
    # NARROWED so a finished resource is collectable: this worktree is another
    # worktree's outbound resource. Compute the content verdict first, then spare
    # it while its claimant is alive / not-confirmed-gone -- UNLESS the owner has
    # demonstrably moved on, i.e. the resource is already FINISHED (its own status
    # is ``finalized``, or its content is proven on the default branch via a
    # merged PR / git-COMPLETED). A finished resource is collectable garbage even
    # under a live claimant, so a host session kept open for days never pins its
    # merged children forever. IN-FLIGHT owned resources (dirty / wip / orphan /
    # open-pr / closed-unmerged / empty / conversation-only) are still spared: the
    # owner may resume, is mid-review, or (empty) is a just-created scaffold. A
    # claimant *confirmed gone* (probe returns ``False``) lets even those fall
    # through.
    verdict = _content_verdict(rec, info, turn_count)
    owner_moved_on = (verdict.category in _OWNER_MOVED_ON
                      or rec.status == "finalized")
    if rec.owner_ref and claimant_alive is not None and not owner_moved_on:
        alive = claimant_alive(rec.owner_ref)
        if alive is not False:
            why = "claimant alive" if alive else "claimant liveness unconfirmed"
            return PruneVerdict(False, "claimed",
                                f"owned as a resource by {rec.owner_ref} "
                                f"({why})", turn_count)
    return verdict


# Content-verdict categories that prove the owner has FINISHED with a resource,
# so it is collectable even while its claimant is alive. NOT
# "empty" (a live owner's just-created scaffold) nor "open-pr"/"unmerged"
# (in-flight); those keep the claimed-resource protection.
_OWNER_MOVED_ON = frozenset({"merged", "completed-local"})


def _content_verdict(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    turn_count: int,
) -> PruneVerdict:
    """The prune verdict from git/PR/session content alone (ownership-agnostic).

    Split out of :func:`assess` so the claimed-resource gate can decide, from the
    resulting category, whether an owned resource is still in-flight (spare) or
    finished (collectable even under a live claimant). Assumes the caller already
    handled ACTIVE / GONE.
    """
    state = info.state
    S = git_ops.WorktreeState

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
    #               held-claims | open-pr | closed-unmerged | dirty | wip |
    #               unmerged
    reason: str


def cleanup_disposition(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    *,
    turn_count: int = 0,
    include_unused: bool = False,
    include_conversations: bool = False,
    claimant_alive: ClaimantAliveProbe | None = None,
    paired_sibling_final: PairedSiblingFinalProbe | None = None,
) -> CleanupDisposition:
    """Map a prune verdict onto a cleanup action + bucket.

    The GONE state is intentionally **not** handled here: a missing worktree
    needs a git branch-merged check the caller owns.  Everything else flows
    from :func:`assess`.

    Safety invariant: a ``finalized`` worktree (or one git proves COMPLETED)
    is cleanable **provided its working tree carries no uncommitted content**
    (``info.dirty == 0``) -- at that point its work is at minimum pushed to
    the remote feature branch, so removing the local copy loses nothing. This
    preserves the long-standing default and avoids over-preserving on a
    *stale* local PR state (use ``--reconcile-prs`` / live reconcile to
    refine those). A worktree finalized earlier and modified afterward
    (``info.dirty > 0``, whether classified ``DIRTY`` or an ``ORPHAN`` that
    still carries a dirty count) is excluded from this shortcut regardless of
    ``rec.status`` -- see the ``info.dirty > 0`` guard below.

    Beyond the dirty exclusion, the other exception is an IN-FLIGHT claimed
    resource (agent-fabric `claimed-resource-not-reclaimed`): when
    ``claimant_alive`` is injected and the claimant is alive /
    not-confirmed-gone, a still-in-flight resource is spared because its
    owner may still be using it. A FINISHED claimed resource (finalized /
    merged / git-COMPLETED) is NOT spared -- it is collectable even under a
    live claimant, so a host kept open for days does not pin its merged
    children.
    """
    v = assess(rec, info, turn_count=turn_count, claimant_alive=claimant_alive)
    S = git_ops.WorktreeState

    if info.state == S.ACTIVE:
        return CleanupDisposition(False, "active", v.reason)

    # Claimed-resource safety spares an IN-FLIGHT owned resource while its
    # claimant is alive/unconfirmed. A FINISHED resource never reaches here as
    # "claimed" (assess returns merged/completed-local, or its status is
    # finalized), so it flows to the finalized/COMPLETED clean path below and is
    # collected even under a live claimant.
    if v.category == "claimed":
        return CleanupDisposition(False, "claimed", v.reason)

    # worktree-finality-and-obligations (effort): a HELD outbound resource
    # claim (``active`` or ``at-rest`` -- see ``ResourceClaim.is_live``)
    # overrides a would-be SAFE verdict, mirroring the follow-up gate below.
    # Since a finalized owner can now accept a new claim (finalize is not
    # terminal; see ``tracking.add_resource_claim``), cleanup must not treat
    # ``status == finalized`` as proof the worktree is claim-free -- only
    # ``finalize`` itself re-validates and releases at-rest claims. A
    # ``released``/``abandoned`` claim is not held and does not block.
    held_claims = [c for c in rec.resources if c.is_live]
    if held_claims and (
        rec.status == "finalized" or info.state == S.COMPLETED
        or v.category == "merged"
    ):
        return CleanupDisposition(
            False, "held-claims",
            f"{v.reason} · {len(held_claims)} held resource claim(s) pending")

    # worktree-status-core: an agent-asserted follow-up overrides a would-be
    # SAFE verdict. A finalized/merged/completed worktree with actionable
    # follow-ups (un-pushed change, undeployed merge, leftover temp state) is
    # REVIEW -- never auto-pruned SAFE. Only downgrades the clean/SAFE path; a
    # dirty/wip/open-pr worktree is already non-cleanable, so this adds nothing
    # there. worktree-finality-and-obligations Phase 3: counts the itemized
    # `follow_ups` ledger (open/pending-transfer items), falling back to the
    # legacy boolean when the ledger is empty -- see
    # `tracking.effective_open_follow_up_count`.
    open_follow_ups = tracking.effective_open_follow_up_count(rec)
    if open_follow_ups and (
        rec.status == "finalized" or info.state == S.COMPLETED
        or v.category == "merged"
    ):
        return CleanupDisposition(
            False, "follow-up",
            f"{v.reason} · {open_follow_ups} open follow-up(s) pending")

    # citadel paired-worktree BOTH-gate (#957): a paired -harness/-knowledge
    # worktree is prunable only once BOTH halves are finalized. When the
    # sibling-final probe is injected and the sibling is not yet finalized
    # (False) or its state is unknown (None), hold a would-be-SAFE worktree so
    # cleanup never prunes one half of a live pair, orphaning the other. Mirrors
    # the follow-up override: only downgrades the clean/SAFE path (a dirty / wip
    # / open-pr worktree is already non-cleanable, so the gate adds nothing
    # there). A satisfied probe (True) flows through normally.
    if rec.is_paired and paired_sibling_final is not None and (
        rec.status == "finalized" or info.state == S.COMPLETED
        or v.category in ("merged", "empty")
    ):
        sib_final = paired_sibling_final(rec)
        if sib_final is not True:
            why = ("sibling not yet finalized" if sib_final is False
                   else "paired sibling state unknown")
            return CleanupDisposition(
                False, "paired-pending",
                f"{v.reason} · held until BOTH paired worktrees finalized "
                f"({why})")

    # (#2635-class ordering fix, extended by the cleanup-toctou-revalidation
    # effort): WIP and un-included conversation-only must be checked BEFORE
    # the finalized/COMPLETED shortcut below -- a record whose tracking
    # status is "finalized" (or whose git state reads COMPLETED) can still
    # gain a committed WIP change or a fresh conversation turn since that
    # status was set. Trusting the shortcut first would let a stale
    # "finalized" status silently mask fresh unsafe content, mirroring the
    # `dirty` ordering bug PR #2635 already fixed one check below. When
    # `include_conversations` is set, conversation-only content is meant to
    # be cleanable, so it is intentionally left to fall through to its later
    # category check rather than being blocked here.
    if info.state == S.WIP:
        return CleanupDisposition(False, "wip", v.reason)
    if v.category == "conversation-only" and not include_conversations:
        return CleanupDisposition(False, "conversation", v.reason)

    # Safety invariant (mirrors _apply_tracking_override in __main__.py): any
    # uncommitted content must never be treated as cleanable via the raw
    # rec.status == "finalized" shortcut below. A worktree finalized earlier
    # and modified afterward still carries a tracking status of "finalized",
    # but that status describes work already verified safe on the default
    # branch at finalize time -- it says nothing about content added since.
    # Checked two ways so neither a missing count nor a stale state label
    # slips through: info.state == S.DIRTY is kept as an explicit fallback
    # because some callers (e.g. __main__._classify_from_cache) reconstruct
    # a WorktreeStateInfo from a cached git_state string without
    # repopulating `dirty`, so state == DIRTY, dirty == 0 can reach here;
    # info.dirty > 0 is needed separately because an ORPHAN classification
    # (no merge base) can also carry a nonzero dirty count with state !=
    # DIRTY. Neither check alone covers both gaps.
    if info.state == S.DIRTY or info.dirty > 0:
        return CleanupDisposition(False, "dirty", v.reason)

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
    return CleanupDisposition(False, "unmerged", v.reason)


# ── Canonical closure descriptor (worktree-finality-and-obligations, Phase 4) ─

#: Descriptor schema version. Bump on any incompatible shape change; older
#: consumers must treat an unrecognized version as provisional, never FINAL.
#: Bumped to 2 for worktree-finality-and-obligations Phase 9: the top-level
#: ``evidence_mode``/``evidence_complete`` pair (a single all-or-nothing
#: freshness flag for the whole descriptor) is replaced by ``facts``, a named
#: sub-state map where each fact carries its OWN ``confirmed`` freshness --
#: see :data:`FACT_NAMES` and :func:`assemble_closure_descriptor`.
DESCRIPTOR_VERSION = 2

#: The fixed, named sub-state facts a closure descriptor decomposes into
#: (worktree-finality-and-obligations Phase 9). Each fact is tracked and
#: freshness-marked independently -- an unconfirmed fact is marked in place,
#: never collapsed into (or spawning) a separate whole state.
#:
#: * ``checkpoint_activity`` -- turns since the last checkpoint; always
#:   locally computed, so always confirmed.
#: * ``upstream_containment`` -- whether the branch's content is at or ahead
#:   of ``origin/[main|master]``; requires a fresh fetch to be ``confirmed``.
#: * ``local_dirtiness`` -- uncommitted change count; always locally
#:   computed, so always confirmed.
#: * ``open_claims`` -- held resource claims, open follow-ups, and any
#:   PR/blocker state that depends on a provider lookup; ``confirmed`` when
#:   that lookup is fresh (mirrors ``upstream_containment``'s freshness input
#:   until the Phase 9 repo-scoped ledger lets them diverge).
#: * ``pending_handoff`` -- an unclaimed context-handoff baton for this
#:   worktree. **Not yet wired**: always reports ``confirmed=False`` with a
#:   ``None`` value until read-only baton wiring lands (Phase 9 follow-up).
FACT_NAMES = (
    "checkpoint_activity", "upstream_containment", "local_dirtiness",
    "open_claims", "pending_handoff",
)

#: The closed blocker-code vocabulary (design.md). A future code requires a
#: version bump. NOTE: this module only ever emits a SUBSET of this set (the
#: codes it can actually derive from `assess`/`cleanup_disposition` -- see
#: `assemble_closure_descriptor`'s docstring for what's not yet wired).
BLOCKER_CODES = frozenset({
    "held-claims", "open-follow-ups", "open-pr", "closed-unmerged", "unmerged",
    "dirty", "wip", "claimed-live", "paired-pending", "active-effort",
    "inbound-obligation", "unverified-squash", "live-session", "finalizing",
    "checkout-missing", "prune-review-required", "incomplete-evidence",
    "unsupported-descriptor",
})

_BASE_STATE_LABELS: dict[git_ops.WorktreeState, str] = {
    git_ops.WorktreeState.ACTIVE: "ACTIVE",
    git_ops.WorktreeState.DIRTY: "DIRTY",
    git_ops.WorktreeState.WIP: "WIP",
    git_ops.WorktreeState.UNUSED: "UNUSED",
    git_ops.WorktreeState.CONVO: "CONVO",
    git_ops.WorktreeState.GONE: "GONE",
    git_ops.WorktreeState.ORPHAN: "ORPHAN",
    git_ops.WorktreeState.UNKNOWN: "UNKNOWN",
}

#: cleanup_disposition bucket -> the single blocker code it maps to (a bucket
#: not listed here contributes no bucket-derived blocker of its own; held
#: claims / open follow-ups are still added independently below by count).
_BUCKET_TO_BLOCKER: dict[str, str] = {
    "follow-up": "open-follow-ups",
    "held-claims": "held-claims",
    "open-pr": "open-pr",
    "closed-unmerged": "closed-unmerged",
    "unmerged": "unmerged",
    "dirty": "dirty",
    "wip": "wip",
    "claimed": "claimed-live",
    "paired-pending": "paired-pending",
}

#: cleanup_disposition bucket -> graded action disposition (design.md's
#: `unsafe > blocked > opt-in/record-reap > safe` precedence). ``gone`` /
#: managed-record-reap buckets aren't produced by `cleanup_disposition`
#: itself (its own docstring: GONE is handled by the caller), so
#: `record-reap` is not reachable from this mapping yet.
_BUCKET_TO_ACTION_DISPOSITION: dict[str, str] = {
    "clean": "safe",
    "active": "unsafe",
    "unused": "opt-in",
    "conversation": "opt-in",
    "follow-up": "blocked",
    "held-claims": "blocked",
    "open-pr": "blocked",
    "closed-unmerged": "blocked",
    "dirty": "unsafe",
    "wip": "unsafe",
    "unmerged": "unsafe",
    "claimed": "blocked",
    "paired-pending": "blocked",
}


@dataclass
class ClosureDescriptor:
    """The one canonical closure/display descriptor (design.md).

    Preserves Git settlement, held-claim count, and open-follow-up count as
    INDEPENDENT facts (never re-derived per surface); ``closure.final`` and
    ``action`` are computed FROM them, not stored opinions. ``facts`` (Phase
    9) carries per-fact provenance: ``FINAL`` requires the
    ``upstream_containment`` and ``open_claims`` facts to BOTH be
    independently ``confirmed`` -- a fact that isn't is marked unconfirmed in
    place, and the whole descriptor is downgraded defensively (see
    :func:`assemble_closure_descriptor`), never collapsed into a separate
    whole state.
    """

    version: int
    computed_at: str
    facts: dict
    base_state: str
    label: str
    style: str
    compact: str
    git: dict
    claims: dict
    follow_ups: dict
    blockers: list[dict]
    final: bool
    action_disposition: str
    action_bucket: str

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "computed_at": self.computed_at,
            "facts": self.facts,
            "base_state": self.base_state,
            "label": self.label,
            "style": self.style,
            "compact": self.compact,
            "git": self.git,
            "claims": self.claims,
            "follow_ups": self.follow_ups,
            "blockers": self.blockers,
            "closure": {"final": self.final},
            "action": {
                "disposition": self.action_disposition,
                "bucket": self.action_bucket,
            },
        }


def assemble_closure_descriptor(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    disposition: CleanupDisposition,
    *,
    held_claims: int,
    open_follow_ups: int,
    evidence_mode: str = "refreshed",
    evidence_complete: bool = True,
    turn_count: int = 0,
    repo_fetch_fresh: bool = False,
    now: str | None = None,
) -> ClosureDescriptor:
    """Assemble the canonical closure descriptor from already-computed facts.

    Pure -- no I/O; the caller supplies fresh (or cached) ``info``/
    ``disposition``/counts (including ``repo_fetch_fresh``, itself sourced
    from :func:`tracking.is_repo_fetch_fresh` -- this function does not read
    the ledger itself). Decomposes into the named :data:`FACT_NAMES`
    sub-states (worktree-finality-and-obligations Phase 9), each carrying its
    own ``confirmed`` freshness rather than one all-or-nothing flag:

    * ``checkpoint_activity``/``local_dirtiness`` are always locally computed,
      so always ``confirmed``.
    * ``upstream_containment`` is ``confirmed`` when THIS call's own evidence
      is fresh (``evidence_mode == "refreshed"`` and ``evidence_complete``)
      OR ``repo_fetch_fresh`` is true -- a fetch performed by any sibling
      worktree of the same repo (its own classify pass, `finalize`/
      `pr-merge`, or the resident status-monitor's periodic sweep) counts as
      current evidence for every worktree of that repo, without each needing
      its own fetch.
    * ``open_claims`` depends on evidence that can go stale (held claims,
      open follow-ups, a provider PR lookup); ``confirmed`` only when THIS
      call's own evidence is fresh -- the repo-scoped ledger covers git
      upstream refs specifically, not provider/claim state, so it does not
      extend to this fact.
    * ``pending_handoff`` is not yet wired (Phase 9 follow-up): always
      reports ``confirmed=False`` with a ``None`` value.

    ``FINAL`` requires ALL of: the ``upstream_containment`` AND ``open_claims``
    facts BOTH independently ``confirmed``, Git upstream-complete (git state
    ``completed`` or the disposition bucket is already ``clean``), zero held
    claims, zero open follow-ups, no other blocker, and the worktree is not
    live (``ACTIVE`` has display precedence -- see design.md). An unconfirmed
    ``upstream_containment`` or ``open_claims`` fact NEVER lets the descriptor
    report FINAL or a ``safe`` action, even if the underlying values would
    otherwise qualify -- destructive authorization always requires a fresh
    recomputation immediately before acting.

    ``rec.status == "finalizing"`` is surfaced as an explicit ``finalizing``
    blocker so a wedged record self-reports rather than rendering as
    blocked-for-no-visible-reason.

    **Not yet wired** (tracked in the effort, not silently omitted): the
    ``active-effort``, ``inbound-obligation``, ``unverified-squash``,
    ``live-session``, ``checkout-missing``, ``prune-review-required``,
    ``incomplete-evidence``, and ``unsupported-descriptor`` blocker codes: a
    caller can still append them itself (this function only owns what it can
    derive from `assess`/`cleanup_disposition`'s existing output), and a GONE
    worktree/managed-record-reap disposition isn't handled here (mirrors
    `cleanup_disposition` itself, which defers GONE to its caller).
    """
    S = git_ops.WorktreeState
    upstream_complete = (
        disposition.bucket == "clean" or info.state == S.COMPLETED
    )

    blockers: list[dict] = []
    if rec.status == "finalizing":
        blockers.append({"code": "finalizing", "count": 1})
    bucket_code = _BUCKET_TO_BLOCKER.get(disposition.bucket)
    if bucket_code:
        count = (
            held_claims if bucket_code == "held-claims"
            else open_follow_ups if bucket_code == "open-follow-ups"
            else 1
        )
        blockers.append({"code": bucket_code, "count": count})
    if held_claims and bucket_code != "held-claims":
        blockers.append({"code": "held-claims", "count": held_claims})
    if open_follow_ups and bucket_code != "open-follow-ups":
        blockers.append({"code": "open-follow-ups", "count": open_follow_ups})

    fresh_and_complete = evidence_mode == "refreshed" and evidence_complete
    upstream_confirmed = fresh_and_complete or repo_fetch_fresh

    facts = {
        "checkpoint_activity": {
            "confirmed": True,
            "turns_since_checkpoint": turn_count,
        },
        "upstream_containment": {
            "confirmed": upstream_confirmed,
            "complete": upstream_complete,
        },
        "local_dirtiness": {
            "confirmed": True,
            "dirty": info.dirty,
        },
        "open_claims": {
            "confirmed": fresh_and_complete,
            "held": held_claims,
            "open_follow_ups": open_follow_ups,
        },
        "pending_handoff": {
            "confirmed": False,
            "value": None,
        },
    }

    final = (
        facts["upstream_containment"]["confirmed"]
        and facts["open_claims"]["confirmed"]
        and upstream_complete
        and held_claims == 0
        and open_follow_ups == 0
        and not blockers
        and info.state != S.ACTIVE
    )

    base_label = _BASE_STATE_LABELS.get(info.state, "UNKNOWN")
    if info.state == S.COMPLETED:
        label = "FINAL" if final else "MERGED"
    else:
        label = base_label

    if final:
        style = "final"
    elif info.state == S.ACTIVE:
        style = "active"
    elif label == "MERGED":
        style = "merged-blocked"
    else:
        style = base_label.lower()

    compact_parts = [label]
    if held_claims:
        compact_parts.append(f"C{held_claims}")
    if open_follow_ups:
        compact_parts.append(f"F{open_follow_ups}")
    compact = " ".join(compact_parts)

    action_disposition = _BUCKET_TO_ACTION_DISPOSITION.get(
        disposition.bucket, "blocked")
    if action_disposition == "safe" and not (
        upstream_confirmed and facts["open_claims"]["confirmed"]
    ):
        # Cached/fetch-free evidence never authorizes a destructive action,
        # even when the underlying facts look clean -- unless BOTH facts
        # are independently confirmed (a repo-scoped ledger hit alone
        # confirms upstream_containment, not open_claims -- see FINAL's own
        # identical two-fact gate above).
        action_disposition = "blocked"

    return ClosureDescriptor(
        version=DESCRIPTOR_VERSION,
        computed_at=now or tracking._now_iso(),
        facts=facts,
        base_state=base_label,
        label=label,
        style=style,
        compact=compact,
        git={
            "upstream_complete": upstream_complete,
            "dirty": info.dirty,
            "ahead": info.ahead,
        },
        claims={"held": held_claims},
        follow_ups={"open": open_follow_ups},
        blockers=blockers,
        final=final,
        action_disposition=action_disposition,
        action_bucket=disposition.bucket,
    )


def interpret_descriptor_payload(payload: dict | None) -> dict:
    """Mixed-version fleet safety (Phase 5): interpret a raw closure-descriptor
    payload from a remote/cached source (e.g. a machine running an older or
    newer `agent-worktrees`) without trusting fields it may not understand.

    An absent (``None``/non-mapping), malformed, or version-mismatched
    payload is NEVER final or prune-safe -- it renders as an explicit
    ``unsupported-descriptor`` review state regardless of what the payload's
    own ``closure.final``/``action.disposition`` claim, so an out-of-version
    consumer degrades safely instead of guessing. Only an EXACT
    ``version == DESCRIPTOR_VERSION`` match is trusted (both an older and a
    newer version are rejected the same way -- neither side of a version
    skew can safely interpret the other's shape).

    Returns a normalized view:
    ``{"supported": bool, "final": bool, "label": str, "style": str,
    "compact": str, "held_claims": int, "open_follow_ups": int,
    "action_disposition": str, "reason": str | None}``.

    ``style``/``compact``/``held_claims``/``open_follow_ups`` are the fields a
    presentation surface (mux, Picker) needs to render the descriptor's exact
    marker counts and semantic color without redefining state -- an
    unsupported payload degrades them the same way as ``label``/``final``
    (a neutral ``"unknown"`` style, bare ``"UNKNOWN"`` compact text, zero
    counts), never fabricating a claim/follow-up count it can't verify.
    """
    if not isinstance(payload, dict):
        return _unsupported_descriptor("unsupported-descriptor")
    version = payload.get("version")
    if version != DESCRIPTOR_VERSION:
        return _unsupported_descriptor(f"unsupported-descriptor:version={version!r}")
    closure = payload.get("closure")
    action = payload.get("action")
    claims = payload.get("claims")
    follow_ups = payload.get("follow_ups")
    # A missing or wrong-shaped required nested field (e.g. no ``closure`` at
    # all, or ``closure: []``) is a malformed/truncated payload -- a genuine
    # ``assemble_closure_descriptor().to_dict()`` always emits all four
    # sections, so an absent one is never a legitimate "nothing to report"
    # case. Reject the whole payload as unsupported rather than silently
    # defaulting to ``{}`` and then trusting the top-level ``label``/``style``
    # as if the payload were sound.
    for field_name, field_value in (
        ("closure", closure), ("action", action),
        ("claims", claims), ("follow_ups", follow_ups),
    ):
        if not isinstance(field_value, dict):
            return _unsupported_descriptor(
                f"unsupported-descriptor:{field_name}-missing-or-not-a-mapping")
    # Required scalar fields must be the exact type a genuine descriptor
    # always emits -- a truthy-but-wrong-type value (e.g. ``closure.final:
    # "false"``, a non-empty string that ``bool()`` would treat as True) must
    # never slip through as if it were sound.
    label = payload.get("label")
    style = payload.get("style")
    final_value = closure.get("final")
    action_disposition = action.get("disposition")
    if (not isinstance(label, str) or not isinstance(style, str)
            or not isinstance(final_value, bool)
            or not isinstance(action_disposition, str)):
        return _unsupported_descriptor("unsupported-descriptor:scalar-field-type")
    compact = payload.get("compact", label)
    if not isinstance(compact, str):
        return _unsupported_descriptor("unsupported-descriptor:scalar-field-type")
    # ``label == "FINAL"`` and ``closure.final`` must agree -- an inconsistent
    # combination (e.g. an empty ``closure: {}`` alongside a top-level
    # ``label: "FINAL"``) is exactly the malformed shape the safety contract
    # exists to catch; never trust either half in isolation.
    if (label == "FINAL") != final_value:
        return _unsupported_descriptor("unsupported-descriptor:label-final-mismatch")
    held_claims = _non_negative_int(claims.get("held", 0))
    open_follow_ups = _non_negative_int(follow_ups.get("open", 0))
    # ``assemble_closure_descriptor`` only ever sets ``final: True`` alongside
    # zero held claims, zero open follow-ups, and a ``safe`` action -- a
    # payload claiming FINAL with any of those inconsistent (e.g. `final:
    # true` but `claims.held: 2`, or a non-`safe` action) is a contradictory,
    # malformed shape the safety contract exists to catch; never render a
    # green FINAL with markers still attached.
    if final_value and (
        held_claims != 0 or open_follow_ups != 0 or action_disposition != "safe"
    ):
        return _unsupported_descriptor(
            "unsupported-descriptor:final-with-blockers")
    return {
        "supported": True,
        "final": final_value,
        "label": label,
        "style": style,
        "compact": compact,
        "held_claims": held_claims,
        "open_follow_ups": open_follow_ups,
        "action_disposition": action_disposition,
        "reason": None,
    }


def _unsupported_descriptor(reason: str) -> dict:
    """The shared degrade-to-neutral result for any ``interpret_descriptor_
    payload`` rejection path -- every field renders as if there were no
    descriptor at all, never partially trusting a malformed payload."""
    return {
        "supported": False, "final": False, "label": "UNKNOWN",
        "style": "unknown", "compact": "UNKNOWN",
        "held_claims": 0, "open_follow_ups": 0,
        "action_disposition": "blocked", "reason": reason,
    }


def _non_negative_int(value) -> int:
    """Coerce a claim/follow-up count from an untrusted descriptor payload to
    a non-negative int, never raising on a malformed value (e.g. a string, a
    list, ``None``, or a negative number) -- degrades to ``0`` instead of
    crashing the caller (``interpret_descriptor_payload``'s mixed-version
    safety net must not itself be a crash surface)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def default_paired_sibling_final(
    rec: tracking.WorktreeRecord,
) -> bool | None:
    """Default :data:`PairedSiblingFinalProbe` for the paired-worktree gate.

    Loads the sibling of a paired -harness/-knowledge worktree from the local
    tracking dir and reports whether the BOTH-finalized gate is satisfied
    (citadel #957):

    * ``True``  -- no sibling to wait on: the record is unpaired, or paired at a
      knowledge **anchor** (a non-worktree-class repo has no sibling worktree),
      or the sibling worktree's record is ``finalized``.
    * ``False`` -- the sibling worktree exists but is not yet ``finalized``.
    * ``None``  -- the sibling has no local record (cross-machine / not yet
      carved) -- unknown; the caller spares the worktree (safe direction).

    Fail-safe: any error resolves to ``None`` (spare).
    """
    try:
        if not rec.is_paired:
            return True
        if rec.pair_kind == "anchor":
            return True
        sibling = tracking.find_paired_record(rec)
        if sibling is None:
            return None
        return sibling.status == "finalized"
    except Exception:
        return None


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


def reconcile_and_persist_best_effort(
    rec: tracking.WorktreeRecord,
    lookup: Callable[[str, int], "object | None"],
    *,
    rec_path: "object | None" = None,
) -> list[tuple[int, str, str]]:
    """Reconcile PR states from the provider, then persist without lost updates.

    Used by the status-render sweeps (``reap_one`` / ``cleanup``) whose ``rec``
    was loaded -- and threaded across git/network work -- long before this call.
    Persisting that stale-base snapshot directly would clobber any concurrent
    foreground update, even though the write itself is atomic. So this:

    1. runs :func:`reconcile_pr_states` on ``rec`` UNLOCKED (the provider lookup
       is the only I/O, and it must never happen under the lock), mutating
       ``rec`` in place so the caller's in-memory assessment sees the healed
       state; then
    2. re-applies just the reconciled ``(number -> new_state)`` deltas onto a
       FRESHLY reloaded snapshot **inside a best-effort** ``_RecordLock`` and
       saves that -- so a concurrent writer's other fields are preserved.

    Best-effort: on lock contention (a foreground verb holds it) or an OS write
    error the persist is skipped; the reconcile is idempotent and self-heals on
    the next sweep. Returns the change list (possibly empty).
    """
    changes = reconcile_pr_states(rec, lookup)
    if not changes:
        return changes
    path = rec_path if rec_path is not None else rec.yaml_path
    try:
        with tracking._RecordLock(path, blocking=False) as lk:
            if not lk.acquired:
                return changes  # contended -- skip; self-heals next sweep
            fresh = tracking.load_record(path)
            by_number = {p.number: p for p in fresh.prs if p.number is not None}
            for number, _old_state, new_state in changes:
                target = by_number.get(number)
                if target is not None:
                    target.state = new_state
            tracking.save_record(fresh, path)
    except OSError:
        pass
    return changes
