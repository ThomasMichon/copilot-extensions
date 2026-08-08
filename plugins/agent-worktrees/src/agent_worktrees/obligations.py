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
Disposition = Literal["active", "at-rest", "released"]

ACTIVE: Disposition = "active"
AT_REST: Disposition = "at-rest"
RELEASED: Disposition = "released"

#: Every recognized disposition value.
DISPOSITIONS: tuple[Disposition, ...] = (ACTIVE, AT_REST, RELEASED)

#: The diagnostic-context key the lease record carries the disposition under.
#: The lease store already round-trips ``context`` (a str->str map) through
#: acquire/renew and surfaces it in ``inspect``/``list`` output, so a leaseable
#: resource's disposition is cross-machine visible with **no store schema
#: change** -- it rides here.
CONTEXT_KEY = "disposition"

#: Canonical dispositions that mean the owner still **holds** the resource (claim
#: not torn down) -- both ``active`` and ``at-rest``. Inputs are normalized to a
#: canonical value before membership is checked, so the empty/unknown legacy case
#: (which normalizes to ``active``) is held.
_HELD: frozenset[str] = frozenset({ACTIVE, AT_REST})

#: Canonical dispositions that **block finalize** -- unsettled work still rides on
#: the resource. Inputs normalize first, so a missing/unknown value (-> ``active``)
#: is conservatively blocking.
_UNSETTLED: frozenset[str] = frozenset({ACTIVE})


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
