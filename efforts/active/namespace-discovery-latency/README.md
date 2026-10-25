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
| Release-blocker repair owner | Bounded source-publication repair, not live control activation | Managed worktree `tinkling-toolbox`; coordination on #8033 |

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

Subsequent operator selection:

> repair-release-gate

**Selection summary:** Diagnose and repair the release blocker through the
normal reviewed source flow. Live Main-control changes, guard bypasses and
installed rollout remain separately authorized.

Source-publication clarification, captured verbatim:

> Wait... we don't care about published artifacts, though, we only care about what is in main; update and consumption is based on what main says, and then we check for matching published artifacts for main's tagged dev source commit.

The bounded repair follows the existing source-owned release identity. It does
not introduce version reservation, move publication after merge, or enable the
separately owned installer artifact-consumption lane.

After comparison with the independently merged reservation repair, the
operator explicitly selected:

> Great. Switch to the source-owned model

**Selection summary:** Remove the now-unneeded tag-reservation dependency
introduced by #8110 while retaining ordinary version bumps from landed Main,
the complete source-keyed artifact set and all current-source verification.
This supersedes the reservation approach for this repair; it does not
authorize live Main-control activation or installed rollout.

Subsequent operator selections, captured verbatim:

> Activate and deploy

> Great. Continue driving now that you have a container to try in

**Selection summary:** Coordinate reviewed control adoption with its existing
owner, use normal authorized local rollout, and continue bounded source
diagnosis and isolated container qualification. This does not authorize a
timeout waiver, installed-code patch, control takeover or fleet-wide change.

Subsequent operator direction, captured verbatim:

> Identify what takes up 8 seconds; break down the architecture and figure out what we can spare

**Direction summary:** Attribute the complete call chain and distinguish
necessary authority checks from removable startup or unrelated configuration
work before selecting another implementation. This is not approval to remove
the validation process boundary.

Subsequent operator direction, captured verbatim:

> Great. Build out an effort plan and tackle it

**Authorization summary:** Develop and execute the measured startup-headroom
follow-up below through reviewed source changes. Reuse this canonical effort;
retain the unchanged eight-second requirement, process isolation and existing
release, installation and lifetime ownership.

Subsequent operator selection, captured verbatim:

> design-reviewed-selector

**Selection summary:** Develop a reviewed trusted-bootstrap contract,
coordinating with its existing owner. This selects design work rather than
silently trusting PATH or an unadmitted runtime; it does not grant shared
implementation ownership or installed rollout.

Subsequent operator selection, captured verbatim:

> assign-new-selector-scope

**Bounded assignment:** Implement a new payload-local native selector under
`libs/installer-engine`, its focused tests/sync declaration, and
Containers/Codespaces delivery. This explicit assignment resolves the earlier
acknowledgement gate; it is not inferred from the existing owner's silence.
Existing installer-selection behavior, SDK validation algorithms,
Manager/lifetime work and installed rollout remain excluded. Coordinate any
newly overlapping consumer PR rather than modifying its still-open change.

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
- [x] Land the implementation through review and required CI. #7998 merged to
  `dev`; that merge is not a qualified Main release.
- [x] Repair the blocking source-publication failure on #8033 through normal
  Dev review. Use the current source's complete signed artifact set rather
  than another source's per-plugin version aliases; preserve source binding,
  immutable bytes, consumer readback and legacy publication compatibility.
  #8115 merged as `c66e0bd7570ef4262d1f9c312a3593fbef52fd30`.
- [ ] Have the existing #3336/#6237 control owner adopt the reviewed helper;
  source publication and a containing release do not change the live pin.
- [x] Confirm qualified promotion to `main` separately from the `dev` merge.
  Natural #8287 contains the source repair; #8302 separately contains the
  registered-Manager launcher repair.
- [x] Land the bounded state-root follow-up through review and required CI.
  #8414 merged as `5a2b202b5ae94b9695f04f4a05c4c424731d3ab7`.
- [x] Land the reviewed [startup-headroom follow-up](startup-headroom.md)
  before its implementation; preserve existing source and control owners.
- [ ] Execute that follow-up in independently reviewable source slices:
  omit unused state-root preferences first, then qualify the canonical
  fresh-process evaluator selection without weakening bootstrap admission.
- [ ] Review the [independent Windows bootstrap contract](trusted-bootstrap.md)
  and agree its shared implementation scope with #6812 before changing the
  evaluator or shared selector.
- [ ] Requalify the remaining installed reader latency after a reviewed
  state-root follow-up, preserving complete discovery within eight seconds.
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
- [ ] The startup follow-up's [qualification matrix](startup-headroom.md#validation-plan)
  proves equivalent source/installation refusals, first-touch behavior and
  complete native discovery; a faster status microbenchmark alone is not a pass.

## Proposal

Review the plan first. Profiling chooses the concrete repair; neither a larger
constant nor environment-only mitigation is an accepted substitute. This
closes a bounded reliability delta within the existing Bridge vision and
namespace-provider pattern, without changing architectural ownership.

## Journal

### 2026-10-24 - Corrected native cost and retained PowerShell home fallback

- Rebuilt both normal installations from corrected source `b5905b6d62` in a
  separate fresh profile: Worktrees completed in 214.453 seconds and Containers
  in 63.266 seconds, both with zero exit and no stderr. Generated deployed
  global Worktrees commands match the ordinary legacy frontdoor, not a payload
  wrapper. Host selectors and source bytes remain unchanged.
- Repeated only the combined evaluator comparison after the native alias
  correction: PowerShell 5.1 native/admitted pairs measured 4.117/2.591,
  3.614/2.185 and 3.858/2.515 seconds; pwsh measured 3.278/2.444,
  3.168/2.134 and 3.152/2.197. All twelve complete SDK JSON values, zero
  exits and empty stderr agree. Every pair retains 0.834-1.526 seconds
  of benefit with native drive-root admission and compilation included.
  No unchanged whole-provider cohort was retried or credited.
