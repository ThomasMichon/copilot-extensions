# Promotion burndown: unit-only release validation

Parent: [Dev/Main Release Pipeline](README.md). Related portfolio:
[Test Portfolio Rationalization](../test-portfolio-rationalization/README.md).

**Status:** policy #7255, Source engine #7269, Source controller #7281 and
independent Main control #7282 are reviewed and merged. One real manual
paused qualification succeeded through actual publication, Main merge and
tagging in 11m22s (11m28s through complete workflow termination). Canonical
resume #7287 is merged and verified. Two natural qualified cycles completed in
11m38s and 15m50s including queueing; a subsequent sampled attempt completed in
14m27s. Stable source operation permits the first regression-led admission
cohort below; client acceptance and periodic execution remain open.
This governs the next recovery slice. It supersedes the earlier requirement to
preserve full, generated, native and installation integration suites as
promotion prerequisites.

## Operator request — verbatim

> Build yourself a manifest of issues that need addressing in order to get promotion working, store it as a session artifact, and log it into our effort journal. Focus on the "burndown" to get to a stable state.
>
> Your focus should likely be at this point on jettisoning any blocking tests which are holding up promotion, aggressivel purging  excessively long tasks until the whole flow can run under 30 minutes, and then working to have a "validation tournament" where more-valuable tests come in to replace lesser ones. End-to-end integration tests which use multi-process, use actual git, etc. all need to be relegated to periodic, containerized tests, and need to stay out of promotion. Promotion can only run unit tests from which we collect coverage, and conditionally run during PR CI. No others.

## Governing policy

**2026-10-21 captured operator continuation (verbatim):**

> Well, release is definitely happening quickly. Taking only 12 minutes or so. Means we have room to stuff more tests back in, especially ones which resulted in regressions filed by other people. See bugs filed by mikedelgaudio, jamesyu5421, and others.

This adds regression-led enrollment using measured headroom. It does not
silently rescind the explicit effect exclusions in the earlier request.

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
- After stability, a **validation tournament** admits valuable product
  regressions using measured headroom and replaces redundant cases when
  stronger evidence supports removal. More cases do not increase the existing
  unit budget or turn integration contenders into promotion dependencies.

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
| PB-02 | P0 | Remove full/native/integration tasks and indirect dependency edges from promotion. | Eleven-job Source/Main controls and actual unit-only canary proven |
| PB-04 | P0 | Produce unit coverage once; verify every admitted file/chunk/node and outcome, with a distinct unit-profile identity. | Actual packet: 236 nodes, 708 passing phases, no effects; receipt/digests verified |
| PB-05 | P0 | Replace general-purpose candidate CI admission with the compact exact unit-evidence contract and aggregate. | Actual v2 original-job/archive reuse qualified and published the candidate |
| PB-06 | P0 | Make required PR testing impact-conditional and unit-only; migrate aggregate/selector requirements coherently. | Source four-job CI and required aggregate merged in #7269 |
| PB-07 | P1 | Route removed integration to periodic containers with named failure ownership, never a release dependency. | Routing and locked CLI provisioning merged; actual container execution still unproven |
| PB-08 | P0 | Preserve fail-closed configuration admission, including quoted/merged keys, if reused by the unit gate. | Explicit unit profile and strict evidence admission merged in #7269 |
| PB-09 | P0 | Keep bounded production build/signing/provenance/freshness/publication/merge safeguards. | Actual canary built, signed, verified, published, merged and tagged |
| PB-10 | P0 | Measure the whole flow under thirty minutes; remove long tasks rather than increasing deadlines. | Canary 11m28s; natural cycles 11m38s/15m50s; subsequent sampled attempt 14m27s |
| PB-11 | P0 | Land reviewed control, prove the unit-only release, resume canonically and observe stable natural cycles. | Done for source cadence: controls, canary, resume and two natural qualified cycles; client acceptance separate |
| PB-12 | P1 | Run the first value-for-cost regression-led admission tournament after stable operation. | First cohort complete: #7554, 86 runtime cases added, exact 322-node proof reused in natural 12m12s release; later contenders and periodic repair remain |

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
7. **Improve by measured admission and replacement.** Start the tournament only
   after stable release operation. Prioritize actual contributor regressions
   within the unchanged budget; preserve stronger existing safeguards.

**Agent-recommended initial budget:** allocate at most five minutes to the
entire unit-plus-coverage phase, reserving the remainder for queueing and
production operations. This is a starting allocation to validate and tighten,
not a new per-stage allowance or permission to exceed the overall limit.

