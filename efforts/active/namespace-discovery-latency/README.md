# Namespace Discovery Latency

- **Slug:** `namespace-discovery-latency`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, sequential plan and implementation PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Active
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

Subsequent operator selection, captured verbatim:

> complete-within-eight

**Selection summary:** Continue optimizing startup and metadata reads; retain
complete cold discovery within eight seconds rather than substituting later
background completion.

## Plan

### Phase 1 - Review the bounded repair plan

- [x] Land this proposal through the repository's automated review and required
  CI gates before implementation.
- [x] Bind the repair owner to the canonical effort and retain the #5761 claim.

### Phase 2 - Profile and repair the measured bottleneck

- [x] Profile the real registered command boundary on Windows, separating shim,
  interpreter/import, account enumeration and native backend time; compare cold
  and warm runs without printing credentials or private roster data.
- [x] Inspect existing provider caches and refresh contracts. Select the
  smallest source-owned repair supported by the profile; do not introduce a
  second registry, a new resident service or a competing refresh owner.
- [x] Implement the repair with focused regression coverage. Preserve the
  eight-second default foreground deadline, native execution/CPU accounting,
  actual-holder lifetime, coalescing, cancellation isolation, shutdown/provider
  replacement tracking and admission/circuit protections.
- [x] If a cache is involved, keep its provenance, freshness and completeness
  explicit. Never turn a stale, failed, incomplete or unavailable enumeration
  into confirmed target absence or a success-shaped empty roster.
  No persistent cache was introduced; existing per-process account memoization
  and per-call marketplace reads retain their scope.
- [x] Update directly affected provider/Bridge documentation and add changefiles
  for each changed plugin. Keep the remaining typed-outcome acceptance on #5761
  explicit instead of declaring the entire issue resolved.

### Phase 3 - Qualify, publish and return to the consumer

- [x] Exercise real command and Bridge discovery boundaries on Windows with
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

- [x] Unit/regression tests cover the measured cause, complete and incomplete
  rosters, provider failure/timeout, cancellation/coalescing, retained native
  holders and admission/circuit behavior without weakening existing coverage.
- [x] A real shim/subprocess check measures at least five cold-process and five
  warm runs; each successful registered provider enumeration completes within
  the unchanged eight-second budget. Report per-run wall time and completeness,
  not just an average or a mocked function's speed.
- [x] A fresh-state check exercises the no-cache/first-touch path. If remote
  backend latency cannot satisfy the contract, expose that limitation explicitly
  and amend the reviewed design rather than silently increasing the deadline.
- [x] Bridge-level integration proves the intended registered target is
  discoverable, healthy peer providers remain usable, and failed/incomplete
  namespaces never become confirmed removals. Include downstream total-budget
  compatibility, not only direct CLI success.
- [ ] Windows cross-account native qualification runs against the candidate
  source without changing shared installed payloads; Linux compatibility runs
  through the existing isolated runner. Record explicit reasons for any
  genuinely unavailable lane and retain its unresolved gate.
- [x] Required source tests, lint and install-contract checks pass for the final
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

### 2026-10-22 - Routing review corrections and native requalification

- Addressed both High findings and both newly reported Medium findings on
  #7998. Every nonzero account-snapshot exit and process startup/timeout
  failure now raises an inventory error; strict storage and peer decoding
  reject malformed account mappings instead of confirming an empty map.
- Routing is read once per discovery call, never cached across calls. A
  regression adds an alias between two refreshes and observes its newly
  configured account with exactly one bulk read per refresh.
- A per-snapshot index feeds the existing canonical account helpers. Regression
  cases preserve first-match registry precedence, exact alias casing and
  owner policy, while 200/400/800-repository cases require at most two slug
  parses per repository rather than quadratic scans.
- Focused contained suites passed: CodeSpaces 137 and Worktrees 157. Lint and
  the changed-source module-size gate passed without raising ceilings.
- Rebuilt only the contained Worktrees developer runtime and exercised the
  actual Bridge scan/default eight-second deadline in five fresh consumer
  processes: 7.844, 6.313, 6.235, 5.672 and 5.906 seconds. Each found the
  target and then served five warm reads. Native and sampling holders settled.
- A malformed stored account map produced an incomplete namespace in 7.078
  seconds, not a complete empty roster; restoring the exact fixture bytes
  restored complete discovery in 5.875 seconds. No shared runtime changed.
- The refreshed unit-source admission passed required Linux source CI before
  these routing corrections. A subsequent review and required CI on the new
  head remain necessary. Focused Linux threaded/I/O qualification and rollout
  are still explicit open gates.

### 2026-10-22 - Code review and unit-admission reconciliation

