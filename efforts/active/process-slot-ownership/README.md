# Process Slot Ownership

- **Slug:** `process-slot-ownership`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-slice PRs targeting `dev`
- **Created:** 2026-10-10
- **Status:** Draft
- **Vision:** `visions/agent-fabric/README.md` - claimed-resource-not-reclaimed
  and reclaim-idle-process; `visions/plugin-services/README.md` -
  work-coalescing-singleton
- **Umbrella issue:** #3963
- **Related:** #5559 (operational process registry), #5579/#5664
  (separately claimed lean session lifecycle)

## Guiding Intent

An outstanding reader retains its role slot until the actual holder finishes.
Caller timeout, cancellation, or a resource-budget breach does not establish
that the holder is gone. Coalesce equivalent work, suppress repeated admission
when it overruns, expose incomplete observations, and preserve ownership-safe
cleanup. Do not replace a slow holder with an overlapping attempt merely
because its result is no longer eligible for publication.

This is the canonical generic implementation plan for #3963. It reuses the
existing [process-slot ownership pattern](../../../docs/patterns/process-slot-ownership.md)
and [work-coalescing singleton pattern](../../../docs/patterns/work-coalescing-singleton.md).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Process ownership driver | Inventory, bounded implementation, serial PRs, validation | #3963 and this effort |
| Process registry driver | Independently owns shared operational membership/query service | #5559 |
| Lean lifecycle driver | Independently owns handoff wake and wait-only launch reduction | #5579/#5664 |

## Coordination

- **Topology:** independent per-slice worktrees, serial PRs targeting `dev`.
- **Host/PR owner:** Process ownership driver owns this effort's continuation
  PRs; the two sibling drivers own only their separately tracked efforts.
- **Slice ownership:** announce one continuation slice at a time on #3963
  before editing; do not overlap another driver's claimed slice.
- **Handoff:** the successor reads this Plan, validation, journal, and any
  open PR obligation before starting the next slice.
- One writer per implementation slice; use #3963 to announce the slice.
- Do not build another process registry or take over the separately claimed
  launch/handoff work.
- Shared membership, role/owner queries, and inventory presentation belong to
  [agent-process-self-report-registry](../agent-process-self-report-registry/README.md).
  This effort owns admission and reader lifetime, not that broker's design.
- A successor resumes this Plan and its Validation Plan, not the latest
  completed PR alone.

## Context

Existing mechanisms already satisfy substantial portions of the contract:

| Existing seam | Evidence and remaining boundary |
|---------------|---------------------------------|
| OS-exclusive daemon role leases | `libs/single-instance-lease`; reuse rather than introduce a rival lease |
| Worktree discovery crawls | `WorktreeDiscoveryCache.crawl` in `plugins/agent-bridge/src/agent_bridge/routes/worktrees.py` serializes with `_crawl_lock` |
| Live/archived single-target probes | The same cache retains and shields per-worktree tasks until completion |
| Namespace roster readers | `plugins/agent-bridge/src/agent_bridge/agent_registry_cache.py` retains `_inflight` tasks and provider references; caller join budgets do not cancel shared work |
| Registry presentation | #5559's existing reviewed proposal owns current process membership and owner-scoped queries |

The namespace cache's cancellation-resistant paths require specific scrutiny:
`force_fail_uninitialized_namespaces` and `_cancel_inflight_nowait` can remove
tracking before the old task has actually completed. Invalidating publication
is necessary, but is not evidence that the underlying reader terminated.
Characterize this with an event-gated reader before changing it. A provider
replacement is a separate identity boundary, not permission to lose the old
holder from shutdown accounting.

## Request

The operator selected process ownership as the continuing work and confirmed
the canonical effort slug `process-slot-ownership`.

The original public scope is #3963: owner-tethered, single-occupancy background
process slots across agent-worktrees, agent-bridge, and agent-dispatch.
The execution decomposition and acceptance details below are
**agent-recommended refinements**, not new operator requirements.

## Plan

### Historical phase mapping

Existing pattern/source references keep their original meanings. The
continuation slices below do not renumber or erase that historical sequence.
Published mechanisms are prior art; full acceptance remains independently
verified by this effort's Validation Plan.

