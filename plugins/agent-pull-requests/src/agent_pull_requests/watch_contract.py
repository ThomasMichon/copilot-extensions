"""Pure PR-transition logic shared by every watch subscriber.

Deliberately narrower than ``agent_worktrees.pr_contract``: this module owns
the generic "did anything observable change" question a long-poll waiter
needs (terminal state, review verdict, mergeability, CI rollup) -- not the
richer worktree-bound merge-readiness/consent/flow-classification logic that
stays with ``agent-worktrees``' own ``pr-*`` family. A future dedup pass may
extract a shared core into a lib both plugins depend on; until then this is a
small, independently testable copy of just the diff semantics.

``PRSnapshot`` is the provider-neutral point-in-time view; ``Baseline`` is the
last-observed reference a subscriber diffs new snapshots against;
``compute_transitions`` returns the (possibly empty) set of named transitions
that fired between ``baseline`` and ``snap``, restricted to the subscriber's
own ``until`` set.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field

#: The complete transition vocabulary this module can fire. A subscriber's
#: ``until`` is a non-empty subset of these names.
MERGED = "merged"
CLOSED = "closed"
REVIEW_CHANGED = "review_changed"
MERGEABLE_CHANGED = "mergeable_changed"
CHECKS_CHANGED = "checks_changed"

RICH_TRANSITIONS = (
    "head_changed", "votes_changed", "approvals_changed", "threads_changed",
    "policies_changed", "gates_changed", "ready_for_human",
)
ALL_TRANSITIONS = (MERGED, CLOSED, REVIEW_CHANGED, MERGEABLE_CHANGED, CHECKS_CHANGED, *RICH_TRANSITIONS)

#: The sensible default for a caller that didn't specify ``--until``: only
#: the two terminal states. A caller that wants early wake on review/CI
#: movement opts in explicitly.
DEFAULT_UNTIL = (MERGED, CLOSED)


def _fingerprints(snap: PRSnapshot) -> dict[str, str]:
    observation = snap.observation
    if observation is None:
        return {}
    result = dict(observation["fingerprints"])
    result["readiness"] = "ready" if observation.get("ready_for_human") is True else "not_ready"
    metadata = {
        name: observation.get(name)
        for name in (
            "complete", "errors", "guidance", "source_push_at", "iteration",
            "ready_for_human", "all_required_satisfied", "agent_actionable",
            "pending", "human_required", "unknown",
        )
    }
    metadata["gates"] = result.get("gates")
    result["gates"] = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    return result


@dataclass(frozen=True)
class PRSnapshot:
    """A point-in-time, provider-neutral view of one pull request."""

    pr_state: str = "open"  # "open" | "closed"
    merged: bool = False
    head_sha: str = ""
    mergeable: str = ""  # provider's raw mergeable enum, "" = unknown
    review_decision: str = ""  # "", "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"
    checks_state: str = ""  # "", "success", "failure", "pending"
    updated_at: str = ""
    provider: str = ""
    observation: dict | None = None
    native_snapshot: dict | None = None

    @property
    def closed_unmerged(self) -> bool:
        return self.pr_state == "closed" and not self.merged


@dataclass(frozen=True)
class Baseline:
    """The last-observed reference values a subscriber diffs against.

    ``None`` baseline fields mean "not yet observed" -- a later concrete
    value is adopted silently (no transition fires) the first time it's
    seen, matching ``agent_worktrees.pr_contract.Baseline``'s own
    lazily-complete-unknown-fields behavior for a provider that computes
    some fields asynchronously.
    """

    merged: bool = False
    closed: bool = False
    review_decision: str | None = None
    mergeable: str | None = None
    checks_state: str | None = None
    head_sha: str | None = None
    fingerprints: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_snapshot(snap: PRSnapshot) -> Baseline:
        return Baseline(
            merged=snap.merged,
            closed=snap.pr_state == "closed",
            review_decision=snap.review_decision or None,
            mergeable=snap.mergeable or None,
            checks_state=snap.checks_state or None,
            head_sha=snap.head_sha or None,
            fingerprints=_fingerprints(snap),
        )


def compute_transitions(
    baseline: Baseline, snap: PRSnapshot, until: tuple[str, ...]
) -> tuple[str, ...]:
    """Return the subset of ``until`` that fired between ``baseline`` and ``snap``.

    A lazily-unknown baseline field (``None``) never fires on its first
    concrete observation -- only a *later* change from that adopted value
    fires. Callers that want the adopted baseline for the next round should
    call :func:`advance_baseline` separately; this function is a pure read.
    """
    fired: list[str] = []
    if (
        "head_changed" in until and baseline.head_sha is not None
        and snap.head_sha and baseline.head_sha != snap.head_sha
    ):
        fired.append("head_changed")
    fingerprints = _fingerprints(snap)
    if (
        REVIEW_CHANGED in until
        and any(
            name in baseline.fingerprints and name in fingerprints
            and baseline.fingerprints[name] != fingerprints[name]
            for name in ("votes", "approvals")
        )
    ):
        fired.append(REVIEW_CHANGED)
    if (
        "ready_for_human" in until
        and baseline.fingerprints.get("readiness") == "not_ready"
        and fingerprints.get("readiness") == "ready"
    ):
        fired.append("ready_for_human")
    for name in ("votes", "approvals", "discussions", "policies", "gates"):
        transition = "threads_changed" if name == "discussions" else f"{name}_changed"
        if (
            transition in until and name in baseline.fingerprints
            and name in fingerprints and baseline.fingerprints[name] != fingerprints[name]
        ):
            fired.append(transition)
    if MERGED in until and snap.merged and not baseline.merged:
        fired.append(MERGED)
    if CLOSED in until and snap.closed_unmerged and not baseline.closed:
        fired.append(CLOSED)
    if (
        REVIEW_CHANGED in until
        and baseline.review_decision is not None
        and snap.review_decision
        and snap.review_decision != baseline.review_decision
    ):
        if REVIEW_CHANGED not in fired:
            fired.append(REVIEW_CHANGED)
    if (
        MERGEABLE_CHANGED in until
        and baseline.mergeable is not None
        and snap.mergeable
        and snap.mergeable != baseline.mergeable
    ):
        fired.append(MERGEABLE_CHANGED)
    if (
        CHECKS_CHANGED in until
        and baseline.checks_state is not None
        and snap.checks_state
        and snap.checks_state != baseline.checks_state
    ):
        fired.append(CHECKS_CHANGED)
    return tuple(fired)


def advance_baseline(baseline: Baseline, snap: PRSnapshot) -> Baseline:
    """Adopt any newly-concrete (previously ``None``) field from ``snap``.

    Never regresses an already-concrete field except through an explicit
    transition elsewhere -- this only fills in gaps, so a poll loop calls it
    every cycle regardless of whether a transition fired.
    """
    return Baseline(
        merged=baseline.merged or snap.merged,
        closed=baseline.closed or snap.closed_unmerged,
        review_decision=baseline.review_decision or (snap.review_decision or None),
        mergeable=baseline.mergeable or (snap.mergeable or None),
        checks_state=baseline.checks_state or (snap.checks_state or None),
        head_sha=baseline.head_sha or (snap.head_sha or None),
        fingerprints={**_fingerprints(snap), **baseline.fingerprints},
    )


__all__ = [
    "ALL_TRANSITIONS",
    "CHECKS_CHANGED",
    "CLOSED",
    "DEFAULT_UNTIL",
    "MERGEABLE_CHANGED",
    "MERGED",
    "REVIEW_CHANGED",
    "Baseline",
    "PRSnapshot",
    "advance_baseline",
    "compute_transitions",
]
