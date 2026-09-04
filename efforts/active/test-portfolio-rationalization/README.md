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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary maintainer | Owns the plan, public issue, PR sequence, and final portfolio decisions | isolated local worktrees |
| Review agents | Review the plan and each implementation wave for lost assurance or unsafe execution | repository PR review |
| Plugin specialists | Analyze and rationalize one plugin slice at a time | serial per-plugin worktrees and PRs |

## Coordination

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

The active [`plugin-process-hygiene`](../plugin-process-hygiene/README.md)
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

- [x] Serialize potentially heavy local runner invocations behind one
  host-wide, liveness-reconciled admission lease while leaving guards and
  collection smoke available as cheap concurrent feedback.
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

## Validation Plan

- [ ] The containment harness kills and reaps all descendants after success,
  failure, timeout, and interruption on Windows and POSIX.
- [ ] Default-tier suites cannot resolve real user, Copilot, plugin, or service
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

## Proposal

Approve the vision, framework, and phased campaign as the repository's
canonical test-portfolio program. Implementation begins with containment, not
with another unbounded full-suite run. Portfolio reductions follow only after
the inventory and evidence gates are available.

## Journal

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