- The second review confirmed both original findings resolved but identified
  another Medium issue in its summary: initial durable-home construction
  required `USERPROFILE` even for POSIX pwsh with an explicit runtime root.
  Both gates now derive Copilot and durable homes from the native evaluator's
  shared `USERPROFILE`-or-`$HOME` fallback. No SDK algorithm changes.
- Eight new absent-`USERPROFILE` legacy/namespaced ABI controls and eight
  selected-route controls pass on both Windows hosts/adopters. The other
  previously passing consumer logic and native helper are unchanged.
  An exact-owned, network-disabled, read-only probe of the existing Linux
  clean-room base confirmed no `pwsh`; its temporary container was removed.
  Windows ABI controls are not a native POSIX-pwsh pass. POSIX shell dispatch
  remains untouched; no new tool installation or held venue substitutes.
- Current-head review/CI and merge remain required. The parent retains its
  actual 9.378-second whole-discovery failure, further owner-coordinated
  startup work, release/control adoption and authorized installed acceptance.

### 2026-10-24 - Native alias and provenance-home review corrections

- #8789's first review found two concrete boundaries. Native controls
  reproduced eight addressed-runtime exclusion failures through ordinary
  SUBST and raw DOS-device aliases, including missing aliased suffixes.
  Four actual-SDK legacy controls reproduced differing complete status when
  `HOME` and `USERPROFILE` disagree. The positive signed-base controls passed;
  those failures were not treated as unavailable native fixtures.
- Admission now verifies drive-root handle identity against its addressed
  drive before candidate execution, reusing the existing final-path primitive.
  Candidate and exclusion aliases refuse; ordinary missing local suffixes
  remain supported. Temporary native test aliases are removed by exact target
  in caller-owned cleanup. Both adopter copies are synchronized.
- Both portable status/validation calls explicitly pass the native evaluator's
  Copilot home. This retains native provenance semantics without changing SDK
  source/policy algorithms. ABI expectations include that exact argument.
- All 62 native helper cases plus four real signed-base consumer cases pass
  without skips. The remaining 116 two-adopter ABI/actual-SDK cases also pass
  without skips, accounting for all 120 consumer cases. Lint passes with the
  existing tooling; no dependency installation, timeout or coverage waiver
  was needed. Review of the corrected head and final combined-cost evidence
  remain required before merge.

### 2026-10-24 - Component publication retains shared-library release fan-out

- Opened #8789 for the bounded selector/two-adopter implementation, with
  actual-profile paired benefit and the unresolved whole-discovery failure
  stated separately. The managed flow rebased cleanly onto current Dev.
- Its first required source guard correctly rejected missing changefile entries
  for seven existing installer-engine consumers. The guard deliberately charges
  a canonical shared-library source addition to its full declared consumer set,
  even though only Containers and Codespaces adopt this new helper.
- Added those required release entries rather than narrowing the guard or
  changing unrelated installers. Required units executed successfully on that
  head; the overall source check remains failed until a corrected head passes.
  Current-head review/CI, merge and parent acceptance are still open.

### 2026-10-24 - Normal-install evaluator comparison retains a bounded benefit

- Compared the actual normal-profile SDK through twelve fresh outer processes:
  three alternating-order native/admitted pairs for each Windows host.
  The admitted route includes registry discovery, offline trust verification,
  native helper compilation, fresh isolated/no-site SDK execution and guard
  disposal. The native route starts the same host's canonical PowerShell SDK.
  Both routes receive identical payload/plugin/legacy inputs.
- PowerShell 5.1 native/admitted seconds were 5.576/3.019, 4.034/3.214 and
  3.463/1.772. pwsh measured 2.886/2.441, 3.157/2.067 and 3.886/2.791.
  Every pair retained a benefit, ranging from 0.445 to 2.557 seconds.
  All twelve full SDK JSON values and zero exits agree, with no stderr.
  Host selectors and candidate source hashes remain unchanged.
- These are paired evaluator measurements, not a new complete-provider
  cohort, first-touch claim or tail-latency guarantee. The actual legacy
  frontdoor's retained 9.378-second whole-command failure remains open.
  Do not substitute isolated SDK time or an average for the unchanged
  eight-second discovery requirement.
- Proceed with bounded selector/two-adopter source review, explicitly retaining
  that residual parent gate. Further Worktrees eager-import or native-frontdoor
  work needs its own owner-coordinated source slice; it is not added to this
  selector assignment. No installed update, shared service or control changed.

### 2026-10-24 - Source-independent native construction exposes the residual budget gap

- Built normal, non-developer Worktrees and Containers installations from
  canonical materialized payloads into a fresh private profile. Construction
  completed in 211.047 and 53.253 seconds; canonical provider registration
  completed in 1.105 seconds. All three returned zero with no stderr.
  Host selectors and the executing source hashes remained unchanged.
- Full bound state-root JSON matches the current host. Both installed module
  origins lie inside the private profile, match the candidate source bytes,
  and have no original source-checkout prefix in their import paths.
  The fixture now distinguishes original-checkout independence from the
  earlier developer-runtime construction.
- The first whole command used the attributable payload Worktrees frontdoor
  for its peer and returned a complete empty roster in 15.348 seconds.
  Source inspection then confirmed this caller's actual legacy contract is
  PATH lookup of the deployed global compatibility command. Those generated
  private command files are byte-identical to the host's. Using that exact
  legacy contract preserves the full state-root response but still takes
  9.378 seconds, with exit zero and no stderr. Retain both measurements with
  their distinct frontdoors; neither qualifies the unchanged eight-second
  requirement. No repeated cold cohort or Bridge pass is claimed.
- A separate actual-profile admission probe selects signed base CPython in
  1.137 seconds. Profiling its guarded, fresh canonical SDK reports 0.398
  seconds of SDK work and complete ready/legacy output; these are distinct
  diagnostic samples, not an additive whole-command timeline.
- Direct selected Worktrees Python returns the complete state-root response
  in 2.254 seconds. Its diagnostic profile attributes 1.371 seconds to eager
  imports; the 0.930-second handler includes subprocess waits. The matching
  Containers runtime profile remains dominated by its real peer wait.
  Existing low-overhead boot tracing is retained separately and is not
  credited as a passing performance sample.