## First regression-led unit admission cohort

The following **agent-recommended implementation** was reviewed in #7543 and
landed in #7554. The exact pre-cohort baseline packet contained 236 units,
28 declared Python sources and 18 production coverage targets with a
300-second cap; it passed locally in 8.355 seconds. Release
`39553184584`, attempt 1, spent 21 seconds in its unit job and 14m27s
event-to-completion including queueing. The first cohort uses this headroom
without raising either budget or introducing another workflow/runner role.
The resulting packet has 322 nodes, 35 declared Python sources and 23
production coverage targets. Natural promotion `39570244030` qualified the
exact expanded source, reused its unit proof at the immutable candidate and
completed publication/Main merge/tag in 12m12s including queueing.

| Report | First-cohort unit target | Boundary retained outside promotion |
|---|---|---|
| #5610 | Real project-independent front-door predicate for explicit `reconcile-plugins --repo`, including both argument forms and missing-selector controls. The current predicate still omits this case; fix that narrow contract. | Actual HOME-cwd shim/CLI/reconcile execution and installed status reporting. |
| #6739 | Actual mux absence/result classifiers and parsers, including healthy-empty versus unconfirmed error, binary/code/stdout/stderr controls and malformed/partial output. Exercise caller decisions where their import closure is genuinely eligible. | Real missing-socket probe and resident current-roster readiness; leaf assertions alone do not claim installed readiness. |
| #4828 | Actual anchor guard decisions for read-only command batches and explicit mutation-denial controls. Supply repository-state evidence at its external boundary without accessing Git storage or mocking the command classifier. | Installed hook/shell payload parity and real Git behavior. |
| #5773 | Actual parent/sub-agent event ownership and turn folding, all supported marker forms, ordering and prior-state controls. Keep unknown fresh state unknown rather than inventing idle. | Actual SDK forwarding and JavaScript delivery/interrupt behavior; Python tests do not stand in for the Node runtime. |

Small closed-import leaf extraction may move the existing production
predicate/folder into a cohesive module and preserve its original imports or
facades. The live call sites must use that same implementation. Do not copy a
production algorithm into a test, execute only an unrelated production call,
or weaken import/effect guards to make an enrollment pass.

Other reports stay explicit rather than being represented by inadequate
proxies:

| Report | Disposition / remaining evidence |
|---|---|
| #7500 | Reporter explicitly owns the forced-registration source fix. Do not duplicate it. Evaluate the actual preservation/malformed-input regression for admission after the fix is available; no skip/xfail in the admitted packet. |
| #7412 | Current shell/PowerShell source uses the normalized plugin root. Preserve a real callback-emission/publication regression in periodic validation; a Python alias-rejection unit does not prove the shell emitted correct paths. |
| #5611 | Launcher asset versus usable-Manager status contract needs its canonical decision. Do not choose or weaken the installation contract merely to write a passing test. |
| #7116 | Fresh session/head readiness, launch recovery and reservation lifecycle require actual source/authority tracing and existing-owner coordination. A missing initial-state parameter is not proof of SQL behavior or permission to default unknown sessions to idle. |
| #6584 | Metadata rollback units cannot establish atomicity under actual Git lock contention. Preserve the real Git/process failure and recovery counterpart. |
| #6564 | Use the actual `agent-machines` PowerShell stamp/hash paths and immutable-slot policy, not another plugin's installer. Preserve real payload-tree/fingerprint parity testing and the existing conflict refusal. |

### Cohort validation

- [x] Record original report, actual production boundary, owner/fix status,
      exact admitted nodes and periodic counterpart for every selected case.
      Open/closed tracker state alone is not source or installed proof.
- [x] Prove relevant faulty behavior is detected, with mutation-denial,
      unknown/unconfirmed and legacy controls. Keep the existing release
      integrity tests unless stronger evidence justifies retirement.
- [x] Run the canonical single-process packet, collecting coverage once;
      verify every admitted node/phase/import/source/production call and no
      prohibited effect. No skips, xfails, hidden selection or budget increase.
- [x] Measure marginal runtime, coverage, defect detection and flakiness against
      the baseline; keep the packet under 300 seconds and the ordinary release
      under thirty minutes including queue/publication/merge/tag.
- [x] Verify impact-conditional PR selection and exact candidate proof reuse
      for the expanded packet, without duplicating its execution.
