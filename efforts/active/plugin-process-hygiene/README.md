# Plugin Process Hygiene — Concurrent Sessions + Mid-Flight Updates

- **Slug:** `plugin-process-hygiene`
- **Repo:** copilot-extensions (plugin + control-plane home; PR-required `main`, self-merge)
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-08-18
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends [`visions/plugin-services`](../../../visions/plugin-services/README.md)
  — **vision-extending**: adds the `single-instance-lease` and
  `work-coalescing-singleton` behaviors (written in first, Phase 1). Also
  **closes** [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md)
  §*The warm-cache accelerator — optional, on-demand, refcounted, losable* (the
  resident tracker), which is stated-but-unbuilt.
- **Umbrella issue:** #736
- **Sub-issues:** #737
  (lease + reaper primitive · enabler),
  #738 (bridge
  strand/churn), #739
  (worktrees resident tracker),
  #740 (launcher
  no-block), #741
  (mcp version GC), #742
  (marker atomicity), #743
  (vault cutover), #744
  (coalescing tier + mcp multiplexer).
- **Related:** #625 (lifecycle pecking order), #438 (bridge cutover-on-update),
  #396 (dispatch hot-reconciled supervision), #229 (worktree state store).

## Guiding Intent

The runtime plugin suite is now routinely operated at a point it was not
originally tuned for: **many concurrent worktree sessions on one host** (order of
5–10), with **frequent mid-flight plugin updates** — every new session launch may
re-run a service's `start` and trigger a reinstall while other sessions are live.
The architecture already handles this *by design* (immutable versioned slots, a
tested zero-downtime cutover, cheap session hooks, liveness-reconciled routing).
The remaining gaps are **hygiene**: a few places where processes are spawned but
not fully reaped, where a cutover leaks its predecessor, or where the launch path
blocks on an update. This effort closes them — and gives the suite the two
missing intent-level primitives (a single-instance lease and an optional
work-coalescing tier) that make the fixes principled rather than ad hoc.

The through-line: **consolidate the warm runtime, complete the reaping, and never
block a launch on an update** — while keeping every consolidation strictly
optional with an always-correct inline fallback (à-la-carte independence is
non-negotiable).

## Context

A process-management audit (framework libs + each plugin's install/lifecycle,
cross-checked against a live host running ~7 concurrent sessions) found:

- **Sound and working:** the coordinator plugin's post-cutover reap (a replaced
  predecessor is retired cleanly); immutable versioned slots; the `zdd` cutover
  library and its tests; the rendezvous/routing libs; cheap session-start hooks
  across all plugins except the worktrees launcher.