- The independently admitted route is implemented and functionally qualified,
  but whole-source headroom is still open. Establish the final actual-profile
  evaluator comparison before publishing a performance claim, then review
  any further eager-import or native startup slice with its retained owner.
  No SDK validation, timeout, service, control pin or installed runtime was
  changed to make these measurements pass.

### 2026-10-24 - Both consumers integrated after the retained owner's merge

- Confirmed #8666 merged as `98c990a4c28e02014618335f56d3d96535cbaef0`
  before integrating Containers. Its legacy predicate, selected runtime-root
  and generation checks, publication/legacy-management probes and under-lock
  refresh remain unchanged. Only initial status/validation uses the new route.
- The shared two-adopter consumer packet passed 112 cases on Windows
  PowerShell 5.1 and pwsh, with no skips and an explicit signed fixture.
  It covers complete SDK status/refusal parity, isolation, cleanup, runtime
  package-site initialization and exact argument/environment contracts.
- That packet exposed a quoted-argument split in the unchanged Dev Containers
  gate on PowerShell 5.1. A separate baseline control reproduced it before
  correction. Legacy forwarding now reuses Codespaces' existing encoded-argv
  module entry; modern argument passing is unchanged. All 24 selected-route
  and empty/numeric/quoted/Unicode/trailing-slash controls passed.
- Boundary review then reproduced a linked addressed-root exclusion escaping
  independence checks, with a real signed positive control on both hosts.
  A small shared root-to-leaf path walk now rejects candidate or addressed-root
  redirection before descending through it. Remote exclusions refuse before
  verification; absent local suffixes remain first-use compatible. The
  inverse short-path hypothesis did not reproduce: existing full-path
  normalization already handles it. Retained both-direction controls instead
  of adding unnecessary native directory APIs.
- The final helper passed all 50 native admission cases and all four real
  admitted-base consumer cases, with no skips. Its two payload copies remain
  byte-identical. The other 108 dispatcher cases exercise unchanged consumer
  code. Existing Containers publication coverage passed in complementary
  partitions: 30 payload cases plus 25 global/fallback cases, with eight
  explicit POSIX-only skips. The final helper was exercised again in the
  payload partition; the earlier other-entry partition covers unchanged
  normal-root publication behavior.
- A combined 175-node invocation exhausted its existing 900-second controller
  envelope without a test verdict. That was not credited as a pass; exact
  complementary partitions account for every existing node without raising
  per-test, production or controller deadlines.
- Next qualification builds both runtimes from materialized source into a
  fresh private profile, with canonical generated commands and provider
  registration. Unlike the earlier developer-Worktrees fixture, this must
  prove no original checkout is needed at runtime before crediting first touch.
  Whole-provider/Bridge headroom, release and authorized installed consumption
  remain open.

### 2026-10-24 - Codespaces evaluator integration and combined native cost

- Integrated only initial status/selected-context validation in the Codespaces
  Windows gate. Selection precedes evaluation; no post-selection failure
  retries through another interpreter or native helper. Complete SDK output,
  generation checks, ordinary package-site loading, both streams and exact
  forwarded arguments remain intact. The guard is disposed before runtime
  resolution, including refusal and malformed-output paths.
- Native PowerShell 5.1 and pwsh passed 45 focused consumer cases with no
  skips, including real signed-base admission and actual canonical SDK
  legacy/namespaced/deactivation JSON equality. Refusals cover foreign
  namespace source, stale context and malformed policy. Twelve existing
  selected-publication-policy cases also pass. Unicode/apostrophe payloads,
  ambient Python poisoning, selector/protocol/launch failures and guard
  cleanup are included. These native cases are not pure-unit admission.
- The first ABI run exposed local shadowing of PowerShell's automatic native
  exit variable; initialization now uses its actual global scope. Subsequent
  stricter assertions corrected two fixture assumptions: launch exceptions
  terminate with code 1, and source refusal must alter the actual namespace
  receipt, not settings overridden by an explicit context or an unknown field.
- Measured twelve fresh real dispatcher processes, including actual registry
  discovery, offline native trust compilation, canonical SDK evaluation,
  readiness and a minimal selected runtime. Three alternating-order pairs
  for PowerShell 5.1 measured native/selected seconds:
  3.850225/3.155037, 3.259938/2.547828 and 3.333858/2.465372.
  pwsh measured 4.787203/2.878162, 3.479542/2.369438 and
  3.496756/2.343991. All twelve returned identical complete legacy status,
  exact runtime output and no stderr. This includes selector cost, not just
  a Python SDK microbenchmark; three pairs do not qualify tails or the
  parent's whole-provider/Bridge eight-second requirement.
- Documentation now distinguishes Codespaces' opted-in reads from Containers'
  delivered-but-inactive helper. #8666 still owns the open Containers gate.
  No installed payload/runtime, service, maintenance or control was changed.
  Consumer review/CI, containing release, whole-provider qualification and
  authorized rollout remain open.

### 2026-10-24 - Native bootstrap selector and scoped delivery implemented

- Implemented the independent helper at the assigned installer-engine owner,
  without changing existing installer selection or SDK validation algorithms.
  The sync owner delivers identical source only to Containers and Codespaces.
- Native PowerShell 5.1 and pwsh contracts passed all 42 cases with an explicit
  signed fixture and no skips. They include real PSF signature admission,
  actual trusted other-publisher and missing-signature refusals, a held write-exclusion guard,
  modified/foreign architecture inputs, registry and verifier errors,
  opaque-tag precedence, ordinal tie-breaking, venv/embedded/linked paths,
  and an actual short-path alias of an excluded runtime.
- Default fixture discovery initially selected an unsigned development
  interpreter and skipped positive cases; those results were not credited as
  native admission. The explicit fixture run exposed a test producer's OEM
  encoding at a non-ASCII path. Matching the real gate's existing UTF-8
  initialization fixed that producer, without lossy decoding or output masking.
