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

import os
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


def _short_repo(repository: str) -> str:
    """The trailing path segment of an ``owner/name`` repo id (display only)."""
    return repository.rsplit("/", 1)[-1] if repository else repository


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
        friendly = m.display_name or m.name
        # The claiming worktree's short id: the cross-machine beacon (the 4-hex
        # borrowing-worktree id) when held elsewhere, else the local lease's
        # effort id, else a #897 claim's owner worktree dir name (3b -- so a
        # claim-held box surfaces WHICH worktree locks it, not a blank). The
        # Picker correlates this to the worktree's TASK title.
        worktree = m.beacon or m.holder_effort or _worktree_dir_id(m.holder_worktree)
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
        entries.append({
            "id": m.name,
            "name": m.name,            # durable GitHub-assigned id
            "display": friendly,       # friendly display name (falls back to id)
            "group": group,            # repo @ account (section grouping)
            "status": status,          # RUNNING / STALE / STOPPED (compact STATE)
            "worktree": worktree,      # claiming worktree short id (-> TASK title)
            "subtitle": subtitle,      # optional 2nd line (durable id + claim)
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