| Historical phase | Meaning and current disposition |
|------------------|---------------------------------|
| Phase 0 | Spawn-site audit and shared primitive design; current caller inventory is reconciled in Continuation A |
| Phase 1 | Launcher/mux slot and tether; pane-lifetime work landed in #712 |
| Phase 2 | Status-query debounce; discovery coalescing landed in #714 and remains prior art |
| Phase 3 | Status-daemon and stdio ownership tethers; preserve rather than rewrite these contracts |
| Phase 4 | Generation tether and backstop unification; the graceful-daemon-cutover pattern owns the published mechanism |
| Phase 5 | Slot observability and pattern documentation; health descriptors/pattern are published, broader inventory presentation belongs to #5559 |
| Phase 6 | Reader budgets, circuit-first admission, and ownership-safe cleanup; the continuing implementation scope below |

### Continuation A - Consolidate current contracts and evidence

- [x] Identify existing leases, crawl/probe coalescing, and namespace readers.
- [x] Separate operational registry presentation (#5559) and lean launch
      lifecycle (#5579/#5664) from reader admission; do not duplicate them.
- [ ] Land this plan through review before implementation.
- [ ] Inventory remaining status/list/discovery caller seams and record
      already-satisfied versus unverified requirements.

### Continuation B - Preserve outstanding reader ownership (Phase 6)

- [ ] Reproduce cancellation-resistant same-provider namespace admission:
      bound the caller wait without starting a second underlying reader.
- [ ] Retain exact task/provider ownership through timeout and cancellation;
      fence late publication independently from slot release.
- [ ] Account for superseded-provider tasks until actual completion or
      bounded shutdown; replacement must not erase shutdown ownership.
- [ ] Land the smallest correction with deterministic async regressions,
      documentation, and an agent-bridge changefile.

### Continuation C - Reader budgets and circuit-first admission (Phase 6)

- [ ] Define role keys, expected wall/CPU cost, fan-out limits, and provenance.
- [ ] Open admission circuits on repeated budget overruns without reclaiming
      live progressing holders; successful bounded probes close them.
- [ ] Define cooperative cancellation and exact identity/no-progress proof
      before any process-tree reclamation. No time-only kill policy.
- [ ] Expose role accounting through existing observability/registry seams;
      distinguish unknown from dead and stale from current.

### Continuation D - Release and validate adoption

- [ ] Complete the validation matrix, release through normal promotion, and
      verify authorized consumer behavior.
- [ ] Resolve or transfer all remaining scope and journal merged outcomes.

## Validation Plan

- [ ] N same-role concurrent requests perform one underlying read.
- [ ] A timed-out/cancelled waiter cannot release or cancel another waiter's
      shared holder; a cancellation-resistant holder cannot multiply.
- [ ] Failed publication cannot overwrite a newer provider/generation.
- [ ] Provider replacement preserves old-holder accounting without serving
      old-provider results as authoritative.
- [ ] Wall/CPU overrun suppresses new admission, not live progress.
- [ ] Exact-holder cooperative cancellation and confirmed no-progress
      cleanup release the slot and complete process tree; ambiguous liveness
      never authorizes reclamation.
- [ ] Circuit recovery is bounded, preserves inline-fallback contracts, and
      does not create a competing registry or resident helper.
- [ ] Shutdown remains bounded and observes all owned tasks.
- [ ] Native platform tests preserve PID/start-time identity, deliberate
      detach, lock release, and crash backstops.
- [ ] Bounded targeted tests and applicable isolated scenarios pass; release
      and running-state evidence are recorded separately from merge evidence.

## Proposal

Retain holder accounting independently from publication eligibility. First
characterize the namespace cache's cancellation-resistant paths with
deterministic event-gated readers, then make the smallest lifetime correction.
Later budget/circuit design must preserve existing inline fallback contracts
and leave shared registry presentation to #5559.

## Journal

### 2026-10-10 - Canonical continuation proposal

Confirmed the slug and mapped current prior art before implementation.
Existing coalescing and registry/lifecycle efforts prevent a greenfield
rewrite. Continuation B's first slice is deliberately a deterministic reader-lifetime
regression, not a live reaper, service restart, or fleet-wide rollout.