- The complete installer-engine delivery/materialization cohort passed
  74 tests with its one explicit POSIX-on-Windows exclusion. Scoped drift,
  missing-source and linked-delivery controls pass. Lint and sync parity pass.
- This is component source, not complete consumer integration or eight-second
  acceptance. The next slice wires only the assigned initial status/validation
  reads while preserving publication/provisioning gates. #8666 still owns the
  overlapping Containers gate; do not pre-patch its open change.

### 2026-10-24 - Operator assigns the independent selector implementation

- The operator explicitly selected `assign-new-selector-scope` after the
  retained coordinator could not be reached through the available live-session
  registry. Its active upstream source work is not presumed orphaned.
- Pulled the sole local journal onto current Dev with a backup and explicit
  commit-range checks after ordinary managed sync refused the deleted plan
  publication ref. The implementation starts from the current source rather
  than the earlier qualification fixture.
- New open #8666 owns Containers' first-use runtime gate. Keep that file with
  its current owner while implementing the independent native selector,
  focused tests and scoped payload delivery; coordinate consumer integration
  instead of pre-patching the open PR.
- The periodic existing PR observation remains read-only and unchanged.
  No installed runtime, service, held venue, source-policy algorithm or
  release control changed as part of this assignment.

### 2026-10-24 - Bootstrap design reviewed; shared assignment remains open

- #8486 merged to Dev as
  `e75ca8ed86212e8dcc50b4256ec47c7a5164275d` after actual
  Copilot approval `5637026840` of head
  `9398aea865a71aa587ade9319941e8595a1e769f`, complete empty
  inline/comment/thread reads, and successful required CI
  `40066709523` plus lockdown/attribution `40066709604`.
- Provider readback confirmed the merge. The managed Maintainer completion
  omitted only the confirmed extra CODEOWNER vote; no required check or
  feedback gate was bypassed. The clean accepted design was pulled forward
  after merged-ancestor and file-equality checks.
- Re-read the parent plan and bootstrap contract after merge. Only the design
  review item is complete. The #6812 shared-selector implementation assignment
  has not been received; silence is not a grant to edit its shared helper.
- The proposed bounded assignment is a standalone native selector at the
  existing installer-engine owner, its focused tests and the two explicitly
  qualified consumer deliveries. Preserve existing installer selection and
  SDK algorithms. Implementation and native evidence remain open.
- No new evaluation, host update, held venue, release pin or shared service
  changed. The latest complete native provider cohort still fails at
  8.844 seconds; the parent effort remains Active.

### 2026-10-24 - Independent bootstrap contract proposed

- After the preference-only native cohort failed, the operator explicitly
  selected developing a reviewed selector contract rather than waiting with
  the evaluator implicitly blocked.
- The proposal uses existing Windows CPython registration for data-only
  discovery and requires independent local executable/publisher admission
  before any candidate runs. It proposes no new interpreter registry, path
  cache, runtime-self-validation, on-read download or larger deadline.
- Existing native PowerShell evaluation remains the preselected fallback when
  no suitable independent bootstrap exists. Once evaluation starts, refusal
  stays terminal. The canonical SDK retains every source/cell/policy and
  generation check in a fresh process.
- The #6812 owner was contacted for an existing selector or concrete overlap.
  Shared implementation requires an explicit bounded owner assignment; this
  design does not pre-patch the installer's existing selection helper.
- The linked design has its own review and native qualification gates,
  including the selector's real cold cost. It is not implementation or
  evidence that the unchanged eight-second objective now passes.

### 2026-10-24 - Preference-only native qualification remains over budget

- Rebuilt the merged source through the canonical native developer installer
  in a new isolated profile. Construction completed in 324.594 seconds with
  no stderr, no predecessor and a no-op monitor transition. The source head
  remained `db4e11c90438a51eaaa6a52c47a20eac53f418f3`.
- Both executing config and context-handler files are frozen under the private
  runtime and match the recorded source hashes. The complete state-root JSON
  equals the installed reader's result. Shared installed selector and
  deployment-manifest hashes remain unchanged.
- Complete provider runs took 6.531, 6.765 and 8.844 seconds, each returning
  the actual empty container inventory without stderr. The third run fails
  the unchanged eight-second contract. The cohort stopped there; no fourth
  or fifth cold run, Bridge cohort, average-based pass or installed rollout
  is credited.
- The preference slice is a reviewed, landed optimization, not a completed
  native repair. The separate evaluator remains unimplemented pending an
  independently trusted pre-admission bootstrap selector. The bounded
  dependency question is retained with the existing #6812 owner in
  comment `6380217728`; no shared helper or SDK is pre-patched.
- Removing imports only from `context_cli` would not remove the same eagerly
  imported config/session/publication dependencies from `__main__`. Its
  composition roots are already covered by other contributors' open work;
  do not fork or silently supersede that work. A broader command-root change
  needs concrete owner coordination and its own reviewed scope.
- The next stretch is the owned bootstrap-contract dependency, followed by
  a reviewed consumer implementation and newly rebuilt native qualification.
  Source publication, required CI, release/control adoption, native discovery
  and authorized running-state acceptance remain separate gates. This effort
  stays Active.

### 2026-10-24 - Preference-read slice reviewed and merged

- #8476 merged to Dev as
  `08b0820621c49f1e8e386f5fd86c9df4ae39c266`. Copilot approved
  the actual head `a233f771c07c907a44fc83fa4cc98d1ecef2e26b`
  in review `5636927916`; the full review, inline comments and complete
  thread inventory contain no unresolved findings.
- Required CI `40064478395` and lockdown/attribution
  `40064478513` passed. The managed merge omitted only the
  provider-confirmed extra CODEOWNER vote, not required CI or feedback.
  Provider readback confirmed the merge; the accepted source was pulled
  forward after clean-head, merged-ancestor and file-equality checks.
- The published-head effect-free eight-node handler proof passed in
  2.891 seconds with no effects and contextual coverage. The real four-child
  CLI smoke passed bound/unbound JSON/text results through the canonical
  synthetic registry writer. Configuration/profile coverage and complete
  Linux CI are distinct from full installed provider/Bridge timing.
