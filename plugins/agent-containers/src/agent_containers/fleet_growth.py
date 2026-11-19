"""Bounded on-demand fleet growth for ``borrow`` (the fleet's ``max_size``).

``size`` stays the warm floor that ``up`` maintains. When a fleet also sets
``max_size`` and ``borrow`` finds no free member, the lease broker provisions
exactly one more member through the fleet's normal ``up`` path (Docker image,
devcontainer or external driver) and leases it. Grown members are ordinary,
warm fleet members: they stay after release and ``down``/``rm`` treat them like
any other member.

Growth is serialized by a fleet-scoped kernel lock held across the capacity
check, provisioning and the lease, so racing borrows cannot overshoot
``max_size``. The lease lock itself is never held while provisioning, so
release/heartbeat stay responsive during a slow build.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TypeVar

from .config import ContainersConfig
from .private_state import ensure_private_dir, private_file_lock

log = logging.getLogger("agent-containers")

# Long enough to wait out another borrow's devcontainer build (whose own
# timeout is 30 minutes) instead of failing while it is still provisioning.
GROWTH_LOCK_TIMEOUT = 1800.0

T = TypeVar("T")


class FleetExhaustedError(RuntimeError):
    """No free member is available for a fleet-scoped borrow."""


def growth_fleet(config: ContainersConfig, fleet: str | None) -> str | None:
    """Name the fleet a non-targeted borrow may grow, or None for no growth.

    An unscoped borrow grows only when exactly one fleet is configured, so
    growth never provisions into a fleet the caller did not imply.
    """
    if fleet is None:
        if len(config.fleets) != 1:
            return None
        fleet = next(iter(config.fleets))
    spec = config.fleets.get(fleet)
    if spec is None or spec.max_size is None:
        return None
    return fleet


@contextmanager
def growth_lock(state_dir: Path, fleet_name: str) -> Iterator[None]:
    """Serialize growth of one fleet across processes and threads."""
    ensure_private_dir(state_dir)
    digest = hashlib.sha256(fleet_name.encode("utf-8")).hexdigest()[:16]
    with ExitStack() as stack:
        try:
            stack.enter_context(private_file_lock(
                state_dir / f"fleet-growth-{digest}.lock", timeout=GROWTH_LOCK_TIMEOUT,
            ))
        except RuntimeError as exc:
            raise FleetExhaustedError(
                f"Timed out waiting for another on-demand growth of fleet '{fleet_name}'"
            ) from exc
        yield


def provision_one(config: ContainersConfig, fleet_name: str) -> str:
    """Provision one more member below ``max_size`` through the ``up`` path."""
    from . import fleet as fleet_mod

    spec = config.fleets[fleet_name]
    current = len(fleet_mod._fleet_members(config, fleet_name))
    if spec.max_size is None or current >= spec.max_size:
        raise FleetExhaustedError(
            f"Fleet '{fleet_name}' is at its max_size ({spec.max_size})."
        )
    log.info(
        "Growing fleet '%s' on demand: %d -> %d (max_size %d)",
        fleet_name, current, current + 1, spec.max_size,
    )
    result = fleet_mod.reconcile_up(config, fleet_name, count=current + 1)
    if not result.created:
        reasons = "; ".join(f"{k}: {v}" for k, v in result.deferred.items())
        raise RuntimeError(reasons or "provisioning created no member")
    return result.created[0]


def borrow_or_grow(
    config: ContainersConfig,
    fleet: str | None,
    state_dir: Path,
    attempt: Callable[[], T],
) -> T:
    """Run ``attempt``; if the fleet is exhausted, grow it by at most one member.

    ``attempt`` is the ordinary lease-lock-protected selection. Without a
    growable fleet its refusal propagates unchanged. Otherwise it runs again
    under the growth lock (another borrow may have freed or grown a member),
    and once more after provisioning. A failure never writes a lease: the
    original refusal is raised with the growth error appended.
    """
    try:
        return attempt()
    except FleetExhaustedError as exc:
        fleet_name = growth_fleet(config, fleet)
        if fleet_name is None:
            raise
        return _grow_and_retry(config, fleet_name, state_dir, exc, attempt)


def _grow_and_retry(
    config: ContainersConfig,
    fleet_name: str,
    state_dir: Path,
    refusal: FleetExhaustedError,
    attempt: Callable[[], T],
) -> T:
    with growth_lock(state_dir, fleet_name):
        try:
            return attempt()
        except FleetExhaustedError:
            pass
        try:
            name = provision_one(config, fleet_name)
        except FleetExhaustedError as exc:
            raise FleetExhaustedError(f"{refusal} {exc}") from exc
        except Exception as exc:  # noqa: BLE001 -- any provisioning failure refuses
            raise FleetExhaustedError(
                f"{refusal} On-demand growth of fleet '{fleet_name}' failed: {exc}"
            ) from exc
        log.info("Provisioned on-demand fleet member '%s'", name)
        try:
            return attempt()
        except FleetExhaustedError as exc:
            raise FleetExhaustedError(
                f"{refusal} The member grown on demand ('{name}') was taken "
                "by a concurrent borrow."
            ) from exc
