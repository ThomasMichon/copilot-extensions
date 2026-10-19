# Promotion burndown: unit-only release validation

Parent: [Dev/Main Release Pipeline](README.md). Related portfolio:
[Test Portfolio Rationalization](../test-portfolio-rationalization/README.md).

**Status:** policy #7255 and Source unit-packet/PR-CI implementation #7269 are
reviewed and merged. The source controller mirror and independent Main
activation are the next slice. Production remains paused.
This governs the next recovery slice. It supersedes the earlier requirement to
preserve full, generated, native and installation integration suites as
promotion prerequisites.

## Operator request — verbatim

> Build yourself a manifest of issues that need addressing in order to get promotion working, store it as a session artifact, and log it into our effort journal. Focus on the "burndown" to get to a stable state.
>
> Your focus should likely be at this point on jettisoning any blocking tests which are holding up promotion, aggressivel purging  excessively long tasks until the whole flow can run under 30 minutes, and then working to have a "validation tournament" where more-valuable tests come in to replace lesser ones. End-to-end integration tests which use multi-process, use actual git, etc. all need to be relegated to periodic, containerized tests, and need to stay out of promotion. Promotion can only run unit tests from which we collect coverage, and conditionally run during PR CI. No others.

## Governing policy

- **Promotion runs unit tests only**, collecting coverage in that execution.
  Required PR CI conditionally selects the eligible unit portfolio by actual
  changed-input impact.
- End-to-end, multi-process, actual-Git, native, service, installer and other
  integration tests move to **periodic containerized validation**, scheduled or
  explicitly dispatched. They do not remain promotion or required-PR
  dependencies under another label.
- Aggressively remove slow and blocking test tasks from the gating path.
  Moving them does not wait for full-corpus equivalence, a native timing
  witness, or an integration-test repair. Preserve their source and periodic
  failure ownership instead of deleting tests or pretending they passed.
- The stable-state gate is **under thirty minutes end-to-end**, including
  queueing, production build/publication, verified Main merge and tagging.
  The earlier one-hour ceiling is not a definition of restored stability.
- Reviewed authority, source identity, package/lock policy, byte integrity,
  signing, freshness, actual publication, exact merge and tagging remain
  production safeguards. This is not permission to reintroduce integration
  suites by calling them operational checks, nor to fake missing unit evidence.
- After stability, a **validation tournament** replaces low-value tests with
  more valuable eligible units within the same or smaller budget. Integration
  contenders remain periodic.

**Agent-recommended eligibility rule:** enroll units positively, by behavior,
not by file name or tier label alone. Use bounded in-process tests with
controlled inputs and mocks at external boundaries. A real child process,
actual Git operation, live service/network, installer or runtime lifecycle is
not an eligible gating unit. Unknown and over-budget cases start outside the
gate. This rule and its mechanical enforcement are implementation proposals,
not an assertion that the current corpus is already classified.

## Issue manifest and execution order

A structured session-scoped manifest retains evidence, dependencies, owners
and closure criteria. The public contract below uses the same stable IDs;
private worktree paths and session locators are intentionally omitted.

| ID | Priority | Issue / next action | State |
|---|---|---|---|
| PB-00 | P0 | Review the unit-only policy and supersede contradictory effort/vision gates. | Done: #7255 |
| PB-13 | P0 | Rework or supersede the old full-corpus proposal; retain code, evidence and claims. | Existing proposal held as draft |
| PB-01 | P0 | Inventory every gating job/step/test as `KEEP_UNIT`, `MOVE_PERIODIC` or `REMOVE_DUPLICATE`, with cost and owner. | Inventoried; closure edges identified |
| PB-03 | P0 | Establish the small positive unit manifest; exclude hidden integration and slow/unclassified cases. | Starter merged in #7269: 236 cells; wider corpus classification stays open |
| PB-02 | P0 | Remove full/native/integration tasks and indirect dependency edges from promotion. | Eleven-job source mirror prepared; Main activation pending |
| PB-04 | P0 | Produce unit coverage once; verify every admitted file/chunk/node and outcome, with a distinct unit-profile identity. | Source engine merged in #7269; hosted promotion proof pending |
| PB-05 | P0 | Replace general-purpose candidate CI admission with the compact exact unit-evidence contract and aggregate. | Explicit v2 helpers merged; controller integration prepared |
| PB-06 | P0 | Make required PR testing impact-conditional and unit-only; migrate aggregate/selector requirements coherently. | Source four-job CI and required aggregate merged in #7269 |
| PB-07 | P1 | Route removed integration to periodic containers with named failure ownership, never a release dependency. | Source routing/inventory merged; actual enrollment/execution still unproven |
| PB-08 | P0 | Preserve fail-closed configuration admission, including quoted/merged keys, if reused by the unit gate. | Explicit unit profile and strict evidence admission merged in #7269 |
| PB-09 | P0 | Keep bounded production build/signing/provenance/freshness/publication/merge safeguards. | Source controller retains safeguards; offline decision checks pass, Main review pending |
| PB-10 | P0 | Measure the whole flow under thirty minutes; remove long tasks rather than increasing deadlines. | Pending |
| PB-11 | P0 | Land reviewed control, prove the unit-only release, resume canonically and observe stable natural cycles. | Blocked on preceding items |
| PB-12 | P1 | Run the first value-for-cost replacement tournament after stable operation. | Deferred until PB-11 |