- The preference slice is merged, not deployed. Rebuild the isolated native
  fixture before measuring it. Bootstrap evaluation remains source-only:
  the reached dispatchers validate with native PowerShell before resolving
  their runtime; installer PATH/launcher probes execute a candidate before
  establishing independent evaluator authority. Do not repurpose those
  probes or an unadmitted selected runtime as its own validator.
- The effort remains Active. Native headroom, first-touch/failure controls,
  qualified release/control adoption and authorized installed consumption
  remain open under their existing owners.

### 2026-10-24 - Narrow state-root preference read implemented

- Refreshed the active source ownership and newly opened Worktrees PR files;
  the preference-read slice has no direct file conflict.
- Added a default-on knowledge-preference option at the existing config owner.
  Plain/JSON `state-root` disables only that unused contribution. Conduct and
  pair modes retain their preference paths; ordinary configuration callers
  remain full by default. Per-scope cache keys distinguish the two loads.
- The contained configuration, state-root, context-resolution and profile
  cohort passed 396 tests. New cases observe actual overlay reads in both
  cache orders and compare complete real resolver/handler results for bound,
  unbound, unregistered, explicit, self-hosted and absent-config inputs.
- Updated the CLI owner reference and Worktrees changefile. The existing eight
  effect-free handler nodes retain their identities; only their two changed
  source hashes are refreshed in the unit manifest. Lint and the 13-plugin
  install-contract gate passed.
- Current-head review, required CI and merge remain pending. This is not native
  eight-second acceptance, release adoption or installed rollout. The separate
  evaluator slice still requires an independently admitted bootstrap selector.

### 2026-10-24 - Startup-headroom plan reviewed and merged

- #8461 merged to Dev as
  `6b793523de1d38b70173c65bbd5eb64cf0537a24` after actual Copilot
  approval `5636807744` of head
  `c492262b19b1c7c18985e429f322a1f8fab999bc`, complete empty inline
  and thread reads, and passing required CI.
- The managed Maintainer merge omitted only the provider-confirmed separate
  CODEOWNER vote; no CI, hold or blocking feedback was bypassed. The clean
  accepted source was pulled forward after merge/provenance checks, preserving
  its backup when routine sync refused the deleted publication ref.
- The reviewed implementation order starts with the independent unused
  state-root preference read. Fresh-process evaluator selection still needs
  its explicit trusted-bootstrap admission and refusal qualification.
  Implementation, complete native discovery, release/control adoption and
  authorized running acceptance remain open; the effort is Active.

### 2026-10-24 - Architecture attribution before selecting the next repair

- The actual path is Bridge's registered command, a CMD/PowerShell launcher,
  a separate canonical installation-context PowerShell evaluator, Containers
  Python, a second CMD/PowerShell/Worktrees Python stack to locate the state
  root, then Docker version/list queries. Most wall time precedes Docker.
- A correlated, low-overhead diagnostic took 7.438 seconds: launcher and
  runtime selection 0.841; installation-mode validation 2.336; Containers
  runtime initialization before the peer read 0.802; the complete Worktrees
  peer read 2.890; backend/serialization/exit remainder 0.569. These are
  non-overlapping intervals of one invocation, not a sum of unrelated
  profiles. The observer used 0.031 seconds of thread CPU. A preceding
  low-overhead 8.437-second result remains an over-budget result.
- Frequent process-tree polling was rejected for latency attribution: it
  used 6.703 seconds of observer CPU and stretched its diagnostic to 14.360
  seconds. It established nesting and verified the private candidate
  interpreter was actually reached, but its wall times are not credited.
- Three alternating fresh-process controls of the existing canonical status
  implementations returned identical complete JSON: PowerShell took
  2.141/1.797/1.797 seconds; isolated no-site Python took
  0.687/0.406/0.453. The roughly 1.34-1.45-second difference is a more
  promising candidate than removing process isolation. This is only a
  positive legacy-mode comparison: trusted bootstrap selection, native
  refusal/parity cases and the real consumer still need qualification.
- The state-root result uses launch policy, the machine-local knowledge
  binding and canonical registry checks. Its current full config loader also
  resolves portable launch preferences (`copilot_profiles`,
  `profile_assignment`, `headless`, `auto_fast_forward`) that this result
  does not consume. That unrelated preference read and unrelated eager CLI
  imports are narrower candidates; the existing approximately one-second
  configuration and 1.14-second import profiles include required work too,
  so their whole totals are not claimed as removable savings.
- The separate Docker version preflight is another small candidate because
  the subsequent list operation already observes daemon availability.
  Preserve actionable missing-provider/daemon errors, source and installation
  ownership, binding refusal, native holder accounting and incomplete-roster
  semantics. No candidate from this attribution is implemented or deployed.

### 2026-10-24 - Full native boundary still misses the unchanged deadline

- The merged #8414 state-root source was built through the canonical native
  developer installer into an isolated profile. Its monitor transition was a
  no-op with no predecessor. The executing `context_cli.py` hash matches the
  reviewed source; the complete state-root JSON matches the installed reader,
  including the knowledge binding. Shared runtime selectors and deployment
  manifests remained unchanged.
- With that candidate reader behind the selected Containers command, five
  fresh full PowerShell invocations took 7.421, 6.968, 6.672, 6.922 and
  8.031 seconds. All returned the actual empty container inventory without
  stderr. The fifth result fails the eight-second requirement; neither its
  small overrun nor the four faster results is a waiver.
- The exact registered CMD command was then exercised through Bridge's real
  namespace resolver and native CPU/lifetime observer. One fresh process
  completed in 7.875 seconds with native success, both holders settled and
  five consistent warm reads. The first process of the required fresh
  five-process cohort instead reached the unchanged eight-second foreground
  timeout and reported incomplete enumeration. The cohort stopped there and
  is not credited as passing. An earlier fixture that supplied a PowerShell
  script as a native executable was invalid and is not acceptance evidence.
