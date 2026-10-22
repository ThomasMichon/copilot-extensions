# Namespace Discovery Latency

- **Slug:** `namespace-discovery-latency`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, sequential plan and implementation PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Draft
- **Vision:** [agent-bridge](../../../visions/plugins/agent-bridge/README.md), ownership clarity and honest cross-provider discovery
- **Umbrella issue:** #5761 (bounded latency slice; broader diagnostic acceptance retained)

## Guiding Intent

Make registered namespace discovery usable within its existing foreground
deadline by fixing the measured provider/startup bottleneck, not by increasing
timeouts or masking incomplete results. Preserve independent provider operation,
actual process ownership and compatibility with callers' total budgets.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Discovery repair owner | Plan, profiling, implementation, review and qualification | Managed worktree `rattling-circuit`; coordination on #5761 |

## Coordination

- **Topology:** Sequential plan and implementation PRs; no shared feature branch.
- **Host (owns PRs):** Discovery repair owner.
- **Delegates:** None.
- **Handoff:** Journal the current slice and remaining qualification before any
  ownership transfer. Existing #3963 reader-lifetime and #5559 registry owners
  retain their scopes; this effort does not replace those campaigns.

## Context

The registered provider command can return a valid roster when invoked directly,
yet take roughly nine to eleven seconds on Windows. Bridge's default namespace
list deadline is eight seconds. That bound currently reaches both the outer
await and the native provider execution, so repeated executions can time out
and engage reader admission's circuit.

Current implementations already retain incomplete namespace information and
protect streaming consumers against unconfirmed removals. Credit those
protections: this is not a claim that every caller still receives an
unqualified empty roster. #5761 also requests distinct unavailable-provider,
timeout, confirmed-absent-target and unregistered-namespace outcomes. A
latency-only repair must not close that broader issue.

The plan below is **agent-recommended implementation and validation detail**,
not an operator request to expand deadlines, restart a shared service, update
installed runtimes, or change provider policy.

## Request

Operator selection, captured verbatim:

> repair-bridge

**Selection summary:** Fix the underlying Bridge/provider issue through the
normal source/review flow first. The confirmed effort slug is
`namespace-discovery-latency`. This does not authorize a foreground deadline
waiver or an ad hoc installed-payload fix.

## Plan

### Phase 1 - Review the bounded repair plan

- [ ] Land this proposal through the repository's automated review and required
  CI gates before implementation.
- [ ] Bind the repair owner to the canonical effort and retain the #5761 claim.

### Phase 2 - Profile and repair the measured bottleneck

- [ ] Profile the real registered command boundary on Windows, separating shim,
  interpreter/import, account enumeration and native backend time; compare cold
  and warm runs without printing credentials or private roster data.
- [ ] Inspect existing provider caches and refresh contracts. Select the
  smallest source-owned repair supported by the profile; do not introduce a
  second registry, a new resident service or a competing refresh owner.
- [ ] Implement the repair with focused regression coverage. Preserve the
  eight-second default foreground deadline, native execution/CPU accounting,
  actual-holder lifetime, coalescing, cancellation isolation, shutdown/provider
  replacement tracking and admission/circuit protections.
- [ ] If a cache is involved, keep its provenance, freshness and completeness
  explicit. Never turn a stale, failed, incomplete or unavailable enumeration
  into confirmed target absence or a success-shaped empty roster.
- [ ] Update directly affected provider/Bridge documentation and add changefiles
  for each changed plugin. Keep the remaining typed-outcome acceptance on #5761
  explicit instead of declaring the entire issue resolved.

### Phase 3 - Qualify, publish and return to the consumer

- [ ] Exercise real command and Bridge discovery boundaries on Windows with
  cross-account enumeration, cold and warm paths, and representative failure
  cases, within unchanged caller budgets.
- [ ] Land the implementation through review and required CI; confirm qualified
  promotion to `main` separately from the `dev` merge.
- [ ] Retain release/rollout responsibility explicitly. Apply a local rollout
  only through the unified updater with its required authorization; verify the
  actual running generation and complete target discovery before claiming live
  readiness. No shared restart or rollout is implied by this proposal.
- [ ] Record the repaired latency slice and any remaining #5761 obligations,
  resolve all Plan/Validation items or transfer them to a named tracked owner,
  then archive the completed effort.

## Validation Plan

All items are agent-recommended means of proving the requested repair.

- [ ] Unit/regression tests cover the measured cause, complete and incomplete
  rosters, provider failure/timeout, cancellation/coalescing, retained native
  holders and admission/circuit behavior without weakening existing coverage.
- [ ] A real shim/subprocess check measures at least five cold-process and five
  warm runs; each successful registered provider enumeration completes within
  the unchanged eight-second budget. Report per-run wall time and completeness,
  not just an average or a mocked function's speed.
- [ ] A fresh-state check exercises the no-cache/first-touch path. If remote
  backend latency cannot satisfy the contract, expose that limitation explicitly
  and amend the reviewed design rather than silently increasing the deadline.
- [ ] Bridge-level integration proves the intended registered target is
  discoverable, healthy peer providers remain usable, and failed/incomplete
  namespaces never become confirmed removals. Include downstream total-budget
  compatibility, not only direct CLI success.
- [ ] Windows cross-account native qualification runs against the candidate
  source without changing shared installed payloads; Linux compatibility runs
  through the existing isolated runner. Record explicit reasons for any
  genuinely unavailable lane and retain its unresolved gate.
- [ ] Required source tests, lint and install-contract checks pass for the final
  diff. Documentation-impact review distinguishes plan-only changes from later
  runtime changes.
- [ ] Promotion and authorized running-state proof are distinct receipts; an
  installed selector version alone is not evidence of a running repaired daemon.

## Proposal

Review the plan first. Profiling chooses the concrete repair; neither a larger
constant nor environment-only mitigation is an accepted substitute. This
closes a bounded reliability delta within the existing Bridge vision and
namespace-provider pattern, without changing architectural ownership.

## Journal

### 2026-10-22 - Kickoff

- Claimed the bounded latency slice on #5761 as `rattling-circuit`.
- Recorded the direct-command/deadline discrepancy and preserved the broader
  diagnostic acceptance and existing reader-lifetime ownership.
- Authored the proposal before implementation; no runtime or guidance change
  is part of this planning delta.
