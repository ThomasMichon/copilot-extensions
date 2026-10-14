# Continuous Worktree Reclamation

- **Slug:** `continuous-worktree-reclamation`
- **Repo:** copilot-extensions
- **Branch(es):** serial proposal, then separately authorized implementation PRs.
- **Created:** 2026-10-13
- **Status:** Draft
- **Vision:** `visions/plugins/agent-worktrees/continuous-reclamation/README.md`
- **Umbrella issue:** #6620
- **Dependencies:** #1488, #1918, #3114; coordinate #6060 and #6471/#6542.

## Guiding Intent

Collect safely disposable worktrees as work completes, across locally registered
repositories, preferably through the existing agent-worktrees daemon. Establish
and improve throughput while preserving lifecycle safety and interactive latency.

**Current authorization is vision and planning only.** Landing this proposal
does not authorize runtime implementation, automatic collection activation,
changing retention, a host rollout or re-enabling any paused producer.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning owner | Vision, source-grounded plan and proposal publication | Source worktree codename `rusty-relay` |
| Implementation owner | Future authorized execution and validation | Unassigned; explicit operator authorization required |

## Coordination

- **Topology:** serial, single-writer changes through the normal dev PR flow.
- **Host (owns PRs):** Planning owner for this proposal only.
- **Delegates:** none; no implementation worker or service is started.
- **Handoff:** the reviewed proposal and #6620 retain future implementation
  custody. A planning task can finish without marking this entire effort Done.