- Read-only profiling still attributes 3.891 seconds of the provider core to
  its nested state-root command. The candidate Worktrees core takes 3.187
  seconds wall time in that diagnostic sample, including roughly 1.14 seconds
  of import work and 1.00 second of layered configuration reads. Existing
  per-load committed-config snapshot reuse is already present; no second
  cache, skipped committed policy read or native-command shortcut was added.
- A separate read-only control of the canonical installation-context status
  script returned identical JSON and exit codes through separate-host and
  scoped invocation, taking 2.152 and 1.567 seconds respectively. This is only
  a potential startup-cost observation, not approval to remove a process
  isolation boundary or a qualified invocation change. All canonical
  validation, publication and installed code remains untouched.
- Release remains independently blocked by #8411's active `ticking-forge`
  source-only repair. The #3336/#6237 control owner retains reviewed helper
  adoption; #7743's `rusty-kiln` owner retains its explicitly assigned stdio
  and confirmed-no-launch settlement repair. #6859 and held #7833 retain
  lifetime ownership. Coordination supplied the real initializer import and
  exact-byte refusal boundary without opening a competing repair.
- Complete native discovery, qualified containing release, authorized normal
  rollout and the parent consumer task remain open. This effort is Active.

### 2026-10-23 - State-root follow-up merged; native consumption still open

- #8414 merged to Dev as
  `5a2b202b5ae94b9695f04f4a05c4c424731d3ab7` after actual Copilot
  approval of final head `f36181de7165b2400b5ec87801b05c3da1909390`,
  empty complete feedback/thread reads and all required green checks.
  Normal repository-authorized Maintainer handling omitted only the separate
  CODEOWNER vote; no CI or blocking-feedback gate was bypassed.
- Publication preserved the concurrent contributor's additional unit:
  final-head admission has 1,156 nodes, 51 targets and the unchanged
  300-second bound. Required CI qualified that actual source head.
- The owned review waiter was stopped after its fresh cursor revealed that
  the passing review had already arrived. No review or build was cancelled.
  The merged worktree was pulled forward only after exact-head, clean-tree
  and merged-ancestor checks, retaining its accepted-head backup.
- This is source merged, not a containing qualified Main release or actual
  complete eight-second running discovery. Follow the normal release and
  authorized consumer path next. Existing control adoption and installer
  integration scopes, broader #5761 outcomes and the parent guidance task
  remain open.

### 2026-10-23 - State-root continuation and fresh-container qualification

- Fresh credential-free Linux source-install witnesses at
  `c66e0bd7570ef4262d1f9c312a3593fbef52fd30` pass all six stages for
  Containers and CodeSpaces: real source preparation/installation, exact
  completed generation, operation without the source checkout, and immutable
  identical reuse. Their containers were removed through the existing rig.
- The first container attempt exposed a stale caller PATH missing Docker's
  already installed credential helper. Only the test process's PATH was
  refreshed from the installer's registered directory. The next fresh run
  correctly refused the blocked public Python feed. A separate fresh witness
  passed with the rig's explicit governed-index fixture; the failed receipt
  remains a failure. No credentials were injected or TLS checks disabled.
- These are fresh-source installation witnesses, not a full Linux suite,
  authenticated remote enumeration, or native Windows deadline qualification.
- The installed container reader still takes 12.406 seconds. Read-only core
  attribution places 7.474 seconds in its optional knowledge-overlay lookup.
  The nested Worktrees state-root read spends about 4.0 seconds composing
  foreign PR overlays, including the enabled-plugin corpus. Docker calls and
  imports do not account for that dominant delay.
- The bounded source follow-up reuses the existing
  `include_control_plane_related_pr=False` option only for the state-root
  reader. Normal publication policy, binding layers, parser/output contracts,
  native command ownership and the eight-second bound remain unchanged.
- The eight new handler cases pass the canonical unit effect observer in
  2.969 seconds with one coverage collection. All 1,147 original packet nodes
  and their source hashes remain admitted; the new packet has 1,155 nodes,
  one additional production coverage target and the same 300-second bound.
- The existing contained state-root and paired-worktree selection passes all
  129 cases after updating its coupled config-loader fixture to accept the
  existing explicit keyword. Its first 128-pass/one-failure result was a real
  fixture incompatibility and was corrected, not waived.
- A real native CLI core read from the prepared source-test environment returns
  the exact installed state-root JSON, with its knowledge binding intact.
  The observed candidate core process takes 1.818 seconds versus an installed
  core read of 5.975 seconds. These different-source environment observations
  are not a controlled speedup or production-shim/aggregate deadline proof.
  Required review/CI, qualified release and complete native caller acceptance
  remain open.
- The exact committed follow-up source
  `f4cb9e8d4bf9cb4a88c9c06c27893321c804091b` also passes the complete
  canonical Linux unit packet in a credential-free container: 1,155 nodes,
  3,465 phase reports, 51 coverage targets, no prohibited effects and 40.006
  seconds within the unchanged 300-second bound. The container received only
  a read-only committed-source archive and was removed after its receipts
  were captured.
- The complete native Windows packet is not credited: after 873 passes and
  one skip it reaches the retained #1303 preservation boundary's forbidden
  `ctypes.dlopen`. No observer exemption or skipped identity was added.
  The independent Linux packet, focused native units and binding/pair
  selection remain distinct results.

### 2026-10-23 - Containing releases and retained installed acceptance

- #8115 merged after current-head Copilot approval, resolved findings and
  required green CI with 911 admitted units. Natural #8287 qualified its
  containing Dev source `9c2b429b1473b0fda70d2b0a88f0cda31a23f417`;
  source publication/readback succeeded before the Main PR.
- Reviewed launcher repair #8297 merged as
  `85e977e6beb7059f6d29ddb4566ba292a57aae68`. Natural run
  `40020841398`, attempt one, qualified that exact source and #8302 landed
  Main `58c60f60b9d5e890f9c3ae6fc5ffdba7c925a098`.
- Normal authorized local rollout reached Manager `0.6.108-dev1` and
  Worktrees `1.24.229-dev1` activation, but the remaining suite handoff
  failed without its child diagnostic. Existing #7743 owners retain both
  reached no-window stdio hops and native settlement/lifetime integration;
  #6859 and held #7833 retain their distinct lifetime repair.
