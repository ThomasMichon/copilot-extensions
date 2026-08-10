"""Resource-obligation vocabulary + helpers (resource-obligation-settlement Ph1).

A worktree answers for every resource it allocated before it may finalize
(agent-fabric ``resource-accountability``). The unit of that accountability is a
**disposition** carried by each outbound resource -- both on the local claim
ledger (``tracking.ResourceClaim``) and, for a leaseable resource, on its
cross-machine lease record (as a ``disposition`` key in the lease's diagnostic
``context``, which the store already round-trips).

The three-value model separates the **resource** from the **claim**:

* ``active``   -- the resource still carries live/unsettled work. **Blocks finalize.**
* ``at-rest``  -- the *work* is safe (merged / off-box / itself finalized); the
  resource may persist. **Does not block finalize.** A claim can be released while
  its resource is at-rest without the resource being destroyed.
* ``released`` -- the *claim* is torn down (its lease tombstoned).

``at-rest`` is a property of the **resource**; ``released`` is a property of the
**claim**. They decouple: a CodeSpace can go ``at-rest`` (work safe) and have its
claim ``released`` (freeing it for the next borrower) without being deleted.

This module is intentionally dependency-free (pure vocabulary + predicates) so
both ``tracking`` and the lease layer can import it without a cycle.
"""

from __future__ import annotations

from typing import Literal

#: The disposition of an outbound resource obligation.
Disposition = Literal["active", "at-rest", "released", "abandoned"]

ACTIVE: Disposition = "active"
AT_REST: Disposition = "at-rest"
RELEASED: Disposition = "released"
#: The reclaim sweep's verdict (Phase 4): the claim's holder is **provably gone**
#: and its resource **provably safe**, so the never-wedge sweep reclaimed the
#: obligation and re-homed it to a durable owner rather than let it freeze the
#: owner's finalize forever. Distinct from ``released`` (a *clean* hand-back) and
#: from ``at-rest`` (the resource's *own* safe verdict) -- ``abandoned`` records
#: an *involuntary* reclaim (worth surfacing/auditing). Does not block finalize;
#: not held. Only the sweep assigns it (never a resource's own hook).
ABANDONED: Disposition = "abandoned"

#: Every recognized disposition value.
DISPOSITIONS: tuple[Disposition, ...] = (ACTIVE, AT_REST, RELEASED, ABANDONED)

#: The diagnostic-context key the lease record carries the disposition under.
#: The lease store already round-trips ``context`` (a str->str map) through
#: acquire/renew and surfaces it in ``inspect``/``list`` output, so a leaseable
#: resource's disposition is cross-machine visible with **no store schema
#: change** -- it rides here.
CONTEXT_KEY = "disposition"

#: Canonical dispositions that mean the owner still **holds** the resource (claim
#: not torn down) -- both ``active`` and ``at-rest``. Inputs are normalized to a
#: canonical value before membership is checked, so the empty/unknown legacy case
#: (which normalizes to ``active``) is held. ``released`` and ``abandoned`` are
#: not held (the claim is torn down / reclaimed).
_HELD: frozenset[str] = frozenset({ACTIVE, AT_REST})

#: Canonical dispositions that **block finalize** -- unsettled work still rides on
#: the resource. Inputs normalize first, so a missing/unknown value (-> ``active``)
#: is conservatively blocking. ``at-rest``/``released``/``abandoned`` do not block.
_UNSETTLED: frozenset[str] = frozenset({ACTIVE})


# ── The finalize gate (Phase 2) ──────────────────────────────────────────────

#: How strictly finalize enforces obligation settlement. ``off`` skips the check
#: entirely; ``warn`` (the default while the settlement hooks + reclaim sweep bed
#: in) surfaces unsettled obligations but lets finalize proceed; ``block`` refuses
#: to finalize while any owned obligation is unsettled (unless abandoned).
GateMode = Literal["off", "warn", "block"]

