"""Declarative transition tables for the provider/PR-target state machine.

This module is the Phase 9 (``review-automation-reliability`` effort,
``efforts/active/review-automation-reliability/phase-9-state-machine-architecture.md``)
provider machine made real, following the same shape
:mod:`agent_dispatch.task_state_machine` used for the task machine: name
every legal transition once, as data, tagged with the recovery mode it is
classified under, so the shape is checkable independent of any adapter
that executes it.

Unlike the task machine, there is no pre-existing runtime enum to source
states from -- this module *is* the first declaration of the provider
machine's shape. Two consequences follow directly from that:

- The provider/PR-target's observable state is **two independent
  dimensions**, not one flat enum. Approval status and mergeability move on
  their own schedules (a PR can gain an approval while checks are still
  running, or fail checks after already being approved) -- flattening them
  into a single combined state would make the reachability check assert
  nothing but "the cross product was declared," not a real invariant.
  ``ApprovalStatus`` and ``Mergeability`` are therefore each their own
  small declared machine below, each independently reachable and exit-
  checked.
- **Hold is a set of flags, not a state.** Draft, WIP, and blocking-review-
  thread holds can co-occur and clear independently of both dimensions
  above (e.g. a PR can be simultaneously approved, clean, and still a
  draft). Modeling hold as a third state dimension would multiply the
  state space without adding a real transition to check, so it is declared
  as a ``HoldReason`` flag set plus a pure predicate
  (:func:`merge_blocked_by_hold`) instead.

Both sub-machines reuse the task machine's :class:`RecoveryMode` taxonomy
(Phase 9: self-recovering / safe-retry / self-repair) rather than
redeclaring it, so the taxonomy stays one vocabulary across all three
machines this phase defines.

This module also declares the **per-provider capability table**: who may
approve/merge, what notification fidelity a provider offers per event
type, and which conflict-handling policy applies. The default conflict
policy is **hand-back** (Phase 9's resolution of Phase 8's open conflict-
handling question) -- a repository must explicitly opt into the
branch-mutating (rebase + force-push) policy via
:data:`REPOSITORY_OVERRIDES`.

This module declares transitions and capabilities; it does not implement a
live adapter for any provider. Its purpose is to make the machine's shape
and the capability table checkable, and to give the Phase 9 simulation
track a declared table to drive against once it is built.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .task_state_machine import RecoveryMode


class ApprovalStatus(Enum):
    """The PR-target's review/approval dimension."""

    NONE = "none"
    PENDING = "pending"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    #: An approval exists but a newer revision has since landed -- the
    #: approval no longer covers the current head and must not be treated
    #: as covering it until re-evaluated.
    STALE = "stale"


class Mergeability(Enum):
    """The PR-target's mergeability dimension, as observed from the provider."""

    UNKNOWN = "unknown"
    CHECKS_PENDING = "checks_pending"
    CLEAN = "clean"
    CHECKS_FAILED = "checks_failed"
    CONFLICTED = "conflicted"


class HoldReason(Enum):
    """Independent hold flags. Any non-empty set blocks merge regardless of
    approval/mergeability -- see :func:`merge_blocked_by_hold`."""

    DRAFT = "draft"
    WIP = "wip"
    BLOCKING_THREADS = "blocking_threads"


@dataclass(frozen=True)
class DimensionTransition:
    """One legal transition within a single state dimension."""

    name: str
    from_states: frozenset[Enum]
    to_state: Enum
    recovery_mode: RecoveryMode


ALL_APPROVAL_STATES: frozenset[ApprovalStatus] = frozenset(ApprovalStatus)
INITIAL_APPROVAL_STATE = ApprovalStatus.NONE