- The operator-authorized Docker prerequisite is now installed with its
  WSL 2 Linux engine running. Container enumeration succeeds, but native
  execution still exceeds the foreground budget and strict streaming reports
  HTTP 503. No shared restart, deadline increase or provider disablement was
  used. This is not completed rollout or complete-roster acceptance.
- The existing Main-control owner was asked to coordinate helper adoption.
  An unanswered comment is not a transfer. Current control, complete native
  consumption, broader #5761 outcomes and the parent guidance objective stay
  open; this effort is Active.

### 2026-10-22 - Retained postmerge journal

- #7998 merged as `958d3e2046bb395d54c7fe8d63280a5bd68e848b` after
  current-head review and required source CI. Its first natural promotion
  `39884763835` rejected candidate
  `79c140926bd984de53a667e315c2a0999f3fbbc2` with
  `manifest source closure differs from candidate`; candidate merge and
  landing recording were skipped. The earlier `39883715115` failed at the
  same boundary and its watchdog created #8033.
- That historical controller was Main
  `9489a0fe8e4284538c3e00d5aaad2ac0cf474f75` with helper
  `ce77da58017442f83bf3d82a5f80b111b63b9737`. No failed-source retry,
  closure waiver, control edit or installed rollout was performed then.
- The original postmerge journal is retained by its local backup. Its
  publication/source facts are reconciled above rather than replaying an
  obsolete checklist over current Dev. The historical partial Linux
  portfolio and all seven failing families remain with #1303; they are not a
  complete-suite passing claim.

### 2026-10-23 - Single verified source-set readback

- Review found the new schema-2 CLI still entered independent helper paths
  repeatedly, causing five candidate validations and three complete source-set
  transfers despite performing no publication writes.
- The canonical readback now returns its validated index after candidate
  validation, source-tag/metadata checks and every exact raw-byte read. The
  schema-2 candidate CLI reuses that result for its publication receipt after
  qualification and all signatures pass. Direct helper callers and the
  historical landing/schema-1 flow retain their independent checks.
- The actual CLI regression now counts exactly one candidate validation and
  one download per index/bundle per invocation. Two pure cases cover receipt
  reuse and readback failure without a success receipt; no network, timing or
  effect-observer exception is introduced.
- The focused publication/source-identity selection passed 100 cases and the
  readiness-core selection passed 32. Its other 23 native/workflow cases were
  outside this focused selection, not credited as passing. Canonical unit
  execution and new-head review/CI remain pending for this correction.

### 2026-10-23 - Source-owned composition selected

- While this repair was under review, #8110 independently merged a
  tag-aware allocator and its regression cohort. The operator explicitly
  selected the source-owned model after comparing that additional dependency
  with Main-selected Dev-source artifacts.
- Rebased onto current Dev, preserving the peer's unrelated control mirror,
  effort state and other changes. Removed only the reservation hook/helper
  and its twelve selector-specific admitted units. Existing composition
  integration tests now require unlanded aliases to have no effect, with
  normal version advancement after Main actually lands.
- Source publication now only verifies its already prepared complete source
  set: it cannot repair a missing set during a final binding. Legacy version-1
  publication and historical alias-validator refusals remain intact.
- The reconciled publication/source-identity selection passed all 98 cases;
  eight real-Git composition cases passed, including unchanged Main versions
  across abandoned aliases and normal advancement after Main lands. The
  twelve historical alias-validator/source-set integration cases also passed.
  Exact unit admission retained every unrelated identity and the unchanged
  300-second bound; lint, module ceilings and documentation guards passed.
- New-head review and required CI are pending. Live helper selection and
  installed acceptance remain separate.

### 2026-10-23 - Coupled alias-regression migration

- Review identified the retained real-Git alias suite as a directly coupled
  consumer of schema-2 version aliases. Migrated its publication assertions to
  the complete Dev-keyed sets, with both failed-Main-merge and squash-landing
  histories. Source publication no longer reads another source's aliases.
- Kept invalid prior-source provenance, signature, composition and payload
  refusals by exercising the retained alias validators directly. The cache
  regression now explicitly authenticates its input before testing reuse;
  corrupt current-source raw bytes still refuse.
- All twelve cases passed in their existing contained integration tier on
  Windows. They were not added to required unit-only CI. This is native Git
  fixture evidence, not successful historical hosted-asset verification or
  trusted Main-control activation.

### 2026-10-22 - Source-keyed publication blocker

- #7998 merged to Dev as `958d3e2046bb395d54c7fe8d63280a5bd68e848b`.
  Promotion `39884763835` passed preceding qualification but failed final
  publication; Main did not advance and no installed rollout was performed.
- #8033 was claimed before source edits. Its immutable artifact index and
  bundle digests were read back. An older unmerged candidate already owned
  Bridge `0.9.116-dev1` and Dispatch `0.18.64-dev1` aliases; the newer source
  adds `process-telemetry` under those same plugin versions. The trusted
  validator correctly rejects that unequal closure when asked to compare it
  against the older alias.
- The defect is that version-2 publication still depends on those aliases
  despite already publishing the complete signed `python-artifacts-<dev-sha>`
  set. The bounded source repair uses that canonical set for publication and
  raw-byte readback, retaining current-candidate validation and all legacy
  version-1 checks. Existing tags and assets remain untouched.
- Source support, reviewed helper activation, actual qualified Main promotion
  and authorized installed proof remain distinct gates. #3336/#6237 retain
  their wider release/control ownership.
- The changed publication and qualification selection passed all 88 focused
  cases, including two Dev sources with different bytes and the same plugin
  version. Four new pure units passed the canonical effect observer and
  single-collection coverage runner. Source hashes, module ceilings and lint
  passed without an observer exemption or widened baseline.
