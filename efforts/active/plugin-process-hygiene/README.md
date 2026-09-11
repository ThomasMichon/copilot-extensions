# Plugin Process Hygiene — Concurrent Sessions + Mid-Flight Updates

- **Slug:** `plugin-process-hygiene`
- **Repo:** copilot-extensions (plugin + control-plane home; PR-required `main`, self-merge)
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-08-18
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends [`visions/plugin-services`](../../../visions/plugin-services/README.md)
  — **vision-extending**: adds the `single-instance-lease` and
  `work-coalescing-singleton` behaviors (written in first, Phase 1), plus the
  least-privilege lifecycle tier, stable register-once launcher, and disposable
  payload guarantees (#625); PR #2300 further extended the same vision with
  `process-count-scales-with-services-not-sessions` and
  `hooks-and-callbacks-are-transient` — the invariant Phase 4b's design work
  now generalizes into a shared client shape. Also
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
  (coalescing tier + mcp multiplexer), #1836
  (vault user-mode ensure + stable launcher/register-once), #1837
  (dispatch register-once under elevated update), #1841
  (declare service lifecycle tiers and reconcile user-mode ensure), #2301
  (audit + close reality gaps against the process-count-scales-with-services-not-sessions
  invariant — Phase 4b(ii)'s per-plugin audit target), #2323
  (resident accelerator daemon for classify/list, thin-client protocol — Phase 4d).
- **Lifecycle-policy slice:** #625 (lifecycle pecking order and conformance
  audit).
- **Related:** #438 (bridge cutover-on-update),
  #396 (dispatch hot-reconciled supervision), #229 (worktree state store),
  #918 (resident status monitor), #1788 (session lifecycle hook coalescing),
  #2259 (agent-dispatch supervisor self-update, landed default-on via #2266/#2280),
  #2300 (the vision extension this sub-phase's design realizes),
  #2315 (Phase 4c design), #2322 (Phase 4c implementation — the classify-pass
  lease Phase 4d's daemon narrows to a fallback path, not supersedes),
  #2319 (vision revision naming the thin-client/ref-counted-subscriber
  expectation Phase 4d realizes).

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

### Phase 1c — Lifecycle supervision policy and conformance audit (#625)

- Extend `visions/plugin-services` with the least-privilege lifecycle pecking
  order, stable register-once launcher, cutover-on-update, and disposable
  payload guarantees.
- Extend `service-lifecycle-supervision` with the four-tier decision rule, the
  stable-launcher update path, and the Windows process-current-directory trap.
- Audit agent-dispatch, agent-bridge, and agent-vault against those guarantees.
  Record conforming evidence, absorb gaps into an existing tracker where one
  exists, and file a focused follow-up only for an untracked gap before closing
  #625. Treat agent-worktrees #1550 as the already-closed hook-launch precedent
  for the payload-working-directory rule. Track the pattern's newly required
  per-plugin tier declarations and user-mode-ensure adoption in #1841.
- Keep this slice intent/documentation-only until its reviewed PR lands; any
  conformance implementation follows in later PRs under the tracked gaps.

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

### Phase 4b — agent-worktrees: coalesced session lifecycle hooks (#1788)

- Replace the nine independent `sessionStart` command trees with one bounded
  lifecycle client path for behavior that can safely reuse the resident
  monitor's warm project, repository, and session state.
- Preserve project-owned session-start hooks, provisioning, registration,
  anchor hygiene, and marketplace reconciliation semantics without repeatedly
  importing the full CLI or performing nested repository discovery.
- Keep a bounded monitor-down fallback that preserves required registration and
  safety behavior, and retain cross-platform parity for hook ordering and
  side-effect-only context production.
- Measure the remaining extension-host and lifecycle-event surface after
  deployment; route any separate hot path to its own issue rather than widening
  the combined client into an unbounded startup coordinator.

### Phase 4b(ii) — Convergence design + adversarial mock (#2300, #2301)

- Generalize agent-worktrees' own bounded-client shape (Phase 4b, above) into
  a suite-wide **transient hook client contract** every plugin's hooks and
  extension callbacks can follow, realizing the two Behaviors PR #2300 added
  to `visions/plugin-services`.
- Design + the adversarial mock harness that validates the contract in the
  abstract (concurrent-flood, absent-daemon, mid-flight-appearance,
  daemon-death, and daemon-election scenarios) **before** any per-plugin code
  change — full design in
  [`adversarial-convergence-mock.md`](adversarial-convergence-mock.md).
  **Landed**: `tools/clean-room/scenarios/plugin-process-hygiene-convergence/`
  (6/6 scenarios PASS locally).
