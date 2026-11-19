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
release/heartbeat stay responsive during a slow build. Instead, a provisioning
marker (``fleet-growth.json``) hides the member being built from every other
borrow and refuses session admission to it until the grower leases it: a
devcontainer member is visible under a temporary name before its final rename,
and no member is ready before its provisioning returns.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path
from typing import Any, TypeVar

from ssh_manager.locks import pid_alive

from .config import ContainersConfig
from .lifecycle import DockerContainerInfo
from .private_state import atomic_write_json, ensure_private_dir, private_file_lock

log = logging.getLogger("agent-containers")

# Long enough to wait out another borrow's devcontainer build (whose own
# timeout is 30 minutes) instead of failing while it is still provisioning.
GROWTH_LOCK_TIMEOUT = 1800.0

# A marker outlives a crashed grower only until this age (or its local pid dies).
MARKER_TTL = GROWTH_LOCK_TIMEOUT + 600.0
_MARKERS = "fleet-growth.json"
INSTANCE_LABEL = "agent-containers.instance"

T = TypeVar("T")
LeaseLock = Callable[[], AbstractContextManager[None]]


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


def _marker_live(marker: dict[str, Any]) -> bool:
    if time.time() - float(marker.get("started_at", 0)) >= MARKER_TTL:
        return False
    return marker.get("host") != platform.node() or pid_alive(int(marker.get("pid", 0)))


def _read_markers(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = state_dir / _MARKERS
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("%s unreadable; treating as no growth in progress", _MARKERS)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict) and _marker_live(v)}


def _write_markers(state_dir: Path, markers: dict[str, dict[str, Any]]) -> None:
    atomic_write_json(state_dir / _MARKERS, markers, indent=2)


def clear_marker(state_dir: Path, token: str) -> None:
    """Drop one provisioning marker. Callers hold the lease lock."""
    markers = _read_markers(state_dir)
    if markers.pop(token, None) is not None:
        _write_markers(state_dir, markers)


def admissible(
    members: list[DockerContainerInfo], state_dir: Path, token: str | None = None,
) -> list[DockerContainerInfo]:
    """Drop members another borrow is still provisioning (callers hold the lease lock).

    A member is hidden when it carries the in-progress target name (as its
    name or instance label), or when it belongs to the growing fleet but did
    not exist when that growth started -- covering a devcontainer's temporary
    pre-rename name. ``token`` exempts the caller's own growth.
    """
    markers = [m for key, m in _read_markers(state_dir).items() if key != token]
    if not markers:
        return members

    def provisioning(member: DockerContainerInfo) -> bool:
        return any(
            member.name == m.get("target")
            or member.labels.get(INSTANCE_LABEL) == m.get("target")
            or (member.fleet == m.get("fleet")
                and member.container_id not in m.get("known_ids", ()))
            for m in markers
        )

    return [member for member in members if not provisioning(member)]


def is_provisioning(state_dir: Path, name: str) -> bool:
    """True when ``name`` is the target of a live on-demand growth."""
    return any(m.get("target") == name for m in _read_markers(state_dir).values())


def provision_one(
    config: ContainersConfig, fleet_name: str, state_dir: Path, lease_lock: LeaseLock,
) -> tuple[str, str]:
    """Provision one more member below ``max_size`` through the ``up`` path.

    Returns the member name and the provisioning-marker token that hides it
    from other borrows until the caller leases it (or clears the marker).
    """
    from . import fleet as fleet_mod

    spec = config.fleets[fleet_name]
    existing = fleet_mod._fleet_members(config, fleet_name)
    current = len(existing)
    if spec.max_size is None or current >= spec.max_size:
        raise FleetExhaustedError(
            f"Fleet '{fleet_name}' is at its max_size ({spec.max_size})."
        )
    prefix = spec.prefix(fleet_name)
    target = f"{prefix}-{fleet_mod._next_indices(existing, prefix, 1)[0]}"
    token = uuid.uuid4().hex
    with lease_lock():
        markers = _read_markers(state_dir)
        markers[token] = {
            "fleet": fleet_name, "target": target,
            "known_ids": [c.container_id for c in existing],
            "pid": os.getpid(), "host": platform.node(), "started_at": time.time(),
        }
        _write_markers(state_dir, markers)
    log.info(
        "Growing fleet '%s' on demand with '%s': %d -> %d (max_size %d)",
        fleet_name, target, current, current + 1, spec.max_size,
    )
    try:
        result = fleet_mod.reconcile_up(config, fleet_name, count=current + 1)
        if not result.created:
            reasons = "; ".join(f"{k}: {v}" for k, v in result.deferred.items())
            raise RuntimeError(reasons or "provisioning created no member")
    except BaseException:
        with lease_lock():
            clear_marker(state_dir, token)
        raise
    return result.created[0], token


def borrow_or_grow(
    config: ContainersConfig,
    fleet: str | None,
    state_dir: Path,
    lease_lock: LeaseLock,
    attempt: Callable[[], T],
    lease_grown: Callable[[str, str], T],
) -> T:
    """Run ``attempt``; if the fleet is exhausted, grow it by at most one member.

    ``attempt`` is the ordinary lease-lock-protected selection. Without a
    growable fleet its refusal propagates unchanged. Otherwise it runs again
    under the growth lock (another borrow may have freed a member) before one
    member is provisioned. ``lease_grown(name, token)`` then leases exactly
    that member and clears its provisioning marker under one lease-lock hold.
    A failure never writes a lease: the original refusal is raised with the
    growth error appended.
    """
    try:
        return attempt()
    except FleetExhaustedError as exc:
        fleet_name = growth_fleet(config, fleet)
        if fleet_name is None:
            raise
        return _grow_and_retry(
            config, fleet_name, state_dir, lease_lock, exc, attempt, lease_grown,
        )


def _grow_and_retry(
    config: ContainersConfig,
    fleet_name: str,
    state_dir: Path,
    lease_lock: LeaseLock,
    refusal: FleetExhaustedError,
    attempt: Callable[[], T],
    lease_grown: Callable[[str, str], T],
) -> T:
    with growth_lock(state_dir, fleet_name):
        try:
            return attempt()
        except FleetExhaustedError:
            pass
        try:
            name, token = provision_one(config, fleet_name, state_dir, lease_lock)
        except FleetExhaustedError as exc:
            raise FleetExhaustedError(f"{refusal} {exc}") from exc
        except Exception as exc:  # noqa: BLE001 -- any provisioning failure refuses
            raise FleetExhaustedError(
                f"{refusal} On-demand growth of fleet '{fleet_name}' failed: {exc}"
            ) from exc
        log.info("Provisioned on-demand fleet member '%s'", name)
        try:
            return lease_grown(name, token)
        except BaseException:
            with lease_lock():
                clear_marker(state_dir, token)
            raise
