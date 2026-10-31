# Test Portfolio Rationalization

- **Slug:** `test-portfolio-rationalization`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed plan PR, then serial per-phase and per-plugin PRs
- **Created:** 2026-08-28
- **Status:** Active
- **Vision:** realizes [`visions/test-portfolio`](../../../visions/test-portfolio/README.md);
  complements [`visions/clean-room-validation`](../../../visions/clean-room-validation/README.md)
- **Umbrella issue:** #1303
- **Sub-issues:** pending Phase 1 decomposition

## Guiding Intent

Turn the repository's accumulated tests into a deliberate portfolio: the most
assurance-dense maintainable set across plugin contracts, regressions,
platforms, and failure boundaries.

This is not a test-count reduction exercise. It is an assurance-density
exercise. High-value tests stay even when expensive; redundant or
implementation-coupled tests are consolidated; valuable side-effecting tests
move behind containment or explicit opt-in boundaries; low-value tests leave
only after stronger evidence is shown to cover their contract.

The campaign starts with safety. No full-suite measurement or mutation campaign
runs on a developer host until the runner owns and bounds every descendant
process and other declared side effect.

### Current release and PR boundary

The reviewed [unit-only promotion burndown](../dev-branch-release-pipeline/promotion-burndown.md)
governs required CI: positively admitted in-process units with coverage only.
Tier labels, including contained T2, do not grant admission. Integration moves
to periodic containers without waiting for full-corpus equivalence; its source
and failure ownership remain here. `tools/periodic-integration-inventory.json`
records the default Linux route and explicitly unenrolled native, explicit-tier
and standalone residuals. Unavailable capacity supplies no passing evidence and
is not a release dependency. The replacement tournament follows stable
under-thirty-minute release operation, not the reverse.

