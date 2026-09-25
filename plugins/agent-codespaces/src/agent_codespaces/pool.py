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
(Phase 2 / #708) are the :func:`plan_allocation` planner in this module; the
Worktree Picker's CodeSpaces pivot (Phase 3 / #709) renders the derived view.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import _repo_matches_codespace
from .driving_worktrees import codespace_claim_owner_worktrees
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
    "largePremiumLinux256gb": 16,
    "xLargePremiumLinux256gb": 32,
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
    has_l2_hold: bool = False,
) -> str:
    """Classify one CodeSpace's disposition from its derived signals.

    Precedence (first match wins):
      1. terminal-failed gh state            -> FAILED
      2. a live lease OR a cross-machine beacon/L2 hold -> IN_USE
      3. genuinely still-coming-up gh state  -> PROVISIONING
      4. a ``prunable`` marker               -> STALE
      5. a ``recovered`` marker              -> CLEAN
      6. unheld + idle past the threshold    -> STALE
      7. otherwise                           -> IDLE

    ``has_l2_hold`` is the cross-machine Git-ref lease overlay (a live L2 lease
    held elsewhere without a local L1 lease) -- the atomic successor to the
    display-name beacon as the cross-machine in-use truth. Degrade-safe: it is
    False whenever the L2 store is unreadable, so the classification collapses to
    the pre-overlay behavior.

    Note ``Shutdown`` is NOT provisioning -- a stopped box boots on connect and
    is reusable, so it falls through to the marker/idle classification (a
    Shutdown+recovered box is ``clean``, an unmarked one ``idle``/``stale``).
    """
    bucket = classify_state(state)
    if bucket == "failed":
        return FAILED
    if has_live_lease or has_beacon or has_l2_hold:
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


