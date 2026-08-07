"""CodeSpace venue pool -- inventory, budget accounting & disposition model.

The venue provider's view of the account's CodeSpaces as a **finite, shared,
budget-bounded pool** rather than a set of isolated machines (see the
``agent-codespaces`` vision, effort ``codespace-venue-pool`` #705, this being
Phase 1 / #706).

This module owns **no store of its own** -- it *derives* the pool view at read
time from the signals the layers below already own (the fabric's
*derive-don't-duplicate*):

- ``lifecycle.list_codespaces()`` -- what CodeSpaces exist, their gh ``state``,
  machine spec, repository, and last-used time (merged across mapped accounts);
- ``lease.list_leases()`` -- the advisory effort->CodeSpace hold (the *in-use*
  signal) and its holder (effort + host);
- ``status.list_status()`` -- the finalize/prune lifecycle marker
  (recovered / prunable) from #164.

From those it computes, per CodeSpace, a **disposition** (in-use / idle / clean /
stale, plus the transient provisioning / failed) and its **allocation** (repo,
holding effort/worktree, machine), and across the pool a **budget** (the
account's concurrent-core ceiling, what running CodeSpaces spend against it, and
the remaining headroom). ``budget-not-exceeded`` and ``reuse-over-recreate``
(Phase 2 / #708) consume this; the Worktree Picker's CodeSpaces pivot (Phase 3 /
#709) renders it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .lease import Lease, list_leases
from .lifecycle import CodespaceInfo, classify_state, list_codespaces
from .status import STATE_PRUNABLE, STATE_RECOVERED, list_status

# --- Disposition vocabulary (meanings fixed by the vision; encoding is here) ---
IN_USE = "in-use"        # actively held by a live lease (or a cross-machine beacon)
IDLE = "idle"            # running/reusable, unheld, fresh -- prime reuse candidate
CLEAN = "clean"          # set up, sessions rescued, no unrescued work (reusable)
STALE = "stale"          # idle past a freshness threshold, or a prune candidate
PROVISIONING = "provisioning"  # transient: still coming up
FAILED = "failed"        # transient/terminal: will not become usable on its own

# The account's default concurrent-core budget. The operator's account allows
# ~64 cores' worth of *running* CodeSpaces at once; a Shutdown box spends none.
DEFAULT_BUDGET_CORES = 64
# An unheld, unmarked, running CodeSpace idle longer than this ages to ``stale``
# (a recycle candidate). Deliberately generous; Phase 4 (#710) owns the actual
# recycling policy -- here it only *classifies*.
DEFAULT_STALE_AFTER = 24 * 3600.0

# Machine-tier -> cores. ``gh codespace list`` exposes ``machineName`` (the tier
# id), not a core count, so this map is the source of truth for the standard
# GitHub Codespaces Linux tiers. Unknown tiers yield 0 (surfaced as ``unknown``
# in the budget so headroom is never silently overstated). Best-effort but
# stable; a new tier only needs a row here.
_MACHINE_CORES = {
    "basicLinux32gb": 2,
    "standardLinux32gb": 4,
    "premiumLinux": 8,
    "largePremiumLinux": 16,
    "xLargePremiumLinux": 32,
    "largePremiumLinux256gb": 32,
}

_CORE_RE = re.compile(r"(\d+)\s*[-\s]?core", re.IGNORECASE)
_SHUTDOWN = "Shutdown"
# #140's cloud-global beacon: the borrowing worktree's 4-hex id suffixed onto the
# display name (e.g. "my-feature#a1b2"). Lets a box held by ANOTHER machine be
# seen as in-use even without that machine's local lease. Forward-compatible: a
# no-op until the beacon slice ships display-name suffixing.
_BEACON_RE = re.compile(r"#([0-9a-f]{4})\s*$", re.IGNORECASE)


def machine_cores(machine_name: str) -> int:
    """Best-effort core count for a CodeSpace's machine tier (``machineName``).

    ``gh codespace list`` exposes only the tier id (e.g. ``largePremiumLinux``),
    so cores come from the ``_MACHINE_CORES`` map, with a defensive parse of a
    ``"<N>core"`` embedded in the tier id. Returns ``0`` when the tier is unknown
    (surfaced as ``unknown`` in the budget so headroom is never overstated).
    """
    if machine_name in _MACHINE_CORES:
        return _MACHINE_CORES[machine_name]
    m = _CORE_RE.search(machine_name or "")
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return 0


def _beacon_id(display_name: str) -> str | None:
    """Return the 4-hex cross-machine beacon suffix on a display name, if any."""
    m = _BEACON_RE.search(display_name or "")
    return m.group(1).lower() if m else None


def _iso_to_epoch(value: str) -> float | None:
    """Parse an ISO-8601 timestamp (gh's ``lastUsedAt``) to epoch seconds."""
    if not value:
        return None
    try:
        s = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def is_running(state: str) -> bool:
    """Whether a CodeSpace state consumes concurrent compute (budget).

    ``Available`` and the transient *pending* states (Starting/Provisioning/...)
    are running; ``Shutdown`` and terminal-failed states spend no cores -- which
    is exactly why finalize+stop keeps a warm box off the active budget (#164).
    """
    bucket = classify_state(state)
    if bucket == "failed":
        return False
    return state != "Shutdown"


def derive_disposition(
    *,
    state: str,
    has_live_lease: bool,
    has_beacon: bool,
    marker: str | None,
    idle_age: float | None,
    stale_after: float,
) -> str:
    """Classify one CodeSpace's disposition from its derived signals.

    Precedence (first match wins):
      1. terminal-failed gh state            -> FAILED
      2. a live lease OR a cross-machine beacon -> IN_USE
      3. genuinely still-coming-up gh state  -> PROVISIONING
      4. a ``prunable`` marker               -> STALE
      5. a ``recovered`` marker              -> CLEAN
      6. unheld + idle past the threshold    -> STALE
      7. otherwise                           -> IDLE

    Note ``Shutdown`` is NOT provisioning -- a stopped box boots on connect and
    is reusable, so it falls through to the marker/idle classification (a
    Shutdown+recovered box is ``clean``, an unmarked one ``idle``/``stale``).
    """
    bucket = classify_state(state)
    if bucket == "failed":
        return FAILED
    if has_live_lease or has_beacon:
        return IN_USE
    if bucket == "pending" and state != _SHUTDOWN:
        return PROVISIONING
    if marker == STATE_PRUNABLE:
        return STALE
    if marker == STATE_RECOVERED:
        return CLEAN
    if idle_age is not None and idle_age > stale_after:
        return STALE
    return IDLE


@dataclass
class PoolMember:
    """One CodeSpace as a pool citizen: identity + disposition + allocation."""

    name: str
    repository: str
    branch: str
    state: str
    machine: str
    account: str
    cores: int
    cores_known: bool
    running: bool
    disposition: str
    # Allocation -- who holds it (None when free). A hold is one of two lease
    # flavors (see ``lease.py``): an advisory **borrow** keyed by ``effort``, or
    # the exclusive #897 **claim** keyed by ``worktree`` (the owner the
    # agent-bridge Session-Host dispatch path acquires, with ``effort`` empty).
    # ``owner`` is the single "who holds it" answer (worktree for a claim, else
    # effort) so a consumer needn't know which flavor recorded the hold -- this
    # is what fixes a dispatched CodeSpace reading as an unheld/``null``
    # allocation (#904). ``host`` is the machine the holder runs on; ``beacon``
    # the cross-machine 4-hex id when held elsewhere without a local lease (#140).
    holder_effort: str | None
    holder_worktree: str | None
    holder_host: str | None
    beacon: str | None
    marker: str | None
    idle_age: float | None

    @property
    def holder_owner(self) -> str | None:
        """The single owner of the hold: the claim worktree, else the effort."""
        return self.holder_worktree or self.holder_effort

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "repository": self.repository,
            "branch": self.branch,
            "state": self.state,
            "machine": self.machine,
            "account": self.account,
            "cores": self.cores,
            "cores_known": self.cores_known,
            "running": self.running,
            "disposition": self.disposition,
            "allocation": {
                "owner": self.holder_owner,
                "effort": self.holder_effort,
                "worktree": self.holder_worktree,
                "host": self.holder_host,
                "beacon": self.beacon,
            },
            "eligibility": self.marker or "active",
            "idle_age_s": round(self.idle_age) if self.idle_age is not None else None,
        }