OFF: GateMode = "off"
WARN: GateMode = "warn"
BLOCK: GateMode = "block"

GATE_MODES: tuple[GateMode, ...] = (OFF, WARN, BLOCK)

#: Operator override of the gate mode. Warn-first by default: flip to ``block``
#: once the per-kind settlement hooks + the reclaim sweep are proven.
GATE_ENV = "AGENT_WORKTREES_OBLIGATION_GATE"

DEFAULT_GATE: GateMode = WARN


def gate_mode(env: object = None) -> GateMode:
    """Resolve the finalize gate mode from the environment (default ``warn``).

    ``env`` is an optional mapping (defaults to ``os.environ``). An unset or
    unrecognized value degrades to the warn-first default, never to ``block`` --
    the gate never starts *enforcing* by accident.
    """
    import os

    source = env if isinstance(env, dict) else os.environ
    raw = str(source.get(GATE_ENV, "")).strip().lower()
    return raw if raw in GATE_MODES else DEFAULT_GATE  # type: ignore[return-value]


def normalize(value: object) -> Disposition:
    """Coerce an arbitrary value to a known disposition (default ``active``).

    A missing / empty / unrecognized value degrades to ``active`` -- the
    conservative default that keeps an un-annotated obligation *blocking* rather
    than silently settled. Never raises.
    """
    text = str(value).strip() if value is not None else ""
    return text if text in DISPOSITIONS else ACTIVE  # type: ignore[return-value]


def blocks_finalize(value: object) -> bool:
    """True when this disposition should **block** the owner's finalize.

    ``active`` (and any missing/unknown value, which normalizes to ``active``)
    blocks; ``at-rest`` and ``released`` do not.
    """
    return normalize(value) in _UNSETTLED


def is_held(value: object) -> bool:
    """True when the owner still holds the resource (disposition != ``released``)."""
    return normalize(value) in _HELD


def is_at_rest(value: object) -> bool:
    return normalize(value) == AT_REST


def is_released(value: object) -> bool:
    return normalize(value) == RELEASED


def is_abandoned(value: object) -> bool:
    """True when the reclaim sweep abandoned this obligation (Phase 4)."""
    return normalize(value) == ABANDONED


# ── The never-wedge reclaim sweep (Phase 4) ──────────────────────────────────

def should_abandon(*, gone: bool | None, safe: bool | None) -> bool:
    """Decide whether the reclaim sweep may abandon an obligation.

    The never-wedge belt beneath the accountable gate: an ``active`` obligation
    may be reclaimed **only** when its holder is **provably gone** (``gone is
    True``) **and** its resource is **provably safe** (``safe is True``). Both
    are tri-state (``True`` / ``False`` / ``None``=unconfirmed); anything short
    of a definitive *gone-and-safe* leaves the obligation alone -- an
    unconfirmed holder or an unproven-safe resource is **never** reclaimed (the
    network / a missing probe must never turn "unknown" into "abandon"). This
    mirrors the claimant tri-state contract: unknown is spare.
    """
    return gone is True and safe is True


def from_context(context: object) -> Disposition:
    """Read the disposition from a lease record's ``context`` map.

    Accepts the context dict (or anything non-mapping -> ``active``). Missing key
    or unknown value degrades to ``active``. Never raises.
    """
    if not isinstance(context, dict):
        return ACTIVE
    return normalize(context.get(CONTEXT_KEY))


def with_disposition(context: object, value: object) -> dict[str, str]:
    """Return a new context map with the disposition key set to ``value``.

    Copies the existing context (or starts fresh when it is not a map) and sets
    ``disposition`` to the normalized value, so callers can thread it through
    ``lease acquire/renew --context``. Every value is stringified (the store's
    context is a str->str map).
    """
    result: dict[str, str] = {}
    if isinstance(context, dict):
        result.update({str(k): str(v) for k, v in context.items()})
    result[CONTEXT_KEY] = normalize(value)
    return result
