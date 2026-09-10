"""Declarative transition table for the bridge/session state machine.

This module is the Phase 9 (``review-automation-reliability`` effort,
``efforts/active/review-automation-reliability/phase-9-state-machine-architecture.md``)
bridge/session machine made real, following the same declared-table shape
:mod:`agent_dispatch.task_state_machine` and
:mod:`agent_dispatch.provider_state_machine` already used: name every legal
transition once, as data, tagged with a recovery mode from the shared
:class:`agent_dispatch.task_state_machine.RecoveryMode` taxonomy.

This module owns the **corrected liveness model** the phase-9 sub-doc
folded in from a downstream deployment's operator design conversation:
liveness is a live, three-tier read (hot/warm/cold), never gated by a
cache. A liveness cache (a discovery
index, a session-record table) is a performance shortcut only -- it must
never be treated as authoritative. :func:`resolve_liveness` makes that
literal: it always calls the live probe and ignores any cache hint's
*value*, using the hint only as an optional hint to callers, never as a
substitute for the live check.

The companion agent-bridge vision
(``visions/plugins/agent-bridge/README.md``) is the intended long-term
source of truth for the bridge's own verb vocabulary. As of this slice it
declares a task-shaped verb set (create/identify/read/steer/wait/
interrupt/stop/resume/end) and a "takeover" pattern, but does not yet
declare the hot/warm/cold liveness tiers or the cache-is-never-authority
rule -- those live only in this effort's Phase 9 design today. This module
does not duplicate or compete with the vision's verb set; it declares the
lifecycle states and the liveness-to-transition coupling this effort
needs, and should be reconciled with the vision's verbs once it declares
its own liveness model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .task_state_machine import RecoveryMode


class BridgeState(Enum):
    """The bridge/session machine's lifecycle states."""

    ABSENT = "absent"
    HYDRATING = "hydrating"
    RUNNING = "running"
    #: Worktree/session held cold; no active lease or embodiment.
    SUSPENDED = "suspended"
    ENDED = "ended"


ALL_BRIDGE_STATES: frozenset[BridgeState] = frozenset(BridgeState)
#: Terminal: no transition below ever names this as a source.
TERMINAL_BRIDGE_STATES: frozenset[BridgeState] = frozenset({BridgeState.ENDED})
INITIAL_BRIDGE_STATE = BridgeState.ABSENT


@dataclass(frozen=True)
class BridgeTransition:
    """One legal lifecycle move, as declared data."""

    name: str
    from_states: frozenset[BridgeState]
    to_state: BridgeState
    recovery_mode: RecoveryMode