- #7998 opened for implementation review. The publication rebase conflicted
  only in the effort index; all peer effort rows were retained. The activation
  compatibility and bounded-overlap suite passed all 88 cases after rebasing.
- Upstream enrolled the activation import closure in the unit-only packet while
  implementation was in progress. The first source CI correctly refused the
  changed `_memo.py` and `resolver.py` bytes. Their exact hashes were refreshed
  after reviewing the existing adapter and original preservation tests: the
  default path remains serial, and no thread-enabled test or observer exemption
  was admitted. All other source hashes and packet nodes remain unchanged.
- A local Windows packet attempt reached 839 passes and one platform skip
  before the unchanged preservation reader attempted `ctypes.dlopen`; the
  effect observer correctly refused it. This is not a passing packet receipt
  and no native-loader exception was added. Required Linux source CI remains
  the packet acceptance gate.
- The focused Linux threaded/I/O lane remains unexecuted: this Windows host
  has neither Docker nor the devcontainer CLI required by the existing isolated
  runner. Installing another venue or weakening the runner is not implied.

### 2026-10-22 - Windows source qualification

- Final five real payload-command cold processes completed in 5.8888, 5.8586,
  6.3526, 5.6877 and 5.6699 seconds under an actual eight-second process bound.
  Each returned the known target. Earlier failed attempts remain failures,
  not qualification.
- A fresh-process, state-reset control removed prior account bindings and had
  no roster cache: declared sources alone discovered the target in 7.424 seconds.
  The isolated runtimes were preprovisioned through their canonical developer
  installers; this is not a virgin-machine or unprovisioned-install latency claim.
- The actual Bridge CLI resolver/CPU observer completed a native scan in
  6.0 seconds, then five warm cache reads. A refused strict account snapshot
  remained an incomplete namespace while an unrelated static agent survived;
  restoring the fixture restored complete discovery. Native and sampling
  holders settled.
- Focused source runs passed: activation 88; Worktrees account compatibility
  152; Bridge timeout/cancellation/native ownership 58; CodeSpaces/config
  coverage 276 with seven repository skips, followed by 83 focused cases after
  final casing/precedence corrections.
- Windows is qualified locally; a focused Linux threaded/I/O lane has not run
  from this Windows owner. Ordinary Ubuntu source CI uses the unit-only packet
  and is not a substitute for that lane. Its validation item remains open.
- No shared installed runtime or service changed. Code publication, required CI,
  qualified release and authorized current-host readiness remain pending.

### 2026-10-22 - First profiling and overlap candidate

- Profiling the released provider measured about 10.3 seconds inside Python,
  including about 6.9 seconds of serialized account-source discovery and
  2.5 seconds of serialized account listings. The real payload launcher adds
  roughly two seconds before Python.
- A source candidate overlaps three independent metadata reads and at most four
  native account listings, preserving deterministic final merge precedence.
  The contained focused run passed 135 tests with one existing skip.
- The latest real-backend candidate process still takes about six seconds
  before launcher overhead. This is improvement, not proof of the eight-second
  complete-discovery requirement. No shared runtime was updated and the
  foreground deadline remains unchanged.
- Qualification is still open. Do not publish this candidate as a completed
  repair or treat a sum of separately measured timings as an end-to-end pass.

### 2026-10-22 - Retained deadline and additional startup headroom

- The operator explicitly retained complete discovery within eight seconds.
  A five-process native check passed its first four runs but timed out on the
  fifth; the candidate is not qualified.
- Remaining profiling identified serialized activation-root metadata reads.
  An in-memory bounded-overlap experiment reduced activation resolution from
  roughly three seconds to roughly 1.6 seconds while retaining the roster.
- The source repair therefore adds an opt-in, bounded local-root overlap mode
  to `plugin-activation`, with synchronized per-call marketplace reads and
  deterministic result merging. CodeSpaces opts in; other consumers remain
  serial by default. All vendored consumers must receive changefiles.
- No deadline, native policy, shared runtime or daemon has been changed.

### 2026-10-22 - Reviewed plan

- #7930 merged after Copilot approval with no findings and successful required
  CI. The implementation worktree was pulled forward onto the reviewed plan.
- Phase 1 is complete. Phase 2 starts with profiling the registered command
  boundary; no timeout, runtime or installed-payload change has been made.

### 2026-10-22 - Kickoff

- Claimed the bounded latency slice on #5761 as `rattling-circuit`.
- Recorded the direct-command/deadline discrepancy and preserved the broader
  diagnostic acceptance and existing reader-lifetime ownership.
- Authored the proposal before implementation; no runtime or guidance change
  is part of this planning delta.