- **Existing owners:** `terminal-worktree-reclamation` (#1488/#1918/#3114)
  retains allocator provenance and terminal claim settlement;
  `worktree-conclusion-tidier` (#6060) retains semantic objective review;
  #6471/#6542 retains held-housekeeping recovery and pair progress.
  Do not replace or independently implement those slices.

## Context

The existing code already supplies much of the safety machinery:

| Boundary | Existing owner / entry point | Implication |
|----------|------------------------------|-------------|
| Lifecycle-triggered sweeps | `agent_worktrees.__main__._sweep_orphans_on_exit`, `_sweep_managed_on_exit`, `_sweep_finished_sessions_on_cadence` | Prefer composition over a replacement cleanup engine. |
| Managed / finished admission | `reap_cli.sweep_managed_worktrees`, `sweep_finished_session_worktrees`; `prune.cleanup_disposition` | Existing policies differ; do not collapse them into "clean and idle." |
| Resident lifecycle | `status_monitor_cli.cmd_status_monitor`, governance and cutover owners | Reuse resident admission, demand, draining and exclusivity. |
| Mutation and persistence | `finalize`, tracking locks and daemon-mediated write path | One lifecycle implementation; a worker cannot become another YAML writer. |
| Allocator conclusion | `terminal_conclusion`, dispatch conclusion/recovery | Ground disposal waits for legitimate allocator settlement. |

Finished-session collection currently runs at Picker launch/session exit, in the
current project context, with a 48-hour default grace and no network fetch.
Managed collection has its own policy. The resident observes demanded projects,
but does not periodically call those workspace collectors across the repository
registry. Its idle-exit model is an explicit integration concern, not proof that
adding a timer will keep collection running.

An exploratory manual-recovery sample showed tens of seconds per successful
finalize and similarly costly lock refusals. Those measurements include wrapper,
Git, network and lifecycle work; they are not controlled benchmarks or proof
that deletion itself is slow. See [performance](performance.md) for hypotheses,
measurement boundaries and proposed acceptance thresholds.

## Request

> The correct flow is clearly to garbage-collect as we go. Now that
> agent-worktrees has a full daemon, we should probably just have the daemon
> periodcally reap safe worktrees (across all repos). Unless we have a similar
> runaway worktree creation flow as we did here, it should be able to keep up
> with creation.

> Help build out the vision and plan out an effort to add this capability to
> the system (likely using the daemon), and also come up with performance
> optimizations to make this process faster.

The operator confirmed the slug `continuous-worktree-reclamation`. Detailed
mechanisms, budgets and thresholds below are **agent-recommended proposals**,
not additional operator requirements or authorization to execute them.

## Plan

### Phase 1 - Review intent and implementation proposal

- [x] Confirm the slug and capture the request without changing its intent.
- [x] Extend the owning vision and reconcile prior reclamation/tidier ownership.
- [x] Trace the existing daemon and collection boundaries; separate measured
  observations from performance hypotheses.
- [x] Land this vision/effort proposal through current-head review and required CI.
- [x] Record proposal outcome and retain future execution in #6620 behind
  separate operator authorization before entering Phase 2.

### Phase 2 - Baseline and one explicit lifecycle contract

_Agent-recommended execution plan; not yet authorized._

- [ ] Capture the controlled baseline and structural counters in
  [performance](performance.md); identify the measured dominant costs before
  selecting optimizations or revising numeric targets.
- [ ] Define an explicit immutable repository/installation context for existing
  collectors and their mutation boundary. Do not switch process-wide CWD,
  environment or active-project globals from a background thread.
- [ ] Inventory and preserve policy for finished user worktrees, managed/disposable
  allocations, paired worktrees and legacy unknown-origin records. Confirm
  current retention, kill switches and manual dry-run compatibility.
- [ ] Share the existing canonical disposal transaction and fresh evidence
  fences with manual cleanup; coordinate any prerequisite extraction with its
  current owner. Do not move owner probes/network work under lifecycle locks.

### Phase 3 - Bounded resident maintenance across repositories

_Agent-recommended execution plan; not yet authorized._

- [ ] Add a bounded maintenance lane to the existing resident, enumerating
  authorized registered worktree-class repositories independently of UI demand.
  Exclude anchors, references, singletons, foreign cells and remote venues.
- [ ] Combine lifecycle-change hints with bounded rediscovery, fair per-repo
  progress and backoff for held candidates. A cache nominates candidates only.
- [ ] Include maintenance in resident demand, governance, admission and graceful
  drain/cutover. Resolve idle-exit/wake behavior through the existing lifecycle,
  without another service or ignoring explicit stop/disable.
- [ ] Persist bounded progress/outcomes through the owning write path; reconcile
  interruption and partial pair removal using canonical evidence. A cursor is
  scheduling state, never independent disposal or claim authority.
- [ ] Expose preview, pause/disable, eligible backlog/age, actual throughput,
  lock deferrals and actionable repeated failures. Do not automatically
  quarantine a hold as success or apply allocator backpressure without its
  separately reviewed owner policy.

### Phase 4 - Measured optimization without safety drift

_Agent-recommended execution plan; not yet authorized._

- [ ] Implement only measured wins from [performance](performance.md):
  direct resident composition, repo-scoped refresh coalescing, shared discovery,
  incremental candidate work and bounded contention handling.
- [ ] Preserve fresh dirty/unmerged, process/session, objective, PR, ownership,
  claimant and pair checks at final admission. Test cache invalidation and
  concurrent resume/claim/checkout changes at the actual removal boundary.
- [ ] Prove normal-load stability, burst drain and foreground latency against
  the declared performance gates. Report all holds separately from successes.
- [ ] Update the capability/operations docs and add plugin changefiles with
  implementation, not this proposal. Do not describe proposed behavior as live.

### Phase 5 - Explicit rollout and closure

_Agent-recommended execution plan; not yet authorized._

- [ ] Start with dry-run evidence on synthetic repositories; obtain separate
  approval for a bounded disposable canary and later default enablement.
- [ ] Verify release and the authorized installed generation, two maintenance
  cycles, pause/disable, cutover and restart. Leave existing producer controls
  unchanged unless separately authorized.
- [ ] Resolve or transfer every Plan/Validation item, record real throughput
  and remaining holds, then archive and release the completed effort.

## Validation Plan

Future runtime validation is planned, not claimed by this documentation change.

- [ ] Unit: one eligibility interpretation for manual/automatic paths; all hold
  classes, grace boundaries and policy/authority isolation preserve the workspace.
- [ ] Native integration: real Git worktrees and daemon requests across several
  repositories; no Picker/session demand; healthy empty and finished cases, mixed
  management classes and per-repo policies.
- [ ] Race/fault injection: resume, new claim, dirty file, moved branch, PR state
  uncertainty, lock contention, stale observations, shutdown, daemon replacement
  and partial pair deletion preserve or reconcile the exact identity safely.
- [ ] Multi-owner/pair: one terminal task cannot release another task's claim;
  both sides and reciprocal identity must pass. Unknown historical ownership is
  held, never silently adopted.
- [ ] Cross-platform: native Windows and Linux run real locks/process boundaries.
  Other supported platforms get evidence or a tracked, explicit validation gap.
- [ ] Performance: structural counters and controlled normal/burst/held-candidate
  runs satisfy [performance](performance.md#acceptance-gates) with identical
  safety decisions; no skipped candidate is counted as reclaimed.
- [ ] Lifecycle: disabled/unavailable resident, zero UI demand, restart, passive
  successor and drain do not create an unfenced second writer or hidden worker.
- [ ] Clean-room: first install and first maintenance pass begin with no prior
  cursor or cache. Live validation uses a separately authorized synthetic canary;
  unavailable tiers and rollout permission are recorded explicitly.

## Proposal

Extend the existing agent-worktrees resident with fair background maintenance
around canonical disposal, rather than invoking the public CLI separately for
every worktree or building a dispatch-specific garbage collector. The first
implementation decision is explicit context and lifecycle admission, not a timer.
Policy expansion, semantic conclusion and historical adoption remain outside it.

Planning completion means this proposal is reviewed and merged, with future
implementation clearly gated. It does not mean any Phase 2-5 work is complete.

## Journal

### 2026-10-13 - Planning

- Operator requested continuous cross-repository reclamation and performance
  improvement, and confirmed this effort slug.
- Checked the existing allocator, tidier and held-recovery work before defining
  this narrower ground-layer scheduling/performance scope.
- Read-only source tracing and anonymous timing summaries informed the plan.
  No runtime code, machine configuration, deletion policy or automatic
  activation was changed.

### 2026-10-13 - Proposal reviewed and merged

- Vision and plan PR #6623 merged to dev with a current-head approving Copilot
  review and all required checks successful. Phase 1 planning is complete;
  this entire effort is not complete.
- Retained the operator's original spelling in the verbatim Request after
  explaining the quotation-only review nit; no design or runtime finding
  remained open.
- Added a separate observed startup stall to the performance hypotheses:
  Windows platform/WMI discovery can block configuration resolution before
  the finalize handler. This is distinct from the recorded lock deferrals,
  and requires a controlled, service-safe reproduction before a runtime fix.
- The planning claim ends at proposal publication. Phase 2-5 implementation,
  activation and rollout remain unassigned and unauthorized under #6620.