@dataclass
class Budget:
    """The pool's concurrent-core budget accounting."""

    total_cores: int
    spent_cores: int
    headroom_cores: int
    running_count: int
    total_count: int
    unknown_cores_count: int  # running boxes whose cores couldn't be determined

    def to_dict(self) -> dict:
        return {
            "total_cores": self.total_cores,
            "spent_cores": self.spent_cores,
            "headroom_cores": self.headroom_cores,
            "running_count": self.running_count,
            "total_count": self.total_count,
            "unknown_cores_count": self.unknown_cores_count,
        }


def build_pool(
    *,
    budget_cores: int = DEFAULT_BUDGET_CORES,
    stale_after: float = DEFAULT_STALE_AFTER,
    now: float | None = None,
    codespaces: list[CodespaceInfo] | None = None,
    leases: list[Lease] | None = None,
    markers: dict[str, str] | None = None,
) -> tuple[list[PoolMember], Budget]:
    """Derive the full pool view (members + budget) from the owning layers.

    Pure derivation -- no persistence. The optional ``codespaces`` / ``leases`` /
    ``markers`` args exist for testing; in production they default to a live read
    of ``list_codespaces`` / ``list_leases`` / ``list_status``.
    """
    import time as _time

    now = _time.time() if now is None else now
    if codespaces is None:
        codespaces = list_codespaces()
    if leases is None:
        leases = list_leases()
    if markers is None:
        markers = {s.codespace: s.state for s in list_status()}

    lease_by_cs = {ls.codespace: ls for ls in leases}

    members: list[PoolMember] = []
    spent = running_count = unknown_running = 0
    for cs in codespaces:
        lease = lease_by_cs.get(cs.name)
        beacon = _beacon_id(cs.display_name)
        marker = markers.get(cs.name)
        cores = machine_cores(cs.machine)
        running = is_running(cs.state)

        last_used = _iso_to_epoch(cs.last_used_at)
        idle_age = (now - last_used) if (last_used is not None and lease is None) else None

        disposition = derive_disposition(
            state=cs.state,
            has_live_lease=lease is not None,
            has_beacon=beacon is not None,
            marker=marker,
            idle_age=idle_age,
            stale_after=stale_after,
        )

        if running:
            running_count += 1
            spent += cores
            if cores == 0:
                unknown_running += 1

        members.append(PoolMember(
            name=cs.name,
            repository=cs.repository,
            branch=cs.branch,
            state=cs.state,
            machine=cs.machine,
            account=cs.account,
            cores=cores,
            cores_known=cores > 0,
            running=running,
            disposition=disposition,
            # Normalize empty strings to None so a claim (effort="") and an
            # advisory borrow (worktree="") each surface as a clean, single
            # owner rather than a blank field (#904).
            holder_effort=(lease.effort or None) if lease else None,
            holder_worktree=(lease.worktree or None) if lease else None,
            holder_host=lease.host if lease else None,
            beacon=beacon,
            marker=marker,
            idle_age=idle_age,
        ))

    budget = Budget(
        total_cores=budget_cores,
        spent_cores=spent,
        headroom_cores=budget_cores - spent,
        running_count=running_count,
        total_count=len(members),
        unknown_cores_count=unknown_running,
    )
    return members, budget