The operator's subsequent measured-headroom request is carried by the
[first regression-led admission cohort](../dev-branch-release-pipeline/promotion-burndown.md#first-regression-led-unit-admission-cohort)
and release Phase 9b. That bounded contribution may restore valuable genuine
units inside the unchanged budget; it does not transfer this effort's broad
periodic inventory or failure-repair ownership, reactivate full suites as
release gates, or count unexecuted native coverage as passing.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary maintainer | Owns the plan, public issue, PR sequence, and final portfolio decisions | isolated local worktrees |
| Review agents | Review the plan and each implementation wave for lost assurance or unsafe execution | repository PR review |
| Plugin specialists | Analyze and rationalize one plugin slice at a time | serial per-plugin worktrees and PRs |

## Coordination

The finite #4395 ordinary developer-entry diagnostic OR supported-local-setup
contract continues through Phase5 workflow and Phase7 migration-intake/domain
review below, its actual global Validation and Journal. Preserve the existing
primary, Worktrees/runner/dependency/documentation/platform, audit/source/
cohort/periodic-failure and release/control custodians, #1303 and actual
source/resource holds. No new runner, implementation assignment, owner
contact, universal signature condition or resource transfer is created.

The finite #609 connection-owner witness and #4056 real MCP chaos family enter
the existing Phase7 migration-intake and Phase4 Vault/MCP waves below, with
direct local and actual global Validation and Journal. Existing primary,
clean-room, Vault/MCP warmth/cutover/IPC/installer, audit/source/cohort,
periodic-failure/native/release and resource custodians retain their scopes.
Keep #609 canonical until an exact eligible stronger receiver accepts every
residual; #4056 narrowing/closure requires every residual in an accepted
DIRECT FINAL-OPEN canonical. Those are source-specific conditions, not new
universal ACKs. OPEN #1303, all incumbent families/holds and excluded
#4036/#4049 custody remain; this intake grants no execution, assignment,
credential transport, rollout or resource release.

The directly retained #5921 finite clean-room verdict-completeness family continues through this SAME Active ROOT's Phase4 payload/harness/tools wave and Phase7 migration-intake/domain-Plan acceptance, its actual global Validation and append-only Journal. Existing primary maintainer, clean-room rig/reducer/script, audit/source/cohort/native/periodic-failure and release/control custodians retain their exact scopes; OPEN #1303 and every incumbent inheritance/hold remain. #5922/#8511 and #4312/#688/#1475 remain independent. This is tracking acceptance only, not implementation, execution, source/resource reassignment, a new generic ACK or a parent-completion waiver.

The direct retained #4123 acceptance continues through this SAME existing Active ROOT's
Plan, own and actual global Validation, and Journal under the incumbent domain, source,
native and resource custodians. Every prior phase, alternative, hold and responsibility
remains. Administrative supersession retires only duplicate tracking; a closed source is
historical provenance, not a required live claim token. This intake grants no
implementation, rollout, process action, reassignment or new blanket approval gate.

The complete #3991 retained finite family continues through this existing Active ROOT's
reviewed Plan, actual global Validation and Journal/open bounded-source process under
the SAME declared and source-specific custodians. Preserve every incumbent alternative,
native/source/resource reservation and independent acceptance gate. Closed source
trackers are historical provenance, never required live claim tokens; no assignment,
claim expiry or new blanket ACK is created.

The finite #3497 historical-Git fixture/policy family uses the existing Phase 7
migration-intake gate and Phase 4 Bridge/repository wave. Preserve OPEN #1303, all
incumbent body inheritances, #1349 native, #2721/#584 mock and #3093 order-leak
acceptance and actual audit/source/cohort/periodic-failure/release custody. Bounded unit
scope is not a broader census, fixture or native-work claim.

The direct #3093 finite order/context-leak family continues through the existing
reviewed Worktrees wave. Preserve #1349 native acceptance, #2721/#584 quota/mock
acceptance, OPEN #1303, and every actual audit, source, cohort, and failure claim. Fix
OR owning-fixture isolation remains an alternative, not permission to weaken assertions
or CI tiers.

The directly retained #2721 quota/reclaim and inherited #584 fixture family belongs to
the existing Phase 4 CodeSpaces wave. Preserve OPEN #1303, its active
audit/source/cohort/failure claims, #6237/#3336 and every existing native family,
containment and current-tier obligation. This intake changes neither fixtures nor test
admission or budgets.

- **Topology:** one reviewed plan PR, followed by serial safety, tooling, and
  per-plugin waves.
- **Host (owns PRs):** primary maintainer.
- **Delegates:** plugin specialists may own independent analyses after the
  inventory schema and scoring calibration land; only one PR affecting a given
  plugin is active at a time.
- **Handoff:** every slice updates the machine-readable inventory, contract map,
  disposition record, and effort journal before it is considered complete.

## Context

The source tree contains well over **8,000 source-level test functions across
more than 500 Python files**, before parameter expansion during collection.
The largest suites (`agent-worktrees`, `agent-bridge`, and `agent-dispatch`)
contain thousands of functions between them. These are provisional kickoff
estimates, not a reproducible inventory; Phase 2 replaces them with a
machine-generated baseline that understands module-, class-, and function-level
markers.

A conservative static scan also finds thousands of references associated with
processes, shells, executable resolution, containers, SSH, and related effects.
That count is not itself a defect, but it shows that the portfolio cannot be
evaluated safely by repeatedly launching every suite on an uncontained host.

The repository already has useful pieces:

- fast `guard` tests;
- per-plugin managed virtual environments;
- changed-plugin selection;
- clean-room validation for fresh-machine claims;
- opt-in end-to-end modules that require explicit targets.

What is missing is the portfolio discipline connecting those pieces: a
contract map, effect declarations, containment, effectiveness evidence,
runtime/reliability budgets, and a repeatable rule for keeping, consolidating,
rewriting, moving, or deleting a test family.

The archived [`plugin-process-hygiene`](../../2026/09/13%20plugin-process-hygiene/README.md)
effort governs installed runtime processes. This effort is complementary and
owns processes and effects created by tests and their runners. Its containment
work reuses or extends the existing shared process primitives
(`agent-procutil`, `single-instance-lease`, and the bridge process-group
adapter) rather than creating a parallel process-management stack.

The detailed scoring and tiering design is in
[`triage-framework.md`](triage-framework.md).

## Request

> It seems like our agent-* plugins now have thousands of tests each. These runs
> take a ton of time, and many risk running actual processes that misbehave or
> collide with the main system. We should come up with a way to "triage" these
> tests for value and effectiveness, and start an effort to pare down the tests
> into the best, most-thorough set.

Scope decision: govern every test-bearing plugin, with the largest `agent-*`
suites first.

## Plan

### Phase 0 — Reviewed intent and public claim

- [x] File the public coordination issue (#1303).
- [x] Establish the repository-wide test-portfolio vision.
- [x] Author the initial triage framework and source-level baseline.
- [x] Submit this plan for repository review and merge it before implementation.

### Phase 1 — Containment before measurement

- [x] Serialize runner invocations that prepare or use shared mutable test
  environments behind one host/operator-scoped, liveness-reconciled admission
  lease. Guards, collection, and preparation remain cheap feedback modes but
  are not exempt when they can rebuild a shared environment; read-only listing
  remains independent. Direct-host and devcontainer entry points use the same
  host-side admission protocol.
- [ ] Make the test runner own the complete descendant process tree on Windows
  and POSIX, including cleanup after interruption and timeout.
- [x] Enforce individual-test, sequential sub-suite, and plugin-aggregate
  wall-clock limits so one hung case or oversized file group cannot monopolize
  a run.
- [x] Add a test-mode spawn policy to the shared process helper so production
  breakaway behavior is disabled under containment without changing production
  semantics; prove that attempted breakaway descendants remain runner-owned.
- [x] Redirect `HOME`, `USERPROFILE`, XDG roots, Copilot roots, and plugin state
  roots into runner-owned temporary state for every default-tier suite; fail
  closed when a test resolves a real host state root.
- [x] Add configurable wall-clock, process-count, memory, and temporary-storage
  budgets with conservative defaults.
- [x] Add explicit effect markers for process, network, service, host-state, and
  external-system interactions; fail collection when a declared tier violates
  its allowed effects.
- [x] Add regression coverage for recursive fixture executables and other
  process-escape cases without reproducing an unbounded process storm.
- [x] Prove containment in an isolated venue before allowing full-suite
  measurement.

### Phase 2 — Machine-readable portfolio census

- [ ] Define the inventory schema for test families, node IDs, contracts,
  provenance, tier, effects, platforms, runtime, reliability, and disposition.
- [ ] Build a static-first inventory command that does not import or execute test
  modules.
- [ ] Add contained collection and timing modes after Phase 1, recording
  parameter expansion and setup cost separately.
- [ ] Select and pin the focused mutation/fault-injection tooling, define the
  critical-module selection rule, and record a bounded baseline procedure.
- [ ] Map existing guards, unit suites, clean-room scenarios, and end-to-end
  modules into one tiered portfolio view.
- [ ] Flag oversized test files and low-assurance-density process families for
  contract-based splitting and consolidation.
- [ ] File focused sub-issues for the runner, inventory tooling, governance, and
  each plugin wave.

### Phase 3 — Calibrate on hazard and scale

- [ ] Pilot the framework on `agent-dispatch`, where executable-resolution and
  process tests provide the strongest safety calibration.
- [ ] Pilot it on `agent-worktrees`, the largest suite, to prove that family
  grouping and evidence collection scale.
- [ ] Use focused mutation sampling and historical regressions to calibrate
  effectiveness thresholds; do not infer value from coverage or count alone.
- [ ] Review the first dispositions before applying the rubric repository-wide.
- [ ] Hold a continue/narrow decision: if the pilots produce little assurance
  improvement relative to analysis cost, narrow the effort to containment,
  budgets, tiering, and contribution gates rather than forcing an exhaustive
  per-plugin disposition campaign.

### Phase 4 — Rationalize the portfolio in waves

- [ ] Complete `agent-worktrees`, `agent-bridge`, and `agent-dispatch`.
- [ ] Complete the remaining `agent-*` runtime plugins and shared libraries.
- [ ] Complete payload-only and harness-plugin suites.
- [ ] Begin every plugin wave by identifying its critical modules and recording
  the focused mutation/fault-injection baseline required by the removal gate.
- [ ] For every wave, consolidate equivalent cases, rewrite brittle
  implementation assertions, move hazardous validation to the right tier, and
  delete only when the removal gate in `triage-framework.md` is satisfied.
- [ ] Split oversized files along contract boundaries, while consolidating
  repeated setup into scenario/parameterized tests that cover multiple related
  observable features per process launch.
- [ ] Keep each plugin's contract map, inventory, and TESTING coverage summary
  current as its wave lands.

#### Finite clean-room verdict completeness retained from #5921

<!-- backlog-reconciliation:7556:transfer:5921:to:efforts/active/test-portfolio-rationalization/README.md -->

This finite case enters the existing Phase4 payload/harness/tools wave through Phase7 migration-intake and domain-Plan review. It preserves the ORIGINAL #5921 source/API contract; this checklist is not a new test programme, a reducer implementation or execution authority.

##### Direct unchecked Plan

- [ ] L5921-P1: Map the ACTUAL documented clean-room scenario-report -> verdict -> consuming setup/publish path and required report evidence. Preserve scenario/version/phase, counters, environment, jams/results and any judgment/completeness fields as appropriate to that EXISTING protocol. Parsed file existence is not completion; do not invent a new universal report schema or a new provider API.
- [ ] L5921-P2: Make the actual Python reducer validate supported version/schema, object/field shapes and required completed/judged evidence BEFORE deriving ok/counters/exit. Malformed, incomplete, unsupported or unjudged reports, including a parsed empty object, stay explicit NON-PASS. Missing scenario/counters/evidence must not be replaced with blank or zero values that manufacture success.
- [ ] L5921-P3: Apply the SAME evidence-complete/non-pass contract to the actual Python-less fallback and its real callers. No absent grep match or missing counter may default to a zero-failure PASS. Preserve its supported dependency-light operation, explicit diagnostics and legitimate control behavior rather than merely fixing the Python arm.
- [ ] L5921-P4: Preserve legitimate success/failure/usage and classified/degraded jam outcomes and their correct documented structured results/0-1-2 exits. At the ACTUAL consuming scenario-then-verdict boundary, missing stage/report/judgment or incomplete upstream outcome cannot be hidden by a final reducer command. Keep literal-mode separation and no self-healing; zero inferred failures is not evidence of success. Programmatic Tier P does not acquire an unrequested agent/Tier E judgment requirement.
- [ ] L5921-P5: Provide source-safe, bounded actionable diagnostics and deterministic missing-field, wrong-shape and valid-control coverage through the ACTUAL reducers and documented report/caller seam, not a mock that bypasses construction/reduction. Preserve original finite source/API acceptance without a live box, driven model, real downstream distribution, native fleet or all-host trial.
- [ ] L5921-P6: Keep recorded documentation-only #5931 and the completed source-reconciliation sweep credited only at their actual scope; they are not reducer implementation receipts. Preserve current unit eligibility/effect/resource rules and separate owning clean-room/script contracts. Do not absorb independent #5922/#8511 or #4312/#688/#1475 into this finite case or claim any was fixed by intake.

##### Local Validation

- [ ] L5921-V1: Use deterministic fixtures covering absent/malformed/unsupported/incomplete reports, empty object, missing required fields, wrong top-level/field/counter shapes and explicitly unjudged/missing-stage evidence. Require explicit non-pass from the actual Python reducer and real report consumer; no absent-evidence success.
- [ ] L5921-V2: Retain genuine complete success, failure, usage and classified/degraded-jam controls with correct structured ok/counters and documented exits. Preserve literal-mode semantics and safe diagnostics. Do not impose a new universal positive passed-count threshold, agent judgment on a declared programmatic report, or leak report contents/secrets merely to diagnose a refused report.
- [ ] L5921-V3: Exercise the actual Python-less reduction path against equivalent complete/incomplete/wrong-shape/control fixtures. A Python-only pass or successful JSON parse cannot stand in for this fallback; use the least expensive faithful bounded existing lane without host changes or a fresh platform programme.
- [ ] L5921-V4: Check the documented scenario/report/verdict caller shape and final consuming status: a missing or incomplete report, failed required stage or unjudged result is never replaced by a final inferred-zero-failures green. Preserve valid control/jam outcomes and separate primary failure from diagnostic/reducer failure; no real installer/model/distribution is needed.
- [ ] L5921-V5: Record exact source, owning test/caller boundary, chosen protocol requirements, actual outcomes and any unavailable case when the separately authorized implementation owner qualifies this finite family. Source inspection, documentation merge or a generic runner is not executed reducer/caller proof. Preserve current budgets/effects and all incumbent portfolio/native/periodic owners; no test/import/reducer execution occurs during this intake.

##### Continuing Coordination and receiving gate

- [ ] L5921-C1: Continue through the SAME Active ROOT primary maintainer, existing clean-room rig/reducer/script custodians, live #1303 and existing migration-intake/serial-wave boundaries. Preserve all incumbent Plan/actual global Validation/Participants/Coordination, completed rows, issue-body inheritances and full inline/linked historical Journal; no implementation assignment or resource/source claim transfer is made by this proposal.
- [ ] L5921-C2: Keep #5922 and #8511 independent, and retain #4312's paired setup/judged evaluation, #688 Tier-E and #1475 WinGet acceptance with their owners. A missing-report defect is not permission to rerun a rig, widen native gates, change admission/CI policy or declare the portfolio Done. No old hold is released by age, silence or an empty assignee.
- [ ] L5921-C3: BEFORE administrative #5921 supersession, obtain a NEW normally reviewed ACTUAL merge delivering ALL this direct local Plan, local Validation, ACTUAL level-two ROOT global Validation entry, continuing Coordination and dated Journal. Then FULLY read back the CURRENT whole ROOT and ALL linked historical journals plus current source/#1303 member-author/state/lock/assignees/eligible discussion/actual claims. Old effort creation, old unrelated intake, a title/link/child-only note or this artifact is not receipt. If any clause, tier, current custody, Active fit or complete delivery is uncertain, RETAIN #5921.

Sources: https://github.com/ThomasMichon/copilot-extensions/issues/5921 and https://github.com/ThomasMichon/copilot-extensions/issues/5921#issuecomment-6331529524 . Existing OPEN #1303 remains the live portfolio coordination token.


### Phase 5 — Tiered developer and review workflows

- [ ] Expose explicit commands for guards, hermetic units, contained components,
  clean-room scenarios, and opt-in end-to-end checks.
- [ ] Make changed-scope selection contract-aware so contributors receive the
  cheapest relevant feedback first.
- [ ] Record and enforce per-plugin runtime and resource budgets.
- [ ] Add contribution guidance requiring new test families to declare their
  contract, tier, effects, and expected budget impact.

### Phase 6 — Final assurance and steady-state governance

- [ ] Re-run contract coverage and focused mutation baselines for every
  rationalized plugin.
- [ ] Demonstrate that interrupted and failed runs leave no descendant process
  or undeclared external effect.
- [ ] Publish before/after portfolio size, runtime, resource use, reliability,
  and effectiveness evidence without treating reduction percentage as the goal.
- [ ] Close or transfer every sub-issue, update reality documentation, mark the
  effort Done, and archive it.

### Phase 7 — Reconcile deferred backlog

- [ ] Accept test-portfolio candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- [ ] Revalidate accepted technical scope against the current contracts, suite
  topology, and budgets; return obsolete or unsafe candidates for explicit
  disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
  extending this plan before implementation when necessary.
- [ ] Use synthetic fixtures and reproducible public test cases.

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a repository-wide sweep of open `bug`-labeled issues against active efforts (semantic discovery + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [ ] **#3497** tests: discourage real git subprocess calls against the ambient checkout (fragile across clone depths)
  - Directly about discouraging real git subprocess calls in tests -- core test-portfolio scope.
- [ ] **#2721** agent-codespaces: complete quota/reclaim mock and inherited fixtures
  - The narrow create/reclaim/retry path is already reached; complete retained
    fixture isolation and mock sensitivity remain in the direct Plan below.
- [ ] **#1052** agent-worktrees: full plugin suite can hang indefinitely without progress output
  - A suite hang without progress output is a test-reliability/portfolio issue.

### Windows SessionHost active-child streaming-hold fixture from #1349

<!-- backlog-reconciliation:7556:transfer:1349:to:efforts/active/test-portfolio-rationalization/README.md -->

Place this exact still-open family in the Phase4 agent-bridge wave and Validation Plan,
not a generic journal link or a test deletion. Retain SessionHost test/production owners
and actual overlapping source claims; no competing reaper/process authority or resource
custody is created.

- [ ] Synchronize the fixture producer, reader and ACTIVE-HOLD lifecycle so the fake
  stream cannot reach EOF before the intended ten progress frames. Replace the
  fixed-cadence assumption with deterministic lifecycle evidence; do not mask
  feed-after-EOF or merely retry for a faster Windows sample.
- [ ] Preserve all original behavior assertions: active streaming across several hold
  windows is NOT reaped, ten frames are actually relayed/buffered, and only a genuine
  full quiet window with no reattach permits reaping. Production reaping behavior/hold
  semantics remain unchanged.
- [ ] Retain the original failed combined SessionHost/GC observation and separate
  isolated1.79s passing retry as different evidence. Concurrent Windows transport abort
  is not by itself a proved common cause or accepted-flake waiver.
- [ ] Record bounded repeated native WINDOWS and adjacent-family qualification after
  synchronization, including reader/producer shutdown and owned cleanup. Preserve
  item/group/plugin/resource limits and original assertions; no timeout enlargement,
  hidden skip/xfail, text-only replacement or new model/cloud requirement.
- [ ] Keep the native/integration family at its proper current periodic/contained tier.
  Routing it out of required-unit CI is not a passing result, but this finite regression
  repair does not wait for the whole portfolio, release cadence, mutation tournament or
  a new ACK ceremony.

No production/runtime/service/native probe was performed by this intake. Close
duplicate1349 only AFTER this COMPLETE named family plus validation is merged/read back
in this existing reviewed Active effort and every requirement/owner/gate survives. If
the receiver is no longer Active, delivery is only a link, or custody/scope cannot be
preserved, retain1349. Preserve immutable first-pass history and do not claim the
fixture repaired merely by moving its tracker.

### Direct complete CodeSpaces quota/reclaim mock and inherited #584 fixture family from #2721

<!-- backlog-reconciliation:7556:transfer:2721:to:efforts/active/test-portfolio-rationalization/README.md -->

Add this exact named family to the SAME existing reviewed ACTIVE ROOT CodeSpaces
wave/Plan AND corresponding Validation, preserving every incumbent row (including
existing native families), owner/claim, Journal and OPEN #1303 coordination. This is a
documentation/acceptance intake only, not a test execution, quota probe, native PASS or
permission to change production reclaim behavior.

- [ ] Preserve and explicitly map the actual quota-error create -> _reclaim_for_quota ->
  conditional second create route and no-reclaim failure. Current --force-create
  intentionally bypasses the unrelated pool-pressure planner, NOT quota reclaim; do not
  repeat the disproved narrow-bypass assertion or remove the right production branch.
- [ ] Make fixtures assert/mock the actual owning reclaim seam and prove the relevant
  failure is detected when reclaim is bypassed, ignored or incorrect. Preserve exact
  retry/no-retry outcomes/call counts and real helper decision coverage, not only a
  return code or a mocked success. Do not silently bypass the branch under test.
- [ ] Carry ALL inherited #584 acceptance: broader create-path pool/core/lease/owner
  cases deterministic without live providers, including hostile ambient provider/project
  configuration; quota exhaustion through create/reclaim/actual retry with no-reclaim
  preserved; owner-reference environment and subprocess-isolation regressions alongside
  the lifecycle family.
- [ ] Map current fixture stores and actual RUNNER-owned
  HOME/USERPROFILE/XDG/Copilot/plugin roots, effect declarations and lock/live-user
  boundaries. Credit default isolated_environment and explicit host-state opt-in rather
  than falsely declaring host writes solely from a missing local HOME patch. Conversely,
  a broad sandbox/tier flag or collection result is not mock sensitivity or actual
  consumer qualification.
- [ ] Preserve supported owner-reference override/no-environment/blank-environment and
  subprocess decision regressions, status/lease/launch state isolation, real
  target-lock/live-user boundary where intentionally retained, and known-current source
  versus old implementation aliases. No ambient provider/account/quota, unrelated
  process census or host state is implicitly authorized.
- [ ] Keep the family at the least expensive correct CURRENT tier, with pure
  deterministic mocks in the admitted unit contract and owned real process/native cases
  only in their proper explicit contained/periodic lanes. Moving a family out of
  required CI or skipping it is not a passing repair; do not restore obsolete full
  integration gates, weaken assertions/budgets or require the whole portfolio to finish
  this finite family.
- [ ] Record original-node/current-consumer mapping, exact evidence/unknown outcomes and
  bounded relevant future validation through the owning runner when separately
  authorized. No code import, collection, test, fault run or actual quota/lifecycle
  operation is performed by this intake.

Receiving gate: a NEW normal reviewed ACTUAL source merge must materialize every
original/inherited unchecked requirement directly in this existing ROOT Plan AND
Validation/continuing Coordination, not only its old #2721 bug-title link or an old
creation PR. Fully read back the CURRENT complete merged ROOT
Plan/Validation/Coordination and unchanged owners before superseding #2721. If the ROOT
is not Active, scope/custody is not accepted or delivery is partial, retain the source.
No test deletion, source/resource handoff, new generic ACK or whole-parent qualification
is introduced.

<!-- backlog-reconciliation:7556:transfer:3093:to:efforts/active/test-portfolio-rationalization/README.md -->

Add the COMPLETE named #3093 family directly to the SAME existing reviewed ACTIVE ROOT
Worktrees Plan wave AND corresponding Validation/continuing Coordination, preserving
every incumbent row, Journal, #1349 native family, #2721/#584 quota/mock family, OPEN
#1303 and actual audit/source/cohort/failure owners. Use the existing migration-intake
and serial plugin-wave review boundaries. This proposal runs no test/collection/import
and grants no source/process/resource authority.

### Whole order/context-dependent Worktrees failure family retained from #3093
- [ ] Map the original scattered full-suite failures, including execution-leg CLI and
  extension-reload-warning-retirement families, to their ACTUAL current cases/consumer
  contracts. Preserve the historical isolated 33/33 observation and separate full-run
  failures as reported evidence, not fresh results or accepted-flake waiver. Explicitly
  map legitimate retired/renamed behavior instead of silently dropping an old case.
- [ ] Identify the ACTUAL shared leaking state across collection AND execution
  boundaries: environment/Path.home, active project/handoff, imported modules/sys.path,
  singleton/cache, fixture teardown, resident/background threads/daemons and
  SQLite/lock/temp state as actually implicated. The listed candidates and current eager
  CLI/module-import boundaries are hypotheses, not proved causes. Alphabetically later
  tests not yet executing does not rule out import-time effects.
- [ ] Fix the actual leak OR strengthen the relevant owning fixture(s), retaining the
  original alternative. Give every implicated owner deterministic setup/restore/teardown
  on pass/failure/interruption; preserve valid isolated behavior and observable
  assertions, current HOME/USERPROFILE/AGENT_HOME and resident-monitor/project/handoff
  containment, and owned shutdown/lock/cache/reader cleanup. Do not paper over ordering
  through a generic catch, retry, skip, xfail or changed expected behavior.
- [ ] Qualify a bounded representative predecessor/affected-family ordering or
  permutation AND/OR same-process repetition appropriate to the identified cause,
  including collection-time state where relevant. Establish meaningful negative
  sensitivity to reintroduced leak/wrong teardown; isolated green files, sharding or an
  unrelated focused daemon/wrapper fix are not order-independence evidence. Record exact
  source/current-node mapping, platform/context and remaining unknowns rather than
  asserting current failures were reproduced here.
- [ ] Explicitly consider and record the original proposed future regression lane
  (representative alternate orders or repeat-in-one-process), at the least expensive
  correct current tier. Keep genuine units eligible only under current admission; real
  native/Git/process/integration families retain their existing contained/periodic or
  explicit lanes and independent failure ownership. No restoration of obsolete whole
  integration suites as required PR CI, budget enlargement, full-corpus tournament
  prerequisite or new unbounded raw suite run.
- [ ] Preserve runner-owned state and descendants, item/group/plugin/resource limits,
  effect declarations and existing platform/credential/provider boundaries. Current pure
  inventory is distinct from code-executing collection; source reading alone is not
  causal or native qualification. No ambient HOME, shared SQLite/campaign state,
  unrelated process, live service/provider or resource may become a fixture.

### Validation and continuing coordination for #3093
- [ ] Record the identified causal boundary, minimal representative predecessor/affected
  consumer, chosen fix-or-isolation alternative, actual repeat/order/permutation and
  relevant negative evidence, with deterministic owning cleanup and preserved valid
  standalone/adjacent contracts. Maintain exact historical versus newly executed results
  and unavailable/uncertain outcomes. A targeted finite family can finish without
  waiting for the whole portfolio, but moving it out of CI or deleting assertions is not
  repair.
- [ ] Preserve all existing ROOT Plan/Validation/Journal rows, native/mock intake gates
  and #1303/source/cohort/failure claims; no new assignee, handoff, claimant expiry or
  universal ACK is introduced. Continue through the existing reviewed Worktrees wave and
  owning bounded runner when separately authorized.

Receiving gate: BEFORE administratively superseding #3093, a NEW normal reviewed ACTUAL
source merge must deliver ALL these direct unchecked ROOT Plan AND Validation/continuing
Coordination obligations, followed by FULL CURRENT ROOT readback and owner/gate
preservation. Existing plan creation, the original accelerator PR, a title/link/child
section or this proposal is not receipt. If either scope/custody or actual complete
delivery is unavailable, retain #3093. The technical order-leak investigation and repair
remain unresolved after tracker consolidation.

<!-- backlog-reconciliation:7556:transfer:3497:to:efforts/active/test-portfolio-rationalization/README.md -->

### Direct ambient-checkout historical-Git contract from #3497

#### Coordination

Accept this finite policy/fixture family through the existing Phase7 migration-intake
deduplication/ownership gate and Phase4 Bridge/repository test wave, not a new
programme. Preserve the whole incumbent Active ROOT Plan/Validation/Journal, OPEN #1303
and ALL existing body inheritances,1349 native/2721-584 mock/3093 order-leak families,
audit/source/cohort/periodic-failure custody and current release/control owners. A
bounded original-body unit contribution is not permission to take broader census,
fixture or native work. No source/resource/claim transfer, test execution or new generic
ACK occurs here.

#### Unchecked Plan

- [ ] Preserve the exact generation-one historical protocol lookup and its minimal
  disconnected/container-snapshot recurrence. Credit6469's hash/blob-verified
  frozen-source fallback and genuine-error preservation;3502 full-depth checkout was a
  workaround, not the complete repository rule.
- [ ] Establish the original alternatives as the supported test-authoring contract: own
  a minimal Git fixture with precisely required commits OR mock/stub the relevant call
  faithfully OR explicitly document/assert a genuinely necessary ambient-history
  dependency with an honest actionable unavailable/skip result. Do not turn OR into
  mandatory full-history acquisition or an unconditional ban on real Git.
- [ ] Prevent silent dependence on ambient checkout depth, branches and external commit
  availability. Preserve bounded local probes, scrubbed Git environment, no
  replacement/config injection, disabled lazy fetch and no network/credential escape
  from an isolated historical contract.
- [ ] Keep unavailable commit distinct from corrupted frozen bytes/identity, actual Git
  failure and available commit with missing path. Preserve SHA256/Git-blob reference
  verification and all real failure diagnostics; no blanket fallback/catch, hidden fetch
  or skipped-as-passing evidence.
- [ ] Explicitly CONSIDER and record the original repo-wide guard/lint suggestion for
  test git-show/git-log external-history patterns. Implement an appropriately scoped
  useful check OR record the reviewed decision/rationale and accepted guidance
  alternative. Do not invent a guessed mandatory new linter, exhaustive rewrite or
  strict history policy; any chosen check must avoid flagging legitimate fixture-owned
  integration.
- [ ] Preserve valid isolated real-Git/native integration and meaningful
  historical-source negatives at the least expensive faithful CURRENT contained/periodic
  tier. Pure rules may qualify only under existing unit admission; a collection result,
  skip, moved CI lane or helper presence is not historical execution.
- [ ] Keep finite3497 completion independent of whole portfolio, full historical test
  census or every platform/parent native gate. Record each genuine remaining
  unsupported/history case honestly; no budget enlargement, assertion loss, source-owner
  expiry or arbitrary exception.

#### Unchecked Validation Plan

- [ ] Verify exact immediate fallback behavior for available/unavailable historical
  commit, corrupted frozen content/identity, unrelated Git error and missing path on an
  available commit, preserving real consumer assertions and no hidden fetch.
- [ ] Qualify supported shallow/partial/minimal snapshot and
  fixture/mock/explicit-dependency cases at their proper bounded tier, with exact source
  and honest skip/unavailable classification. No need to copy the whole host history
  into a clean venue.
- [ ] Record a concrete reviewed guard/lint disposition, positive/negative sensitivity
  if implemented and legitimate fixture-owned integration compatibility. Document
  intended authoring/diagnostic behavior even if a separate linter is declined.
- [ ] Preserve original effect/containment/budget/provenance and existing
  native/periodic/audit/cohort failure ownership. Source inspection, policy wording and
  current runtime execution are distinct evidence; no existing family or assertion is
  silently removed.

Receiving gate: a NEW normal reviewed ACTUAL MERGE must deliver the ENTIRE direct
unchecked Plan AND Validation/continuing Coordination, including the real ROOT
Validation entry, then FULL CURRENT Active ROOT Plan/Validation/Coordination/Journal and
open1303/actual claim readback BEFORE3497 supersession. The old3497 bug-title link, old
effort creation, child-only note, unmerged proposal or this text is not receipt. If
complete scope/intake/custody cannot be verified, RETAIN #3497. The source's technical
policy remains open after administrative consolidation; no
tests/imports/collection/checkers/clone/runtime effects occur here.

<!-- backlog-reconciliation:7556:transfer:3991:to:efforts/active/test-portfolio-rationalization/README.md -->

Add this COMPLETE finite THREE-case historical Vault Linux family DIRECTLY to the SAME
existing normally reviewed ACTIVE ROOT Phase 4 Vault wave Plan, local Validation and
continuing Coordination, preserving the WHOLE incumbent ROOT Plan/Validation/Journal,
OPEN #1303 and every actual audit/source/cohort/periodic-failure/native/release
custodian and 1349/2721-584/3093/3497 family. Use existing migration intake and serial
source ownership, not a new test programme, effect runner, agent or assignment.

### Three original Vault Linux baseline failures retained from #3991
- [ ] Recover ALL THREE original test identities, failure signatures and historical
  source/profile from public source history or an existing owner-recorded report. The
  original has no names/traces/pin; keep these unknown until evidenced. Never nominate
  three plausible current tests, assume unchanged failures or count unrelated later
  green suites as original correspondence.
- [ ] Classify EACH recovered case against genuine Linux behavior, Windows-only
  contract, WSL boundary, fixture/environment leakage or actual product regression.
  Preserve its distinct assertion and cause. A legitimate approved domain
  correction/unsupported outcome is not a positive Linux capability pass; retain the
  applicable supported-domain coverage elsewhere rather than dropping the contract.
- [ ] Map EACH actual repair/supersession/domain treatment to current causal code and a
  REAL merged/source witness with exact scope. Credit #5651's specific have_uv=False
  multi-PATH fixture fix and #3085's separate Windows readiness work without declaring
  either a match for the unnamed trio. No prospective SHA, guessed original PR or
  touched-file/count association is proof.
- [ ] Preserve all original assertions through faithful isolated fixtures OR an
  explicitly documented approved domain treatment. No blanket skip/xfail, deleted cases,
  success-shaped fallback, weaker production credential/identity/publication boundary or
  arbitrary timeout/retry makes an unknown baseline complete.
- [ ] Record the appropriate existing/owned native-Linux or faithful contained
  qualification for EACH mapped case, with exact source/profile/node/domain and
  historical versus new outcomes. No Windows/WSL/synthetic/installed evidence
  conflation, current-failure claim or unexecuted-as-green result. Keep least expensive
  faithful CURRENT unit/contained/periodic/explicit tier and existing budgets; no
  restored mandatory whole integration suite or whole-portfolio gate.
- [ ] Preserve host-safe source/fixture/history investigation. Any further reproduction
  needs the existing owner's separately admitted isolated lane and synthetic data. Do
  NOT read real vault inventory/database/credential/token stores, login/unlock,
  contacts/endpoints or services; no test/import/collection/probe is performed by
  intake. If original evidence remains unrecoverable, preserve the exact all-three
  unknown as a named tracked UNRESOLVED family with explicit owner disposition, not
  closure-by-age or a claimed repair.

### Local Validation and continuing Coordination for #3991
- [ ] Resolve all THREE original identities/signatures/profile or preserve their exact
  tracked unknown; for each recovered case, record real causal/domain disposition,
  preserved assertion and appropriate bounded Linux/supported-domain positives/negatives
  with exact source/merged witness. A later unrelated test count/green job, skipped
  lane, same version or broad source hardening is insufficient.
- [ ] Keep every incumbent ROOT/native/mock/order-leak/history and
  #1303/audit/source/cohort/failure claim unchanged; no owner expiry, generic ACK,
  production credential feature or resource takeover. This finite family can finish
  independently of unrelated portfolio phases while every unresolved original case
  remains represented.

 The preceding exact global bullet MUST be inserted in the ROOT's REAL '## Validation
Plan', not only this child/local subsection, a Journal note or an inline
cross-reference. Continue through existing ROOT Coordination/migration intake and
journal accepted routing without implying tests ran.

Receiving gate: BEFORE administratively superseding #3991, a NEW normal reviewed ACTUAL
MERGE must deliver the ENTIRE direct unchecked Plan/local Validation/continuing
Coordination AND actual GLOBAL ROOT Validation entry. Then FULL CURRENT Active ROOT
Plan/Validation/Coordination/Journal, source/1303 eligibility and actual same-owner/hold
readback must verify every clause/unknown/alternative/custodian survives. Old creation
or #8113's unrelated prior family intake, title/link, Draft/Done/closed token,
child-only note or this proposal is NOT receipt. If full scope/ownership/intake cannot
be verified, RETAIN #3991. Original technical reconciliation stays unresolved after
administrative intake; no test, checker, collection, import, native,
Vault/credential/service/provider or source effect is authorized.

<!-- backlog-reconciliation:7556:transfer:4123:to:efforts/active/test-portfolio-rationalization/README.md -->

### Direct complete fixture-origin temporary-link cleanup family from #4123

Accept this finite family through the existing Phase7 migration-intake
deduplication/domain-ownership gate into the SAME reviewed Active ROOT's Phase4
Worktrees wave. Preserve the ENTIRE incumbent ROOT Plan, actual global Validation,
Coordination and Journal, OPEN #1303, all direct inherited
native/mock/order/history/unknown-case families and every actual
audit/source/cohort/periodic-failure/release owner. This is proposed documentation
intake only; it does not claim cleanup, run a test or confer source/resource/claim
custody.

#### Numbered unchecked Plan

- [ ] Recover the actual supported pytest temporary-root/current-alias and explicit
  fixture-created symlink routes that can place run-owned debris in a plugin directory.
  Preserve the historical empty-pivots/empty-plugins origin and exact source/profile
  evidence without inspecting real private targets or assuming today's leak from a
  historical filename. Framework-created current aliases and explicit symlink_to calls
  are different producers.
- [ ] Establish ownership-qualified fixture finalization OR an appropriate
  autouse/framework boundary, preserving the original alternatives. Register cleanup
  early enough for setup failure as well as after yield; environment restoration is not
  deletion proof and each individual test need not delete a framework-owned target
  blindly.
- [ ] Remove only the addressed run's proven-owned temporary link/artifact. Preserve
  pre-existing authored files, foreign links, external targets and other runs'
  retained/live/unknown ownership. Do not follow a link, remove its target or sweep a
  shared plugin/temp root. Uncertain ownership is an explicit retained residual, not
  deletion permission.
- [ ] Preserve cleanup after success, primary test failure, setup failure, after-yield
  failure and interruption/timeout through the supported bounded teardown boundary. Do
  not mask the original failure, weaken assertions/budgets or use retry/skip/xfail as
  cleanup proof.
- [ ] When cleanup cannot finish, expose truthful residual ownership and actionable
  diagnostics; preserve primary result separately. A nominal pytest exit or successful
  environment reset does not mean no plugin-directory debris remains.
- [ ] Keep this fixture-origin implementation with its actual owning fixture/Worktrees
  portfolio custodians. #4122 installer-source tolerance, historical #5869 runner
  sandbox-status suppression and long-run descendant/handle cleanup retain their
  separate scopes and current owners. Do not reopen a closed historical token or use it
  as a required live claim.
- [ ] Preserve current runner-owned roots, declared effects, item/group/plugin/storage
  limits and proper least-expensive faithful tiers. Genuine unit rules may use existing
  unit admission; actual Linux/WSL filesystem/native integration retains its supported
  bounded contained/periodic lane. No mandatory broad integration release gate, all-host
  requirement or whole-portfolio prerequisite is introduced.
- [ ] Document exact original/current producer, fixture and cleanup authority mapping,
  historical hygiene fixes versus real behavior, supported/unavailable outcomes and any
  remaining unknown. Git removal, ignore policy, missing literal .pytmp strings,
  unrelated green suite counts and runner cleanup code are not the actual
  fixture-cleanup postcondition.

#### Numbered unchecked local Validation

- [ ] Qualify the actual addressed fixture/framework routes with owned synthetic data in
  the existing separately authorized lane. Record exact source, profile, platform and
  producer; prove the run's own plugin-directory aliases/artifacts are gone without
  following or changing targets.
- [ ] Cover success, primary failure, setup-before-yield failure, after-yield failure
  and supported bounded interruption/timeout. Demonstrate cleanup sensitivity rather
  than a return-code-only assertion; preserve exact primary errors and no new arbitrary
  deadlines.
- [ ] Prove pre-existing authored files, foreign links, external targets, another run
  and unresolved ownership are protected. A refused unsafe cleanup remains visible and
  retained, not falsely successful.
- [ ] Exercise cleanup refusal/failure and truthful residual ownership/diagnostics
  separately from the original primary result. Keep #5869 runner status and #4122
  installer tolerance distinct; neither supplies a substitute positive for this family.
- [ ] Record actual bounded Linux/WSL/faithful contained results and unavailable-tier
  reasons independently from source/ignore/merge/release evidence. Maintain current
  admission, effect and budget rules; no fixture, import, collection, checker or test is
  executed by this intake.

#### Numbered continuing Coordination and delivery gate

- [ ] Keep the existing fixture/test/Worktrees wave and portfolio
  audit/source/cohort/failure custodians. Do not assign implementation to the
  documentation planner or transfer process/resource leases. Apply the existing Phase7
  deduplication and domain-Plan review acceptance; no new generic ACK is required.
- [ ] Continue through current ROOT Plan/Validation/Journal and eligible OPEN
  bounded-source coordination, retaining OPEN #1303 and all incumbent
  native/mock/order/history/unknown-case and release holds. Preserve historical #4123
  provenance without leaving a closed #4123 as a required live claim token.
- [ ] Obtain a NEW normal reviewed ACTUAL source merge delivering this complete direct
  unchecked Plan, local Validation, actual global ROOT Validation entry and continuing
  Coordination. Old ROOT creation, #8178's unrelated intake, a title, child-only note,
  Draft/Done target or this proposal is not delivery.
- [ ] BEFORE #4123 supersession, read back the FULL CURRENT Active ROOT Plan/own
  Validation/Coordination/Journal and verify all original/inherited requirements,
  alternatives, unknowns, evidence tiers, actual current owners/holds and live #1303
  survive. Journal only the actual administrative intake; preserve every old row and
  historical outcome. If any genuine hold requires the source live, intake is
  incomplete, eligibility changes or custody cannot be preserved, RETAIN #4123. No
  cleanup, test, implementation or parent completion is claimed.

### Direct retained preparation lifetime from #5868

**Agent-recommended tracking amendment.** Preserve the complete original and
current refined preparation contract, existing primary maintainer and runner/
UV/containment/source/native/periodic-failure custodians. #7408's separately
delivered finite selector-routing result and Done container foundations are not
the remaining preparation-lifetime proof.

#### Plan

- [ ] **P5868-1:** Own/bound preparation process tree, wall time, memory,
  temporary/mutable state, cancellation/failure/cleanup for cold, drift/rebuild
  and prepare-only before child work.
- [ ] **P5868-2:** Retain #7382 setup-through-cleanup ordinary accounting and
  provide meaningful enforced/recorded preparation budget rather than
  post-return observation; environment admission and reuse do not replace
  holder-lifetime ownership.
- [ ] **P5868-3:** Preserve canonical governed editable dependency acquisition,
  expressly supported preparation networking without fixture/test imports or
  collection, guards/collection modes, leased-cache directory identity and safe
  reuse.

#### Local Validation

- [ ] **V5868-1:** Use synthetic UV/build fixtures for cold/rebuild/prepare-only
  timeout, descendant settlement, cancellation/failure, mutable-state/temp
  boundary and admission release; no live package/provider credentials or host
  process storm.
- [ ] **V5868-2:** Retain accounting/admission/no-pytest controls, cached reuse/
  editable source and disconnected execution selectors; do not regress shared-
  state protection or count lease ownership as resource-lifetime proof.

#### Continuing Coordination

Existing primary maintainer and runner/UV/containment/source/native/periodic-
failure custodians remain. Use the current migration-intake deduplication/
ownership gate and explicit per-origin Plan, local AND global Validation and
Journal readback. The delivered #7408 routing result remains finite credit,
not another patch or whole preparation acceptance. Normal reviewed merge and
full current receiving readback precede a separate source judgment; this paper
amendment executes no fixture, resource action or implementation and releases
no claim.

### Finite Vault warm connection-owner witness from #609

**Agent-recommended tracking amendment.** Receive the complete original and
current refined contract through Phase7 and the Phase4 Vault/clean-room wave,
without replacing the existing scenario contract or creating another effort.

- [ ] **P609-1:** Qualify warm TTL/no-reunlock continuity AND non-resumable
  in-flight work surviving OR deliberately draining cleanly, with actual
  admitted activation/routing, old-generation retirement and interruption/
  breadcrumb recovery. Preserve owner-gated safe transport and explicit
  platform/degrade limits at the finite Tier-P connection-owner boundary.
- [ ] **P609-2:** Credit #634 rendezvous/forward-ready INFO and #2486
  POSIX/systemd handoff plus its recorded isolated KeePass update/no-reunlock
  witness at their delivered scopes. Do not rebuild completed warm handoff
  or equate INFO/import/marker success with the remaining acceptance battery.
  #873 Windows DACL/cross-user security, #688 Tier-E and #743 implementation
  remain independent; none substitutes for every residual here.

#### Local Validation for #609

- [ ] **V609-1:** At the existing separately authorized finite Tier-P venue,
  record exact source/generation, warm TTL/no-reunlock, in-flight survival OR
  clean-drain, routing, retirement and interrupted-recovery outcomes.
  Preserve explicit unavailable platform/degrade facts and safe transport;
  no unsafe secret transfer, live credential/store access or whole-parent/
  native-every-host prerequisite is introduced by this intake.

### Finite four-case real MCP chaos family from #4056

**Agent-recommended tracking amendment.** Receive all remaining cases through
Phase7 and the Phase4 MCP wave; keep the warmth half credited as delivered,
not a new daemon design or latency benchmark.

- [ ] **P4056-1:** Retain ALL FOUR original real-consumer cases: supported
  immutable-runtime update/cutover mid-call with a warm daemon; client-connect
  versus idle eviction; Windows stale listener/token/generation; upstream
  failure during a pooled call followed by isolated recovery of later calls.
  Do not force obsolete in-place venv mutation or treat an atomic marker as
  complete consumer evidence.
- [ ] **P4056-2:** Use ONE caller deadline. Distinguish known pre-admission
  failure from accepted-but-unknown work; timeout, lost reply or EOF after
  transmission never permits automatic consequential-call replay. Preserve
  winner handles/active attachments, unrelated warm sessions and truthful
  results. Keep exact daemon/child/config/endpoint/control-token/runtime-
  generation custody across repeated/concurrent updates, orchestrator
  failure, drain/undrain, failed passive promotion and stale recovery.
  Unknown native evidence and active/non-expiring work remain holds, not
  age-prune targets; do not extend bounds to hide a hang.
- [ ] **P4056-3:** Specify reproducible isolated real-consumer evidence using
  admitted harmless MCP subjects, exact source/installed generation and all
  four outcomes, deadline measurements, failures and postconditions.
  Preserve warmth credit: optional cross-platform IPC, on-demand/single-
  instance/refcount/idle lifecycle (recorded default 300 seconds without
  references), stateless fallback, per-key serialization, configuration-
  replacement lease drain and poisoned-entry isolation. Shared component,
  helper and Bridge controls are partial evidence, not proof no rig exists
  anywhere or that the whole MCP rig passed.

#### Local Validation for #4056

- [ ] **V4056-1:** Qualify EACH of the four original cases with bounded
  outcome/deadline/effect/ownership/postcondition evidence, explicit
  unavailable Windows/installed/native facts and positive absence versus
  unknown. Endpoint file presence, parser success or PID age proves neither
  listener readiness nor cleanup authority.
- [ ] **V4056-2:** Preserve at-most-once negatives, unrelated-session recovery
  and every exact custody/hold above. Use the original faithful contained/
  periodic/explicit/native boundary without live consequential subjects,
  increased budgets or restored integration-as-required-CI. Keep compatible
  handoff OR bounded cold recovery and separately owned installer/old-daemon
  bootstrap/backstop policy; this does not waive #609's explicit warm witness.

Both finite families retain the existing Phase7 deduplication/domain-Plan
acceptance and serial-wave process. Normal reviewed actual merge, complete
current receiving readback and separate literal source-condition judgments
precede administrative supersession. This is direct planning intake, not
acceptance of an executed battery, current liveness or a new implementation
appointment. Preserve every incumbent Plan, Validation, Request, participant,
checked credit, journal, source and resource obligation.

### Finite ordinary developer-entry and collection semantics from #4395

**Agent-recommended tracking amendment.** Preserve the whole original and
current refinements through the existing Phase5/Phase7 boundaries.

- [ ] **P4395-1:** At the actual ordinary developer entry, provide an
  actionable package-missing/collection-time diagnostic pointing to the
  sanctioned bounded runner OR document a working supported plugin-local
  dependency/setup route after which ordinary pytest works without manual
  `PYTHONPATH` surgery. Choose one supported realization; both are not
  required. Dependency declarations or a separately known TESTING guide
  alone do not supply this caller-visible outcome.
- [ ] **P4395-2:** Reuse the existing runner, governed UV/vendored dependency
  preparation and documentation/Worktrees owners. Preserve dependency
  seasoning, containment, supervision, declared effects, runner-owned state
  and item/group/plugin budgets. Do not invent a pip-compatible recipe,
  ambient environment patch, new runner or whole-suite prerequisite, or
  reopen delivered admission/isolation/budget controls.
- [ ] **P4395-3:** Preserve accurate collection guidance: supported
  `--test-file` bounds selected file collection, whereas `-k` filters tests
  after those selected files are imported. Preparation, code-executing
  collection and execution are distinct. Credit runner/file-selection
  progress; the historical 300-second/about-five-file observation is not a
  fresh timeout, performance SLA or universal speedup claim.

#### Local Validation for #4395

- [ ] **V4395-1:** Qualify the ONE chosen original alternative at the real
  ordinary entry/setup boundary using the least expensive faithful
  separately authorized bounded lane. The diagnostic gives an actionable
  supported next step rather than a bare missing-module error; the setup
  arm verifies its documented supported dependency route without manual
  `PYTHONPATH`. A helper that bypasses the failing boundary proves neither.
- [ ] **V4395-2:** Preserve selected-file versus `-k` semantics and narrow-
  iteration guidance, including the preparation/collection/execution
  distinction. Record exact original/source/caller evidence and any
  separately authorized measurements; no present 300-second failure,
  mandatory timing experiment or broad benchmark is inferred.
- [ ] **V4395-3:** Preserve existing runner/dependency/file-selection credit,
  unit/contained/periodic boundaries, effect/state/resource budgets and
  owning cleanup. Source/docs are not actual caller/setup evidence.
  Intake authorizes no import, collection, dependency install, test,
  native trial or host operation and adds no unrelated whole-portfolio/
  every-host prerequisite.

## Validation Plan

- [ ] G5921: Resolve EVERY direct #5921 P1-P6, V1-V5 and C1-C3 clause in the Phase4 finite clean-room verdict family at its ORIGINAL source/API consumer tier: actual Python AND Python-less version/schema/evidence-complete reduction and real caller shape, explicit malformed/incomplete/unjudged NON-PASS, correct valid control/classified-jam results/exits, literal-mode separation and source-safe diagnostics. Preserve all incumbent Plan/Validation, OPEN #1303, same owners and independent #5922/#8511/#4312/#688/#1475 gates. Administrative intake is not a reducer fix or executed evidence; no live box/model/all-host/native programme or unrelated whole-portfolio prerequisite is added, and this does not waive the ROOT's own whole completion gate.

- [ ] Resolve EVERY directly retained #4123 fixture-origin Plan/local Validation clause
  above, preserving original finalizer-OR-autouse/framework alternatives, same
  custodians, ownership/foreign-target safety, setup/pass/failure/interruption/residual
  outcomes and original bounded Linux/WSL evidence tier. Administrative intake is not
  cleanup, native proof or implementation. Preserve EVERY incumbent ROOT Plan AND own
  Validation obligation; mark the parent Done only after all are resolved OR explicitly
  transferred to named accepted tracked objectives. This parent gate does not delay
  completion of the finite fixture family on unrelated portfolio work.


- [ ] Resolve EVERY direct #3991 three-case historical Vault Linux Plan/Validation
  clause at its original scope/evidence tier and alternatives, including all-three
  identity/cause/domain/assertion/qualification OR explicitly preserved unresolved
  evidence, with same custodians and current test-tier/containment bounds. Tracker
  consolidation is not a Linux pass, native/credential qualification, implementation or
  whole-parent completion.

- [ ] Resolve ALL direct3497 Plan/Validation clauses and its original alternatives
  through existing migration intake and owning Bridge/repository portfolio wave,
  preserving whole incumbent ROOT/1303/native/mock/order-leak work and same custodians.
  Tracker consolidation is not repair or native acceptance.

- [ ] Resolve every directly retained #3093 Plan/Validation clause above at its original
  finite scope and evidence tier, preserving alternatives, same owners, holds, and
  current portfolio bounds. Administrative intake is not implementation, native
  acceptance, rollout, or completion of unrelated parent work.

- [ ] Complete every directly retained #2721 Plan/Validation clause above at its original scope and evidence
  tier, preserving original alternatives, same owners, holds and current
  portfolio bounds. Tracker intake is not implementation or native acceptance.

- [ ] Complete every directly retained #1349 Plan/Validation row above at its
  original evidence tier and bounds, preserving existing owners and holds.
  Tracker consolidation is not implementation or native acceptance.

- [ ] The containment harness kills and reaps all descendants after success,
  failure, timeout, and interruption on Windows and POSIX.
- [x] Default-tier suites cannot resolve real user, Copilot, plugin, or service
  state roots.
- [ ] Default suites perform no undeclared network, service, host-state, or
  external-system interaction.
- [ ] Every retained family maps to an observable contract, proven regression,
  platform boundary, or unique falsification result.
- [ ] Removed families have an explicit replacement/subsumption record and do
  not reduce critical contract coverage.
- [ ] Focused mutation detection for critical modules is no worse after each
  wave; accepted equivalent mutations are documented rather than hidden.
- [ ] Per-plugin runtime, peak process count, peak memory, and flake budgets are
  measured in contained venues and remain within their approved bounds.
- [ ] Guard and changed-scope paths remain fast enough for routine development;
  clean-room and end-to-end tiers remain available for the claims only they can
  falsify.
- [ ] Repository documentation and contribution guidance prevent silent
  unbudgeted regrowth.
- [ ] **G5868:** Resolve every directly retained #5868 preparation Plan/local
  Validation row at its original synthetic/contained evidence tier: actual
  preparation lifetime/resources, accounting and cleanup, no fixture/test
  import or collection, governed editable acquisition and safe cache reuse.
  Preserve the same owners and current migration-intake gates. #7408's finite
  routing credit and Done container foundations do not complete this duty;
  unrelated whole-portfolio, native-every-host or new ACK gates are not added.

- [ ] **G609:** Resolve EVERY direct #609 Plan/local Validation clause at its
  finite Tier-P connection-owner scope: warm continuity AND non-resumable
  survival OR deliberate clean drain, real routing/retirement/recovery,
  safe owner/platform boundaries and preserved completed partials. Retain
  independent #873/#688/#743 scope and the exact stronger-receiver condition.
  Tracker intake is not a run or source acceptance; unrelated portfolio work
  is not an added finite-family gate.
- [ ] **G4056:** Resolve EVERY direct #4056 four-case Plan/local Validation
  clause under ONE caller deadline, pre-admission versus accepted-but-unknown/
  no consequential replay, exact ownership/generation and preserved warmth
  credit. Routing to a contained/periodic/explicit/native lane is not a pass.
  Retain every original alternative/hold, excluded coordinator scope and the
  accepted DIRECT FINAL-OPEN all-residual condition without adding a whole-
  portfolio prerequisite or waiving this ROOT's own completion gate.
- [ ] **G4395:** Resolve EVERY direct #4395 Plan/local Validation clause:
  actionable ordinary-entry diagnostic OR documented working supported
  local setup without manual `PYTHONPATH`, truthful `-k` versus explicit
  `--test-file` collection semantics and preparation/execution separation.
  Preserve existing UV/runner/dependency/file-selection credit, current
  tiers/budgets and the same custodians. Normal reviewed receiving delivery
  and complete current readback precede a separate source judgment;
  documentation is not an implemented fix or executed result. No new
  generic ACK or unrelated native/whole-parent gate is added or waived.

## Proposal

Approve the vision, framework, and phased campaign as the repository's
canonical test-portfolio program. Implementation begins with containment, not
with another unbounded full-suite run. Portfolio reductions follow only after
the inventory and evidence gates are available.

## Journal

### 2026-10-28 - Finite ordinary developer-entry intake

- Added direct #4395 Plan/local Validation, actual level-two global
  Validation and continuing coordination. Preserve diagnostic OR working
  supported setup, exact `-k`/`--test-file` collection semantics and all
  existing owners, rows, checked credit and first gates.
- Existing UV/vendored dependency preparation and explicit-file guidance
  are credit, not an ordinary-entry diagnostic or verified local recipe.
  No product-code audit, test/import/collection, install, benchmark,
  native operation or source/resource disposition occurs here.

### 2026-10-28 - Finite Vault and real MCP witness intake

- Added complete direct #609 and #4056 Plan/local Validation, actual global
  Validation and continuing coordination under the existing Phase7/Phase4
  owners, retaining every incumbent row and checked foundation.
- Preserved #609's warm continuity AND survival OR clean drain, full
  routing/retirement/recovery and stronger-receiver condition; kept #4056's
  ALL FOUR cases, one deadline, ambiguous-effect/no-replay custody and
  accepted direct final-open canonical condition. Existing warm/partial
  source credit is not missing implementation or a fresh run.
- Normal reviewed actual delivery and complete current readback precede
  separate source judgments. No test/import/collection, scenario, credential/
  endpoint/token/native probe, service operation, implementation, rollout or
  source/resource release occurs here. The effort remains Active.

### 2026-10-28 - Direct retained preparation lifetime

- Added complete #5868 preparation Plan/local Validation, actual global
  Validation and continuing Coordination under the existing primary and
  source/resource custodians. Original Request, status, participants, checked
  credit and first unresolved gates remain unchanged.
- Retained #7382 ordinary setup-through-cleanup accounting and #7408's delivered
  finite routing credit while keeping bounded preparation process/resource
  ownership open. No test/collection/import, native process, package/provider,
  implementation, source disposition or resource release is performed here.
  Normal reviewed delivery and full receiving readback precede a separate
  administrative judgment.

### 2026-10-24 - Direct retained clean-room verdict acceptance from #5921

- Plan the complete finite #5921 report/Python/Python-less/consuming-caller acceptance directly in this existing Phase4 ROOT Plan, local Validation, actual global Validation and continuing Coordination. Preserve every incumbent byte/checked row, original alternative/evidence tier, live #1303 claim and entire inline/linked historical Journal.
- Keep #5922 without-base composition and #8511 concurrent artifact ownership independent; #4312 paired setup AND judged evaluation, #688 Tier-E and #1475 WinGet keep their owners and gates. Documentation-only historical receipts are not reducer implementation or a live consumer PASS.
- This is proposed tracker intake only. NEW normal review, ACTUAL merge and FULL CURRENT receiving/root/history/source coordination readback must precede #5921 administrative supersession. Record that real receiving identity/readback only after it exists; no test, import, reducer, rig, model, installer, provider, runtime or resource operation was performed or authorized here.
- The effort remains Active. Existing owners continue the next genuinely unfinished domain Plan item through their separately admitted lane; this finite source/API contract neither depends on unrelated whole-programme completion nor waives it.

- [2026-10-24 - Direct retained acceptance from #4123](<journal/2026/10.24 backlog-intake-85-86.md>)

### 2026-10-23 - Direct retained acceptance from #3991

- Retained the complete #3991 acceptance directly in this ROOT Plan, its actual global
  Validation and continuing Coordination. Every incumbent obligation, historical
  outcome, alternative and custodian remains; neutral historical privacy/coordination
  corrections change no substantive gate. New normal review, actual merge and complete
  current readback precede source supersession. The effort stays Active; no
  implementation, tests, native result or rollout is claimed.

### 2026-10-23 - Direct retained acceptance from #3497

- Added the complete #3497 direct retained acceptance to this ROOT Plan, its actual
  global Validation and continuing Coordination. Every incumbent obligation,
  alternative, historical outcome and custodian remains; controlled historical
  identifier abstractions preserve their technical roles. A new normal reviewed actual
  merge and complete current receiving-section readback must precede source
  supersession. The effort stays Active; no implementation, test, native result, worker
  activation, rollout or resource effect is claimed.

### 2026-10-22 - Direct retained acceptance from #3093

- Materialized the complete #3093 receiving portion directly in this ROOT Plan and
  Validation, with continuing coordination. All incumbent obligations and custody
  remain; historical private identifiers, where present, are represented by neutral
  historical placeholders without changing their role or outcome. A new normal reviewed
  merge and full current readback must precede administrative source supersession. No
  behavior, test, native result, or rollout is claimed.

### 2026-10-22 - Complete retained acceptance from #2721

- Materialized the complete #2721 scope directly in Plan and Validation,
  retaining incumbent coordination, source/resource custody and all old rows.
  Normal reviewed merge and full current readback precede administrative
  source supersession. No behavior, test, native result or rollout is claimed.

### 2026-10-22 - Complete retained acceptance from #1349

- Materialized the complete #1349 residual directly in the Plan and
  Validation, preserving existing source/resource custody and partial delivery.
  Normal reviewed merge and current full readback precede administrative
  source supersession; no behavior, native run or rollout is declared complete.

### 2026-10-19 - Accept periodic-container residual ownership

Policy #7255 supersedes integration-as-required-CI and full-corpus equivalence
as release prerequisites. The recovery implementation reuses the existing
schedule/manual rotation for hardened default-full Linux plugin containers,
with immutable source/log evidence and deduplicated failure ownership. Hosted
Windows execution is quiesced until Windows containers are available; standalone
and explicit-tier residuals remain named in the machine-readable inventory.
This is routing and ownership, not a claim that those integrations passed.
The release owner retains Source/Main control and timed release/resume; this
portfolio retains periodic enrollment, failures and the later value-for-cost
replacement tournament.

### 2026-08-28 — Kickoff

- Filed #1303 as the public coordination claim.
- A provisional static scan estimated well over 8,000 source-level test
  functions across more than 500 plugin test files. Phase 2 replaces the
  estimate with a reproducible inventory before any portfolio decisions.
- Confirmed existing fast guards and opt-in end-to-end boundaries, but no
  repository-wide contract/effectiveness inventory or effect-containment gate.
- Classified the change as **vision-extending** and added the test-portfolio
  effectiveness vision.
- Chose repository-wide scope with the largest `agent-*` suites first.
- Next gate: merge this reviewed plan, then implement Phase 1 containment before
  running broad measurements.

### 2026-08-28 — Phase 1 started

- The reviewed plan is merged and the effort is active.
- Began the repository-owned containment supervisor: isolated mutable state,
  runner-owned process trees, configurable wall/process/memory/temp budgets, and
  collection-time tier/effect validation.
- Added the local admission boundary: non-guard/non-collection runs share one
  host-wide kernel lease, fail fast on contention, and may request a bounded
  wait.
- Credential-dependent explicit-tier checks may opt into host credentials and
  config roots without inheriting live Copilot session or worktree ownership.
- Added focused Linux and Windows CI coverage for the admission contract.
- Merged #1321 for process-tree containment, tier/effect policy, and resource
  budgets, then #1329 for shared contained-spawn semantics and adversarial
  detachment proof on Linux and Windows.
- The remaining Phase 1 containment gate is explicit success, failure, and
  interruption cleanup proof for the complete descendant tree.

### 2026-08-28 — Time and assurance density direction

- Added operator direction to bound individual tests, sequential file-group
  sub-suites, and aggregate plugin runs.
- Portfolio waves will split large files by contract and aggressively
  consolidate process-heavy micro-tests into evidence-dense scenario families
  that validate multiple related features per launch.

### 2026-09-04 — Heavy-run admission merged

- Merged #1314: potentially heavy plugin test runs now share one host-wide
  admission lease, while guard and collection-only paths remain concurrent.
- Default suites receive runner-owned user, Copilot, plugin, service, and
  temporary state roots. Explicit-tier checks may opt into host configuration
  without inheriting live session affinity or escaping temporary-state
  accounting.
- Added focused Linux and Windows contract coverage for admission and state
  isolation. Complete descendant cleanup after every exit mode remains the
  outstanding Phase 1 gate.