- Feed the mock's evidence, plus #2301's per-plugin audit citations, into a
  scoped decision on which plugin (if any) needs an actual code change versus
  already conforming (agent-worktrees post-#918/#1788 is the reference
  implementation).
- No plugin code changes in this sub-phase; the design doc + mock harness are
  the deliverables, each cleared through the review gate before the next
  lands.

### Phase 4c — agent-worktrees: single-instance lease over the classify pass

- **Problem, evidence-grounded.** `_classify_records` (the ~5-git-calls-per-
  worktree batch classification) is the shared entry point behind BOTH the
  Picker's in-process `data_local.load(classify=True)` Phase 2 AND a standalone
  `list --json --classify` CLI invocation. Nothing today keeps two callers from
  running it **concurrently for the same project** — e.g. an open Worktree
  Manager's own populate racing an independently launched `resolve`/`list
  --classify` (observed live: two distinct process trees, different parent
  PIDs, touching the same tracking directory at once). `_RecordLock` prevents
  torn writes, but not duplicated work, and a caller that arrives mid-pass has
  nothing to wait on today.
- **Symptom this produces.** While a project's `state` is transiently
  unavailable to a reader (mid-race, or any other cause), `derive._state()`'s
  fallback (`status == "active" -> WIP if turn_count > 0`) renders a **freshly
  invented, potentially wrong** interim label instead of the row's last-known
  correct value — the "cache renders, then jumps to something completely
  different" flicker an operator can observe in the Picker.
- **Fix, placement decided.** Wrap the batch classify pass — at
  `_classify_records`, the lower/shared entry point, so both the Picker and any
  standalone CLI classify call inherit it automatically — with a **per-project**
  `single_instance_lease.SingleInstance` (Phase 2's primitive; a short-lived
  critical-section use, not a persistent daemon lease). Keyed on the project's
  tracking dir, mirroring the existing lease's `port`-keying idiom for "two
  processes, same identity, must not run set at once."
- **Contention policy, chosen deliberately (do not default to skip).** A caller
  that loses the race must **wait (bounded, on the order of a typical classify
  pass) for the winner to finish, then re-read the now-repaired session-render
  cache** — never fall back to computing its own competing/incomplete answer,
  and never simply skip-and-render-stale (that reproduces the exact reported
  symptom: the loser renders a worse answer and never gets the winner's
  repair). This realizes the operator's framing directly: one process does the
  (more expensive, authoritative) recompute; every other caller's job is to
  read the result it produces, not race it.
- **Scope discipline.** This is a narrow, single-consumer application of the
  Phase 2 primitive to a real, evidence-grounded race in agent-worktrees's own
  classify path — not a new cross-cutting cache layer, and not the same
  problem Phase 4b(ii) governs (that phase is about hook/callback transience;
  this is about two independent *batch classification* callers).
- **Landed:** design (#2315) then implementation (#2322) — both merged.
  ``_classify_records`` acquires the per-project lease; a losing caller waits
  (bounded, default 15s) then answers from `_classify_from_cache` (a fresh
  disk re-read, never a guessed state); the lease is advisory-only (any
  lease-layer failure degrades to classifying live, unchanged from before this
  phase). See Phase 4d below for the larger direction this narrow fix is a
  down payment on.

### Phase 4d — agent-worktrees: resident accelerator daemon for classify/list (#2323)

- **Relationship to Phase 4c.** Phase 4c's lease serializes two racing
  classify callers but does not eliminate the second caller's cost — the
  winner still pays the full live classification, and every caller still
  boots its own process, resolves its own config, and reasons about its own
  cache. This phase builds the steady-state target PR #2319's vision revision
  now states explicitly: a reachable resident accelerator that **owns** the
  computation, with ordinary readers (not only session-lifecycle hooks)
  reaching it as thin, ref-counted subscribers. Phase 4c's lease is **not
  superseded** — it remains the correct fallback/degraded path for exactly
  the case this phase's daemon is unreachable or absent.
- **Placement.** Extend `hook_ipc.py`'s existing token-authed TCP surface
  (currently scoped to `session-lifecycle-v1` hook decisions only) with a new
  request kind for list/classify data. The resident status-monitor
  (`cmd_status_monitor` — already single-active via a liveness lock, already
  idle-exits on no demand, already self-retires on supersession) becomes the
  daemon that answers it, rather than a second, purpose-built process.
- **Client shape.** An ordinary caller (CLI `list`/`get`, the Picker) becomes
  a thin client: resolve identity, boot the monitor on demand if none is
  reachable (waiting for it to publish its address), request, read, exit. The
  daemon's own lifetime is governed by **explicit subscriber ref-counting**
  plus a bounded linger — not the TTL/demand-registration heuristic
  `list_cache.py` uses today, which infers demand rather than counting live
  subscribers.
- **The cold-start budget constraint (flagged during design, must not be
  dropped in implementation).** `hook_ipc`'s existing request read timeout is
  1 second — correct for a hook decision, far too short for a cold
  classification pass (which can take seconds for a worktree-heavy project).
  The client's boot-wait-request sequence needs its own, much larger timeout
  budget, with an explicit fallback to today's direct path (Phase 4c's lease,
  or plain live classification when the lease is also unavailable) when no
  daemon becomes reachable in time. A first caller after an idle-exit must
  never be worse off than before this phase existed.
- **Validation.** Extend the adversarial mock harness (Phase 4b(ii),
  `tools/clean-room/scenarios/plugin-process-hygiene-convergence/`) with new
  scenarios: cold-boot-and-wait-for-port, concurrent-callers-during-cold-boot
  (exactly one boots, the rest wait, all receive the correct answer),
  ref-counted-exit (the daemon lingers until the last subscriber's grace
  period elapses, then exits), and the fallback-to-direct-computation path
  firing correctly when the boot-wait times out.
- **Sequencing.** Design-first per this effort's standing convention: a
  reviewed design PR (naming the exact request/response wire shape, the
  ref-count/linger algorithm, and the timeout budgets) lands before any
  plugin code change.

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
- **Session lifecycle performance** for Phase 4b: focused composition, timeout,
  IPC, and monitor-down fallback tests; process-count and wall-time benchmarks
  proving that the healthy path uses one client launch instead of nine command
  trees; deployed multi-session measurements after cutover.
- **Regression guards:** `check-install-contract.py` clean; version-consistency
  guards green.
- **Lifecycle-policy checks (#625):** docs-consistency guards pass; each named
  plugin has cited evidence for stable launcher, register-once update,
  cutover-on-update, and payload-working-directory safety; every failed item is
  represented by an existing or newly filed public follow-up issue. The new
  declared-tier/escalation-rationale requirement is adopted through #1841 rather
  than treated as evidence that predates the pattern.
- **Adversarial convergence mock (Phase 4b(ii), #2300/#2301):** the six
  scenarios in
  [`adversarial-convergence-mock.md`](adversarial-convergence-mock.md) —
  `flood-against-live-daemon`, `flood-against-absent-daemon`,
  `daemon-appears-mid-flood`, `daemon-dies-mid-packet`,
  `concurrent-daemon-race`, and `process-count-invariant-under-repeated-floods`
  — all PASS, with zero leftover processes (of either the mock daemon or the
  transient client) after the full suite, verified by an OS process census
  before/after (the same technique that found the original
  11-coordinator/68-conhost finding). Per-plugin conformance against the mock's
  validated contract is #2301's own audit, cited here once complete rather than
  re-validated.
- **Classify-pass single-instance lease (Phase 4c):** a concurrent-caller test
  driving two threads/processes through `_classify_records` for the same
  project at once, asserting (a) exactly one performs the actual git
  classification, (b) the other blocks and then observes the winner's
  repaired session-render cache rather than computing (or falling back to) its
  own answer, and (c) neither corrupts the tracking YAML (existing
  `_RecordLock` coverage extended, not replaced). Live reproduction case:
  Picker populate racing an independently-launched `list --json --classify`.
  **Landed** (#2322): 11 new tests plus the existing `TestClassifyRecordsConvo`
  suite unchanged.
- **Resident accelerator daemon (Phase 4d, #2323):** four adversarial mock
  scenarios extending Phase 4b(ii)'s harness — cold-boot-and-wait-for-port,
  concurrent-callers-during-cold-boot, ref-counted-exit-after-linger, and
  fallback-to-direct-computation-on-boot-timeout — each a PASS/FAIL check with
  zero leftover processes, matching this effort's existing validation
  discipline.

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

### 2026-09-02 — Phase 4b session lifecycle audit proposal (#1788)

- The post-deployment baseline showed three dedicated plugin extension hosts per
  active Copilot session (`agent-worktrees`, `agent-bridge`, and
  `context-handoff`), each carrying a full extension-host process. Resident
  `agent-worktrees` monitor idle CPU remained low.
- `agent-worktrees` still declares nine separate `sessionStart` commands.
  Synthetic warm-runtime invocation through the same separate PowerShell
  process boundary measured about 21 seconds in aggregate. The largest paths
  were project-hook discovery (~8.4 seconds), session registration
  (~4.7 seconds), and provisioning preview (~3.0 seconds); each imported or
  discovered state independently.
- Filed #1788 and added Phase 4b before implementation. The next slice is to
  design the smallest combined lifecycle request and explicit monitor-down
  fallback, then land focused tests and deployed before/after evidence.

### 2026-09-03 — Lifecycle supervision policy and conformance audit (#625)

- Extended the plugin-services vision with a four-tier, least-privilege
  lifecycle hierarchy: user-mode ensure/auto-run, scheduled activation, system
  service, and consumer-selected container management. Added the stable
  register-once launcher, cutover-on-update, and disposable-payload guarantees.
- Extended `service-lifecycle-supervision` with the capability-based tier
  decision, alignment to the install contract's non-elevated user-mode ensure,
  stable runtime-resolving launchers, and the Windows distinction between
  PowerShell provider location and the inherited Win32 process cwd.
- Audited the three service exemplars:
  - **agent-bridge:** conforms to #625's original four checks: stable launcher,
    register-once, cutover-on-version-update, and payload-cwd safety. Its
    documented stop-and-swap path is only the permitted cutover-failure
    fallback.
  - **agent-dispatch:** the coordinator's stable launcher, ZDD cutover, and
    payload-cwd safety conform. The supervisor task also uses the stable
    launcher, but an ordinary update launched from an elevated shell can still
    force-register an already-correct task; filed #1837.
  - **agent-vault:** payload-cwd safety conforms. Its start path is gated on
    Scheduled Task registration, its lifecycle definitions embed concrete
    runtime slots, and they are rewritten during updates; filed #1836. Its
    separate drain-safe cutover gap remains tracked by #743.
- None of the three architecture docs yet declares the selected tier,
  availability contract, platform mapping, and escalation rationale required by
  the revised pattern. Filed the cross-plugin adoption pass as #1841.
- Self-staged installer copies are intentionally disposable and do not count as
  the original marketplace payload: each audited installer first releases the
  original payload cwd before running from its external staging copy.
- Next: land this reviewed intent/pattern PR, report the evidence and follow-up
  trackers on #625, then close #625 while #1836, #1837, #1841, and #743 carry
  the remaining implementation.

### 2026-09-09 — agent-dispatch supervisor self-update + Phase 4b(ii) design

Landed independently of this effort's own PR sequence, then reconciled into it:

- **#2259 — agent-dispatch supervisor self-update** (PR #2266, then flipped
  default-on + clean-room validated in PR #2280). The `supervise serve`
  singleton daemon had no live version-staleness check, unlike the coordinator
  — a scheduled-task-launched daemon with no periodic trigger could run stale
  indefinitely. Landed opt-in first, then corrected to **default-on / opt-out**
  (`AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE=0`) once it became clear this
  harness's launch paths have no protocol to ever flip an opt-in flag before a
  daemon's first boot — an opt-in gate here would simply never activate for a
  real operator. Validated end-to-end (real spawn, real single-instance lease
  release/reacquire, a genuinely converging successor) by a new Tier-P
  clean-room scenario, `agent-dispatch-supervisor-self-update`, before
  defaulting on.
- **PR #2300 — vision extension.** Grounded in a live process census (11
  separate `agent_dispatch serve` coordinators, 68 `conhost.exe` on one dev
  box) that surfaced while drafting this: extended `visions/plugin-services`
  with **`process-count-scales-with-services-not-sessions`** and
  **`hooks-and-callbacks-are-transient`**, and cross-referenced the invariant
  into the agent-dispatch, agent-bridge, agent-ssh, and agent-worktrees
  visions (the four plugins the operator named as owning exactly one per-host
  daemon). Also fixed a real leak found while grounding the vision text: the
  new clean-room probe's teardown never killed the coordinator it autostarts
  for each isolated HOME (only processes matching its own `--machine` tag),
  now fixed and verified (process count identical before/after a full probe
  run).
- **Reconciling #2301 (filed to track the census/audit work) against this
  effort's own history** — most of what #2301 asks for auditing is **already
  landed here**, just not yet cited back to #2301:
  - #739 (this effort) already landed the agent-worktrees resident
    status-monitor, default-on / opt-out — the exact "exactly one status
    process regardless of session count" guarantee #2301 asks to confirm.
  - #738 (this effort) already resolved the bridge stranded-passive leak via
    generation self-retire (default-on / opt-out) — no code change needed
    there for #2301's ask.
  - #737 (this effort) already landed the shared `single-instance-lease` +
    reaper library agent-bridge's own singleton guard is built on.
  - The 2026-09-02 Phase 4b entry (this effort) already measured and fixed
    agent-worktrees' hook-coalescing (nine command trees → one bounded
    client, 76.6ms median, monitor-down fallback) — this **is**
    `hooks-and-callbacks-are-transient` in practice, with deployed evidence.
  - **Still genuinely open** from #2301: root-causing the *real* (not
    probe-leak) coordinator/conhost proliferation on a live box; confirming
    agent-ssh's `dtssh host --persist` path never starts a second instance;
    and generalizing the *client-side* half of the contract (agent-dispatch,
    agent-bridge, agent-ssh hooks/callbacks reaching their respective
    daemons) the way agent-worktrees already did for its own hooks.
- Added **Phase 4b(ii)** to this effort's Plan: a suite-wide transient-hook-
  client design (generalizing agent-worktrees' own Phase 4b shape) plus an
  adversarial mock harness that validates the contract in the abstract before
  any further per-plugin code change. Full design in
  [`adversarial-convergence-mock.md`](adversarial-convergence-mock.md).
- Next: land this reviewed plan (README + design doc), then build the mock
  harness as its own PR per the review gate, then use its evidence plus a
  narrowed #2301 (comment reconciling the above) to scope any remaining
  per-plugin work.

### 2026-09-09 (later) — Adversarial mock harness landed

- Built `tools/clean-room/scenarios/plugin-process-hygiene-convergence/`: a
  Tier-P, stdlib-only, plugin-agnostic clean-room scenario implementing the
  design's six scenarios against a synthetic mock daemon (no real plugin
  install, no Copilot/gh auth needed). All 6/6 PASS locally, with zero
  leftover processes verified by an OS process census before/after.
- Two real bugs found and fixed **in the harness itself** (not the contract)
  while getting the process-count assertions right:
  - The Windows process-counting helper's own PowerShell query embedded the
    search tag literally in its `-Command` string, so the querying process's
    own command line self-matched the filter, inflating every count. Fixed by
    passing the tag through an environment variable instead of string
    interpolation.
  - PowerShell auto-unwraps a single-element pipeline result to a bare
    scalar object, which has no `.Count` property -- silently printing
    nothing (coerced to 0 by the Python side) whenever *exactly one* process
    matched. Fixed by wrapping the query in `@(...)` to force an array
    regardless of match count. This one is worth remembering generally: any
    future PowerShell-based process census in this suite should force-array
    its `Where-Object` result before reading `.Count`.
- Confirms the contract from the design doc requires no changes; the harness
  needed fixing, not the shape it validates.
- Next: this PR is the mock harness landing per the plan's own checklist;
  #2301's per-plugin audit (citing #737/#738/#739/the Phase 4b measurement as
  already-conforming evidence, and scoping what's genuinely still open) is
  the remaining work this effort's Phase 4b(ii) hands off to.

### 2026-09-09 (later) — Checklist reconciliation

Found the Phase 4b(ii) sub-doc's own checklist a step behind reality while
resuming this effort: PR #2303 (design doc) and PR #2305 (mock harness) were
both already merged, but their own checklist items ("land this design doc",
"land the mock harness as its own PR") were still unticked. Ticked both,
citing the now-merged PR numbers (never the in-flight/self-referential form).
No code or design changes -- purely a bookkeeping catch-up so the next
resumer reads an accurate Plan. The one remaining Phase 4b(ii) checklist item
is unchanged: the per-plugin #2301 audit against the now-validated contract.

### 2026-09-09 (later still) — Phase 4c: classify-pass race found and designed

- **Grounded in a live reproduction**, not a hypothetical: opening a harness
  project's Worktree Manager showed several rows briefly render `WIP`
  then correct themselves to their true state on the very next paint — no app
  reload involved, purely within one open session. Traced to
  `derive._state()`'s fallback (`status == "active" -> WIP if turn_count > 0`),
  which fires whenever a row's `state` field is momentarily absent.
- Disproved the first two hypotheses empirically before settling on this one:
  (1) the session-render-cache write-back (`_stamp_from_raw` /
  `_apply_session_state_stamp`) does land and does so promptly -- confirmed by
  reading a live tracking YAML's `session_state_at` immediately after a
  populate pass matched the observed timing; (2) calling
  `data_local.load(classify=False)` in-process, live, for the exact rows that
  flashed `WIP` returned their correct cached states with no fallback firing --
  so Phase 1 was not the culprit either.
- What *was* found live: a second, independently-parented `agent-worktrees
  ... resolve` -> `list --json --classify` process chain running against the
  same project's tracking directory at the same moment as the Picker's own
  populate -- i.e. two callers of the shared `_classify_records` batch
  classification, for the same project, concurrently, with nothing serializing
  them.
- Added **Phase 4c** to this effort's Plan: apply the Phase 2
  `single-instance-lease` primitive (already vendored, already proven by
  agent-bridge/agent-vault) as a **short critical-section lock** around
  `_classify_records`, per-project, so a second concurrent classify caller
  waits for the winner's pass and re-reads its cache rather than computing (or
  worse, falling back to a wrong heuristic for) its own answer.
- Design-only entry; no plugin code in this PR, per the effort's standing
  convention. The follow-up implementation PR wraps `_classify_records`,
  extends `TestControlPlaneRelatedPRTier`-adjacent coverage with a
  concurrent-caller test (two threads/processes racing the same project,
  asserting the loser observes the winner's repaired cache rather than
  computing its own), and updates the Picker/`cmd_list` call sites only if
  either needs the wait-vs-immediate-return choice made explicit at its layer.

### 2026-09-09 (later still) — Phase 4c implementation landed; vision strengthened; Phase 4d scoped

- **Phase 4c implementation landed** (#2322): `_classify_records` now
  acquires the per-project lease before `_classify_records_live` (the
  original body, factored out unchanged); a losing caller waits (bounded,
  default 15s) then answers via the new `_classify_from_cache` (re-reads each
  record fresh off disk so the winner's stamp is visible, never guesses a
  state); the lease is advisory-only, degrading to live classification on any
  non-contention failure. 11 new tests
  (`tests/test_classify_lease.py`); the existing `TestClassifyRecordsConvo`
  suite required no changes.
- **Vision strengthened in the same sitting** (#2319): the operator's own
  framing of the target end-state — a debounced, globally-reused daemon that
  ref-counts subscribing callers and exits after both its work and its
  subscriber count go to zero — was checked against the visions first, per
  the operator's explicit instruction ("there should be a vision pushing for
  this already"). It mostly wasn't: `visions/plugin-services`'s
  `hooks-and-callbacks-are-transient` was scoped to Copilot-invoked hooks
  only, and `visions/plugins/agent-worktrees`'s "Derived status" section
  described the resident monitor as merely optional. Both were strengthened:
  the hook behavior generalizes to any repeated caller of a
  work-coalescing-singleton, and agent-worktrees now states the thin-client/
  ref-counted-subscriber/bounded-linger expectation explicitly, naming direct
  computation as the correct degrade path rather than the steady-state
  target.
- **Phase 4d added** (#2323): the daemon architecture itself — a new request
  kind on `hook_ipc.py`'s existing TCP surface, the resident status-monitor
  as the daemon, explicit subscriber ref-counting replacing today's TTL/
  demand-inference, and (flagged during design, load-bearing for
  implementation) a client-side cold-start timeout budget separate from
  `hook_ipc`'s existing 1s hook-decision timeout, with an explicit fallback to
  Phase 4c's lease (not superseded) when no daemon is reachable in time.
  Design-only; a reviewed design PR precedes any plugin code, per this
  effort's standing convention.

### 2026-09-09 (later still) — Phase 4b(ii)'s per-plugin audit landed on #2301

Closed Phase 4b(ii)'s last open checklist item: a direct-read audit of
agent-worktrees, agent-bridge, agent-dispatch, and agent-ssh against the
mock-validated transient-hook-client contract, posted as a
[#2301 comment](https://github.com/ThomasMichon/copilot-extensions/issues/2301#issuecomment-5613535208)
rather than re-litigated here. Verdicts: **agent-worktrees CONFORMS**
(`hook_client.py` never spawns; `_ensure_status_monitor` is off the hook path
entirely, confirmed by grep); **agent-bridge PARTIAL** (its
`bootstrap-check.ps1` background-spawn was fixed same-day by #2317,
`write-session-guidance.ps1` left unverified); **agent-dispatch DEVIATES**
(confirmed, unfixed — same ungated `Start-Process -FilePath 'conhost.exe'`
shape #2317 fixed elsewhere); **agent-ssh SPLIT** (same hook deviation, but
its actual persistent daemon, `dtssh-host-launcher.ps1`, conforms via a named
Mutex). The 8 further sibling plugins #2317 named are explicitly flagged as
*that PR's* claim, not independently re-verified by this audit — the comment
is deliberately careful not to let an unverified list masquerade as a
finding.

Resolution recorded there and here: the confirmed deviations are **not**
fixed by fanning out #2317's opt-in-gate pattern to each sibling. A new
effort, [`tiered-payload-provisioning`](../tiered-payload-provisioning/README.md)
(created earlier the same day from an operator critique that gating "only
changes who pays the cost, not whether it's expensive"), removes the
expensive hook-triggered path entirely via a unified, serialized/debounced
stamp-now/provision-on-first-use model applying to every version update. Its
Phase 3 is where agent-dispatch's and agent-ssh's confirmed deviations
actually get fixed — this effort's Phase 4b(ii) is now fully closed, with
implementation handed off rather than duplicated into a new phase here.

### 2026-09-10 — Worktree-scoped registrar pointer: a distinct root cause (#2417)

A live incident on a shared dev box ("agent-dispatch ran amok spawning headed
processes") led to a newly-diagnosed, **distinct** contributing bug — related
to this effort's theme but not part of the Phase 4b(ii) hook-transience audit
above, which is already closed:

- **Root cause**: `agent-dispatch reviewer-loop setup` / `repository-issue-loop
  setup` (and their shared `status`/`doctor`/`enable`/`disable` declaration
  loader) derived a declaration's registration **owner** from raw filesystem
  path structure (`repo_root.name`), with zero awareness of worktree-vs-anchor
  identity. Run from inside a worktree checkout instead of the repo's
  registered anchor, this silently registered a **permanent global pointer**
  (in `~/.agent-dispatch/registrar/pointers.json`) scoped to that worktree's
  ephemeral directory name. Because the resulting registration id is unique
  per worktree, it never reconciled with prior registrations, so every
  occurrence spawned a **brand-new set of headed reviewer-loop workers** that
  worktree deletion never cleaned up.
- Traced the exact trigger: the `missing-pointer` doctor diagnostic literally
  suggests `agent-dispatch reviewer-loop setup <relative-path>` as its fix
  action — an agent following that suggestion from inside a worktree is
  exactly how this fires. Confirmed via evidence on the affected machine: a
  `dotfiles` worktree's declared registrations were already disabled (by a
  different agent noticing the runaway spawns) and the bad pointer entry was
  already removed from `pointers.json` — contained, but the code path that
  produced it was still live.
- Filed as issue #2417 with full code-level root cause and fix directions.
- **Landed the fix** (this entry's own change): added
  `_reject_worktree_checkout_as_repo_root()`, wired into both
  `_reviewer_loop_declarations` and `_repository_issue_loop_declarations` (the
  shared chokepoint both `setup` paths and every other reviewer-loop/
  repository-issue-loop subcommand funnel through). Deliberately a **cheap,
  dependency-free path-pattern check** (does the parent directory name end in
  `.worktrees`, this harness's own worktree-root naming convention) rather
  than an authoritative subprocess probe out to `agent-worktrees`: an early
  subprocess-based version of this guard measured ~9s per invocation on this
  loaded box, which would have made every reviewer-loop CLI call slow exactly
  when the host is already struggling — the wrong tradeoff for what should be
  a fast safety check. Added a regression test
  (`test_setup_refuses_worktree_checkout_path`) proving a worktree-scoped
  declaration is refused rather than silently registered. Full agent-dispatch
  suite: 2410 passed, 3 pre-existing unrelated failures (bootstrap/session-
  guidance tests, confirmed untouched by this diff), 8 skipped.
- **Extended `visions/plugin-services`** with the operator-directed strong
  invariant this bug violates: **`identity-resolves-by-name-not-path`** — the
  only place a repo's current filesystem path may ever be recorded is its
  owning registry (e.g. `repos.yaml`/`projects.yaml`); every other component
  (a registrar pointer, a registered task's repo binding, a scheduled-task
  action) carries the repo's **name** and resolves the path fresh at the
  point of use. Added companion Behaviors `refuse-not-silently-misidentify`
  and `registered-tasks-target-by-name`. Cross-referenced from
  `visions/plugins/agent-worktrees` (the canonical name-to-path registry
  owner) and `visions/plugins/agent-dispatch` (the registrar pointer
  convention this incident hit directly).
- Not yet done: a broader audit of "task registrations specify at most agent
  or repo names" and "scheduled tasks only invoke installed binstubs" across
  the rest of the suite, per the operator's full ask. The scheduled-task half
  is likely already covered by the existing `Stable lifecycle launcher`
  concept + `register-once-cutover-on-update` behavior (audited for
  agent-bridge/agent-dispatch/agent-vault under #625), but that audit
  predates this new, more general invariant and should be re-checked against
  it explicitly as a follow-up.

### 2026-09-11 — Scheduled-task binstub audit re-checked against `identity-resolves-by-name-not-path` (#736 follow-up)

Re-confirmed the #625 scheduled-task audit against the new invariant, by
citing the concrete `Action` construction for each service exemplar:

- **agent-bridge** (`scripts/install.ps1` `Register-ScheduledTask_`): action
  is `conhost.exe --headless pwsh -File "$launcherPath"` where `$launcherPath`
  is the stable `start-agent-bridge.ps1` written into `$InstallDir` (never a
  versioned slot path). The launcher body itself resolves the active version
  from the `current-version` marker at **every run** ("SINGLE routing point
  ... never a pinned path").
- **agent-dispatch** (coordinator + supervisor + embody-supervisor tasks):
  all three actions are `conhost.exe --headless powershell.exe -File
  "$launcher"` with a stable launcher in `$InstallDir`
  (`serve-service.ps1`/`supervise-service.ps1`); each launcher re-resolves
  `$_py` from the `current-version` marker via the shared
  `resolve-runtime.ps1` chain at every run, matching agent-bridge's pattern.
- **agent-vault** (`Register-AgentVaultTask`): action is `conhost.exe
  --headless powershell.exe -File "$TaskLauncher"`, `$TaskLauncher` stable in
  `$InstallDir`. Its slot python IS baked into the launcher body's `& '<path>'`
  line at registration time rather than re-resolved per run -- a real but
  **already-tracked** deviation (filed as #1836 during the original #625
  audit; the task is re-registered on every install/update so it does not go
  stale between reinstalls, but it is not per-run dynamic like the other two).
  No new gap beyond #1836.
- Spot-checked the 3 other plugins that also register Scheduled Tasks
  (agent-logger, agent-codespaces, agent-index): all follow the identical
  `conhost.exe --headless ... -File "<stable $InstallDir launcher>"` shape,
  no raw versioned-slot or worktree path in any `Action`.

**Conclusion: no new conformance gap.** All scheduled-task actions target a
name-stable, installed launcher, never a worktree-scoped or raw versioned-slot
path; agent-vault's known per-update (not per-run) slot resolution remains
tracked separately by #1836.

Also re-verified #2417/#2421 has no coverage gap: traced both
`_reviewer_loop_setup` and `_repository_issue_loop_setup` -- each calls its
sibling `_..._declarations()` helper (which invokes
`_reject_worktree_checkout_as_repo_root`) **before** its own `rd.add_pointer`
call, so a worktree-checkout path is refused before any pointer is persisted.
Manually reproduced against a `dotfiles.worktrees\<id>\.agent-dispatch\
registrar\reviewer.json` declaration: `reviewer-loop setup` returns non-zero
with the expected refusal message; no pointer written. `repository-issue-loop
setup`'s existing regression test (`test_setup_refuses_worktree_checkout_path`)
passes. Grepped the rest of `agent_dispatch` for other
`repo_root_from_surface_path`/`f"repo:{...root.name}"` call sites: none found
outside the two guarded declaration helpers.

**Live daemon-status re-check:** `agent-dispatch supervise daemon-status`
still shows the original 6 `declared:repo:tmichon-cloud1-win-20260910-171507-5474:*`
/ `logical:repo:...` override entries from the #2417 incident, but all are
`disabled: true` with `at` timestamps (~2026-09-11T00:31Z) that **predate**
#2421's merge (2026-09-11T02:57Z) -- confirming these are the original
contained incident, not a recurrence. No new `declared:repo:<worktree-id>:...`
entries have appeared since the fix landed.

This closes out the scheduled-task-binstub audit slice of the operator's full
ask; the #736 umbrella's own remaining sub-issues (#738, #742, #743, #744)
are unrelated open work tracked separately above.