The recovery owner owns the manifest, Source publication, Main control proposal,
release/resume and deployment. The existing portfolio owner retains periodic
corpus and tournament coordination. A worker's local result does not resolve
the parent gate or transfer ownership.

### No longer promotion blockers

The following remain tracked in the periodic portfolio, not on the release
critical path:

- All-nine full/T3 source-corpus equivalence and repeated real-network
  installer comparisons.
- Native Windows artifact timing, actual-Git/CRLF fixture matrices and
  multi-process lifecycle tests.
- Standalone chat installed-runtime/platform qualification.
- Native archive admission based on the old smoke matrix.
- Disk-versus-tmpfs integration-fixture preconditions and other integration
  harness repairs.
- Caller-cap repair in the old full collector, except where that code is
  deliberately reused by the new unit-only collector.

This does not resolve an unsafe parser or incomplete unit-coverage proof by
omission. Such data-admission defects stay blocking when the unit gate uses
that code; otherwise the old implementation remains inactive.

## Ordered burndown plan

The decomposition below is **agent-recommended** execution of the operator's
policy, through normal reviewed Source and independently governed Main changes.

1. **Freeze the old objective.** Stop expanding full-corpus/native canaries.
   Preserve in-flight results, drafts and obligations. Land this policy before
   implementing its new gate.
2. **Subtract first.** Inventory the actual critical path, quarantine integration
   and excessive-cost tasks, and remove their transitive gate edges. Do not
   spend the recovery repeatedly repairing tests that no longer belong there.
3. **Build the minimal unit path.** Declare and audit eligible cases, collect
   their coverage once, reject omissions/stale evidence, and wire the same
   impact-aware portfolio into required PR checks.
4. **Retain periodic integration.** Reuse the existing periodic/container
   machinery where possible. Name the owner, schedule/dispatch route and
   failure tracker for each moved family; add no second release controller.
5. **Activate through review.** Update Source and Main gate declarations,
   required provider contexts and qualification inventory together. A skipped
   old integration job is not unit evidence, and an unreviewed prototype is not
   the production controller.
6. **Earn stability.** Verify publication, exact Main merge/tag and canonical
   resume, then observe natural cycles under thirty minutes. Keep deployment
   and client acceptance owned separately from the test-policy verdict.
7. **Improve by replacement.** Start the tournament only after the burndown
   delivers stable release operation.

**Agent-recommended initial budget:** allocate at most five minutes to the
entire unit-plus-coverage phase, reserving the remainder for queueing and
production operations. This is a starting allocation to validate and tighten,
not a new per-stage allowance or permission to exceed the overall limit.

## Validation and closure

- [x] Capture matches the operator's request without softening the exclusion
      of integration or the under-thirty-minute objective.
- [ ] Every actual promotion test and transitive test dependency maps to an
      admitted unit; no real-Git, multi-process, native, service, installer,
      end-to-end or periodic suite remains in that path.
- [ ] Required PR test selection is conditional on impact and admits no hidden
      integration through a mixed file, fixture or generic runner.
- [ ] Unit proof rejects a declared-but-uncollected file/chunk, dropped node,
      unexpected test, hidden selection, stale profile or failed outcome.
- [ ] Representative removed families are enrolled in periodic containers;
      failures have owners and are not silently treated as passing releases.
- [ ] Production authority, source/lock/byte integrity, signing, Main freshness,
      publication, merge and tagging remain enforced.
- [ ] An actual normal promotion, measured from event creation through landed
      tag, completes in less than thirty minutes including queueing.
- [ ] Canonical resume and natural cycles demonstrate stable operation.
- [ ] The first tournament records defect-detection value, coverage, runtime,
      flakiness and maintenance cost, replacing rather than accumulating cases.
- [ ] Every issue in the manifest is resolved or transferred to a named tracked
      objective before the parent effort is declared complete.

## See also

- [Parent effort](README.md)
- [Release intent](../../../visions/release-pipeline/README.md)
- [Test portfolio intent](../../../visions/test-portfolio/README.md)
- [Main control campaign](../main-owned-workflow-control/README.md)