- **Leaks under concurrency + churn:**
  - The bridge cutover retires only its **direct** predecessor, so repeated
    same-version cutovers (one per session launch) **strand live passives**
    holding a port + memory (#738).
  - The worktrees **per-session status-updater** is a detached durable runner
    that is not re-asserted on update and not reaped on session end, and it
    **pins old version slots alive** (blocking GC) (#739).
  - The launcher **joins/applies a plugin update in the launch path**, so bursts
    of session launches serialize on the install lock (#740).
  - agent-mcp never **GCs stale versioned installs** (#741), and runs **one heavy
    stdio bridge per session per server** — the dominant process-count/memory
    multiplier (#744).
  - No shared **single-instance lease**; overlap prevention is heuristic (#737).
  - Binstub **newest-slot fallback** can bind the wrong version during a
    marker-absent window mid-swap (#742).
  - agent-vault restarts (not drains) on update, forcing a **re-unlock** (#743).

## Plan

### Phase 1 — Intent (vision + this effort) — *this PR*

- Extend `visions/plugin-services` with **`single-instance-lease`** and
  **`work-coalescing-singleton`** behaviors + Concepts entries + provenance.
- Author this effort; file the umbrella (#736) and sub-issues (#737–#744).
- No implementation; the reviewed intent lands before any code. Reconciled:
  vision-extending (lease + coalescing tier), vision-closing (worktrees resident
  tracker), the rest below-altitude conformance.

### Phase 2 — Shared single-instance lease + reaper primitive (#737)

- New stdlib library beside `zdd`/rendezvous: a host-local, liveness-reconciled
  lease ("one active per service per host") + a reconcile-set reaper (retire every
  own-process not `active`/self, anchored on the promoted port; fail-soft).
- Vendored into consumers the way `zdd` is. Unit tests for acquire/stand-down,
  dead-owner reclaim, and reap-the-strays.
- Coordinates with the in-flight upstream process-spawn-guard work so the guard
  lands **here** (shared) rather than per-plugin.

### Phase 3 — agent-bridge: idempotent start + complete reap (#738)

- Same-version `start` against a healthy active daemon becomes a **no-op** (kill
  the generation churn at the source).
- Post-cutover, invoke the shared reconcile-set reaper (Phase 2) so no
  drained-but-live passive lingers. Fold into the update/restart path per #438.

### Phase 4 — agent-worktrees: no-block launch + resident tracker (#740, #739)

- Launch on the **currently-active** slot immediately; apply any staged update on
  the **next** launch (or hand to the durable service); apply is async +
  lock-guarded (#740).
- Build the **refcounted resident tracker** (closes the agent-worktrees vision
  accelerator): one warm, idle-exiting monitor that coalesces status sweeps for
  all sessions, refcounted per session, reaped on last-consumer exit, with an
  inline poll fallback — retiring the per-session updater fan-out (#739).

### Phase 5 — agent-mcp: version GC + optional multiplexer (#741, #744)

- Call `versioned_runtime.gc()` on successful activation (prune non-current,
  non-live slots) — apply the same convention suite-wide (#741).
- Promote the optional serve tier into a per-`(host, server)` **multiplexer**
  (one warm runtime + shared upstream; thin per-session stdio shims), **gated by
  identity/credential equivalence**, optional with a direct-bridge fallback
  (#744).

### Phase 6 — Cross-cutting: marker atomicity + vault cutover (#742, #743)

- Atomic `current-version` marker (temp+rename); binstubs prefer last-known-good
  over the newest-slot guess; guess only on true first-run (#742).
- agent-vault adopts the shared drain-safe cutover so a version bump doesn't force
  a re-unlock (#743) — pairs with the #609 clean-room scenario.

### Phase 7 — Reconcile deferred backlog

- Accept process-lifecycle candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- Revalidate accepted technical scope against the current service, lease, and
  cutover contracts; return obsolete or unsafe candidates for explicit
  disposition.
- Place each accepted public tracker item in exactly one existing phase,
  extending this plan before implementation when necessary.
- Keep process evidence synthetic and free of machine-specific values.

## Validation Plan

- **Unit tests** for the lease/reaper primitive (Phase 2) and the marker
  atomicity (Phase 6).
- **Clean-room scenarios** (`tools/clean-room/`) for the install/bootstrap/cutover
  changes — extend the existing bridge/vault cutover scenarios (#609) to assert
  **no stranded passive** after repeated same-version starts, and add a
  worktrees resident-tracker refcount/reap scenario.
- **Field before/after** on a host at the target operating point: total agent-\*
  process count, per-service daemon count (assert exactly one active per service),
  count of coexisting versioned installs, and session-launch latency under a burst
  of concurrent launches.
- **Regression guards:** `check-install-contract.py` clean; version-consistency
  guards green.

## Journal

### 2026-08-18 — Kickoff (Phase 1)

- Audited the framework libs (`versioned-runtime`, `plugin-resolve`, `zdd`,
  `endpoint-rendezvous`, `config-migrate`) and each plugin's install/lifecycle,
  cross-checked against a live host running ~7 concurrent sessions.
- Confirmed the coordinator's post-cutover reap works (a replaced predecessor was
  gone); confirmed the bridge **strands** its previous passive after a
  same-version cutover, and that per-session status-updaters accumulate across
  mixed version paths and pin stale slots.
- Extended `visions/plugin-services` with `single-instance-lease` +
  `work-coalescing-singleton`; filed the umbrella #736 and sub-issues #737–#744.
- Next: Phase 2 (the shared lease + reaper primitive, #737) as the enabler, aligned
  with the upstream process-spawn-guard work so the guard is shared, not
  per-plugin.

### 2026-08-19 — Phases 3-5 landed; Phase 2 primitive extracted; Phase 6 started

Phase 4 & 5 conformance fixes (merged):

- **#741 — agent-mcp version GC on activation** (PR #750). `versioned_runtime.py gc`
  now runs on successful activation, pruning non-current, non-live slots.
- **#739 — agent-worktrees resident status-monitor** (PR #752, then default-on
  opt-out in PR #758). One warm, idle-exiting monitor coalesces the per-session
  status sweeps and is refcounted + reaped on last-consumer exit, retiring the
  per-session status-updater fan-out. Opt-out via
  `AGENT_WORKTREES_STATUS_MONITOR=0`. Realizes the resident-tracker accelerator.
- **#740 — agent-worktrees no-block launch** (PR #755). The marketplace install
  is detached from the launch path (safe because slots are immutable), so a
  burst of session launches no longer serializes on the install lock. Escape
  hatch `WORKTREE_BLOCKING_INSTALL=1`.
- **#738 — bridge stranded-passive leak** — resolved by the generation
  self-retire loop (now default-on / opt-out): a demoted daemon drains and
  exits on its own once a live, strictly-newer generation has taken over, so a
  repeated same-version cutover no longer strands a live passive. The *active*
  reconcile-set reap (retiring strays from the promoted daemon) is available in
  the Phase 2 primitive below and remains to be wired into the cutover path.

Phase 2 — shared primitive extracted (merged):

- **#737 — single-instance-lease** (PR #759). New pure-stdlib shared library
  `libs/single-instance-lease` (vendored like `zdd`) with three pieces:
  `SingleInstance` (an OS-level, liveness-reconciled lease), `is_superseded`
  (the pure fail-safe self-retire decision on a routing-table dict), and
  `reconcile_set_reap` + `superseded_pids_from_table` (the fail-soft reaper;
  process identity is the caller's responsibility, guarding pid reuse).
  agent-bridge's `singleton.py` and `self_retire.py` are now thin adapters over
  it, with the historical lock naming and call shapes unchanged. Realizes the
  `single-instance-lease` vision behavior. Adoption into agent-vault (#743) and
  wiring the reaper into the active cutover path (#738) build on this next.

Phase 6 — cross-cutting (partial):

- **#742 — marker atomicity + last-known-good** (PR #760). The `current-version`
  marker was already written atomically; this landed the read-side preference
  for the canonical agent-worktrees resolver — a 3-tier resolution
  (marker -> `last-known-good` -> newest slot), with the installer stamping
  `last-known-good` on activate. The hot path is unchanged. Rolling the same
  fallback into the other plugins' inlined binstubs is tracked follow-up on #742.

Remaining: Phase 5's optional agent-mcp **multiplexer** (#744, the dominant RAM
consumer — one heavy stdio bridge per session per server collapses to a thin
forwarder + one shared `serve` session-map process); Phase 6's agent-vault
**drain-safe cutover** (#743, now unblocked by the #737 lease primitive); and the
per-plugin last-known-good rollout (#742).

### 2026-09-02 — Phase 4 performance conformance (#918)

- Audited the effective Copilot hook surface and found agent-worktrees launching
  three Python guards before every tool plus two post-tool command trees after
  every successful tool. The bind nudge imported the complete CLI for a tiny
  advisory decision, costing about 2.5 seconds per post-tool event on Windows.
- Consolidated the five commands into one tiny Python client per event class.
  It makes one bounded, token-authenticated request to the resident monitor over
  a dynamic `127.0.0.1:0` endpoint advertised through the monitor's atomic
  liveness-lock/rendezvous record.
  If the monitor is absent, pre-tool safety guards still run together in one
  process; advisory post-tool fallback stays lightweight.
- Promoted the monitor into the warm policy/state owner: delegated roots resolve
  directly from the loaded topology and repo registry (no nested CLI fan-out),
  anchor and topology inputs are cached, nudge lookup uses the active project's
  tracking directory, and bind-nudge is now an in-process decision helper.
- Added a shared status-segment cache with a 60-second maximum age and targeted
  invalidation after mutating tool events. Read-only shell commands remain cache
  hits, multiple sessions on the same worktree share one Git classification,
  and unchanged mux option values are no longer republished every sweep.
- A 20-process local benchmark of the final pre-tool client measured 76.6 ms
  median / 108.7 ms p95, replacing three separate roughly 109-130 ms guard
  launches. The full post-tool CLI import is removed from the hook path.