BRIDGE_TRANSITIONS: tuple[BridgeTransition, ...] = (
    BridgeTransition(
        name="spawn",
        from_states=frozenset({BridgeState.ABSENT}),
        to_state=BridgeState.HYDRATING,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
    BridgeTransition(
        name="hydration_failed",
        from_states=frozenset({BridgeState.HYDRATING}),
        #: A failed spawn reverts to ABSENT rather than dead-ending in
        #: HYDRATING -- the next resume attempt re-observes a clean slate
        #: and retries the spawn, per this taxonomy's SELF_REPAIR mode.
        to_state=BridgeState.ABSENT,
        recovery_mode=RecoveryMode.SELF_REPAIR,
    ),
    BridgeTransition(
        name="ready",
        from_states=frozenset({BridgeState.HYDRATING}),
        to_state=BridgeState.RUNNING,
        recovery_mode=RecoveryMode.SELF_RECOVERING,
    ),
    BridgeTransition(
        name="suspend",
        from_states=frozenset({BridgeState.RUNNING}),
        to_state=BridgeState.SUSPENDED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
    BridgeTransition(
        name="resume",
        from_states=frozenset({BridgeState.SUSPENDED}),
        to_state=BridgeState.RUNNING,
        recovery_mode=RecoveryMode.SELF_REPAIR,
    ),
    BridgeTransition(
        name="spawn_fresh_bound",
        #: The "cold" resume outcome: no live process at all, so resume
        #: spawns a fresh session bound to the *existing* checkout/target
        #: rather than creating a new one. Modeled as its own transition
        #: (distinct from "spawn") because it is reached only via a resume
        #: request against an ABSENT target, never a first-time creation.
        from_states=frozenset({BridgeState.ABSENT}),
        to_state=BridgeState.HYDRATING,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
    BridgeTransition(
        name="end",
        from_states=frozenset({BridgeState.RUNNING}),
        to_state=BridgeState.ENDED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
    BridgeTransition(
        name="end_suspended",
        from_states=frozenset({BridgeState.SUSPENDED}),
        to_state=BridgeState.ENDED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
    ),
)


def reachable_states(start: BridgeState = INITIAL_BRIDGE_STATE) -> frozenset[BridgeState]:
    """Every state reachable from ``start`` by declared transitions."""
    seen = {start}
    frontier = {start}
    while frontier:
        nxt: set[BridgeState] = set()
        for transition in BRIDGE_TRANSITIONS:
            if transition.from_states & frontier:
                nxt.add(transition.to_state)
        nxt -= seen
        seen |= nxt
        frontier = nxt
    return frozenset(seen)


def states_without_exit() -> frozenset[BridgeState]:
    """Non-terminal states with zero outgoing declared transition."""
    states_with_exit = {s for t in BRIDGE_TRANSITIONS for s in t.from_states}
    return frozenset(ALL_BRIDGE_STATES - TERMINAL_BRIDGE_STATES - states_with_exit)


def terminal_states_with_exit() -> frozenset[BridgeState]:
    """Terminal states that (incorrectly) still have a declared exit."""
    states_with_exit = {s for t in BRIDGE_TRANSITIONS for s in t.from_states}
    return frozenset(TERMINAL_BRIDGE_STATES & states_with_exit)


class Liveness(Enum):
    """The three-tier, always-live observation. Never derived from a cache."""

    #: A live interactive controller is actually attached right now.
    HOT = "hot"
    #: No live interactive controller, but the backing process is alive.
    WARM = "warm"
    #: Neither of the above.
    COLD = "cold"


def resolve_liveness(cache_hint: Liveness | None, live_probe) -> Liveness:
    """Resolve the target's liveness.

    ``cache_hint`` is accepted only so a caller may use it as a
    *performance* signal elsewhere (e.g. to prioritize which targets to
    probe first) -- it is never consulted for the result. ``live_probe`` is
    **always** called, regardless of whether ``cache_hint`` is present,
    stale, or ``None``: this is the literal fix for the load-bearing bug
    class Phase 9 identified, where a cache miss or stale entry was
    (incorrectly) treated as "target absent" instead of falling through to
    a live check.
    """
    return live_probe()


class ResumeAction(Enum):
    """The observed outcome of a resume request, keyed by resolved liveness."""

    #: Hot without an explicit force-takeover: refuse. Spawning a second
    #: controller on an already-hot target is exactly the bug this refusal
    #: prevents.
    REFUSE = "refuse"
    #: Hot with an explicit force-takeover. This is a **composite** action,
    #: not an atomic lifecycle transition: stop the existing headed
    #: session, then perform the ordinary resume-from-suspended transition
    #: below. It has no direct entry in ``BRIDGE_TRANSITIONS``.
    FORCE_TAKEOVER = "force_takeover"
    #: Warm: reattach. Maps to the declared ``resume`` transition.
    REATTACH = "reattach"
    #: Cold: spawn a fresh session bound to the existing checkout/target.
    #: Maps to the declared ``spawn_fresh_bound`` transition.
    SPAWN_FRESH_BOUND = "spawn_fresh_bound"


#: Every non-refusing, non-composite action must name a transition that
#: actually exists in ``BRIDGE_TRANSITIONS`` -- this is what keeps the
#: liveness-outcome table and the lifecycle table coupled instead of being
#: two tables that happen to share a file.
RESUME_ACTION_TRANSITIONS: dict[ResumeAction, str] = {
    ResumeAction.REATTACH: "resume",
    ResumeAction.SPAWN_FRESH_BOUND: "spawn_fresh_bound",
}


def resolve_resume(liveness: Liveness, *, force_takeover: bool = False) -> ResumeAction:
    """The observed resume outcome for a given liveness, per Phase 9's
    "one universal resume" model: the caller never guesses hot/warm/cold in
    advance, it requests resume and is told which outcome actually applied.
    """
    if liveness is Liveness.HOT:
        return ResumeAction.FORCE_TAKEOVER if force_takeover else ResumeAction.REFUSE
    if liveness is Liveness.WARM:
        return ResumeAction.REATTACH
    return ResumeAction.SPAWN_FRESH_BOUND


class RegistryClass(Enum):
    """Whether a target's own registry class permits a second, additional
    checkout ("create fresh") alongside its existing one."""

    #: Exactly one head possible -- "create fresh" is a declared error;
    #: "resume" (whichever outcome it resolves to) is the only correct verb.
    SINGLE_HEAD = "single_head"
    #: A genuinely new, additional checkout is possible.
    MULTI_HEAD = "multi_head"


def create_fresh_allowed(registry_class: RegistryClass) -> bool:
    """Whether a distinct "create fresh" gesture is legal for this target's
    registry class, as opposed to resume being the only correct verb."""
    return registry_class is RegistryClass.MULTI_HEAD
