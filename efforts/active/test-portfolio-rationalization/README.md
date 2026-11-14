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

The six finite native-timeout families #6662/#6663/#6690/#6900/#9442/#9815
below continue through the SAME primary maintainer, Phase 4 Dispatch/
Worktrees/Manager/Bridge fixture waves, Phase 5 owning-runner accounting and
Phase 7 migration-intake/domain review. Preserve OPEN #1303, all 240
incumbent check states/13 checked, complete history, original alternatives
and actual fixture, runner, production, platform, source/audit/cohort/
periodic-failure/native/release and resource custodians. #9815's broader
PR-watch service/source duties remain separate; this intake does not appoint
an implementer, merge different causes, enroll execution or transfer custody.

Ordinary review and ACTUAL complete direct Plan/local AND level-two ROOT
global Validation/Coordination/dated Journal delivery, followed by permitted
whole-current receiving/history/source/actor/custody readback, precede EACH
separate administrative source judgment. Unknown scope, condition, delivery,
owner or retained OPEN obligation means KEEP. Documentation is not a repaired
test or native/installed PASS. Required CI stays unit-only; hosted Windows
execution remains quiesced, with separately admitted periodic/explicit/native
tiers and all original process, time, storage and cleanup budgets unchanged.
No new whole-portfolio, every-host or generic acknowledgement prerequisite
is added to a finite family; this ROOT's own completion gate remains intact.