def _holder_worktree_gone(worktree_path: str | None) -> bool:
    """True when a #897 **claim**'s owner worktree PATH is positively gone.

    A cheap, host-local check (no subprocess): the host-local ``leases.json``
    only records claims made on THIS host, so the claim's worktree path is local
    -- an absolute path no longer on disk means the owning worktree was
    finalized/pruned while the lease lingered (an **orphaned** lock). Conservative
    (biased toward alive): a non-path/legacy owner (an advisory borrow's effort)
    or an unreadable path is treated alive, so a live hold is never false-flagged,
    and a cross-machine hold (which rides the beacon/L2 overlay, not a local
    lease) is never seen here at all.
    """
    if not worktree_path or not os.path.isabs(worktree_path):
        return False
    try:
        return not os.path.exists(worktree_path)
    except OSError:
        return False


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
    # Cross-machine L2 (Git-ref lease) overlay -- a *derived read* over
    # ``agent-worktrees lease list`` (git-ref-resource-leases Phase 3), never a
    # store of its own. ``l2_holder`` is the qualified ClaimRef holding the
    # atomic cross-machine lease, ``l2_live`` whether that lease is unexpired,
    # ``l2_expires_at`` its deadline. All default to empty/False so a missing or
    # unreadable L2 store leaves the member exactly as the pre-overlay view.
    l2_holder: str | None = None
    l2_live: bool = False
    l2_expires_at: str = ""
    # The gh ``displayName`` -- a user/tool-assigned FRIENDLY name, distinct from
    # ``name`` (the durable GitHub-assigned id). Defaults to ``name`` when unset.
    display_name: str = ""
    # Worktree-lock legibility (venue-pool Phase 3 / 3b): True when this box is
    # held by a #897 claim whose owner worktree is positively **gone** (an
    # orphaned lock -- the lease lingers past the worktree's finalize/prune). A
    # derived, host-local fact (see :func:`_holder_worktree_gone`); default False.
    orphaned: bool = False
    # Cleanliness-beacon verdict (venue-pool Phase 3 / codespace-clean-beacon):
    # the tri-state "is all work off-box?" safety gate the destructive Recycle
    # keys on -- True (safe to delete), False (work still on-box), or None
    # (unknown: no fresh/known verdict -> Recycle hidden, Verify offered).
    # Derived from the ``codespace-clean`` git-ref overlay; default None.
    off_box_safe: bool | None = None
    # Self-recognition (agent-claim-awareness): True when THIS worktree/session is
    # the holder -- so ``pool``/``leases`` can mark the row ``(you)`` and an agent
    # reuses its own box instead of steering clear of it as if foreign. Annotated
    # by the command after resolving the caller's own ClaimRef; default False.
    held_by_self: bool = False

    @property
    def holder_owner(self) -> str | None:
        """The single owner of the hold: the claim worktree, else the effort."""
        return self.holder_worktree or self.holder_effort

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
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
            # Cross-machine L2 lease overlay (derived, degrade-safe). ``holder``
            # is the ClaimRef holding the atomic Git-ref lease across machines;
            # ``live`` whether it is unexpired; ``expires_at`` its deadline.
            "l2": {
                "holder": self.l2_holder,
                "live": self.l2_live,
                "expires_at": self.l2_expires_at or None,
            },
            "eligibility": self.marker or "active",
            "idle_age_s": round(self.idle_age) if self.idle_age is not None else None,
            # Cleanliness-beacon safety verdict (True/False/None-unknown).
            "off_box_safe": self.off_box_safe,
            # Self-recognition: True when THIS worktree/session holds the claim
            # (agent-claim-awareness) -- so a consumer reuses its own box instead
            # of treating it as foreign and steering clear.
            "held_by_self": self.held_by_self,
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
    l2_leases: dict | None = None,
    clean_records: dict | None = None,
) -> tuple[list[PoolMember], Budget]:
    """Derive the full pool view (members + budget) from the owning layers.

    Pure derivation -- no persistence. The optional ``codespaces`` / ``leases`` /
    ``markers`` args exist for testing; in production they default to a live read
    of ``list_codespaces`` / ``list_leases`` / ``list_status``.

    ``l2_leases`` is the cross-machine Git-ref lease overlay (a ``{key: L2Lease}``
    map from ``coordination.list_leases``). When omitted it is read live and
    **degrade-safe** -- an unavailable/unreadable L2 store yields ``None`` and the
    overlay is simply absent, so the pool view is identical to the pre-overlay
    behavior. Pass ``{}`` in tests to assert the no-overlay path without shelling.

    ``clean_records`` is the per-box **cleanliness beacon** overlay (a
    ``{name: CleanRecord}`` map from ``coordination.list_cleanliness``): the last
    published git-safety verdict, read WITHOUT SSH. Same degrade-safe contract as
    ``l2_leases`` (omit -> live read; unavailable -> absent -> every box's
    ``off_box_safe`` is None/unknown). Pass ``{}`` in tests for the no-overlay path.
    """
    import time as _time

    now = _time.time() if now is None else now
    if codespaces is None:
        codespaces = list_codespaces()
    if leases is None:
        leases = list_leases()
    if markers is None:
        markers = {s.codespace: s.state for s in list_status()}
    if l2_leases is None:
        # Best-effort cross-machine overlay; never let a lease-store failure break
        # the pool. ``None`` (unavailable) collapses to an empty overlay.
        try:
            from . import coordination
            l2_leases = coordination.list_leases() or {}
        except Exception:
            l2_leases = {}
    if clean_records is None:
        # Best-effort cleanliness-beacon overlay; a missing/unreadable store just
        # leaves every box's safety ``unknown`` (Recycle stays Verify-gated).
        try:
            from . import coordination
            clean_records = coordination.list_cleanliness() or {}
        except Exception:
            clean_records = {}

    lease_by_cs = {ls.codespace: ls for ls in leases}

    members: list[PoolMember] = []
    spent = running_count = unknown_running = 0
    for cs in codespaces:
        lease = lease_by_cs.get(cs.name)
        beacon = _beacon_id(cs.display_name)
        marker = markers.get(cs.name)
        cores = machine_cores(cs.machine)
        running = is_running(cs.state)

        l2 = l2_leases.get(cs.name)
        l2_live = bool(l2 and getattr(l2, "live", False))
        l2_holder = (getattr(l2, "holder", "") or None) if l2 else None
        l2_expires_at = (getattr(l2, "expires_at", "") or "") if l2 else ""
        # A live L2 lease held cross-machine (no local L1 lease) is an in-use
        # signal -- the atomic successor to the display-name beacon.
        has_l2_hold = l2_live and lease is None

        last_used = _iso_to_epoch(cs.last_used_at)
        idle_age = (now - last_used) if (last_used is not None and lease is None) else None

        # Cleanliness-beacon overlay: the last-published git-safety verdict for
        # this box (read without SSH). ``off_box_safe`` is tri-state -- True (all
        # work off-box, safe to Recycle), False (work still on-box), or None
        # (unknown: no fresh/known verdict -> Recycle stays Verify-gated).
        crec = clean_records.get(cs.name)
        off_box_safe = crec.off_box_safe if crec is not None else None

        disposition = derive_disposition(
            state=cs.state,
            has_live_lease=lease is not None,
            has_beacon=beacon is not None,
            marker=marker,
            idle_age=idle_age,
            stale_after=stale_after,
            has_l2_hold=has_l2_hold,
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
            l2_holder=l2_holder,
            l2_live=l2_live,
            l2_expires_at=l2_expires_at,
            display_name=cs.display_name or "",
            # 3b: flag an orphaned claim (holder worktree positively gone).
            orphaned=_holder_worktree_gone(lease.worktree if lease else None),
            # Cleanliness-beacon verdict (venue-pool Phase 3): tri-state safety
            # gate for the destructive Recycle -- True/False/None (unknown).
            off_box_safe=off_box_safe,
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


# --- Allocation planner (Phase 2 / #708): reuse-before-create, budget-bounded ---
# The pure decision core a venue request consults BEFORE creating a CodeSpace:
# prefer reusing a suitable idle/clean box; respect the concurrent-core budget;
# with no headroom recycle a stale box; else surface the pressure. Side-effect
# free -- it decides, the caller acts.

ALLOC_REUSE = "reuse"        # reuse an existing suitable idle/clean box (named)
ALLOC_CREATE = "create"      # no reuse candidate + headroom -> create a fresh box
ALLOC_RECYCLE = "recycle"    # no headroom -> recycle a stale box (named), then allocate
ALLOC_PRESSURE = "pressure"  # no reuse, no headroom, nothing recyclable -> surface it


@dataclass
class AllocationDecision:
    """The resolved venue-request decision (see :func:`plan_allocation`)."""

    action: str
    # The box to REUSE, or the stale box to RECYCLE; None for CREATE/PRESSURE.
    codespace: str | None
    reason: str
    # For RECYCLE only: the follow-on activation once the recycle frees budget --
    # ``reuse`` (a stopped reuse candidate exists) with ``then_codespace`` named,
    # else ``create``.
    then: str | None = None
    then_codespace: str | None = None
    # Budget context echoed for legible callers/logs.
    headroom_cores: int = 0
    needed_cores: int = 0

    def to_dict(self) -> dict:
        d: dict = {
            "action": self.action,
            "codespace": self.codespace,
            "reason": self.reason,
            "headroom_cores": self.headroom_cores,
            "needed_cores": self.needed_cores,
        }
        if self.then is not None:
            d["then"] = self.then
            d["then_codespace"] = self.then_codespace
        return d


def plan_allocation(
    members: list[PoolMember],
    budget: Budget,
    *,
    repo: str,
    new_cores: int = 0,
) -> AllocationDecision:
    """Resolve a venue request for ``repo`` to a reuse/create/recycle/pressure
    decision -- the pure core of ``reuse-before-create`` + ``budget-not-exceeded``
    (Phase 2 / #708).

    Precedence (first match wins):
      1. **Reuse** a suitable idle/clean box for ``repo``. A *running* candidate
         costs no extra budget and is always chosen first; a *stopped* one (boots
         on connect, spends its cores) is chosen only when it fits the headroom.
      2. Else **create** a fresh box when the intended machine (``new_cores``)
         fits the headroom.
      3. Else (no headroom) **recycle** a running *stale* box to reclaim its cores,
         then reuse-a-stopped-candidate / create -- but only when the reclaim
         actually makes the activation fit.
      4. Else **pressure**: the pool is full and nothing is recyclable -- surface
         it (``N/M cores``) rather than silently over-provision or fail opaquely.

    Pure + side-effect-free (a planner, not an executor). ``new_cores`` is the
    intended new box's core cost; ``0``/unknown is treated as a conservative ``1``
    so a full pool still blocks a create. Budget-correct: no returned action
    pushes ``spent`` past the ceiling.
    """
    needed = new_cores if new_cores > 0 else 1
    headroom = budget.headroom_cores

    matching = [m for m in members if _repo_matches_codespace(repo, m.repository)]
    reusable = [m for m in matching if m.disposition in (IDLE, CLEAN)]

    def _reuse_key(m: PoolMember) -> tuple:
        return (
            0 if m.running else 1,               # running first (0 extra cores)
            0 if m.disposition == CLEAN else 1,  # a rescued clean box over a bare idle
            m.idle_age if m.idle_age is not None else 0.0,  # freshest first
            m.name,
        )

    reusable.sort(key=_reuse_key)

    # 1. Reuse a running candidate -- always fits (already spending its cores).
    running_reuse = next((m for m in reusable if m.running), None)
    if running_reuse is not None:
        return AllocationDecision(
            action=ALLOC_REUSE, codespace=running_reuse.name,
            reason=(f"reusing running {running_reuse.disposition} CodeSpace "
                    f"'{running_reuse.name}' for {repo} "
                    f"(no new create, no extra budget)"),
            headroom_cores=headroom, needed_cores=0,
        )

    # A stopped reuse candidate boots on connect -> costs its cores.
    stopped_reuse = next((m for m in reusable if not m.running), None)
    stopped_cost = (
        (stopped_reuse.cores if stopped_reuse.cores > 0 else 1)
        if stopped_reuse is not None else 0
    )

    # 2. Reuse a stopped candidate, or create fresh, when it fits the headroom.
    if stopped_reuse is not None and headroom >= stopped_cost:
        return AllocationDecision(
            action=ALLOC_REUSE, codespace=stopped_reuse.name,
            reason=(f"reusing stopped {stopped_reuse.disposition} CodeSpace "
                    f"'{stopped_reuse.name}' for {repo} (boots on connect; "
                    f"{stopped_cost} core(s) fit the {headroom}-core headroom)"),
            headroom_cores=headroom, needed_cores=stopped_cost,
        )
    if headroom >= needed:
        return AllocationDecision(
            action=ALLOC_CREATE, codespace=None,
            reason=(f"no reusable CodeSpace for {repo}; creating fresh "
                    f"({needed} core(s) fit the {headroom}-core headroom)"),
            headroom_cores=headroom, needed_cores=needed,
        )

    # 3. No headroom -- recycle a running stale box to reclaim its cores. Prefer
    # the activation needing the least reclaim: reuse a stopped candidate if one
    # exists, else a fresh create.
    if stopped_reuse is not None:
        follow, follow_cs, follow_cost = "reuse", stopped_reuse.name, stopped_cost
    else:
        follow, follow_cs, follow_cost = "create", None, needed

    stale = [m for m in members if m.disposition == STALE and m.running]
    stale.sort(key=lambda m: (-(m.idle_age or 0.0), -m.cores, m.name))
    for m in stale:
        if headroom + m.cores >= follow_cost:
            then_verb = (f"reuse '{follow_cs}'" if follow == "reuse"
                         else "create a fresh box")
            return AllocationDecision(
                action=ALLOC_RECYCLE, codespace=m.name,
                reason=(f"pool full ({budget.spent_cores}/{budget.total_cores} "
                        f"cores); recycle stale '{m.name}' (+{m.cores} cores) "
                        f"then {then_verb} for {repo}"),
                then=follow, then_codespace=follow_cs,
                headroom_cores=headroom, needed_cores=follow_cost,
            )

    # 4. Nothing recyclable frees enough -- surface the pressure.
    return AllocationDecision(
        action=ALLOC_PRESSURE, codespace=None,
        reason=(f"pool full: {budget.spent_cores}/{budget.total_cores} cores in "
                f"use, {headroom} free; no idle/clean box to reuse and no stale "
                f"box to recycle for {repo}"),
        headroom_cores=headroom, needed_cores=needed,
    )


def _short_repo(repository: str) -> str:
    """The trailing path segment of an ``owner/name`` repo id (display only)."""
    return repository.rsplit("/", 1)[-1] if repository else repository


def _configured_workspace_repo(repository: str | None) -> str | None:
    """Declarative workspace repo for a CodeSpace launcher repo, if configured.

    Reads ``repos.<repo>.workspace_repo`` from agent-codespaces config as a
    cheap local fast-path for callers that only need to know which logical
    product repo a GitHub-hosted CodeSpace repo is configured to host.
    """
    if not repository:
        return None
    try:
        from .config import load_merged_config

        repo_cfg = load_merged_config(include_cwd=False).repos.get(repository)
    except Exception:
        return None
    workspace_repo = repo_cfg.workspace_repo if repo_cfg else None
    return workspace_repo if isinstance(workspace_repo, str) and workspace_repo else None


def _worktree_dir_id(worktree_path: str | None) -> str:
    """A #897 claim owner's worktree **dir name** (its id) from its absolute
    path, for the pivot's ``worktree`` column -- so a claim-held box shows WHICH
    worktree locks it (3b). ``""`` for a non-path/empty owner (an advisory
    borrow surfaces via its effort id instead)."""
    if not worktree_path or not os.path.isabs(worktree_path):
        return ""
    return os.path.basename(worktree_path.rstrip("/\\"))


def _short_claim_ref(ref: str) -> str:
    """Render a qualified ClaimRef (``machine/project/worktree[#session]``) as a
    compact ``worktree@machine`` label for the L2 cross-machine holder column."""
    if not ref:
        return ref
    machine = ref.split("/", 1)[0] if "/" in ref else ""
    tail = ref.rsplit("/", 1)[-1]
    worktree = tail.split("#", 1)[0]
    return f"{worktree}@{machine}" if machine else worktree


def _claims_summary_for_worktree(worktree_id: str) -> str:
    """The ranked ``claims_summary`` for the worktree claiming this box
    (picker-venue-pivots Phase 1), via the shared ``agent_worktrees.claims_rank``
    module -- the same ranking every claims-showing pivot consumes.

    Lazily imports ``agent_worktrees`` (mirroring ``config
    ._registered_repo_paths``'s own cross-plugin pattern): agent-codespaces
    does not declare a hard dependency on agent-worktrees, so an environment
    where it is not installed alongside still renders a full payload -- just
    without a ``claims_summary``. Never raises: an empty/unknown ``worktree_id``,
    a missing record, or an import failure all degrade to ``""``.
    """
    if not worktree_id:
        return ""
    try:
        from agent_worktrees import claim_kinds_registry, claims_rank, tracking
    except ImportError:
        return ""
    try:
        record = tracking.load_record_by_id(worktree_id)
    except Exception:
        return ""
    if record is None:
        return ""
    try:
        pecking_order = claim_kinds_registry.effective_pecking_order()
        label_overrides = claim_kinds_registry.effective_label_overrides()
    except Exception:
        pecking_order = None
        label_overrides = None
    try:
        return claims_rank.summarize_claims(
            record.resources,
            pecking_order=pecking_order,
            label_overrides=label_overrides,
        )
    except Exception:
        return ""


def _driving_worktree_id(member: PoolMember) -> str:
    """The full driving-worktree id when this CodeSpace is locally backed by a
    tracked worktree, else ``""``.

    The existing ``worktree`` picker field intentionally remains the compact
    cross-link token Phases 1-2 already use for title/claims correlation
    (beacon / effort / local worktree id). Phase 4's drill-in actions need the
    **full** tracked worktree id because the picker's internal worktree jump
    resolves rows by stable id, not a 4-char beacon/preview token.
    """
    if member.holder_worktree:
        return _worktree_dir_id(member.holder_worktree)
    return ""


def _driving_worktree_mark(*, has_driving_worktree: bool, orphaned: bool) -> str:
    """The reserved line-two mark for a row's most relevant relation."""
    if orphaned:
        return "\u26a0"
    if has_driving_worktree:
        return "\u2192"
    return ""


def _prefixed_subtitle(
    subtitle: str,
    *,
    has_driving_worktree: bool,
    orphaned: bool,
) -> str:
    """Apply the reserved Phase 4 line-two mark to the composed subtitle."""
    mark = _driving_worktree_mark(
        has_driving_worktree=has_driving_worktree,
        orphaned=orphaned,
    )
    if mark and subtitle and not subtitle.startswith(f"{mark} "):
        return f"{mark} {subtitle}"
    return subtitle


def _worktree_status_for_worktree(worktree_id: str) -> dict[str, Any]:
    """A read-only Worktree Status card payload for the driving worktree, or
    an explicit unavailable card when this row isn't backed by a resolvable
    tracked worktree."""
    unavailable = {
        "title": "Worktree status unavailable",
        "status": "unknown",
        "link": None,
        "body": "No tracked driving worktree is recorded for this CodeSpace.",
    }
    if not worktree_id:
        return unavailable
    try:
        from agent_worktrees import claim_kinds_registry, claims_rank, status_bar_cli, tracking
    except ImportError:
        return unavailable
    try:
        record = tracking.load_record_by_id(worktree_id)
    except Exception:
        return unavailable
    if record is None:
        return unavailable
    try:
        payload = status_bar_cli._status_segment_json(record.path)
    except Exception:
        payload = None
    try:
        claims = claims_rank.summarize_claims(
            record.resources,
            pecking_order=claim_kinds_registry.effective_pecking_order(),
            label_overrides=claim_kinds_registry.effective_label_overrides(),
        )
    except Exception:
        claims = ""
    state = str((payload or {}).get("state") or "unknown")
    closure = (payload or {}).get("closure") or {}
    git_bits = [
        f"ahead {payload.get('ahead', 0)}" if payload is not None else None,
        f"behind {payload.get('behind', 0)}" if payload is not None else None,
        "dirty" if payload and payload.get("dirty") else "clean" if payload else None,
    ]
    git_summary = ", ".join(bit for bit in git_bits if bit)
    live = (
        "mux live" if record.mux_live is True else
        "bound live" if record.bound_live is True else
        "idle"
    )
    body = "\n".join([
        f"- Repo: `{record.repo}`",
        f"- Worktree: `{record.worktree_id}`",
        f"- Branch: `{record.branch}`",
        f"- Turns: {(payload or {}).get('turn_count', 0)}",
        f"- Live: {live}",
        f"- Git: {state}" + (f" ({git_summary})" if git_summary else ""),
        f"- Closure: {closure.get('label', 'unknown')}",
        f"- Claims: {claims or 'none'}",
    ])
    return {
        "title": f"Worktree {record.worktree_id} ({record.repo})",
        "status": closure.get("style") or state,
        "link": None,
        "body": body,
    }


def _codespace_git_probe_command(repository: str | None) -> str:
    """A bash snippet that probes the real workspace git remote + branch."""
    workspace = ""
    if repository:
        try:
            from .config import load_merged_config

            workspace = (
                load_merged_config(include_cwd=False).resolved_workspace_folder_for(repository)
                or ""
            )
        except Exception:
            workspace = ""
    workspace_literal = shlex.quote(workspace) if workspace else '""'
    return (
        f"repo={workspace_literal}; "
        'if [ -z "$repo" ] || ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; '
        'then repo="${WORKING_DIRECTORY:-${VM_REPO_PATH:-$PWD}}"; fi; '
        'origin=$(git -C "$repo" config --get remote.origin.url 2>/dev/null || true); '
        'branch=$(git -C "$repo" symbolic-ref --quiet --short HEAD 2>/dev/null || '
        'git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null || true); '
        'printf "origin=%s\\nbranch=%s\\n" "$origin" "$branch"'
    )


@dataclass(frozen=True)
class _AdoRepoRef:
    host: str
    organization: str
    project: str
    repository: str

    def api_base(self) -> str:
        if self.host.endswith(".visualstudio.com"):
            return f"https://{self.host}/{self.project}"
        return f"https://{self.host}/{self.organization}/{self.project}"

    def pr_url(self, pr_id: int) -> str:
        return f"{self.api_base()}/_git/{self.repository}/pullrequest/{pr_id}"


def _ado_remote_ref(remote_url: str) -> _AdoRepoRef | None:
    """Parse an Azure DevOps git remote into its repo coordinates."""
    url = (remote_url or "").strip()
    if not url:
        return None
    ssh = re.match(
        r"^(?:ssh://)?git@(?P<host>ssh\.dev\.azure\.com)[:/](?:v3/)?(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/\s]+)$",
        url,
        re.IGNORECASE,
    )
    if ssh:
        return _AdoRepoRef(
            host="dev.azure.com",
            organization=str(ssh.group("org")),
            project=str(ssh.group("project")),
            repository=str(ssh.group("repo")),
        )
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host == "dev.azure.com" and len(parts) >= 4 and parts[2] == "_git":
        repo_index = 4 if len(parts) >= 5 and parts[3] == "_optimized" else 3
        return _AdoRepoRef(
            host=host,
            organization=parts[0],
            project=parts[1],
            repository=parts[repo_index],
        )
    if host.endswith(".visualstudio.com") and len(parts) >= 3 and parts[1] == "_git":
        repo_index = 3 if len(parts) >= 4 and parts[2] == "_optimized" else 2
        return _AdoRepoRef(
            host=host,
            organization=host.split(".", 1)[0],
            project=parts[0],
            repository=parts[repo_index],
        )
    return None


def _codespace_git_probe(
    codespace_name: str,
    repository: str | None = None,
    owner_worktree: str | None = None,
) -> tuple[str | None, str | None]:
    """Best-effort remote-origin + checked-out-branch probe inside one CodeSpace."""
    command = _codespace_git_probe_command(repository)
    if owner_worktree:
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "agent_codespaces", "ssh", codespace_name,
                    "--effort", owner_worktree,
                    "--remote-cmd", command,
                    "--timeout", "90",
                ],
                capture_output=True,
                text=True,
                timeout=150,
            )
        except Exception:
            return None, None
        if proc.returncode != 0:
            return None, None
        output = proc.stdout or ""
    else:
        try:
            from . import gh_account
            from .lifecycle import account_for_codespace
        except Exception:
            return None, None
        try:
            account = account_for_codespace(codespace_name)
        except Exception:
            account = None
        try:
            proc = subprocess.run(
                [
                    "gh", "codespace", "ssh", "-c", codespace_name,
                    "--", "-T", "bash", "-lc", command,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=gh_account.env_for_account(account),
            )
        except Exception:
            return None, None
        if proc.returncode != 0:
            return None, None
        output = proc.stdout or ""
    origin = None
    branch = None
    for line in output.splitlines():
        key, _, value = line.partition("=")
        if key == "origin":
            origin = value.strip() or None
        elif key == "branch":
            branch = value.strip() or None
    return origin, branch


def _codespace_remote_origin_url(
    codespace_name: str,
    repository: str | None = None,
) -> str | None:
    """Best-effort remote-origin probe inside one CodeSpace."""
    origin, _branch = _codespace_git_probe(codespace_name, repository)
    return origin


def _codespace_current_branch(
    codespace_name: str,
    repository: str | None = None,
) -> str | None:
    """Best-effort current branch probe inside one CodeSpace's real workspace."""
    _origin, branch = _codespace_git_probe(codespace_name, repository)
    return branch


def _ado_rest_bearer() -> str | None:
    """Best-effort ADO REST bearer from the same sources the relay uses."""
    try:
        from .auth_preflight import _ado_scope
        from credential_relay.sources.injected_token import InjectedTokenSource
        from credential_relay.sources.az_login import AzLoginSource
        import asyncio
    except Exception:
        return None

    async def _resolve() -> str | None:
        scope = _ado_scope()
        for source in (
            InjectedTokenSource(allowed_resources=["*"]),
            AzLoginSource(allowed_resources=["*"], cache_ttl_override=0),
        ):
            try:
                response = await source.resolve(
                    "get-azure-token",
                    {"scope": scope},
                    timeout=30.0,
                )
            except Exception:
                continue
            if not response:
                continue
            match = re.search(r"(?:^|[\r\n])token=(.+)", str(response))
            if match:
                return match.group(1).strip()
        return None

    try:
        return asyncio.run(_resolve())
    except Exception:
        return None


def _normalize_branch_ref(branch: str) -> str | None:
    value = (branch or "").strip()
    if not value or value == "HEAD":
        return None
    return value if value.startswith("refs/heads/") else f"refs/heads/{value}"


def _odsp_web_pr_ref(
    codespace_name: str,
    branch: str,
    *,
    remote_url: str | None = None,
) -> str | None:
    """The active odsp-web PR produced by this CodeSpace's current ADO branch,
    or ``None`` when no such PR is detectable."""
    branch_ref = _normalize_branch_ref(branch)
    if not branch_ref:
        return None
    remote = _ado_remote_ref(
        remote_url if remote_url is not None else (_codespace_remote_origin_url(codespace_name) or "")
    )
    if remote is None or remote.repository.casefold() != "odsp-web":
        return None
    token = _ado_rest_bearer()
    if not token:
        return None
    query = urllib.parse.urlencode({
        "searchCriteria.sourceRefName": branch_ref,
        "searchCriteria.status": "active",
        "api-version": "7.1",
    })
    url = (
        f"{remote.api_base()}/_apis/git/repositories/"
        f"{urllib.parse.quote(remote.repository, safe='')}/pullrequests?{query}"
    )
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except Exception:
        return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    values = data.get("value") if isinstance(data, dict) else None
    if not isinstance(values, list) or not values:
        return None
    pr_id = values[0].get("pullRequestId")
    try:
        pr_number = int(pr_id)
    except (TypeError, ValueError):
        return None
    return remote.pr_url(pr_number)


def _auto_claim_odsp_web_pr(
    codespace_name: str,
    repository: str,
    _branch: str,
    worktree_id: str,
) -> str | None:
    """Best-effort producer for Phase 4's odsp-web PR auto-claim.

    Detection is intentionally read-triggered and idempotent: when the
    Codespaces pivot materializes a row backed by a resolvable driving
    worktree, it first checks whether config already declares the GH-hosted
    launcher repo to back some *other* product repo and, when so, skips the
    expensive live SSH probe entirely. Otherwise it probes the CodeSpace's
    current real git workspace (ADO origin + checked-out branch) for an active
    odsp-web PR and, if found, journals it onto that worktree's existing claim
    ledger through the already-shipped `claims add pr` verb. No new claim store
    exists here; `claims add` deduplicates by ref.
    """
    if not worktree_id:
        return None
    try:
        from agent_worktrees import tracking
    except ImportError:
        return None
    try:
        record = tracking.load_record_by_id(worktree_id)
    except Exception:
        return None
    if record is None:
        return None
    owner_ref = record.owner_ref or tracking.format_claim_ref(
        getattr(record, "machine", None),
        getattr(record, "repo", None),
        getattr(record, "worktree_id", worktree_id),
    )
    owner_worktree = getattr(record, "path", None) or getattr(record, "worktree_path", None)
    configured_workspace_repo = _configured_workspace_repo(repository)
    if (
        configured_workspace_repo
        and _short_repo(configured_workspace_repo).casefold() != "odsp-web"
    ):
        return None
    remote_url, branch = _codespace_git_probe(
        codespace_name,
        repository,
        owner_worktree,
    )
    pr_ref = _odsp_web_pr_ref(codespace_name, branch or "", remote_url=remote_url)
    if not pr_ref:
        return None
    try:
        from .coordination import journal_claim
    except Exception:
        return pr_ref
    journal_claim("pr", pr_ref, owner_ref)
    return pr_ref


#: agent-bridge liveness labels (``LiveSessionInfo.liveness`` /
#: ``routes.live_sessions._live_liveness``) that read as "a turn is actually
#: running right now" for the picker's compact ``sess`` column -- "stalled"
#: still means a turn is in flight (just silent past the stall threshold), so
#: it counts as LIVE alongside "active"; only "idle"/None do not.
_LIVE_TURN_LIVENESS = frozenset({"active", "stalled"})


def _bridge_client_from_env() -> Any | None:
    """A ``BridgeClient`` dialed at the locally-configured agent-bridge
    daemon, or ``None`` when agent-bridge isn't installed alongside, has no
    auth token yet (not started), or any other resolution step fails.

    Deliberately **not** ``BridgeClient.from_config()`` -- that classmethod
    prints to stderr and calls ``sys.exit(1)`` when the auth token is
    missing, which is correct for a one-shot CLI command but would corrupt
    (or kill) `agent-codespaces pool --picker-json`'s own output merely
    because agent-bridge happens not to be running. This replicates its
    config/port/token resolution (routing-table re-resolution omitted --
    per the original's own comment, that's "an optimization, never a hard
    dependency") but degrades to ``None`` instead of exiting.
    """
    try:
        import yaml
        from agent_bridge.client import BridgeClient
        from agent_bridge.config import config_dir
        from agent_bridge.models import default_port
    except ImportError:
        return None
    try:
        cfg_path = config_dir() / "config.yaml"
        auth_path = config_dir() / "auth.yaml"
        if not auth_path.exists():
            return None
        auth_data = yaml.safe_load(auth_path.read_text(encoding="utf-8")) or {}
        token = auth_data.get("token")
        if not token:
            return None
        port = default_port()
        bind = "127.0.0.1"
        if cfg_path.exists():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            port = data.get("port") or port
            bind = data.get("bind", bind) or bind
        if bind in ("0.0.0.0", ""):
            bind = "127.0.0.1"
        elif bind == "::":
            bind = "::1"
        return BridgeClient(f"http://{bind}:{port}", str(token), timeout=5)
    except Exception:
        return None


def _live_session_for_venue(kind: str, target: str) -> dict[str, Any] | None:
    """The registered agent-bridge live session whose ``venue`` targets this
    ``kind``/``target`` (e.g. ``"codespace"``/a CodeSpace name), or ``None``
    when agent-bridge is unreachable/not installed or no session matches.

    Never raises -- an offline or absent agent-bridge simply means no
    live-session join, not a broken pool listing (picker-venue-pivots
    Phase 1)."""
    if not target:
        return None
    client = _bridge_client_from_env()
    if client is None:
        return None
    try:
        sessions = client.list_live_sessions(include_dead=False)
    except Exception:
        return None
    for session in sessions or []:
        venue = (session or {}).get("venue") or {}
        if venue.get("kind") == kind and venue.get("target") == target:
            return session
    return None


def _sess_column(live_session: dict[str, Any] | None, worktree_id: str) -> str:
    """The Worktrees pane's own compact ``sess``/``live`` column vocabulary
    (picker-venue-pivots design), reused as-is: ``"LIVE"`` when agent-bridge
    reports an actually-running turn, ``"IDLE"`` when a worktree is driving
    but no turn is live, else ``""`` when nothing is driving at all."""
    if live_session and live_session.get("liveness") in _LIVE_TURN_LIVENESS:
        return "LIVE"
    if worktree_id:
        return "IDLE"
    return ""


def _activity_from_live_session(live_session: dict[str, Any] | None) -> str:
    """The transient-activity half of line two: the live session's most
    recent ``latest_progress`` beat (``"{phase}: {summary}"``, or just
    ``summary`` with no phase), or ``""`` when there is no live session or
    it hasn't reported one yet (graceful-absence, never a placeholder)."""
    if not live_session:
        return ""
    progress = live_session.get("latest_progress") or {}
    summary = progress.get("summary") if isinstance(progress, dict) else None
    if not summary:
        return ""
    phase = progress.get("phase")
    return f"{phase}: {summary}" if phase else str(summary)


def picker_payload(
    members: list[PoolMember],
    budget: Budget,
    *,
    note: str = "",
    banner: str = "",
    banner_level: str = "warn",
) -> dict:
    """Shape the pool view for the Worktree Picker's **CodeSpaces** pivot (D1).

    Returns the registered-pivot ``{"entries": [...], "summary": {...}}`` payload
    the extended interop protocol consumes (a summary/header line + a table). Pure
    presentation over :func:`build_pool`'s output -- no persistence, no I/O -- so
    it is trivially testable and the CLI wrapper only supplies the live model +
    an optional ``note`` (e.g. a missing-``codespace``-scope hint, #980).

    Each entry carries picker-friendly fields (identity, disposition, cores,
    holder) plus **distinct** ``health`` (active health: running vs stopped) and
    ``use`` (active agent-use: in-use vs free) signals -- derived, no SSH -- so a
    running-but-idle box reads differently from one an agent is working in. The
    summary carries the budget accounting plus the optional ``note``.

    A non-empty ``banner`` sets the summary's reserved ``banner_text`` /
    ``banner_level`` keys, which the picker renders as a **prominent** styled
    alert line (distinct from the plain ``{note}`` summary token) -- the
    actionable missing-``codespace``-scope notice (#980).
    """
    entries: list[dict] = []
    claim_owner_worktrees = codespace_claim_owner_worktrees(m.name for m in members if m.holder_effort and not m.holder_worktree)
    for m in sorted(members, key=lambda x: (x.repository, x.disposition, x.name)):
        if m.holder_effort:
            holder = f"{m.holder_effort}@{m.holder_host or '?'}"
        elif m.beacon:
            holder = f"#{m.beacon}"
        elif m.l2_live and m.l2_holder:
            # Held cross-machine via the atomic L2 lease, with no local L1 lease.
            holder = _short_claim_ref(m.l2_holder)
        else:
            holder = ""
        driving_worktree_id = _driving_worktree_id(m) or claim_owner_worktrees.get(m.name, "")
        has_driving_worktree = bool(driving_worktree_id)
        friendly = m.display_name or m.name
        worktree = (
            m.beacon or driving_worktree_id or m.holder_effort
            or _worktree_dir_id(m.holder_worktree)
        )
        # A concise uppercase status for the compact table: RUNNING when live,
        # STALE for an aged recycle candidate, else STOPPED.
        if m.running:
            status = "RUNNING"
        elif m.disposition == STALE:
            status = "STALE"
        else:
            status = "STOPPED"
        # Grouping key: repo @ account (the account is a shared-pool axis).
        group = f"{_short_repo(m.repository)} @ {m.account or 'ambient'}"
        # Second-line fallback (durable id + claim) kept for pivots that opt into
        # a subtitle; the compact grouped layout uses columns instead.
        subtitle = m.name if friendly != m.name else ""
        if m.holder_effort:
            claim = f"claimed by {m.holder_effort}"
            claim += f" on {m.holder_host}" if m.holder_host else ""
            subtitle = f"{subtitle} · {claim}" if subtitle else claim
        elif m.beacon:
            held = f"held elsewhere #{m.beacon}"
            subtitle = f"{subtitle} · {held}" if subtitle else held
        elif m.l2_live and m.l2_holder:
            held = f"held cross-machine by {_short_claim_ref(m.l2_holder)}"
            subtitle = f"{subtitle} · {held}" if subtitle else held
        if m.orphaned:
            # 3b: make the stale lock legible on the fallback subtitle too.
            gone = "\u26a0 holder worktree gone (orphaned lock)"
            subtitle = f"{subtitle} · {gone}" if subtitle else gone
        # Phase 1 (picker-venue-pivots): the agent-bridge live-session join,
        # keyed on venue.kind == "codespace" + venue.target == this box's own
        # name -- the transient-activity half of line two, and the sess/live
        # column signal. "" / "" / blank when agent-bridge is unreachable, not
        # installed, or no session is registered for this box (see
        # _live_session_for_venue / _activity_from_live_session / _sess_column).
        live_session = _live_session_for_venue("codespace", m.name)
        activity = _activity_from_live_session(live_session)
        if activity:
            subtitle = f"{subtitle} - {activity}" if subtitle else f"{friendly} - {activity}"
        elif has_driving_worktree and not subtitle and not m.orphaned:
            subtitle = friendly
        subtitle = _prefixed_subtitle(
            subtitle,
            has_driving_worktree=has_driving_worktree,
            orphaned=m.orphaned,
        )
        _auto_claim_odsp_web_pr(
            m.name,
            m.repository,
            m.branch,
            driving_worktree_id,
        )
        entries.append({
            "id": m.name,
            "name": m.name,            # durable GitHub-assigned id
            "display": friendly,       # friendly display name (falls back to id)
            "group": group,            # repo @ account (section grouping)
            "status": status,          # RUNNING / STALE / STOPPED (compact STATE)
            "worktree": worktree,      # claiming worktree short id (-> TASK title)
            "worktree_id": driving_worktree_id,  # full tracked id for drill-in
            "has_driving_worktree": "true" if has_driving_worktree else "false",
            "subtitle": subtitle,      # optional 2nd line (durable id + claim)
            "worktree_status": _worktree_status_for_worktree(driving_worktree_id),
            # Phase 1 (picker-venue-pivots): the claiming worktree's own ranked
            # claims-list (PR/bug/etc.), via the shared claims_rank module --
            # "" when unclaimed, unresolvable, or agent-worktrees isn't
            # installed alongside (see _claims_summary_for_worktree).
            "claims_summary": _claims_summary_for_worktree(driving_worktree_id),
            # Phase 1: the Worktrees pane's own compact sess/live column,
            # reused as-is -- LIVE/IDLE/blank (see _sess_column).
            "sess": _sess_column(live_session, driving_worktree_id),
            "repository": m.repository,
            "repo": _short_repo(m.repository),
            "branch": m.branch,
            "account": m.account,
            "disposition": m.disposition,
            "state": m.state,
            "cores": str(m.cores) if m.cores_known else "?",
            "running": m.running,
            "holder": holder,
            # health vs. use: two distinct axes (venue-pool Phase 3 / #709).
            "health": "running" if m.running else "stopped",
            "use": "in-use" if m.disposition == IN_USE else "free",
            # 3b: an ORPHANED lock (holder worktree gone) reads distinctly in the
            # `use` column via ``occupancy`` (-> magenta ORPHAN palette), while
            # ``disposition`` stays in-use so the Release verb still offers to
            # free the stale lock. ``orphaned`` is the raw signal for gating.
            "orphaned": m.orphaned,
            "occupancy": "orphan" if m.orphaned else m.disposition,
            # Cleanliness-beacon safety verdict as a picker-gate string (venue-
            # pool Phase 3 / codespace-clean-beacon): "yes" (all work off-box ->
            # Recycle offered), "no" (work still on-box), "unknown" (no fresh
            # verdict -> Recycle hidden, Verify offered). The Recycle/Verify
            # actions gate on this via their ``when`` clause.
            "safe": (
                "yes" if m.off_box_safe is True
                else "no" if m.off_box_safe is False
                else "unknown"
            ),
        })
    summary = dict(budget.to_dict())
    summary["note"] = note or ""
    if banner:
        summary["banner_text"] = banner
        summary["banner_level"] = banner_level or "warn"
    return {"entries": entries, "summary": summary}


def picker_stream_frames(
    members: list[PoolMember],
    budget: Budget,
    *,
    note: str = "",
    banner: str = "",
    banner_level: str = "warn",
) -> list[dict]:
    """The one-shot NDJSON envelope (D2) for the CodeSpaces pivot's ``--stream``.

    Reuses :func:`picker_payload` so the streamed rows carry the **identical**
    entry/summary shape as the non-streaming ``--picker-json`` payload (including
    any ``banner`` in the summary), then frames them as ``begin`` -> a ``row``
    per CodeSpace -> ``summary`` -> ``done``. ``begin.count`` is exact: the pool
    roster is a single ``gh`` call, so the size is known up front. Pure -- the
    caller flushes each frame."""
    payload = picker_payload(
        members, budget, note=note, banner=banner, banner_level=banner_level,
    )
    entries = payload["entries"]
    frames: list[dict] = [{"type": "begin", "count": len(entries)}]
    for entry in entries:
        frames.append({"type": "row", "entry": entry})
    frames.append({"type": "summary", "summary": payload["summary"]})
    frames.append({"type": "done", "count": len(entries)})
    return frames


def diff_entries(
    prev: list[dict],
    curr: list[dict],
    *,
    id_key: str = "id",
) -> tuple[list[dict], list[str]]:
    """Diff two entry snapshots by ``id_key`` for a ``subscribe`` live re-scan.

    Returns ``(deltas, removed_ids)`` -- whole-row ``delta`` entries for ids that
    are new or whose content changed, and the ids present before but gone now.
    Whole-row granularity (per the effort's Phase B decision) keeps the protocol
    trivial: the consumer replaces/removes by id. Pure + order-preserving."""
    prev_by = {str(e.get(id_key)): e for e in prev if e.get(id_key) is not None}
    curr_by = {str(e.get(id_key)): e for e in curr if e.get(id_key) is not None}
    deltas = [e for e in curr
              if e.get(id_key) is not None
              and prev_by.get(str(e.get(id_key))) != e]
    removed = [rid for rid in prev_by if rid not in curr_by]
    return deltas, removed
