"""Canonical closure/display descriptor for a worktree's prune/finalize state
(worktree-finality-and-obligations effort, Phase 4/9 -- split out of
``prune.py`` to stay under the module-size cap, worktree-claims-transitive-
finalization effort Phase 4).

``prune.py`` re-exports everything here (``DESCRIPTOR_VERSION``,
``FACT_NAMES``, ``BLOCKER_CODES``, ``ClosureDescriptor``,
``assemble_closure_descriptor``, ``interpret_descriptor_payload``), so every
existing ``prune.X`` call site keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import git_ops, tracking

if TYPE_CHECKING:
    from .prune import CleanupDisposition


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
#: * ``pending_handoff`` -- opened-but-unlinked session handoffs on this
#:   worktree (``rec.pending_handoffs``: a predecessor recorded a token, no
#:   successor session linked yet). Read-only, always confirmed (a local
#:   tracking-record read, no fetch/staleness concept); purely informational
#:   -- does not gate FINAL.
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
#: ``held-claims-cross-machine`` maps to the SAME ``held-claims`` wire code --
#: display-only nuance for ``cleanup_bucket``/the Picker chip tables, not a
#: new entry in the version-gated :data:`BLOCKER_CODES` closed set.
_BUCKET_TO_BLOCKER: dict[str, str] = {
    "follow-up": "open-follow-ups",
    "held-claims": "held-claims",
    "held-claims-cross-machine": "held-claims",
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
    "held-claims-cross-machine": "blocked",
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
    cross_machine_claims: int = 0,
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
    * ``pending_handoff`` reads ``rec.pending_handoffs`` (agent-worktrees'
      own cooperative, already-tracked record of opened-but-not-yet-linked
      session handoffs -- a predecessor recorded a token with no successor
      session linked yet) -- read-only here, never composed or consumed by
      this function. Always ``confirmed`` (a local tracking-record read, no
      fetch/staleness concept), and purely informational: it does NOT gate
      ``FINAL`` -- a pending handoff signals someone intends to resume this
      worktree, but that is a distinct concern from whether its CONTENT is
      safely landed.

    ``cross_machine_claims`` is a sub-count of ``held_claims``: how many are
    ``worktree``-kind claims naming a different machine -- not a LOCAL
    blocker, but **not known to self-resolve** either: ``sweep.py``'s
    ``worktree``-kind ``gone_of``/``safe_of`` both spare (return ``None``
    for) an unjudgeable cross-machine ref, and ``worktree`` isn't in
    ``sweep._LEASEABLE_KINDS`` (the lease-mirror-settled path), so nothing
    today actually sweeps one of these. Purely informational -- rides in
    the ``open_claims`` fact as ``cross_machine_held`` and renders as an
    ``XM<n>`` ``compact`` marker; never changes ``final``/
    ``action_disposition`` (still blocks like any held claim).

    ``FINAL`` requires ALL of: the ``upstream_containment`` AND ``open_claims``
    facts BOTH independently ``confirmed``, Git upstream-complete (git state
    ``completed`` or the disposition bucket is already ``clean``), zero held
    claims, zero open follow-ups, no other blocker, and the worktree is not
    live (``ACTIVE`` has display precedence -- see design.md). An unconfirmed
    ``upstream_containment`` or ``open_claims`` fact NEVER lets the descriptor
    report FINAL or a ``safe`` action, even if the underlying values would
    otherwise qualify -- destructive authorization always requires a fresh
    recomputation immediately before acting.

    ``compact`` renders an ``U*``/``OC*`` marker for an unconfirmed
    ``upstream_containment``/``open_claims`` fact respectively (independent
    of each other and of the base label) -- the general per-fact marker
    convention that replaces the old implicit "COMPLETED reads MERGED unless
    freshly fetched" special case. Neither marker ever appears when the
    corresponding fact is confirmed, and ``FINAL`` never carries either
    (both facts are confirmed by construction whenever ``final`` is True).

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
            "cross_machine_held": cross_machine_claims,
        },
        "pending_handoff": {
            "confirmed": True,
            "count": len(rec.pending_handoffs),
            "tokens": [h.token for h in rec.pending_handoffs],
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
    # A purely cross-machine held claim isn't a local blocker (always a
    # subset of held_claims) -- its own marker alongside "C<n>".
    if cross_machine_claims:
        compact_parts.append(f"XM{cross_machine_claims}")
    # worktree-finality-and-obligations Phase 9: render the marker on the
    # SPECIFIC unconfirmed fact, not a whole separate state -- replaces the
    # old implicit "COMPLETED reads MERGED unless freshly fetched" special
    # case with a general per-fact marker that applies regardless of base
    # state. `checkpoint_activity`/`local_dirtiness` are always confirmed
    # (see `facts` above), so never marked; `pending_handoff` is
    # deliberately excluded here too -- it always reports `confirmed=False`
    # until wired (a later Phase 9 slice), so marking it now would put a
    # meaningless asterisk on every single row.
    if not facts["upstream_containment"]["confirmed"]:
        compact_parts.append("U*")
    if not facts["open_claims"]["confirmed"]:
        compact_parts.append("OC*")
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
    payload from a remote/cached source without trusting fields it may not
    understand. An absent/malformed/version-mismatched payload is NEVER
    final or prune-safe, regardless of what its own fields claim. Only an
    EXACT ``version == DESCRIPTOR_VERSION`` match is trusted.

    A matched payload is further validated before being trusted:
    ``closure``/``action``/``claims``/``follow_ups`` must each be present
    mappings; ``label``/``style``/``action.disposition``/``compact`` must be
    ``str`` and ``closure.final`` a ``bool`` (never truthiness-coerced);
    ``label == "FINAL"`` must agree with ``closure.final``; a ``final: True``
    payload must carry zero held claims, zero open follow-ups, and a
    ``safe`` action (the only combination :func:`assemble_closure_descriptor`
    ever produces for FINAL); and the claim/follow-up counts must be genuine
    non-negative ints (never laundered from a malformed value into a ``0``
    that would look like verified evidence of no blockers). Any violation
    degrades the whole payload to unsupported.

    Returns ``{"supported": bool, "final": bool, "label": str, "style": str,
    "compact": str, "held_claims": int, "open_follow_ups": int,
    "action_disposition": str, "reason": str | None}`` -- an unsupported
    payload degrades every field to a neutral/zero value.
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
    # A missing/wrong-shaped required nested field is a malformed/truncated
    # payload -- a genuine descriptor always emits all four sections.
    for field_name, field_value in (
        ("closure", closure), ("action", action),
        ("claims", claims), ("follow_ups", follow_ups),
    ):
        if not isinstance(field_value, dict):
            return _unsupported_descriptor(
                f"unsupported-descriptor:{field_name}-missing-or-not-a-mapping")
    label = payload.get("label")
    style = payload.get("style")
    final_value = closure.get("final")
    action_disposition = action.get("disposition")
    if (not isinstance(label, str) or not isinstance(style, str)
            or not isinstance(final_value, bool)
            or not isinstance(action_disposition, str)):
        return _unsupported_descriptor("unsupported-descriptor:scalar-field-type")
    compact = payload.get("compact")
    if not isinstance(compact, str):
        return _unsupported_descriptor("unsupported-descriptor:scalar-field-type")
    # label/final consistency: never trust either half in isolation.
    if (label == "FINAL") != final_value:
        return _unsupported_descriptor("unsupported-descriptor:label-final-mismatch")
    held_claims = _non_negative_int(claims.get("held"))
    open_follow_ups = _non_negative_int(follow_ups.get("open"))
    if held_claims is None or open_follow_ups is None:
        return _unsupported_descriptor("unsupported-descriptor:invalid-count")
    # FINAL only ever combines with zero blockers + a safe action.
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


def _non_negative_int(value) -> int | None:
    """Validate a claim/follow-up count from an untrusted descriptor payload
    as a non-negative int, returning ``None`` for anything else (a string, a
    list, ``None``, a bool, or a negative number) rather than silently
    coercing it to ``0`` -- a coerced ``0`` would be indistinguishable from a
    verified zero count and could launder a malformed payload into apparent
    evidence that no blockers exist. The caller rejects the whole payload as
    unsupported when this returns ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None