The four finite fixture/runner contracts below use the SAME primary
maintainer and existing Phase 4 Logger/shared-fixture waves (#7243/#9614),
Phase 5 owning runner/cache/disposal work (#9436/#10225), and Phase 7
deduplication/domain-Plan review. Preserve OPEN #1303, its entire inherited
acceptance and actual source/audit/cohort/periodic-failure/native/release
claims, all incumbent rows and the historical Journal. An empty assignee
does not release custody. This is documentary tracking, not an implementation
appointment, common-cause finding, execution enrollment or cleanup grant.
#7643 remains independently OPEN; #8521's explicit diagnostic/private-cache
residual and #9460/#9467's claimed fixture/production ownership remain
separate. No #9375/#10239 supported-uninstall scope is acquired.

Normal review and actual complete Plan/local AND global Validation/
Coordination/Journal delivery, followed by permitted whole-current source,
receiver, history and custody readback, precede separate source judgments.
If any clause, original alternative, current owner or condition is uncertain,
KEEP the source OPEN. Documentary supersession is not a repaired test,
native/installed PASS, parent completion or source/resource release.

The proposed finite #5245 family continues through Phase7 migration-intake
and Phase4's owning fixture/runner/hygiene wave. Preserve the existing
primary, installer/test/process/GUI/native, audit/cohort/periodic-failure,
release and resource custodians, OPEN #1303 and #6237/#6747/#7256/#7075
reservations. Its completed ten-adopter sweep and cache repair stay credited.
The inherited #6675 registration-authority family retains #6682's exact
fixture/186-case credit and the existing coordination owner's remaining-group
coverage request OR attributed accepted narrowing/tracked disposition.
Normal review, actual complete direct Plan/local AND global Validation/
Coordination/Journal delivery and permitted whole-current source/receiver/
custody readback precede any separate administrative judgment. No new
implementer, blanket ACK, expiry, execution or source/resource transfer follows.

The finite #5115 Windows-hosted container route and #5140 five-case causal
ledger below continue through the existing Phase7 migration-intake/domain
review and Phase5 workflow/Phase4 Manager waves, respectively. Preserve
OPEN #1303, all incumbent families/checkstates and the actual wrapper,
concrete-environment, fixture, runner, shared-identity, platform, audit,
cohort, periodic-failure, release and resource custodians. This is
agent-recommended documentary tracking, not an implementation appointment,
backend/test/process operation, generic ACK or claim/resource release.
Normal review, actual complete delivery and whole-current receiving/source/
custody readback precede any separate administrative source judgment.

The proposed #4835 finite contained-Windows stale-lock family continues through
the existing Phase 7 migration-intake/domain review and Phase 4 Worktrees wave.
Preserve the primary maintainer, fixture/runner/containment/shared-liveness/
launch-identity, native/source/audit/cohort/periodic-failure/release and resource
custodians, OPEN #1303 and all incumbent rows and holds. No process cleanup,
new runner, owner appointment or generic ACK is introduced. Normal reviewed
complete direct Plan/local and actual global Validation/Journal delivery and
permitted whole-current readback precede a separate source judgment; this is
neither an implemented fixture correction nor a native PASS.

The proposed #4217/#4233 families use existing Phase 7 migration-intake/domain
review and Phase 2/4 scenario and Bridge/repository waves, preserving primary,
rig/scenario/runner, dependency/fixture/platform/source/audit/cohort,
periodic-failure/native/release and resource custodians, OPEN #1303 and
all prior claims/holds. No new runner, transfer, budget or generic ACK is
appointed. Normal reviewed complete direct Plan/local AND actual global
Validation/Coordination/Journal delivery and permitted whole-current
readback precede any separate source judgment; intake is not technical
PASS, implementation, acceptance or resource release.

The complete #4217 family now proposed includes its directly inherited finite
default-allocator lane from #8511, not only the older four-outcome enrollment
request. Continue through the SAME primary maintainer, existing Phase 2/4
scenario and owning clean-room wave and Phase 7 migration-intake/domain review.
The existing rig/scenario/CI-portfolio/allocator, source, runner, native,
periodic-failure, release and resource custodians retain their responsibilities.
No container, credential, results directory, retained log, native execution or
cleanup custody moves. #5921's missing-report/reducer family and #5922 remain
independent; the existing statement keeping #8511 separate from that family is
preserved, not silently deleted or treated as an enrollment dependency.

Before any separate #4217 redundant-tracking judgment, normal review must
deliver this whole addition to the actual ROOT Plan, local Validation,
level-two global Validation, Coordination and dated Journal; read back the
complete current receiving/source contracts and actual custody under the
existing gate. Keep #4217 and #1303 OPEN now. A proposal, map, old intake merge,
unfinished code or generic new ACK is not that acceptance or a new barrier.
This tracking proposal authorizes no test, workflow, runtime, native, cleanup,
resource allocation or implementation action.

<!-- backlog-reconciliation:7556:qualified-sixteen:4128:Coordination -->

The proposed #4128 family retains its finite filesystem-registry owner and
the existing Worktrees/registry/installation-context/fixture/runner/source
custodians. #1303 retains broad containment; closed #1906 is the distinct
historical persistent OS-environment mechanism, not a live claim or another
transfer. Existing Phase 4 wave/Phase 7 intake, all budget/effect/storage
limits and actual source/resource holds remain; no executor is appointed.

The later #4128/#10154 Manager runtime-source lane uses the SAME existing
Phase 4 Worktrees/Manager wave and Phase 7 intake, under the incumbent
primary maintainer and actual Worktrees-registry, Manager-source/delivery/
provider/queue, fixture/runner/containment, native/source/audit/cohort/
periodic-failure, release and resource custodians. Preserve OPEN #1303,
every incumbent participant, requirement, alternative, check state and
actual claim/hold. This does not activate an effort, appoint an implementer
or create another registration authority.

Normal review and actual direct Plan/local AND ROOT-global Validation/
Coordination/dated-Journal delivery, followed by complete current source,
discussion, receiver, provenance and custody readback, precede a separate
#4128 tracking-retirement judgment. Redundant tracking retirement is not
completion of either technical lane or source/resource release. Genuine
intervening source-owner conditions remain; unfinished implementation,
a generic new ACK or unrelated whole-portfolio completion is not an added
intake prerequisite. This proposal performs no import, collection, test,
native interaction, provider operation, registration repair or cleanup.

The proposed finite #1012 conditional-field synchronization family below uses the
existing Phase 4 Worktrees/Manager wave and Phase 7 intake, not a new timing
campaign. Preserve the actual form/test, production-Picker, portfolio and
source/resource custodians, OPEN #1303 and all incumbent families. Proposed
tracking does not appoint an implementer, authorize collection or execution,
or make an isolated passing retry a completed repair.

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

The finite #6093/#6138 families enter through existing Phase 7
deduplication and normal domain-Plan review into Phase 4 ONLY:
Logger/Manager link-fixture capability and Budget portable-PowerShell
timeout disposition remain distinct families under this same portfolio
primary maintainer. Existing source/fixture/investigation custodians,
OPEN #1303, all incumbent rows/check states, first unresolved gates,
native/periodic/release/resource holds and historical Journal remain.

#6093 retains all LF/LFV conditions, its inherited #9618 taking statement
and FULL CURRENT OPEN parent readback before administrative retirement.
Its held fixture source supplies no current-repair assertion.
#6138 retains its existing investigator, actual-candidate-first
source-correction OR bounded-same-SHA-retry alternative, original bounds
and independent #6131/#6135 repair.

Normal review must actually merge the complete direct Phase 4 Plan/local
Validation, actual level-two global Validation, Coordination and dated
Journal before domain receiving is accepted. Ledger publication alone is
not acceptance. Separate permitted whole-current source/receiver/custody
readback and each literal source condition govern any later administrative
judgment. Both sources stay OPEN now; no generic ACK, implementation
appointment, execution, activation, privilege change or custody release
is introduced.

## Context

The [weighted native fixture/runner cohort](../backlog-reconciliation/semantic-theme-results.md#native-fixturerunner-cohort)
indexes related #7243/#7643/#8521/#9436/#9460/#9467/#9614/#9699/#10225
cases through existing fixture, runner and platform owners. The shared
neighborhood's #9375/#10239 supported-uninstall cases retain separate caller
custody. This is snapshot navigation, not accepted whole-source intake,
changed budgets, test execution, cleanup or source/resource transfer.

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


#### Six distinct finite native-timeout families

These are direct unchecked tracking obligations under the existing Phase 4
waves and Phase 7 intake, not a common-cause claim or a new native programme.
The [whole-contract comparison](../backlog-reconciliation/native-timeout-cohort.md)
records original failures, completed diagnostic/source gains and the limited
meaning of sixteen completed-work relations. Same-source isolated or broad
passes remain evidence, not proof of the unknown cause.

##### #6662 - Dispatch completion boundary

- [ ] **N6662-P1:** Retain Windows
  `test_completion_records_worker_failure_and_clears_inherited_staging`,
  both original 15-second waits for `completed_at` in
  `reconcile-status.json`, and every producer/consumer, failure-result,
  inherited-staging and cleanup assertion. Keep the focused and broader
  passes distinct from the original two failures.
- [ ] **N6662-P2:** Credit completed #6665 bounded stderr, last-status,
  synthetic-receipt and log-tail diagnostics; do not request the same patch
  again. Through the existing owner, resolve the finite remaining failing
  boundary/correction OR record an attributable requirement-preserving
  disposition of its unknown cause. Silence does not identify disk,
  filesystem, allocator or service failure.
- [ ] **N6662-P3:** Use the least expensive faithful existing fixture/runner
  boundary, preserving real completion/result delivery and unchanged
  deadlines. #9347 Index recovery, #10144 Bridge mutation fixtures and
  #10264 extracted installer admission are different contracts, not this
  completion receipt or authority to run hosted Windows.
- [ ] **N6662-V1:** When separately authorized, qualify the actual original
  completion/status consumer with bounded failure and successful controls;
  retain source/selection, last status, actual elapsed stage, primary and
  cleanup outcomes. A passing retry alone cannot explain the earlier
  failures; no new native run is required by documentary intake.
- [ ] **N6662-V2:** Record the causal correction OR explicit retained unknown
  disposition, preserve all original assertions and 15 seconds, and distinguish
  completed diagnostic credit from actual test acceptance. No deadline
  increase, omitted test or generic CI pass substitutes for this finite result.

##### #6663 - Real twelve-allocator lock admission

- [ ] **N6663-P1:** Preserve
  `test_concurrent_allocation_serializes_bag_positions`, twelve ACTUAL
  allocator subprocesses, both original two-second lock-admission failures,
  all two-generation/six-profile-per-generation position, capacity and
  containment assertions, and the production two-second bound.
- [ ] **N6663-P2:** Keep the original 210-pass/two-failure observation,
  same-source isolated 1.78-second pass and later complete 211-case pass
  distinct. Diagnose the actual lock boundary IF the failure recurs,
  under its existing owner; do not create a fresh mandatory native campaign,
  infer CPU/filesystem/SQLite causality or widen the deadline.
- [ ] **N6663-P3:** Preserve the real-process concurrency contract rather
  than replacing it with #6667's six reader fixtures, #9693 generation-cache
  work, #10319 decline closeout or #10262's actor-free/faked lease controls.
  Their bounded credit neither repairs nor duplicates this admission case.
- [ ] **N6663-V1:** On an authorized recurrence, retain actual per-allocator
  admission/position, source, elapsed and cleanup evidence for all twelve,
  both generations and six profiles, without weakening capacity/uniqueness
  assertions or the two-second production limit.
- [ ] **N6663-V2:** Distinguish reproduced failure/causal correction from
  unchanged passing observations and an unavailable or non-recurrent case.
  Preserve the source's CONDITIONAL recurrence requirement and existing
  containment/tier/owner boundaries; no newly unconditional trial is added.

##### #6690 - Version-change Bash registration child

- [ ] **N6690-P1:** Preserve the exact Windows `subprocess.communicate()`
  timeout at the version-change Bash registration child and original source
  `fa0c29604ef05beb867a567e6c539eb771bc46b8`, verified cleanup and unchanged
  bounds. Same-source isolated 46.57-second and 159-case selection passes
  are not causal repairs; #6682's Linux authority-reader fixture is separate.
- [ ] **N6690-P2:** Diagnose the actual child, captured-stream/pipe and
  registration boundary before selecting a supported correction. Preserve
  real Bash consumption, producer-version/session/CWD coalescing identity,
  same-launch reuse, version invalidation, empty context on replay and owning
  shutdown. Do not mock past the child or infer cause from empty output.
- [ ] **N6690-P3:** Keep #10305's opt-in native unit-packet tooling and
  #10065's prerequisite diagnostic credit separate. Protected whole-message/
  source holds remain in force; no linked subset retry, whole Picker
  completion or new execution/deployment authority follows from intake.
- [ ] **N6690-V1:** In the existing separately authorized bounded lane,
  exercise the actual version-change child and unchanged coalescing/replay/
  stream/cleanup controls. Attribute stage/exit/timeout and primary/cleanup
  outcomes to the real consumer; fixture collection or an unrelated pass
  cannot qualify this Windows boundary.
- [ ] **N6690-V2:** Retain exact source/selection, original failing and passing
  observations, unknown cause and all original limits until an attributable
  finite correction or requirement-preserving disposition is established.
  No threshold increase, skip, held-source study or installed acceptance is
  licensed by this tracking amendment.

##### #6900 - ConciseView error latency and pane acceptance

- [ ] **N6900-P1:** Preserve measured 13.272-second `WriteError`,
  15.188-second total against 15 seconds, approximately 47-millisecond lock
  and six-millisecond catch. Those measurements contradict lock-delay
  attribution, not every native cause. Retain NormalView/minimal-File
  2.453-second total/0.355-second inside and ConciseView/Command
  2.391-second controls as DIFFERENT invocations.
- [ ] **N6900-P2:** Diagnose the ACTUAL error-output/pane boundary through a
  minimal faithful regression OR owning isolated-fixture correction. Preserve
  exit 3, sanitized stderr, pane exclusivity, deletion/receipt behavior,
  consumer 15 seconds and original item 30 seconds; do not replace broader
  lifecycle acceptance or silently change a host's default error view.
- [ ] **N6900-P3:** Credit #6904's merged timing/sink work only at its actual
  nonpassing pane scope; #9470's literal pane environment and #9896's
  process-only/desktop distinction and `pump()` are different contracts.
  Persistent User-scope OneDrive environment drift remains unattributed:
  no rollback, environment mutation, causal assertion or protected retry.
- [ ] **N6900-V1:** In an independently authorized existing lane, exercise
  actual sanitized error emission and pane/result/receipt assertions with
  the original consumer/item budgets. Keep primary, diagnostic and cleanup
  outcomes distinct; timing instrumentation alone is not pane acceptance.
- [ ] **N6900-V2:** Compare faithful invocation/error-view controls without
  altering machine settings, preserve failed measurements and unknowns, and
  record the narrow correction OR explicitly retained causal uncertainty.
  Neither an isolated green nor a broader lifecycle result waives this case.

##### #9442 - Individual queued-handoff worker budget

- [ ] **N9442-P1:** Preserve
  `actual_queued_engine_keeps_each_scope_and_original_invocation`, both
  original native worker failures at the INDIVIDUAL 20-second limit,
  intervening 72-case pass and later two-case 21.64-second aggregate.
  Keep actual scope/original-invocation, Job, process and cleanup assertions.
- [ ] **N9442-P2:** Through the existing owner, diagnose actual monotonic
  stage boundaries and child/queue/pipe/import/installer handoff without
  inferring any cause or child absence from empty output. Resolve that finite
  boundary OR retain an attributable requirement-preserving unknown
  disposition; #9436's cached-rebuild/preparation denial remains separate.
- [ ] **N9442-P3:** Credit ONLY the later aggregate item-30 to item-60
  correction for two up-to-20-second workers. It neither increases each
  worker's 20 seconds nor explains their original failures. #10167 history
  avoidance, #9669 bootstrap holds and #9913's Python-free plan are not
  causal receipts or permission to relax required CI.
- [ ] **N9442-V1:** At the separately authorized actual queued-engine
  consumer, retain both scope/invocation identities, monotonic stages,
  individual elapsed limits and Job/cleanup outcomes; distinguish individual
  timeout from aggregate runner interruption without launching a new programme.
- [ ] **N9442-V2:** Preserve original failures, intervening and later passes,
  the finite aggregate-budget credit and unresolved individual cause.
  Empty output, a passing aggregate or a source-only plan cannot establish
  absent child, repaired preparation or unchanged per-worker acceptance.

##### #9815 - Four real readers entering before stop

- [ ] **N9815-P1:** Retain the three-second wait for FOUR real readers to
  enter and both combined 210-case/209-pass/one-timeout failures. Keep the
  unchanged isolated pass, 45-case service pass, coupled remainder 165
  and earlier combined 205/207 passes distinct from the failed selections.
- [ ] **N9815-P2:** Correct deterministic real-thread startup/scheduling
  through the owning fixture, retaining writer ownership until ALL reads
  settle and the actual drain/cancellation assertions. Preserve watchdog,
  operation, process and cleanup bounds; do not raise the three-second
  deadline, skip a selected case or mock away real concurrency.
- [ ] **N9815-P3:** Preserve #9795's actual current-head required CI as an
  independent gate; isolated packets do not waive it. #9641's generation
  plan, #9762's installer progress and #10264's extracted installer updates
  do not test this entry condition. Broader PR-watch service/source/resource
  obligations stay with their existing owners outside this finite fixture.
- [ ] **N9815-V1:** In the separately authorized existing lane, reproduce
  actual four-reader startup and full writer/drain/cancel settlement with
  unchanged deadlines, original isolated and combined selected controls and
  truthful failure/cleanup reporting. No isolated pass is a combined PASS.
- [ ] **N9815-V2:** Record exact source/selection and available current-head
  required CI independently from fixture outcomes; keep failed, skipped,
  unavailable and unexecuted cases explicit. Tracking delivery, a different
  service repair or unrelated whole-portfolio progress is not this acceptance.

Sources: #6662, #6663, #6690, #6900, #9442 and #9815, each with its complete
original and refinement. OPEN #1303 remains the live portfolio coordination
token; historical source trackers are not required live claim tokens after
a separately qualified administrative supersession.

#### Finite Logger and HostProfile link-fixture capability from #6093

Proposed Phase 4 Logger/Manager fixture-wave receiving through Phase 7
migration-intake and domain-Plan review. The portfolio remains the single
canonical domain owner; existing Logger and HostProfile source custodians
retain their separate implementation scopes.

The five original Logger nodes are
`test_explicit_repo_config_env_rejects_symlinked_ancestor`,
`test_explicit_repo_config_env_rejects_symlinked_non_git_root`,
`test_explicit_repo_config_env_rejects_symlink_masked_by_dotdot`,
`test_repo_config_rejects_symlinked_candidate`, and
`test_repo_config_rejects_symlinked_ancestor_directory`.

The inherited #9618 HostProfileTests controls are
`test_linked_or_hardlinked_owned_profile_is_refused` and
`test_incomplete_receipt_publication_and_linked_ancestor_are_refused`,
including their direct promotion-unit packet invocation. Preserve that
source's taking statement and unchanged nodes, effects and budgets.

##### Direct Plan

- [ ] LF1 (#6093) - Establish an explicit portable capability contract OR
  native reparse/junction coverage that genuinely proves the five original
  scaffold link-rejection boundaries: explicit env-config ancestor, non-Git
  root, link masked by dotdot, config candidate and ancestor directory.
  Correct the directory-link target_is_directory=True fixture declaration.
  Do not require both alternatives or replace rejection with ordinary
  relative-path acceptance.
- [ ] LF2 (#9618) - Use the existing explicit Windows WinError-1314 skip
  convention for actual symlink controls in the two named HostProfileTests
  cases, including their direct promotion-packet invocation. Preserve
  unrelated hardlink/incomplete-publication assertions even when only
  symlink creation lacks capability; do not skip the whole composite test.
- [ ] LF3 - Retain real POSIX link rejection, selected test nodes/effect
  allowances/budgets, production trust rules and otherwise modeled host
  policy. Report skipped/unreached assertions as such, never passes.
  No automatic elevation, Developer Mode/host setting change, trust
  relaxation or corpus-inventory change.

##### Local Validation

- [ ] LFV1 - Under separately authorized ordinary native Windows capability,
  run the actual Logger scaffold and direct host-profile promotion-unit
  paths without undeclared elevation. Show explicit unavailable symlink
  controls, surviving independent assertions and completion of the selected
  packet; retain real rejection coverage where symlinks/reparse controls
  are supported and on POSIX.
- [ ] LFV2 - Verify each original rejection boundary still runs when its
  capability is present, including directory and dotdot masking cases.
  Preserve genuine non-1314 failures and truthful pass/skip/failure results.
  Historical suite counts and passing inventory behavior are not a fresh
  fixture-repair receipt.
- [ ] LFV3 - Keep the existing claimants, source/portfolio owners and gates;
  read back the FULL current OPEN parent before administrative retirement.
  Acceptance stays unchecked. This intake creates no link, runs no test
  and changes no privilege, selected packet or production policy.

#### Finite portable PowerShell spaced-path timeout disposition from #6138

Proposed Phase 4 Budget fixture-wave receiving through Phase 7 intake and
domain-Plan review. Preserve the existing bounded release-blocker
investigator; #6131 / PR #6135's watch-generation repair remains independent.

##### Direct Plan

- [ ] **B6138-P1:** Retain actual Dev run38106720038, Budget
  job114376374185, head e5990b0f0940e893c0dffca6c5707f11d4333d42:
  `test_powershell_self_stage_preserves_spaced_paths_and_force`
  exceeded the 30-second item limit while waiting for its PowerShell child;
  the job reported 1 failed/52 passed. Diagnose the actual subprocess and
  contained-test assumptions before budget edits or release retry.
- [ ] **B6138-P2:** Preserve the owner's unchanged contained Linux
  8.17-second pass as limited evidence, explicitly not CI-flake proof.
  Inspect the attributable actual candidate result THEN choose a justified
  source correction OR bounded same-SHA retry, not both. A genuine
  regression requires a versioned fix-forward through normal review and
  release gates; retry is not a substitute. Keep the original bounds and
  existing investigator's decision custody.
- [ ] **B6138-P3:** Preserve spaced profile/payload/runtime paths, actual
  self-staging and Force forwarding. Credit real materialization and
  bounded-copy fixture improvements at their delivered scopes. This is a
  portable PowerShell case skipped on native Windows; native-only,
  unrelated-plugin, absent-job, cancelled or skipped results do not prove
  this consumer passed or its timeout was causally dispositioned.

##### Local Validation

- [ ] **B6138-V:** At the existing separately authorized finite portable
  consumer boundary, retain exact candidate/source, invocation and selected
  test, literal child/item outcomes and unchanged budgets, original failed
  receipt and limited Linux pass. Record the investigator's justified
  correction-OR-bounded-same-SHA-retry disposition without hiding failures,
  assuming a flake or substituting native/unrelated green. Preserve
  unit-only required CI, proper existing contained/periodic/native tiers,
  owning cleanup and independent #6131/#6135 credit. Documentary intake
  runs no installer, workflow or test and grants no retry or rollout.

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

### Proposed finite #1012 conditional-field synchronization

_(Agent-recommended tracking proposal; normal review, actual delivery and complete
current readback remain outstanding.)_

- [ ] **P1012-1:** Preserve the actual Other-field visibility and collected-value
  assertions in the production Picker form test. Synchronize on the actual
  visible state/event instead of assuming one generic `pilot.pause()` settles
  it. Relocation, a larger fixed sleep or a weakened expectation is not repair.
- [ ] **P1012-2:** Through the existing wave/intake owners, retain the historical
  full-run failure separately from the isolated passing rerun. Use the cheapest
  faithful bounded validation tier; no unrelated whole-portfolio or native-UI
  qualification is added to this finite synchronization correction.

#### Local Validation for #1012

- [ ] **V1012-1:** Exercise actual conditional visibility/event synchronization
  while retaining the field and collected-value assertions. Include deterministic
  bounded slow-visible and never-visible controls, with a truthful failed
  expectation when the field never appears. These controls are proposed
  validation refinements, not results already obtained.
- [ ] **V1012-2:** Do not substitute relocation, a green isolated retry, skip,
  xfail, deadline inflation or fixed-sleep growth for the exact contract.
  Preserve current containment, admission and owning cleanup; this document
  authorizes no import, collection, test, native interaction or host action.

<!-- backlog-reconciliation:7556:qualified-sixteen:4128:Plan -->

### Proposed finite all-authority filesystem-registry isolation

_Agent-recommended direct retention through the existing Worktrees wave and
domain intake. Existing checked isolation foundations remain credit; this
is not a finding that every test is uncontained or a live escape occurred._

- [ ] **T4128-P1:** Keep temporary-only resolution AND ACTUAL writes for
  BOTH `projects.yaml` and `repos.yaml` under every supported registry
  authority, including explicit validated installation-context `pluginRoot`
  and inherited overrides. Synthetic context stays runner-owned; a valid
  live context must not silently override isolated HOME.
- [ ] **T4128-P2:** Cover first collection/import/session-fixture work
  before function-autouse isolation, ordinary tests, workers/subprocesses
  and teardown. Credit real legacy HOME/AGENT_HOME/USERPROFILE/Path.home
  isolation, #6874's actual-writer correction and #7483's early session-seed
  protection. A literal session fixture is a suggested mechanism, not
  justification to discard correct per-test isolation.
- [ ] **T4128-P3:** Exercise actual reconcile/prune/write for BOTH registries
  with controlled hostile SYNTHETIC ambient/context configuration. Observe
  temporary outputs, unchanged synthetic outside state, exceptional
  setup/teardown, truthful environment/config restoration and subprocess
  propagation. Never use a real registry or overwrite concurrent operator
  state as fixture cleanup.
- [ ] **T4128-P4:** Preserve all original assertions, effect/item/group/
  plugin/storage limits and cheapest faithful unit/contained/periodic/native
  tiers. Persistent User/Machine environment effects remain separately
  owned. No blanket context-disabling workaround, real OS mutation or
  whole-portfolio prerequisite substitutes for this finite authority proof.

### Additive Manager runtime-source containment from #4128/#10154

_Planned tracking intake only. Keep incumbent T4128-P1-P4 and T4128-V1,
their check states, participants and source/resource duties unchanged.
Filesystem-registry isolation and Manager real-update delivery are distinct
consumers; neither completes the other._

- [ ] **T4128-P5:** Isolate
  `test_runtime_source.py::test_real_update_error_record_requires_explicit_missing_argument`
  BEFORE its real `update_orchestration.run_update_steps`/error-record path,
  including earlier import/collection/fixture work reaching those authorities.
  Bind source/runtime selection, home, control-plane provider registry,
  selected Manager installation and queue/global authority to runner-owned
  synthetic state, not only a temporary Manager runtime root. Audit adjacent
  runtime-source tests reaching registration, launcher, daemon or update-journal
  side effects and contain the same boundaries.
- [ ] **T4128-P6:** Preserve meaningful real-orchestration missing-argument
  and error-record coverage; do not mock away the entire orchestration to
  obtain containment. Temporary provider registration must not escape to
  an outside selected installation or be consumed by a later ordinary update.
  Credit #5122's distinct-caller repair without treating it as proof that this
  module enters the sandbox. Preserve expected outputs/errors and actual
  caller propagation.
- [ ] **T4128-P7:** Propagate the same authority isolation into worker
  subprocesses and restore temporary environment/configuration bindings
  truthfully across normal and exceptional setup/teardown. Preserve primary
  failures and truthful cleanup outcomes; never overwrite concurrent operator
  state or reproduce against a live registry. Keep #10118 legacy upgrade,
  normal queue recovery policy and persistent User/Machine environment effects
  separately owned. Retain delivered Worktrees autouse/actual-writer/session-seed
  and shared-runner protections at their actual scopes.

#### Focused local validation for the Manager lane

- [ ] **T4128-LV2:** Under hostile SYNTHETIC ambient/source/context input,
  exercise the actual Manager runtime-source real-update/error-record
  consumer and adjacent delivery paths without bypassing meaningful
  orchestration. Seed outside-surrogate provider/selected-Manager/source/
  queue state; verify its identity/content stays unchanged while intended
  sandbox effects and expected missing-argument/error records occur. Verify
  a later ordinary synthetic consumer cannot discover a leaked temporary
  provider. Include wrong/unavailable authority refusal/noninterference,
  exceptional setup/teardown and worker propagation/restoration controls.
  Outside state is synthetic, never the live installation. Runtime-root
  redirection, blanket context disabling or an isolated green retry is not
  the whole proof. Use the original bounded faithful consumer tier and
  existing execution/cleanup budgets, not a new every-platform/native programme.

### Proposed finite intake: clean-room enrollment and client warnings

_(Agent-recommended documentary refinement; no test, CI, package or
implementation operation is authorized.)_

- [ ] **T4217-P1:** Map ALL FOUR deterministic original outcomes to actual
  owning scenarios AND jobs: plugin installation, actual first-use runtime
  provisioning, installed-command health AND chosen core-lifecycle round
  trips. Reuse the existing disposable rig, not another runner. Manual
  scenario capability or one passing run is not automated enrollment.
- [ ] **T4217-P2:** Enroll missing chosen coverage through authorized bounded
  periodic/disposable lanes with prerequisites, source/profile, reporting,
  failure AND owning cleanup. Missing capacity, skip or unavailable remains
  non-PASS; default full-plugin selection does not imply a clean-room matrix.
- [ ] **T4217-P3:** Preserve unit-only required PR/promotion policy, existing
  admission/coverage, effect/containment/item/group/plugin/storage limits,
  native/release/source/failure custody and narrow exceptions. Credit the
  real rig, installation-context lanes, scoped five-phase credential-free
  Linux case and partner Tier-P setup without calling every outcome enrolled.
- [ ] **T4217-LV:** Record the exact four-outcome source/scenario/job map and
  selected enrollment, then meaningful real bounded positive AND intentional
  failure/reporting/cleanup controls through existing authorized lanes.
  Preserve truthful unsupported/unavailable and original tiers; no broad
  required integration, new runner or every-platform/adopter program.
- [ ] **T4217-P4:** Retain #4217's complete inherited #8511 finite default
  host-results allocator contract as an independently progressable lane beside
  T4217-P1/P2 enrollment. Atomically allocate an exclusive default results
  directory for each simultaneous run, including same-second starts and
  distinct container-name suffixes. BOTH `cr-report.json` AND copied `cr-logs/`
  must remain attributable to their correct independent run; unique container
  names alone do not provide independent artifact ownership. Preserve the
  existing distinct explicit `CR_RESULTS_DIR` workaround outside the checkout
  and its intended override semantics. Container-local retained logs remain
  evidence only under their actual owners until authorized teardown.
- [ ] **T4217-P5:** Check the corresponding PowerShell allocation path without
  assuming either safety or an observed Windows incident. Preserve the actual
  Linux collision separately from static PowerShell susceptibility, and add
  the focused collision/concurrency regression without expanding required
  native integration or waiting for the whole enrollment program.
- [ ] **T4217-LV2:** Exercise simultaneous default allocations in the same
  timestamp interval with different container suffixes. Verify distinct
  exclusive destinations, correct independent report AND copied-log contents,
  collision/refusal behavior, explicit-directory override and truthful printed
  paths. A successful container execution, unique container name or shared
  host report does not prove correct artifact attribution.
- [ ] **T4217-LV3:** Record Linux observation, corresponding PowerShell source
  findings, focused regression results and any independently authorized native
  qualification as distinct evidence grades. Preserve original unit-only
  required PR/promotion policy, existing periodic/explicit tiers, prerequisites,
  budgets and owning cleanup. Missing capacity, skip, incomplete cleanup or
  unexecuted native coverage remains unavailable/non-PASS.

- [ ] **T4233-P1:** Retain only the exact original supported web-client
  dependency SET and public-suite deprecation outcome across supported
  platforms. Identify original warning/class, supported combination and
  actual causal/owner mapping OR an attributable requirement-preserving
  disposition; otherwise keep the precise UNKNOWN.
- [ ] **T4233-P2:** Credit modern combined requirements and real
  ASGITransport/TestClient fixtures. Runtime FastAPI>=0.141.1 and
  HTTPX>=0.28.1,<1 plus dev HTTPX>=0.27 do not yield an old effective
  HTTPX floor. Declarations/fixtures are not warning-free suite receipts.
- [ ] **T4233-P3:** Preserve meaningful unsuppressed diagnostics and supported
  platform limits. A bumped version/title, empty generic warning search,
  unrelated historical artifact or blanket filter is neither original
  outcome nor attributable disposition. Excluded bot-provider #152 stays
  metadata-only; no linked BODY reconstruction or reproduced-failure claim.
- [ ] **T4233-LV:** Resolve exact warning/dependency-set attribution and
  original supported-suite/platform result OR rightful attributable
  disposition through appropriate existing evidence/tier, retaining
  modernization credit and uncertainty. No all-future ecosystem-warning,
  fresh hardware, paid/native/every-host or package-install program.

### Finite contained-Windows stale-lock fidelity from #4835

**Agent-recommended tracking amendment.** Preserve the entire original and
current refinement through the existing Phase 4 Worktrees/Phase 7 boundary.

- [ ] **P4835-1:** Make
  `test_active_launch_pids_prunes_a_dead_pid_and_excludes_it` deterministic
  under the supported contained Windows runner: the dead PID is excluded
  from `active_launch_pids()` AND the stale lock is pruned. Preserve both
  meaningful assertions; this is not evidence of a production lock leak.
- [ ] **P4835-2:** Retain the distinction between the fixture's own
  kill/wait/`Popen` release and an independently retained containment-owner
  handle to the exited process object. Existing `del`/garbage collection and
  direct pytest success are credited, not a new fix or proof that the
  containing runner released its handle.
- [ ] **P4835-3:** Choose a narrow fixture correction OR a causally supported
  exit-state correction without depending on when the runner releases
  handles. Successful `OpenProcess` alone is not running-versus-exited
  discrimination. Preserve start/birth identity, reused-PID safeguards and
  access-denied/unsupported/UNKNOWN as protection vetoes.
- [ ] **P4835-4:** Retain actual fixture/runner/shared-liveness/launch custody
  and existing containment/source credit. The reported #4839 anchor-guard
  fix explicitly left this case outstanding; it is not a liveness receipt.
  Do not weaken production safety or widen this finite test interaction
  into a process-retirement policy.
- [ ] **P4835-5:** Attribute the actual finite source/fixture/contained result
  and any unavailable evidence. A corresponding finite source correction
  can establish completion when found; retention is not simply absence of
  a fresh native PASS, nor a demand for all portfolio or every-host work.

#### Local Validation for #4835

- [ ] **V4835-1:** Qualify the actual named stale-lock consumer at its supported
  contained Windows boundary with an independently held exited handle,
  running process, reused birth identity and access-denied/unsupported/
  UNKNOWN controls. Check PID exclusion AND lock pruning without relying
  on the fixture's own reference-release timing.
- [ ] **V4835-2:** Demonstrate meaningful positive/negative sensitivity for the
  selected fixture OR exit-state correction. An exited-but-queryable object
  and a running process must not be conflated; no unknown target is reaped,
  assertion weakened or direct-run green relabeled contained evidence.
- [ ] **V4835-3:** Keep the original finite tier, unchanged budgets and current
  owners. Documentary/source evidence is not an executed native result;
  no test, process operation, broader lifecycle campaign or unrelated
  whole-portfolio prerequisite is authorized by this tracking amendment.

### Finite Windows-hosted container pathway from #5115

**Agent-recommended tracking amendment.** The original permits ONE
supported route; the existing Done Linux devcontainer outcome stays Done.

- [ ] **P5115-1:** Document AND test the local/interactive Windows-hosted
  Docker Desktop WSL2 Linux-container pathway: invoke the wrapper within
  WSL against that backend OR support native-Windows invocation with its
  necessary path handling. One chosen alternative suffices; do not require
  both or substitute hosted Windows process-containment CI.
- [ ] **P5115-2:** Preserve the actual wrapper's volume/read-only-rootfs,
  dropped capabilities, absent Docker socket, disconnected test network,
  credential/resource bounds and owned cleanup. Attribute the real host,
  backend and invocation path; generic Linux on the same hardware or a
  Dockerfile is not the requested Windows-hosted backend witness.
- [ ] **P5115-3:** Credit #5040/#5093/#5095/#5100 Linux isolation and
  #5117/#5335 documentation at their delivered scopes. Linux-only guidance
  without an enforced platform gate, direct host paths or absent explicit
  adaptation does not independently prove a native invocation fails.
  Keep supported, unsupported, unvalidated and refused outcomes truthful.

#### Local Validation for #5115

- [ ] **V5115-1:** Qualify documentation AND a bounded actual invocation for
  the ONE chosen WSL-backend OR native-Windows route, retaining the exact
  Windows-hosted Docker Desktop WSL2 boundary and relevant path behavior.
  Existing Linux proof and hosted Windows Job controls are distinct tiers.
- [ ] **V5115-2:** Preserve isolation, concrete-environment admission,
  original resource/credential/cleanup limits and existing owners; record
  unavailable combinations without a passing inference. No new runner,
  both-alternatives, every-host or whole-portfolio prerequisite is added.

### Finite five-case Manager causal ledger from #5140

**Agent-recommended tracking amendment.** Preserve ALL FIVE original cases
and their Linux/WSL2 context; do not assert that all still fail today.

- [ ] **P5140-1:** Retain the FOUR exact
  `tests/test_harness_state.py` outcomes:
  `test_mis_registered_repos_flags_non_git_checkout` (not-git),
  `test_mis_registered_repos_flags_missing_path` (missing),
  `test_mis_registered_repos_rejects_empty_dot_git_directory` (not-git),
  and `test_mis_registered_repos_surfaces_inconclusive_probe_separately`
  (UNKNOWN, not clean). Credit the reported #6164 exact-WSL fixture
  correction without reopening those repairs or borrowing another
  platform's path; retain intentional `include_wsl=False` controls.
- [ ] **P5140-2:** Resolve the FIFTH exact
  `tests/test_mux_daemon_cutover_helper.py::test_terminate_mux_daemon_pid_accepts_a_matched_real_process`
  historical recognized-process/False result through its authoritative
  causal repair or owning validation record. Preserve the actual owned
  matched process, real helper True AND process exit. If correspondence
  is unavailable, keep a named tracked causal uncertainty OPEN; recording
  uncertainty is not a passing repair or completion.
- [ ] **P5140-3:** Credit capability checks, recognition waiting and later
  identity/process-discovery improvements only at their evidenced scope.
  Generic mismatch, root recognition, an unrelated pass or #5167's
  claims/topbar repair cannot stand in for the fifth causal outcome.
  Preserve original assertions and the incidental #5136 scope distinction.
- [ ] **P5140-4:** Keep original Linux/WSL2 Ubuntu 24.04 versus hosted-CI
  uncertainty explicit. Historical approximately 0.1-0.4-second observations
  are not new bounds, current reproduction or a new native/all-platform
  matrix. Preserve actual fixture/source/platform ownership and budgets;
  this intake grants no production termination or process probe.

#### Local Validation for #5140

- [ ] **V5140-1:** Maintain an exact FIVE-row original-to-current ledger:
  four finite WSL fixture credits with not-git/missing/UNKNOWN controls,
  and the fifth real matched-process/helper/exit causal disposition or
  explicitly retained named uncertainty. No assertion deletion, generic
  mismatch proxy or requirement to redo already credited work.
- [ ] **V5140-2:** Attribute exact source/fixture/platform and causal versus
  merely related evidence through the existing bounded owning lane.
  Preserve current containment, source/native distinctions and independent
  holds; a finite family does not wait on unrelated portfolio completion.

### Proposed finite containment credit and diagnostic tail from #5245

_(Agent-recommended additive tracking proposal; the original hygiene sweep
is delivered, not work reopened by these unchecked accounting rows.)_

- [ ] **P5245-1:** Preserve real Git fixtures rooted in their own throwaway
  repository, explicit real shell/PowerShell subprocess bounds and contained
  HOME/profile/XDG/temp/plugin/Python environments for provisioning,
  snapshot and retry. Source-only mocks cannot replace these boundaries.
- [ ] **P5245-2:** Account for ALL TEN original adopters: Logger first,
  Bridge, Vault, SSH, CodeSpaces, Index, Dispatch, Containers, MCP and
  Machines. Credit #5248's detailed Logger/Bridge/CodeSpaces repairs versus
  bounded spot checks of the other seven; do not claim every subprocess
  was rewritten or duplicate that completed finite sweep.
- [ ] **P5245-3:** Retain both historical optional-native observations:
  Worktrees sub-suite1/16 reached unchanged300s after progress/no reported
  assertion failure; supported `--test-file`/25-file300s grouping passed
  original group11 with793 passed/2 skipped, then group12 timed out near60%.
  Group13 GUI-parent exit1 is separate. These are not a current production
  diagnosis, passing group, deadlock proof or accepted flake.
- [ ] **P5245-4:** Credit #6788's proven stale-wheel repair and actual
  TWO-CYCLE windowless probe. It does not explain the retained group12
  timeout or prove the complete native full suite. Focused four-case
  project-scoping/#6763 and disjoint green groups remain distinct evidence.
- [ ] **P5245-5:** Continue the explicitly retained bounded group/
  partition/startup tail through its actual owners: #6747/#7256 PowerShell
  fixture/startup and #7075 real-CLI provisioning cost. Preserve assertions,
  process/GUI boundaries and reservations; #6800 source-safety containment
  and unrelated workflow work are not harness closure receipts.
- [ ] **P5245-6:** Obtain an accepted bounded source/fixture fix OR explicit
  tracked disposition of the residual, without inventing a global failure.
  Preserve actual installer/subprocess/native/GUI coverage, inventory,
  assertions and current resource/deadline limits. No dropped boundaries,
  skip, timeout growth, CI waiver or new every-native-host programme.
- [ ] **P5245-7:** Retain the complete inherited #6675 exact-session fixture
  contract: use the shared configuration/registration-authority fixture,
  preserve actual registrations and deterministic record ordering, and credit
  merged #6682's established 186-case related family and exact fast guard.
  The historical 15-group run stopped in group 11; it does not prove later
  groups. Obtain the actual requested remaining registration-authority
  family/remaining-group evidence OR an attributed accepted narrowing/tracked
  disposition through its existing coordination owner. Do not reopen the
  delivered fixture fix, waive that request or impose a universal new
  native/full-plugin qualification requirement.
- [ ] **V5245-1:** Keep an exact TEN-adopter delivered-versus-spot-check
  ledger and cache/two-cycle credit, naming the actual remaining diagnostic
  tail and custodian rather than reopening the original repairs.
- [ ] **V5245-2:** Qualify the causal/partition/startup fix OR attributable
  tracked disposition through the existing separately authorized bounded
  owning lane with meaningful real controls and original assertions.
  Progress without assertion failure proves neither pass nor deadlock.
- [ ] **V5245-3:** Keep optional native full-profile, required Windows
  launch, current required-unit/periodic and containing Linux release
  evidence separate. Current policy need not preserve every historical
  budget byte-for-byte; no relaxed retry or waiver is authorized.
- [ ] **V5245-4:** Reconcile the inherited #6675 remaining-family/group
  request against the already-passing 186-case family and exact real
  registration/deterministic-order fixture. Record executed, blocked and
  unexecuted boundaries separately. Later groups are not proved by the failed
  historical run; any accepted narrowing/disposition is attributable to its
  existing coordination owner, not inferred as a pass.

### Finite Logger interpreter, probe and retry fixtures from #7243

Agent-recommended Phase 4 Logger-wave tracking through Phase 7 intake.
Preserve ALL THREE original failure families, not a generic SRE fix.

- [ ] **F7243-P1:** Retain the native Windows x86_64/Git Bash/PowerShell
  5.1 and 7 report at its actual source/environment: six failed, ten passed,
  three skipped in the narrow packet, plus the seventh variable retry
  parametrization in the full selection. Map both bootstrap-reconcile
  cases, independent cold-store manifest creation/replacement and all
  reported SRE-retry cases without asserting they still fail today.
- [ ] **F7243-P2:** At the actual Bash test/script consumer, isolate inherited
  `PYTHONHOME` and relevant Python environment state OR select the matching
  test interpreter on PATH. Preserve the original alternative, uv-venv/
  first-PATH version mismatch and real JSON import/name/manifest outcomes.
  Do not change global Python, production profile authority or assertions
  merely to get a reconcile message.
- [ ] **F7243-P3:** Separately decide and document the original failed
  bootstrap-name-probe reporting question. A failed interpreter/JSON probe
  currently looks like a successful empty-name exit; environment sanitation
  alone is not that diagnostic decision. Preserve legitimate no-op/output
  behavior and the actual production hook owner; intake changes neither.
- [ ] **F7243-P4:** Keep isolated `-I` policy-harness timing DISTINCT from
  inherited-Python-state failures. The original four-attempt measurements
  were 23.3 seconds on PS5.1 and 23.2 seconds on PS7 against a 20-second
  child limit, with expected results, not a diagnosed deadlock. Choose
  actual fixture/work reduction OR a justified reviewed bounded child-budget
  correction (the original 60-second suggestion is not an applied value).
  Preserve outer item/group/plugin/process/resource bounds and actual
  retry-success, exhaustion/nonzero, unrelated-error and delay assertions.
- [ ] **F7243-P5:** Keep the finite native x86_64 report distinct from
  #1874 ARM64 consistency, other installer-recovery families and #7245's
  metrics feature. Passing unrelated Logger suites, a version bump or
  managed-publication changes are not causal repair of all three families.

#### Local Validation for #7243

- [ ] **F7243-V1:** At the actual supported contained Bash boundary, prove
  both reconcile cases AND cold-store manifest creation/replacement under
  controlled mismatched/matched interpreter environments. A message-only
  assertion or mock past Python selection cannot establish manifest output.
- [ ] **F7243-V2:** Record the separate failed-probe reporting decision and
  meaningful failure/no-op controls for its chosen realization. Preserve
  real diagnostics without leaking environment secrets or altering a live
  hook during validation.
- [ ] **F7243-V3:** Qualify the selected reduction-OR-child-budget treatment
  on BOTH native PowerShell hosts, retaining real retry/exhaustion/unrelated-
  error/delay behavior, attributable work/timing and owning cleanup.
  Historical completed timings, unsupported skips and new outcomes stay
  distinct. No blanket timeout expansion, retry-to-green or full-suite/
  every-host prerequisite is introduced.

### Finite contained native-profile fixture from #9614

Agent-recommended Phase 4 shared-fixture wave through Phase 7 intake;
production incumbent-command protection remains authoritative.

- [ ] **F9614-P1:** Retain the reported nineteen Linux legacy-uninstall
  failures across BOTH `pwsh` and Bash, the installed developer binstub
  versus empty-CI distinction, and actual shared-case adopters affected.
  A temporary HOME alone does not establish native-profile containment.
- [ ] **F9614-P2:** Bind the shared uninstall fixture's REAL native
  command-recovery/profile consumer to a contained-test OS-profile seam
  OR an explicit controlled profile argument at the native entrypoint.
  Preserve the original alternatives and actual contained-test admission;
  do not globally replace authoritative passwd/profile selection with
  arbitrary inherited HOME.
- [ ] **F9614-P3:** Preserve production refusal of unrecognized/foreign
  incumbent commands and all original uninstall assertions. The fixture
  must neither inspect nor mutate the developer's real binstub/home.
  A Codespaces working-directory correction is a different consumer, not
  proof of this native-uninstall profile boundary.

#### Local Validation for #9614

- [ ] **F9614-V1:** Map all nineteen original cases and actual shared-case
  adopters to their real native entry/command-recovery consumer on both
  supported shell hosts. With synthetic populated outside and contained
  profiles, prove only the admitted contained profile is used; test the
  absent/present/foreign command outcomes without reading real host state.
- [ ] **F9614-V2:** Exercise the chosen seam-OR-argument through actual
  fixture construction, not a mock past recovery, while preserving
  production authoritative-profile and incumbent-refusal negatives.
  Record exact source/tier/adopter outcomes and unavailable cases honestly;
  unchanged CI green or HOME assignment is not the reported boundary proof.

### Finite retained preparation/cache denial family from #9436

Agent-recommended Phase 5 owning-runner work through Phase 7 intake;
keep three observations distinct until their actual causes are established.

- [ ] **F9436-P1:** Preserve the source-fingerprint-triggered
  `clear_cache_contents` bootstrap-uv executable denial; post-assertion
  disposable group-sandbox teardown denial; and separate selected-environment
  preparation refusal. Readable, non-read-only/Archive-only bytes, inherited
  Modify access and negative exact-path/Restart Manager queries do not prove
  an ACL defect, absence of a holder or one shared cause.
- [ ] **F9436-P2:** Investigate through the ordinary contained runner after
  a local source dependency changes, keeping containment AND environment
  leases enabled. Preserve the affected cache for diagnosis. A fresh
  separately leased environment can distinguish cache from test behavior;
  it is not permission to repair/delete the original or claim it recovered.
- [ ] **F9436-P3:** Keep the failed aggregate's unchanged 30-second item
  limit, the isolated two-case/37.66-second result with failed cleanup,
  separate ten-case normal teardown and later two-regression/65.22-second
  canonical-contained result as different evidence. Preparation refusal
  means no tests ran in that attempt; passing assertions or alternative
  contained execution does not supply successful managed preparation.
- [ ] **F9436-P4:** Obtain an attributable owning runner/cache/fixture
  diagnosis and bounded correction OR an explicit requirement-preserving
  disposition of each retained unknown. Do not mask failed cleanup, infer
  a lock owner from a miss, alter ACLs, terminate unrelated processes,
  clear an uncertain cache or enlarge deadlines to manufacture acceptance.

#### Local Validation for #9436

- [ ] **F9436-V1:** At the actual separately authorized Windows owning
  preparation/rebuild AND group-disposal boundaries, distinguish each
  original stimulus, test outcome, cleanup outcome and remaining unknown.
  Preserve primary test/preparation failure alongside cleanup diagnostics,
  exact source/environment/lease identity and retained affected artifacts.
- [ ] **F9436-V2:** Demonstrate the chosen causal correction or explicit
  retained disposition without bypassing admission/containment or mutating
  an unrelated environment. A fresh leased control or one successful
  alternative run supplies only its actual evidence; original failed
  attempts stay recorded. Native execution/repair is not performed by intake.

### Finite post-exit sharing-handle family from #10225

Agent-recommended Phase 5 runner/native-test-path disposal work through
Phase 7 intake; this is not #7643's already-attributed external mapping.

- [ ] **F10225-P1:** Retain the original isolated eight passes/three opt-in
  skips followed by repeated `Scripts/python.exe` WinError 5, supervisor-
  verified process cleanup, non-read-only executable and later exclusive-read
  sharing violation. A negative process-image/argv query does not identify
  the remaining handle owner; retained denied files are not cleanup proof.
- [ ] **F10225-P2:** Identify the ACTUAL handle owner and make the owning
  test/runner release it BEFORE targeted sandbox cleanup at
  `tools/native_test_paths.py` and the real transport-owner suite boundary.
  Preserve process and wall-clock bounds, primary failures and truthful
  cleanup status. Do not substitute generic retained-artifact policy,
  wider recursive removal, failure suppression or unrelated-process stops
  for this original explicit release requirement.
- [ ] **F10225-P3:** Keep process independence separately credited:
  the transport-owner suite requires `agent_worktrees` not importable,
  whereas the real native launch-plan suite imports it. The #10149
  independent-suite entry addresses import contamination, not the unknown
  handle. Preserve both suites' actual assertions and current tier.
- [ ] **F10225-P4:** Credit the later successful owner-boundary-only
  invocation (eight passes, three opt-in skips, runner exit zero and
  verified supervisor cleanup) without erasing failed aggregate attempts
  or claiming that one success identifies/releases the original holder.

#### Local Validation for #10225

- [ ] **F10225-V1:** Prove actual owner attribution and owning handle release
  before scoped disposal, with meaningful sharing-denial/nonrelease and
  successful-release controls. Exercise the real owning consumer rather
  than mocking past disposal; process exit alone is insufficient.
- [ ] **F10225-V2:** Record repeated bounded Windows runs settling BOTH
  assertions and sandbox cleanup, preserved import independence, complete
  source/attempt identity, skips and primary/cleanup outcomes. Retain prior
  failures and unknowns. Unavailable native capacity, process cleanup or
  an isolated green cannot stand in for every original acceptance clause.

#### Continuing delivery gate for the four finite families

- [ ] **F4-C1:** Continue through the SAME primary maintainer, owning Logger/
  shared-fixture/runner/cache/platform and actual source/audit/cohort/native/
  periodic-failure/release/resource custodians and existing Phase 7 review.
  Preserve all #1303 body inheritance and live claims, original alternatives,
  checked foundations, actual ROOT global Validation and complete history.
  No new executor, blanket acknowledgement, claim expiry or handoff follows.
- [ ] **F4-C2:** Preserve #7643 independently OPEN, #8521's explicit OPEN
  diagnostic/private-cache residual and #9460/#9467's separate claimed
  teardown-fixture/production guard. #9699 is finite completed fixture
  credit, not a production-guard relaxation or portfolio completion.
  The separate #9375/#10239 cases are not intake scope.
- [ ] **F4-C3:** BEFORE any administrative source supersession, obtain
  ordinary review and actual delivery of ALL applicable direct Plan, local
  AND actual level-two ROOT global Validation, Coordination and dated Journal;
  then fully read the current ROOT and linked historical Journal, freshly
  qualified whole source/umbrella discussion and actual custody/holds.
  Apply each source's literal condition separately. Unknown scope, fit,
  delivery, current ownership or retained OPEN condition means KEEP.
  A study, graph edge, child-only note or document merge is not a fix.

## Validation Plan

- [ ] **N6662-G:** Resolve EVERY N6662 Plan/local Validation and the six-family
  Coordination clauses at the original 15-second Dispatch completion consumer:
  retained result/staging/cleanup assertions, credited #6665 diagnostics and
  attributable causal correction OR explicit requirement-preserving unknown
  disposition. Different fixtures and isolated/broad passes are not causal proof.
- [ ] **N6663-G:** Resolve EVERY N6663 Plan/local Validation and Coordination
  clause while preserving CONDITIONAL IF-RECURRENCE diagnosis, twelve real
  allocators, both generations/six profiles, actual positions/capacity and
  production two-second admission/containment. No fresh unconditional native
  programme, mocked lease substitute or inferred CPU/filesystem cause is added.
- [ ] **N6690-G:** Resolve EVERY N6690 Plan/local Validation and Coordination
  clause at the actual version-change Bash child/stream/registration boundary,
  preserving coalescing identity, reuse/invalidation/replay and owning cleanup.
  Retain original source, bounds, failed/passing distinctions and protected
  holds; Linux fixture/tooling/prerequisite gains are not native child acceptance.
- [ ] **N6900-G:** Resolve EVERY N6900 Plan/local Validation and Coordination
  clause through the original error/pane consumer and minimal faithful
  regression OR owning-fixture correction: exact timing/invocation distinctions,
  exit 3/sanitized stderr/pane/deletion/receipt and consumer-15/item-30 limits.
  Preserve unattributed environment drift, no host mutation and prior failed
  pane outcome; instrumentation is not acceptance.
- [ ] **N9442-G:** Resolve EVERY N9442 Plan/local Validation and Coordination
  clause for BOTH individual 20-second queued workers, scope/invocation/Job/
  cleanup and actual monotonic cause OR retained unknown disposition. Preserve
  ONLY the distinct aggregate item-30 to item-60 correction, original failures
  and later passes; no child-absence, preparation or per-worker repair inference.
- [ ] **N9815-G:** Resolve EVERY N9815 Plan/local Validation and Coordination
  clause: deterministic entry of FOUR real readers within the unchanged three
  seconds, writer ownership through settlement and drain/cancel/cleanup, truthful
  isolated AND combined selection outcomes and separate actual required CI.
  Broader PR-watch service/source custody and all incumbent portfolio gates
  remain; a passing isolated packet or intake is not a complete fixture result.

All six global entries preserve unit-only required CI, quiesced hosted Windows
execution, independently admitted periodic/explicit/native tiers, original
budgets, every incumbent check state and actual source/resource holders.
They add no unrelated whole-parent prerequisite to a finite source outcome
and waive none of this ROOT's own whole completion obligations.

- [ ] **F7243-G:** Resolve EVERY F7243 Plan/local Validation and F4
  Coordination clause: both Bash reconcile cases, independent cold-store
  publication, isolation-OR-matching-PATH, separate failed-probe decision
  and BOTH native PowerShell retry families with attributable bounded
  reduction-OR-reviewed-child-budget treatment. Preserve all original
  assertions, three-family causality distinctions and current custodians.
- [ ] **F9614-G:** Resolve EVERY F9614 Plan/local Validation and F4
  Coordination clause for all nineteen original cases and actually affected
  shared adopters: admitted seam-OR-controlled-profile at the REAL native
  consumer, both hosts, no real-home access/mutation, and unchanged
  authoritative production/incumbent refusal.
- [ ] **F9436-G:** Resolve EVERY F9436 Plan/local Validation and F4
  Coordination clause: all three retained denial contexts, actual causal
  correction OR explicit requirement-preserving unknown disposition,
  lease-safe ordinary preparation and honest primary/cleanup results.
  Preserve original artifacts, unsuccessful attempts and native uncertainty;
  no shared-cause, cache-repair or successful-preparation inference.
- [ ] **F10225-G:** Resolve EVERY F10225 Plan/local Validation and F4
  Coordination clause: ACTUAL owner and owning release BEFORE targeted
  disposal, repeated Windows assertions AND cleanup, unchanged bounds
  and separate import-independence credit. Retained artifacts or a later
  isolated pass are not substitutes for the original handle requirement.

These four finite families preserve current unit-only required CI,
periodic/explicit/native admission, effect/storage/process/time budgets
and every incumbent parent gate. Tracking is not implementation, executed
acceptance or source/resource release; no unrelated whole-portfolio,
every-host or release prerequisite is added to a finite family.

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

- [ ] **G1012:** Resolve every direct #1012 Plan/local Validation clause through
  the existing finite Worktrees/Manager wave and intake, retaining actual
  visibility/event and collected-value assertions, slow-visible/never-visible
  controls, truthful failure and the original evidence tier. Preserve every
  incumbent family, owner and gate. Tracking is not a synchronization fix or
  executed proof; unrelated portfolio work is not an added finite-case
  prerequisite and this ROOT's own completion gate remains unchanged.

<!-- backlog-reconciliation:7556:qualified-sixteen:4128:Validation-Plan -->

- [ ] **T4128-V1:** Resolve ALL FIVE refined #4128 authority, first-touch,
  actual-writer, restoration and separate-OS-mechanism clauses through
  T4128-P1 through P4: BOTH filesystem registries AND explicit installation
  contexts/overrides, early collection/import/session, ordinary functions,
  workers and failure/teardown. Prove real temporary writes, unchanged
  synthetic outside state and truthful restoration/propagation. Preserve
  existing autouse/writer/session-seed credit and finite #4128 ownership;
  temporary HOME, generic checked root-isolation, collection or one seed
  family is not whole all-authority proof. No source transfer, live escape,
  native-every-host or new blanket ACK is inferred.

- [ ] **T4128-V2:** Resolve ALL T4128-P5-P7 and local T4128-LV2 Manager
  runtime-source clauses: before-work isolation across source/home/provider/
  selected-installation/queue/global authorities and adjacent side-effect
  paths; meaningful actual `run_update_steps`/missing-argument/error-record
  behavior; intended temporary effects AND unchanged synthetic outside
  provider/Manager/source/queue state; no later temporary-registration pickup;
  correct worker propagation and normal/exceptional restoration. Preserve
  original assertions, T4128-P1-P4/T4128-V1 and earned partial delivery, and
  actual fixture/source/native/resource owners. Keep #10118, ordinary queue
  recovery and persistent OS mechanisms separate. This is planned receiving,
  not execution proof, a new runner/native admission or portfolio completion.

- [ ] **T4217-G:** Resolve EVERY T4217 Plan/local Validation condition:
  all FOUR real scenario/job enrollment/reporting outcomes in authorized
  bounded periodic/disposable lanes, not rig/manual-run proxies. Preserve
  unit-only required CI, failure/cleanup/resource owners and unavailable
  states; documentary delivery is not CI coverage or parent completion.
- [ ] **T4217-G2:** Resolve every T4217-P4/P5/LV2/LV3 inherited allocator
  condition alongside the unchanged T4217-P1/P2/P3/LV/G all-four-outcome
  enrollment family. Keep the lanes independently progressable: allocator
  correction need not await enrollment completion, and enrollment cannot infer
  reliable host artifacts merely because independent containers ran. Preserve
  explicit-directory semantics, BOTH report/log ownership, Linux versus
  PowerShell evidence grades, all existing source/resource/native owners and
  original finite tiers. This is neither technical completion nor a new
  every-platform/adopter or broad blocking native/release program.

- [ ] **T4233-G:** Resolve EVERY T4233 Plan/local Validation condition:
  exact original warning/dependency-set attribution and supported-suite/
  platform outcome OR attributable requirement-preserving disposition.
  Preserve modern client/fixture credit, diagnostics, platform and excluded
  BODY limits; intake is not warning-free execution or source/resource release.

- [ ] **G4835:** Resolve every direct #4835 Plan/local Validation and continuing
  Coordination clause at the original contained-Windows stale-launch-lock
  boundary: deterministic PID exclusion AND stale-lock pruning despite
  independently held exited handles, faithful birth/reuse/unknown controls
  and a narrow fixture OR exit-state correction. Preserve original
  assertions, current tiers/budgets, OPEN #1303, all incumbent portfolio work
  and the same custodians. Intake is not repair, production/native proof,
  parent completion or source/resource release.

- [ ] **G5115:** Resolve EVERY direct #5115 Plan/local Validation and
  Coordination clause for ONE documented AND tested Windows-hosted Docker
  Desktop WSL2 route, within WSL OR supported native-Windows invocation.
  Retain Done Linux credit and actual isolation/path/resource/cleanup
  behavior; no documentary intake, generic Linux result or hosted Windows
  process test is backend qualification.
- [ ] **G5140:** Resolve EVERY direct #5140 Plan/local Validation and
  Coordination clause across ALL FIVE named originals, preserving FOUR
  causal fixture credits and the FIFTH actual matched-process/helper/exit
  evidence requirement. Named unresolved uncertainty remains tracked, not
  passed. Preserve original Linux/WSL tier, same custodians and all
  incumbent parent obligations; no production kill or universal native gate.

- [ ] **G5245:** Resolve EVERY P5245/local Validation/continuing Coordination
  clause at its original finite scope: completed TEN-adopter sweep and cache
  repair credited, retained bounded diagnostic tail fixed OR explicitly
  tracked, complete inherited #6675 exact-registration/186-case credit and
  remaining-family/group evidence OR attributed accepted narrowing retained,
  real controls/assertions/current tiers and limits preserved by the same
  holders. Tracking intake is not a native full pass, accepted
  flake, implementation, parent completion or custody/resource release.

- [ ] **LF6093-G:** Resolve EVERY LF1-LF3 and LFV1-LFV3 condition:
  all five original Logger rejection boundaries, directory-link declaration,
  capability contract OR genuinely equivalent native reparse/junction proof,
  both named HostProfile controls and their direct promotion packet,
  surviving hardlink/incomplete-publication assertions, real POSIX rejection
  and truthful capability-present/unavailable/non-1314 outcomes. Preserve
  unchanged nodes, effects, budgets, production trust, source custodians,
  OPEN #1303 and LFV3's FULL CURRENT OPEN parent readback condition.
  Documentary receiving is not fixture repair, native PASS, privilege
  authority, implementation appointment or source/resource release.
- [ ] **B6138-G:** Resolve EVERY B6138 Plan/local Validation and continuing
  Coordination condition at the original finite portable PowerShell
  spaced-path/Force boundary: exact failed run/job/source and 30-second
  budget, Linux8.17 not-flake distinction, actual-candidate inspection THEN
  justified source correction OR bounded same-SHA retry, real caller/fixture
  assertions and truthful result grades. Preserve the retained investigator,
  independent #6131/#6135 work, genuine-regression fix-forward and normal
  release gates, unit-only required CI and existing bounds/custodians.
  No new native, whole-release or whole-portfolio prerequisite is added;
  planning is not causal closure, execution or source/resource release.

## Proposal

Approve the vision, framework, and phased campaign as the repository's
canonical test-portfolio program. Implementation begins with containment, not
with another unbounded full-suite run. Portfolio reductions follow only after
the inventory and evidence gates are available.

## Journal

### 2026-11-13 - Proposed completion of inherited #4217 allocator intake

The current #4217 original, two eligible refinements, two non-null original
versions and title history retain four-outcome deterministic CI enrollment AND
the independent finite #8511 host-artifact allocator lane. Existing T4217
enrollment credit and acceptance stay unchanged. The additive allocator rows
retain same-second exclusivity, distinct container suffixes, BOTH report/log
ownership, explicit-directory semantics, Linux versus PowerShell evidence and
focused regression/cleanup controls. All new technical rows remain unchecked.
Normal review, actual complete delivery/readback and a separate source-specific
judgment remain pending; #4217 and #1303 are not closed by this proposal.
No product or execution/resource effect occurred.

### 2026-11-12 - Proposed inherited registration-family receiving

Added unchecked P5245-7/V5245-4 and the corresponding actual global G5245
and continuing Coordination clauses. The current #5245 union includes the
entire #6675 fixture correction and its remaining-family/group request,
which were not directly retained by the older hygiene/diagnostic family.
#6682's real registration, deterministic ordering, 186-case family and exact
guard remain credited. The failed historical group-11 stop does not prove
later groups; requested evidence OR attributed accepted narrowing/tracked
disposition remains with the existing coordination owner.

All incumbent rows, checked credit, participants and source/resource
reservations remain. This is documentary intake, not a fixture change,
native/full-suite pass, waiver, implementation appointment, source closure
or custody transfer. Actual reviewed delivery, current whole receiving and
source-specific conditions precede any separate administrative judgment.

### 2026-11-12 - Proposed Manager runtime-source containment intake

The additive T4128-P5-P7/local-LV2/global-V2 and continuing Coordination retain
the later #10154 contract adopted by #4128 alongside the earlier five
filesystem-registry refinements. Meaningful real error-record coverage,
before-work authority containment, synthetic noninterference and existing
source/resource custody remain. All incumbent boxes and participants stay
unchanged. This is a proposal, not implementation, execution, future receiving
acceptance or source disposition. Actual reviewed merge and whole-current
readback precede the separate tracking judgment.

### 2026-11-04 - Six finite timeout contracts retained directly

Added direct unchecked N6662/N6663/N6690/N6900/N9442/N9815 Plan and local
Validation families, six entries in this ROOT's ACTUAL global Validation Plan,
and continuing Coordination under existing Phase 4/5/7 owners. All 240 earlier
states/13 checked and full history remain. The original issue bodies and
refinements retain their authority: diagnostic/fixture partial credit, exact
limits, distinct failed/passing observations and unknown causes are not erased.
#6663 diagnosis stays conditional on recurrence; no new native programme is
required. #9442's aggregate-only budget credit cannot relax individual limits;
#9815's broader service/source duties and required CI remain independent.

The complete pre-amendment ROOT and unchanged previously whole-read framework/
intake/vision/linked historical journal supported this proposal, not a repair.
Sixteen bounded comparisons do not supply a common cause. The independent
#7820 source completion retains its unresolved broader startup finding and
does not add an acceptance row here. Review, actual complete delivery, permitted
whole-current readback and separate source-specific judgments remain necessary
before administrative supersession. No test, collection, native/installer/
environment action, implementation assignment, rollout or custody transfer
occurred. OPEN #1303, unit-only CI, hosted Windows quiescence and all incumbent
source/audit/cohort/periodic-failure/native/release/resource holders remain.

### 2026-11-04 - Four finite fixture and runner contracts

Added complete unchecked direct Plan/local AND actual global Validation
and continuing Coordination for #7243/#9436/#9614/#10225 after whole-
contract and actual current ROOT/framework/history/umbrella-custody reading.
The [nine-contract study](../backlog-reconciliation/native-fixture-cohort.md)
separates mechanisms and preserves original evidence, alternatives, unknowns
and owners. #9699's finite completion is independent; #7643/#8521 and
#9460/#9467 remain separate OPEN/claimed work. All incumbent rows, checked
credit, journal and #1303 inherited/live responsibilities remain unchanged.

This is documentary intake, not a new suite, effect authority or native/
installed PASS. Normal review, actual complete delivery, permitted current
readback and separate source judgments remain required. No test, collection,
import, cache/environment mutation, process/service action, cleanup,
implementation appointment or source/resource transfer occurred.

### 2026-10-30 - Proposed retained hygiene diagnostic tail

Added unchecked direct #5245 Plan/local and actual global Validation with
continuing same-owner coordination. The original ten-adopter containment
sweep and cache/two-cycle repair retain their finite credit; only the
explicitly retained bounded group/partition/startup tail remains. This is
documentary research, not source/native qualification, reviewed receiving
delivery, assignment, issue closure or an execution grant.

### 2026-10-30 - Windows-hosted route and five-case causal tracking

- Added direct #5115/#5140 Plan/local Validation, actual global Validation
  and continuing coordination after whole current-contract admission.
  Preserve ONE documented-and-tested Windows-hosted route, Done Linux
  credit, ALL FIVE exact Manager cases and FOUR finite fixture credits.
- The fifth matched-process historical cause remains unresolved, not
  replaced by a mismatch proxy or an invented current failure. No test,
  backend/provisioning/process action or native qualification occurred.
  Normal review, actual delivery, full current readback and separate
  judgments remain; every incumbent row/checkstate and custodian survives.

### 2026-10-30 - Proposed finite contained-Windows stale-lock family

- Added direct #4835 Plan/local and actual global Validation plus continuing
  same-custodian coordination after complete qualified original/refinement
  and fixed-home reading. Preserve the independent held-exited-handle
  boundary, original assertions, fixture OR exit-state alternative and
  unknown/birth-identity protection.
- The amendment is local and pending normal review, actual complete delivery,
  current readback and separate source judgment. No fixture, liveness
  implementation, contained/native test, process or resource was changed;
  all incumbent acceptance and parent-completion gates remain.

### 2026-10-30 - Proposed finite enrollment and warning-attribution intake

Whole eligible #4217/#4233 originals/refinements and complete fixed-pin ROOT
research support these unchecked proposals. Actual rig/scoped-run and
combined client/fixture modernization remain credited; all-four automated
enrollment and exact original warning acceptance/disposition remain open.
No scenario, workflow, dependency, test or native operation occurred.
Normal review, actual delivery and whole-current readback are still required;
this amendment establishes neither source disposition nor new custody.

<!-- backlog-reconciliation:7556:qualified-sixteen:4128:Journal -->

### 2026-10-29 - Proposed finite filesystem-registry authority qualification

- Added direct unchecked #4128 Plan, actual global Validation and continuing
  custody after whole current contract and complete fixed-ROOT qualification.
  Both filesystem writers, explicit contexts, early/session/worker/teardown
  and restoration remain distinct from legacy HOME and one seed family's
  real delivered protections.
- Preserved #1303's broad scope, finite #4128 ownership, #1906's separate
  historical OS mechanism and every incumbent checked family/first gate.
  No import, collection, test, real registry/environment action,
  implementation or source disposition occurred. Normal review, actual
  delivery and complete current readback remain outstanding.

### 2026-10-28 - Proposed finite conditional-field synchronization family

- Added direct #1012 Plan/local Validation, actual global Validation and
  continuing coordination under the existing wave/intake owners. Historical
  full-run failure, isolated passing retry and test relocation remain distinct.
- Preserved actual field/value assertions and proposed bounded slow/never-visible
  controls without executing tests or appointing a source owner. Normal review,
  actual delivery and full current readback remain outstanding; no source
  disposition or implementation completion is claimed.

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

### 2026-11-13 - Proposed finite link-capability and portable-timeout receiving

Proposed complete #6093 original/inherited LF1-LF3/LFV1-LFV3 plus actual
global LF6093-G, and #6138 B6138-P1-P3/local-V/actual-global-G, through
existing Phase 7 intake into Phase 4 only. Twelve new technical rows remain
unchecked; all 13 checked and 275 incumbent unchecked rows, original
alternatives, first Plan/global gates, participants and source/resource
custodians remain unchanged.

The complete current ROOT, strict triage framework, linked historical
Journal and intake ROOT/append-only ledger were read. Normal review of
the accompanying complete domain amendment supplies domain acceptance,
not this proposal or ledger alone. #6093's held fixture body remains
unread and no current repair is asserted. #6138's failed portable receipt,
limited Linux pass and candidate-first correction-OR-retry decision remain
distinct. Both sources and #1303 stay OPEN. No implementation, import,
collection, test, native/privilege probe, workflow retry, rollout or
source/resource effect occurred.