- A broader historical selection was not green: old materializer fixtures
  require Main-only state in a Dev checkout, and workflow fixtures expect
  retired CI jobs. Keep that non-release portfolio with #1303, not as a
  passing full-suite claim. The directly coupled raw-readback assertion now
  expects the canonical helper's actual digest error. A separate focused run
  passed 287 cases before one newly added fixture-construction failure; the
  corrected fixture is included in the final 88-case result.
- Read-only native verification of the historical published set remains
  incomplete: local bundle verification could not initialize its public
  verifier, and this platform's candidate checkout did not reproduce the
  hosted payload hash. Neither failure was waived or normalized away. Those
  attempts are not release qualification. Source review and required CI,
  separately authorized helper activation and actual hosted publication
  remain open.

### 2026-10-22 - Required peer-map metadata and retained Linux scope

- The latest review identified a successful peer response lacking `account_map`.
  That required response field is now validated instead of defaulting to an
  empty map. The legacy account-list shape with an explicit empty map remains
  supported. The focused CLI/account/peer run passed all 136 cases.
- Retained hosted execution receipts confirm Linux CodeSpaces default-full
  qualification at `81065d74a03477c0e7e91ce7775c94d5f514d909`: 1,984 passes
  and 98 explicit skips across all four batches. This includes the new native
  owner-exit and overlap regressions; skips are not credited as passes.
- The Worktrees snapshot regression file belongs to its first successful
  batch. The full Worktrees invocation later stopped in batch five of 24 on
  two bootstrap/installer assertions, so it is only partial suite evidence.
- The completed inspection had seven failing plugin jobs. Their failing tests
  and named implementation paths are unchanged from the candidate's base;
  no baseline-control run was performed, so this is not proof of a fully
  green or regression-free portfolio. Failures cover Worktrees bootstrap,
  container probe structure, Vault service-output/publication, Logger source
  redirection, MCP generated-resolver/manifest fixtures, Bridge ordinary-task
  fixture identity, and Dispatch process-flag fixture typing.
- The workflow declares Test Portfolio Rationalization #1303 as the failure
  owner and marks this portfolio non-release-dependent. Preserve those
  findings for that owner rather than modifying unrelated installer/contracts
  in the namespace repair. Later parser/configuration-seam corrections have
  focused Windows evidence, not a new same-head Linux full-suite receipt.

### 2026-10-22 - Existing configuration seam and explicit individual refusal

- Source CI refused four additional lines in the grandfathered configuration
  module. Removed the new loader parameter and reused its existing
  `provider_reports` seam to supply the opt-in activation scan. The unchanged
  module ceiling now passes; no baseline was widened or assertion removed.
- Preserved explicit-context decoding refusal for individual account lookups
  too. They raise `ContextRefused`, rather than returning no preference and
  permitting ambient credentials. A regression exercises `env_for_repo`.
- The focused consumer/config/authentication run passed 310 cases with six
  existing skips. Requalification through the actual default-budget Bridge
  consumer completed five fresh scans in 6.078, 5.906, 5.985, 5.641 and 5.844
  seconds, each finding the target and serving five warm reads. The malformed
  map remained incomplete in 7.078 seconds; exact restoration recovered in
  6.218 seconds. Native and sampling holders settled.
- The existing periodic Linux inspection is pinned to candidate
  `81065d74a03477c0e7e91ce7775c94d5f514d909` (run `39881444618`).
  Its CodeSpaces suite passed, but several other jobs failed and the run was
  still active when inspected. Their logs and the exact exercised coverage
  remain owed; this is not an all-green Linux qualification claim.

### 2026-10-22 - Namespace-only overlap and bounded owner compatibility

- The next review summary identified interpreter-shutdown retention in the
  existing bounded credential-owner helper. Its daemon thread must not acquire
  executor workers whose shutdown hooks extend the caller's process lifetime.
- Narrowed overlap to explicit namespace enumeration. Ordinary listing,
  account-candidate and configuration reads retain their original serial
  defaults; namespace enumeration opts into all three overlap layers.
  No daemon executor, detached worker, larger budget or new shutdown owner
  was introduced.
- A real child-process regression blocks an owner listing for twenty seconds
  while the bounded helper returns unknown; ordinary interpreter shutdown must
  still finish within the five-second test bound. Additional cases prove
  serial default execution, namespace opt-in and activation-root opt-in.
- The focused consumer/config/authentication suite passed 309 cases with six
  existing platform/optional skips. Five fresh actual Bridge scans completed
  in 6.266, 5.812, 6.422, 5.750 and 5.766 seconds under the unchanged default
  eight-second deadline, each followed by five warm reads. Malformed stored
  mapping remained incomplete in 6.547 seconds; exact restoration recovered
  complete discovery in 6.688 seconds. Native and sampling holders settled.
- The existing manual periodic Linux workflow can inspect an immutable
  candidate ref through the canonical hardened container runner. That hosted
  lane is distinct from unavailable local Docker and the unit-only PR packet;
  its candidate execution remains pending.

### 2026-10-22 - Undecodable peer-output refusal

- The next review had no remaining formal threads but identified undecodable
  peer output in its summary. Both legacy and same-cell snapshot calls now
  translate decoding errors into `AccountInventoryError`; the optional wrapper
  cannot turn those errors into a successful empty namespace.
- Added legacy snapshot and explicit-context namespace CLI decoding regressions.
  The focused CLI/account/peer suite passed all 134 cases. No deadline,
  success-path worker, activation-library source or effect-observer rule changed.

### 2026-10-22 - Explicit-context failure preservation

- The next #7998 review identified a separate boundary: same-cell peer startup,
  timeout and receipt failures raise `ContextRefused`, not `AccountInventoryError`.
  The optional namespace wrapper now preserves both exception types.
- Three CLI regressions exercise the real same-cell peer adapter with startup,
  timeout and exit-126 failures. Each produces a nonzero namespace-list exit
  without a successful JSON roster. The focused CLI/account/peer run passed
  132 cases.
- The carried strict-mapping finding was already corrected by `16ef16a14d`:
  the strict reader rejects malformed stored mappings, seven real-storage
  bulk-command cases passed, and native malformed-map qualification retained
  incompleteness. This is an addressed finding, not another required source fix.

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