- [x] Keep actual shell/native/Git/process families enrolled or visibly
      unenrolled in the existing periodic inventory with named repair owners.
      Unavailable capacity is not passing evidence or a new release gate.

**Recorded first-cohort result:** four real #5610 assertions failed before the
fix with 82 runtime controls passing; the effect-fenced packet rejected the
first new failure after its 236 existing cases. After the fix, the same
admitted bytes passed in working-tree, committed, published-head and natural
release executions with no observed failed/skipped phases or effects. Local
whole-packet samples were 8.603/8.234/7.738 seconds; the actual release packet
took 7.034 seconds and its job took 24 seconds. These small samples do not
establish a statistical flake-rate guarantee or a causal speedup.

## Stamped-candidate ordering and coverage

Parent: [Phase 9c](README.md#phase-9c---stamped-candidate-tests-and-development-coverage).
Bounded tracker: #8390. The complete operator direction is captured verbatim
in the parent Request. The following mechanisms and validation decomposition
are **agent-recommended**; the existing unit-only policy remains governing.

- [ ] Reuse the release composer to freeze version state, vendoring, governed
      control inputs and the candidate before authoritative release tests.
      Dev remains the release owner; candidate content identity describes
      its tested composition, not another source release.
- [ ] Execute the positively admitted unit portfolio once on that composition
      and its actual vendored/import closure. Preserve node, effect and time
      limits. Raw-Dev PR checks remain source admission, not automatic proof
      of later transformed release bytes.
- [ ] Build and validate artifacts from the same frozen composition. Preserve
      safe unsigned-build overlap, but gate attestation, publication and merge
      on successful exact-candidate qualification. Do not restamp or
      rematerialize mutable inputs after tests.
- [ ] Preserve raw candidate coverage and a deterministic candidate-to-Dev
      projection bound to both source contents and the actual transformation.
      Retain vendored-copy origins before combining attribution. Explicitly
      classify changed version statements and generated/unmapped regions;
      projected coordinates do not claim that raw Dev bytes executed.
- [ ] Extend existing packet/receipt and coverage admission coherently.
      Preserve validated historical formats, but never reinterpret a raw-source
      packet or an unverified path relabel as candidate execution. Reuse only
      evidence for identical admitted candidate and execution inputs.
- [ ] Land Source changes normally, then use the existing independently reviewed
      Main-control activation path. No implicit resume, guard bypass, competing
      publisher or revival of the superseded full/native profile.
- [ ] Verify a containing natural release and coverage readback under the
      existing thirty-minute gate. Broader Phase 9, periodic integration and
      adopter acceptance retain their existing owners.

### Stamped-candidate validation

- [ ] Reproduce the version-fallback mismatch through composition, unit-input
      and qualification decision boundaries using eligible units. Test changed
      stamped bytes rather than stripping them during source comparison.
- [ ] Prove execution loads candidate and vendored inputs, not ambient installs
      or raw Dev. Missing/extra nodes, sources, targets, outcomes and prohibited
      effects remain refusals.
- [ ] Reject post-test mutation, changed stamping/materialization inputs,
      incorrect Dev anchoring, incompatible profiles/control and mismatched
      archives before qualification reuse or publication.
- [ ] Cover exact-copy vendoring, multiple copies of an origin, explicit
      version transforms, line shifts and unsupported/generated regions.
      Validate both sides of each map, preserve raw hits, and refuse forged or
      unsupported development attribution.
- [ ] Preserve historical coverage/receipt admission without claiming projected
      coverage executed raw Dev. Different candidate contents sharing a Dev
      anchor cannot collide or reuse incompatible evidence.
- [ ] Keep effectful composition/import/installed checks in their admitted
      bounded or periodic lanes, not as new required-unit dependencies.
      Measure the real unit/build/publication/merge critical path and mapped
      coverage readback before marking the slice complete.

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
- [x] Canonical resume and two natural qualified cycles demonstrate stable
      source operation; client acceptance remains separate.
- [x] The first tournament records defect-detection value, coverage, runtime,
      flakiness and maintenance cost, admitting valuable regressions within the
      unchanged budget and replacing redundant cases when stronger evidence
      supports removal.
- [ ] Every issue in the manifest is resolved or transferred to a named tracked
      objective before the parent effort is declared complete.

## See also

- [Parent effort](README.md)
- [Release intent](../../../visions/release-pipeline/README.md)
- [Test portfolio intent](../../../visions/test-portfolio/README.md)
- [Main control campaign](../main-owned-workflow-control/README.md)