APPROVAL_TRANSITIONS: tuple[DimensionTransition, ...] = (
    DimensionTransition(
        name="request_review",
        from_states=frozenset({ApprovalStatus.NONE}),
        to_state=ApprovalStatus.PENDING,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
    DimensionTransition(
        name="approve",
        from_states=frozenset({ApprovalStatus.PENDING}),
        to_state=ApprovalStatus.APPROVED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
    DimensionTransition(
        name="request_changes",
        from_states=frozenset({ApprovalStatus.PENDING}),
        to_state=ApprovalStatus.CHANGES_REQUESTED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
    DimensionTransition(
        name="resubmit_after_changes",
        from_states=frozenset({ApprovalStatus.CHANGES_REQUESTED}),
        to_state=ApprovalStatus.PENDING,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
    DimensionTransition(
        name="revision_invalidates_approval",
        from_states=frozenset({ApprovalStatus.APPROVED}),
        to_state=ApprovalStatus.STALE,
        recovery_mode=RecoveryMode.SELF_REPAIR,
    ),
    DimensionTransition(
        name="revalidate_stale",
        from_states=frozenset({ApprovalStatus.STALE}),
        to_state=ApprovalStatus.PENDING,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
)

ALL_MERGEABILITY_STATES: frozenset[Mergeability] = frozenset(Mergeability)
INITIAL_MERGEABILITY_STATE = Mergeability.UNKNOWN

MERGEABILITY_TRANSITIONS: tuple[DimensionTransition, ...] = (
    DimensionTransition(
        name="checks_started",
        from_states=frozenset(
            {
                Mergeability.UNKNOWN,
                Mergeability.CLEAN,
                Mergeability.CHECKS_FAILED,
            }
        ),
        to_state=Mergeability.CHECKS_PENDING,
        recovery_mode=RecoveryMode.SELF_RECOVERING,
    ),
    DimensionTransition(
        name="checks_passed",
        from_states=frozenset({Mergeability.CHECKS_PENDING}),
        to_state=Mergeability.CLEAN,
        recovery_mode=RecoveryMode.SELF_RECOVERING,
    ),
    DimensionTransition(
        name="checks_failed",
        from_states=frozenset({Mergeability.CHECKS_PENDING}),
        to_state=Mergeability.CHECKS_FAILED,
        recovery_mode=RecoveryMode.SELF_RECOVERING,
    ),
    DimensionTransition(
        name="conflict_detected",
        from_states=frozenset(
            {
                Mergeability.UNKNOWN,
                Mergeability.CLEAN,
                Mergeability.CHECKS_PENDING,
                Mergeability.CHECKS_FAILED,
            }
        ),
        to_state=Mergeability.CONFLICTED,
        recovery_mode=RecoveryMode.SELF_REPAIR,
    ),
    DimensionTransition(
        name="conflict_resolved",
        from_states=frozenset({Mergeability.CONFLICTED}),
        to_state=Mergeability.CHECKS_PENDING,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
)


def reachable_states(transitions: tuple[DimensionTransition, ...], start: Enum) -> frozenset[Enum]:
    """Every state reachable from ``start`` by ``transitions``."""
    seen = {start}
    frontier = {start}
    while frontier:
        nxt: set[Enum] = set()
        for transition in transitions:
            if transition.from_states & frontier:
                nxt.add(transition.to_state)
        nxt -= seen
        seen |= nxt
        frontier = nxt
    return frozenset(seen)


def states_without_exit(
    all_states: frozenset[Enum], transitions: tuple[DimensionTransition, ...]
) -> frozenset[Enum]:
    """States with zero outgoing declared transition (neither dimension has a
    declared terminal state, so any such state is a dead end)."""
    states_with_exit = {s for t in transitions for s in t.from_states}
    return frozenset(all_states - states_with_exit)


def merge_blocked_by_hold(holds: frozenset[HoldReason]) -> bool:
    """Any declared hold reason blocks merge, independent of approval status
    or mergeability. An empty set never blocks."""
    return bool(holds)


class NotificationFidelity(Enum):
    """How reliably a provider signals a given event type."""

    PUSH = "push"
    POLL = "poll"
    NONE = "none"


class ProviderEventType(Enum):
    """Event types whose notification fidelity varies by provider."""

    NEW_COMMIT = "new_commit"
    REVIEW_SUBMITTED = "review_submitted"
    THREAD_RESOLVED = "thread_resolved"
    CHECK_STATUS = "check_status"


class ConflictPolicy(Enum):
    """Phase 9's resolved conflict-handling modes. ``HAND_BACK`` is the
    default for every provider/repository unless explicitly overridden."""

    HAND_BACK = "hand_back"
    BRANCH_MUTATING = "branch_mutating"


@dataclass(frozen=True)
class ProviderCapability:
    """A provider's declared capability, overridable per repository via
    :data:`REPOSITORY_OVERRIDES`."""

    provider: str
    #: Whether this automated identity is itself an eligible approver under
    #: the provider's default policy (branch-protection/CODEOWNERS-style
    #: gating may still override this per repository -- this is the
    #: provider-level default, not a guarantee for every repo).
    automated_identity_eligible_approver: bool
    notification_fidelity: dict[ProviderEventType, NotificationFidelity]
    conflict_policy: ConflictPolicy = ConflictPolicy.HAND_BACK


#: Declared per-provider defaults. Adding a new provider means adding an
#: entry here, not a new branch in adapter code.
PROVIDER_CAPABILITIES: dict[str, ProviderCapability] = {
    "github": ProviderCapability(
        provider="github",
        automated_identity_eligible_approver=True,
        notification_fidelity={
            ProviderEventType.NEW_COMMIT: NotificationFidelity.PUSH,
            ProviderEventType.REVIEW_SUBMITTED: NotificationFidelity.PUSH,
            ProviderEventType.THREAD_RESOLVED: NotificationFidelity.PUSH,
            ProviderEventType.CHECK_STATUS: NotificationFidelity.PUSH,
        },
    ),
    "azure_devops": ProviderCapability(
        provider="azure_devops",
        automated_identity_eligible_approver=True,
        notification_fidelity={
            ProviderEventType.NEW_COMMIT: NotificationFidelity.PUSH,
            ProviderEventType.REVIEW_SUBMITTED: NotificationFidelity.PUSH,
            #: A downstream deployment's operator design conversation found
            #: this provider does not signal thread resolution as a push
            #: event -- a driver that assumes push fidelity here silently
            #: misses the transition it was waiting for.
            ProviderEventType.THREAD_RESOLVED: NotificationFidelity.POLL,
            ProviderEventType.CHECK_STATUS: NotificationFidelity.PUSH,
        },
    ),
    "gitea": ProviderCapability(
        provider="gitea",
        automated_identity_eligible_approver=True,
        notification_fidelity={
            ProviderEventType.NEW_COMMIT: NotificationFidelity.POLL,
            ProviderEventType.REVIEW_SUBMITTED: NotificationFidelity.POLL,
            ProviderEventType.THREAD_RESOLVED: NotificationFidelity.POLL,
            ProviderEventType.CHECK_STATUS: NotificationFidelity.POLL,
        },
    ),
}

#: Per-(provider, repository) overrides. Only fields present here are
#: overridden; everything else falls back to the provider default. Empty
#: until a repository explicitly opts into a non-default policy (e.g.
#: branch-mutating conflict resolution).
REPOSITORY_OVERRIDES: dict[tuple[str, str], dict[str, object]] = {}


def capability_for(provider: str, repo: str | None = None) -> ProviderCapability:
    """Resolve the effective capability for ``provider``, applying any
    ``(provider, repo)`` override. Raises ``KeyError`` for an undeclared
    provider rather than silently defaulting -- an unknown provider must be
    declared here before anything consults its capability."""
    if provider not in PROVIDER_CAPABILITIES:
        raise KeyError(f"no declared capability for provider {provider!r}")
    base = PROVIDER_CAPABILITIES[provider]
    override = REPOSITORY_OVERRIDES.get((provider, repo)) if repo else None
    if not override:
        return base
    return replace(base, **override)
