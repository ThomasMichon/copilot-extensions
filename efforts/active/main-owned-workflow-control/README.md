# Main-Owned Workflow Control

- **Slug:** `main-owned-workflow-control`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees; ordinary changes target `dev`
- **Created:** 2026-10-12
- **Status:** Active; portfolio audit and shared consolidation roadmap refreshed; release qualification and gated implementation remain open
- **Vision:** `visions/main-owned-workflow-control/README.md`
- **Umbrella issue:** #6237
- **Related campaigns:** #3336; #7122; #1303; `promotion-failure-reactive-fix-agent`; #6142

**Current shared entry point:** [Workflow portfolio audit and consolidation
roadmap](workflow-portfolio-audit.md). It connects the pinned inventory,
operating baseline, prioritized work, ownership seams and acceptance gates to
the [standing vision](../../../visions/main-owned-workflow-control/README.md).

## Guiding Intent

Reduce redundant status checks and overall pipeline executions so Copilot review
can run more often without unnecessary throttling. Preserve validation assurance
and contribution gates while removing repeat work, redundant triggers and
ownerless automation. Main-owned workflow control is a means to that outcome,
not the campaign's highest-priority deliverable.

Make critical control independently repairable only where needed for safe
consolidation. All other contribution, including admin-authored implementation,
continues through dev. A safe reduction under existing proven gates does not
wait for the entire ownership migration. Record ownership and consolidation
completion separately; neither alone completes the overall objective.

The October 19 test-portfolio direction makes reliable, speedy promotion and PR
CI the immediate priority: parallel unit suites with coverage baselines,
conservative impact selection for PRs, and every unit-level test run for every
Main release. Integration and multiprocess coverage moves to scheduled Dev
execution with accountable repair ownership, not silent deletion. Reduced
promotion frequency or integration-gated promotion remains a later
dev-backed-deployment consideration.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign driver | Lead portfolio audit, shared plan, sequencing, measurements and accepted implementation slices | Umbrella issue #6237 and per-slice managed worktrees |
| CI/release controller | Own active release qualification, promotion writer, CI integration and release admission | #3336 and coordinated #6237 slices |
| Trusted Linux offload owner | Own backend selection and runner-routing source contracts; coordinate CI edits | #7122 and its existing participant |
| Test portfolio owner | Preserve selected coverage, platform/stress boundaries and coverage equivalence | #1303 and component test owners |
| Capacity operator | Qualify the selected image/venue and enforce admitted shared-host limits | #7122 capacity handoff; provider-specific state stays downstream |
| Remediation owner | Own failure signatures, bounded repair and named escalation | #6142 and the existing remediation effort |
| PR completion steward | Own Phase 5a readiness, author guidance, consent automation and completion visibility; coordinate shared provider and release seams | #6237 Phase 5a; #3638, #4234 and the linked completion contract |
| Repository owner | Critical-path acceptance and bounded administrative decisions | Owner-authored PRs and issue steering |
| Automated reviewer | Review each plan and implementation PR | Repository Copilot review flow |

## Coordination

- **Topology:** independent per-slice PRs, serial landing.
- **Host (owns PRs):** Campaign driver, explicitly appointed by the operator.
- **Delegates:** bounded read-only audit evidence may be partitioned by workflow
  family. Implementation ownership must be accepted for one non-overlapping
  slice before editing; no competing implementation is assigned by this plan.
- **Handoff:** journal the current slice, exact evidence and outstanding gates;
  successors continue this canonical effort, not a peer plan.
- **Reconciliation:** the other planning agent's #6237 comment delegated
  canonical vision/effort publication here and retained a separate ownership
  completion gate. No published competing effort or PR was found.
- **October 18 reaffirmation:** the operator explicitly assigned this campaign
  driver point ownership of the audit and consolidation plan. This is the same
  canonical #6237 effort, not a second control or runner campaign. Existing
  #3336/#7122/#1303 and remediation implementation/admission scopes remain.
- **October 18 PR completion assignment:** the operator assigned a PR completion
  steward to drive the existing Phase 5a through reviewed implementation and
  acceptance. Its [completion contract](maintainer-pr-completion.md) incorporates
  readiness, guidance and claims-visibility findings. This does not transfer the
  campaign driver's portfolio lead, release writer, review-rescue or runner
  authority. Shared-file changes require coordination with those owners.
- **Accepted Wave A seam:** the existing Main-control participant accepted
  read-only metadata workflow/helper/event closure and separately reviewed
  negatives/docs, reporting no active writer on the named workflows/helpers.
  [The bounded closure](metadata-event-closure.md) records the contract.
  Live integration remains subject to release/control acceptance; a public
  overlap scan does not clear unpublished custody.
- **October 19 single-driver decision:** following the operator's direction
  below, the Campaign driver owns the source-only synchronize consolidation
  and its directly related metadata tests/docs through Dev review. The scope
  changes only the departure synchronize trigger and an isolated refresh job
  in the trusted Dev entrypoint, reusing unchanged refresh helpers. Existing
  release/publisher, pause, qualification, Main activation, runner selector
  and PR-completion authorities remain with their current owners. Concrete
  conflicting edits must be reconciled before landing; responsibility is not
  handed off merely by sending another message.

## Context

The campaign began with wholesale Main regeneration, which could delay critical
workflow repairs behind unrelated Dev failures or erase them at promotion.
The preservation exception remains narrow, not permission to keep arbitrary
Main drift.
See [design.md](design.md) for the proposed contract and staged cutover.

The October 11 audit is historical evidence, not current failure status. The
[October 18 snapshot](workflow-portfolio-audit.md) records the current source
catalog, distinct preserved Main inputs and a fixed-window run baseline. Live
policies and active runs must be refreshed before each enforcement cutover.
Related review-request repair #6233 was already released; do not repeat it.

## Request

> One proposal I have with another agent is to support main have its own,
> direct workflow declarations, with a second set pushed from dev. This way,
> we can direct-to-main control over critical pipelines, while still allowing
> dev to drive most CI-flow pipelines and receive proper review and validation.

> Note that I want main PRs scoped to (critical) workflow and metadata updates
> only; all other contributions, even from admins, must go via dev.

> Sounds good. I'll put you in charge of this, start driving

> Ultimately, your goal is consolidation and elimination of redundant status
> checks, to produce fewer overall pipeline runs so that Copilot reviews can
> run more often without getting throttled. The main-owned-workflow-control
> is a means to an end, but not the highest priority.

> Yes, noting we always still need an escape hatch. The Copilot reviewer still
> isn't perfect, so we as maintainers always need a way to forcbly self-merge as
> a worst-case, but we don't want agents reaching for that button prematurely.

**2026-10-18 - Audit and consolidation lead (verbatim):**

> Take point on auditing and consolidate of the workflows, based on the analysis you ran. Ensure we have a checked-in effort providingte plan and vision, so everyone can work toward it

This reaffirms the existing campaign driver and requires one discoverable,
checked-in plan and vision. The detailed waves, measurement dimensions and
proposed implementation assignments below remain **agent-recommended**.

**2026-10-18 - Maintainer completion automation (verbatim, with an unrelated
implementation reference generalized):**

> One thing we can do is develop one more background automation in GitHub which, similar to request-review-if-maintainer, operates on maintainer-owned PRs, and helps admin-merge PRs form maintainers which have passed CI, have an Approved stamp from Copilot (and no change requests from anyone else), and are tagged with auto-merge. It should also take care of auto-rebase, similar to [the existing review automation], leaving only conflict resolution to originals submitters. If we add this, then hopefully we can make agents know to just set auto-merge and let PRs go through.

Only the implementation reference is generalized; the requested merge gates,
label consent, automatic rebase and submitter conflict ownership are preserved.
The implementation and activation decomposition below is agent-recommended.

**2026-10-18 - PR completion ownership and inhibitor repair:**

The operator requested immediate clarity for authorized Maintainer submitters,
hierarchical claims visibility, and ownership of the existing completion
automation slice through implementation. The exact public-safe direction,
analysis, sequencing and acceptance gates are recorded in
[Maintainer PR completion](maintainer-pr-completion.md). The existing Phase 5a
remains the canonical plan; this is not another effort or release controller.

**2026-10-18 - Bounded review fallback (verbatim):**

> Great. Continue the plan and figure out how to make fallback possible

> So we'll need to periodically check Runner status, and if we start seeing long queue durations or failures for Copilot Code Review, and be able to admin-check-in a switchover to assign to our runners, then re-queue Copilot Review for some bounded duration, then release and check again.

The operator selected a repository-wide bounded rescue design with hosted
default and reviewed activation. The [rescue contract](bounded-review-rescue.md)
contains **agent-recommended** lifecycle, authority and qualification details;
this request does not itself authorize a live routing or credential change.

**2026-10-18 - Provider-neutral owner-only scope (verbatim):**

> Make sure the rescue plan doesn't mention linux-N machines; it should just be a general proposal for local providers (running on behalf of the repo owner only) to step in with a backup runner set in the case of congestion.

The rescue proposal is provider-neutral and carries no concrete machine
inventory. Only repository-owner-delegated local providers may supply its
backup runner set; provider/repository/workload admission must enforce that
boundary before activation. This does not authorize a shared contributor pool.

**2026-10-19 - One accountable driver (verbatim):**

> Let's not stalemate. Make sure one of you drives it

The Campaign driver retains delivery accountability. The bounded source-only
metadata slice is the first execution step selected under that direction;
this decomposition is **agent-recommended**. It neither transfers the existing
release publisher nor authorizes a second controller, pause bypass, Main
activation or live rescue configuration.

**2026-10-19 - Test-portfolio burndown (summary):**

Assist the existing Main agent by sweeping promotion and PR CI history for
flaky and overlong tests, reducing validation count and critical-path time
without losing unit release coverage. The full operator request and the
agent-recommended evidence and implementation decomposition are recorded in
[Test validation burndown](test-validation-burndown.md). This extends the
existing portfolio audit, not the release writer's ownership or authority.

**2026-10-19 - Upstream repairs need not wait for Main (verbatim):**

> Ideally we don't wait on main activation. Fixing tests upstream will help unblock main

Take independently owned upstream test repairs through ordinary Dev review
without treating Main activation as a prerequisite. Existing contributors'
reserved hunks and release/control authority remain unchanged.

The operator confirmed the explicit main-owned control/dev-owned content model.
The detailed decomposition and validation cases below are **agent-recommended**,
reconciled with the other planning agent's public handoff on #6237.

**2026-10-21 - Return to whole-flow optimization (verbatim):**

> What's the wait here? ARC is working; move on to this worktree's original goal of auditing, analyzing, and improving the overall CI and promotion flows. Analyze runner queue times, improve conditional execution, and reduce redundancy across the board

The immediate execution focus is the existing flow-consolidation objective,
not further capacity setup or individual installer-test repairs. The next
source reduction and measurement methods are agent-recommended; required
checks, release-unit coverage and existing source/control boundaries remain.

**2026-10-22 - Immediate review timing (operator-selected behavior):**

The operator selected `immediate_if_unrequested`: request review immediately
when no pending/current-head review or recent request exists. Preserve the
observed-request grace, Maintainer-only eligibility and all merge gates.
Trusted Main activation and actual timing remain separate from source merge.

## Plan

All slices use the execution gates in [design.md](design.md#phase-execution-gates).
The campaign driver owns implementation/review; the repository owner alone
authorizes critical main-path acceptance or a bounded administrative change.

### Current slice - Portfolio audit and shared consolidation roadmap

- [x] Inventory all 21 workflow YAML declarations and the separate generated
  source, classify Dev workflow families and compare the pinned Main catalog
  and blob/declaration differences. Record literal job counts separately from
  matrix executions, checks and live runner allocation.
- [x] Capture an initial complete fixed-window workflow-run baseline and the
  required-context sources; preserve its pagination, time bounds and limits.
- [ ] Complete helper/recipe closure and comparable event/job/check/queue
  measurements for the next proposed reduction; the initial audit is not proof
  every apparent duplicate is safe to remove.
- [x] Map the accepted Wave A metadata event/helper boundary at pinned source
  commits and exercise existing negatives, including its promotion-verifier
  consumer; [record prospective Wave B obligations](metadata-event-closure.md).
  This does not close portfolio-wide equivalence or authorize production edits.
- [ ] Accept non-overlapping implementation ownership and execute the ordered
  waves in [the shared roadmap](workflow-portfolio-audit.md#ordered-execution-plan).
- [x] Land the Campaign-driver-owned source synchronize consolidation and
  its required metadata regressions (#7236).
- [ ] Obtain the separately owned qualified Main activation and live
  synchronize/close/retarget, rollback and comparable measurements.
- [ ] Prove actual admitted self-hosted execution before claiming offload;
  preserve hosted review and distinct full/native qualification.
- [ ] Design and qualify [bounded native review rescue](bounded-review-rescue.md)
  with existing runner/controller owners: owner-only local-provider backup
  set, periodic observation, authorized checked-in switch, bounded requeue
  window, hosted restoration and recheck.
  Prove the control/admission path's availability or explicitly hold/escalate;
  reviewed planning is not live enablement.
- [ ] Observe the resulting release and contribution cycles, preserve rollback,
  and resolve the parent Plan/Validation Plan before declaring consolidation done.

### Phase 0 - Reviewed contract and baseline

- [x] Land this vision/effort through ordinary dev plan review (#6284).
- [ ] Complete the branch protection, ruleset, environment, workflow,
  credential-binding metadata and in-flight release snapshot; never read secret
  values. Identify enforced freshness and admission beyond the initial audit.
- [ ] Classify every workflow and its helper/action dependencies, including
  required contexts, event filters, credentials and failure-remediation owner.
- [ ] Measure runs/jobs/checks per contributor update, dev advance and release;
  reviewer queue/start latency and observed throttling. Distinguish GitHub
  Actions concurrency from Copilot service limits; do not assume one shared quota.
- [ ] Rank safe redundant trigger/check removals under existing gates ahead of
  ownership work that is not needed for those reductions.
- [x] Resolve the trusted-check publication limitation explicitly: the common
  Actions app and a check name cannot prevent deliberate Write-actor spoofing.
  Do not claim stronger guarantees than the selected enforceable boundary.
  Operator accepted the existing trusted-collaborator boundary on October 13;
  no cryptographic same-app spoofing guarantee or new publisher is claimed.

### Phase 1 - Earliest safe run and check reductions

- [x] Land the bounded source-CI job consolidation and remove duplicate
  report-only PR inventory (#7656), preserving real selected-unit outcomes,
  every admitted unit, parallel guards and the independent release pass.
  Natural source/push CI and the closed, unmerged narrative validation #7663
  prove the actual conditional outcomes and retained push diagnostic.
  This does not close the whole-flow measurement and release-cycle items.
- [x] Omit the checkout-only unit job before runner allocation on pushes,
  preserving source guards, PR outcome checks, manual/unknown-event behavior
  and promotion's independent full unit execution (#7953). Actual source PR
  CI runs all 877 admitted cases; natural post-merge push CI succeeds with
  the unit job skipped, no runner and zero steps. This removes one no-test
  allocation per push, not a workflow invocation or guard critical path.
- [x] Land bounded same-release upload batching and four-way independent raw
  readback in the source helpers (#7679), preserving qualification, signer,
  metadata, source/tag identity and receipt gates. Existing-control selection
  and comparable natural publication timing remain open under Phase 6;
  source merge alone does not activate a new trusted helper pin.
- [x] Land and observe the bounded tracking-write guard traversal reuse (#7692),
  preserving all sibling files, failure handling, alias fixed points and
  diagnostic ordering. Keep local paired timing separate from hosted timing
  and the broader whole-flow acceptance gates.
- [x] Land and observe bounded headless-guard analysis reuse (#7700), preserving
  all production roots, six enforcement rules, allowlists and failure handling.
  Keep local paired timing distinct from natural hosted observations.
- [x] Land and observe conservative narrative-only PR omission of the three
  source AST scans (#7706), retaining full non-narrative and non-PR execution,
  pinned input/error handling, every other guard and all required unit evidence.
  Draft validation #7712 proves actual narrative-only omission without
  manufactured unit evidence; it was explicitly closed without merging.
- [x] Land bounded independent bundle-attestation verification (#7732),
  retaining serial index/schema/original-admission checks, all exact signer
  identities, consumer authority, deadlines and joined failures. Existing
  control selection and comparable live publication timing remain open;
  this source reduction does not update the trusted helper pin.
- [x] Land and observe fixed three-worker execution of the unchanged full
  source AST scanners, preserving pinned narrative admission, joined failure
  diagnostics and bounded step/child execution (#7768). Actual source CI
  retains every scanner and completes the group in 25 seconds. Local paired
  timing is not controlled hosted whole-flow acceptance or a CPU-work reduction.
- [x] Land bounded, invocation-local dependency source-tree reuse, retaining
  every evidence read, changed-input reparse, path/identity/closure check and
  failure (#7845). Equivalent attribution and current required CI pass; the
  actual all-ecosystem guard audits 332 declarations with zero violations.
  Local phase timing is not full live metadata or pipeline timing.
- [x] Land and observe shared install-contract lexical scanning (#7967),
  retaining both exact views, all 286 PowerShell files and ordered violations.
  Native/Linux differential and attributed complete-guard comparisons pass;
  actual PR and natural push guards pass for all thirteen installer plugins.
  This bounded CPU reduction is not controlled hosted or release latency.
- [x] Remove only the duplicate full Bridge registry invocation on source PRs
  (#7979), retaining the complete diff-aware check and the non-PR full check.
  Actual hosted PR and natural push each execute the intended checker once,
  covering both contracts and all 456 fixtures. This removes one repeated
  invocation per PR, not a workflow run or runner allocation.
- [x] Land the bounded history-free source-unit checkout reduction (#8001),
  preserving the pinned Dev snapshot, all 877 admitted cases, contextual
  coverage and every ancestry-dependent release checkout. A genuinely shallow
  fresh-Dev fixture qualifies the actual 94-file source closure; source review
  does not migrate the independently preserved Main controller or establish
  a hosted promotion critical-path saving. Qualified control adoption remains
  separate.
- [ ] Remove redundant release-shaped PR and main-push validation only where
  existing trusted gates and exact-SHA release evidence cover the removed work;
  first prove no generated-output coverage or required context is lost.
- [x] Land and observe bounded asyncio ancestry-map reduction, preserving
  every file read/parse, path validation, handler classification and ordered
  diagnostic/list output (#7865). Actual required source CI runs all three
  scanners in 22 seconds. Local paired comparisons and this natural sample
  do not close comparable whole-flow acceptance.
- [x] Land the operator-selected immediate review request when no existing
  activity qualifies, retaining recent-request suppression and all author,
  identity, epoch, credential and one-write boundaries (#7914).
  Qualified trusted Main activation and natural timing remain open; source
  merge does not establish a deployed latency reduction.
- [x] Consolidate main-PR admission into its existing trusted reminder
  invocation: release and prove admission and generated-content identifier
  scanning first, then remove legacy CI allocation while retaining full
  main-push validation.
  #6789 proves zero CI invocations and preserved required admission/configured
  scanning. Main-push CI still runs; its generated-tree failures remain open
  below and are not a passing-coverage claim or permission to remove that lane.
- [x] Coalesce or prevent redundant event-triggered runs, not merely mark all
  jobs skipped; preserve timely contributor feedback and review delivery.
  #6308/#6314 reduce trusted invocations; released #6359 prevents irrelevant
  PR ancestry runner allocation. Natural events verified both remaining jobs.
- [ ] Measure before/after run and check counts and review latency over comparable
  events; retain distinct heavy/platform/installation coverage.
- [x] Deploy only the minimal critical control repair required to unblock the
  next safe consolidation slice, through the narrow owner-reviewed main path.
  #6619 unblocked the actual promoter and #6631 merged; post-merge artifact
  publication remains separately owned under #6632.

### Phase 2 - Inert preservation support where needed

- [x] Extend the existing composer with strict, exact-path preservation from
  pinned main; absent manifest retains existing behavior (#6521).
- [x] Reject unsafe/missing files, collisions and conflicting ownership; record
  control provenance and test intentional deletion (#6521).
- [x] Land implementation, tests and related documentation via dev (#6521).
- [x] Verify a covering promotion releases inert support while no preservation
  manifest is active; release is separate from source merge.
  #6631 releases #6521's exact support blob without an activation manifest.

### Phase 3 - Trusted admission and composed validation

- [ ] Implement full-diff owner authorization on both relevant targets,
  protecting wrappers, metadata and promoted implementation/actions.
- [ ] Validate final composed output, same-commit helper compatibility and
  exact dev/main evidence; fence stale candidates before merge.
- [ ] Prove failure/rejection and rollback contracts before activation.

### Phase 4 - Narrow main control activation

- [ ] Establish reviewed critical workflow/metadata allowlist; no helper source,
  tests or docs admitted directly to main.
- [ ] Activate the exact preservation manifest only after support is released.
- [ ] Land thin control wrappers through an owner-authored main PR; reusable
  implementations use same-composed-commit local `workflow_call` references.
- [ ] Fence/drain old release writers and demonstrate one canary, concurrent
  control repair, repeated promotion and rollback preserving current control.
- [ ] Restore/verify any separately authorized temporary policy adjustment
  before resuming ordinary publication; that restoration adds no bypass actor
  or broad credential.

### Phase 5 - Finish validation and automation consolidation

- [ ] Complete the [test validation burndown](test-validation-burndown.md):
  measured promotion/PR CI history, reliable parallel unit suites, per-suite
  coverage baselines and conservative PR selection, full unit execution on
  every Main release, and scheduled Dev integration/multiprocess runs with a
  named repair owner. Coordinate the implementation with the existing release
  and test-portfolio owners; do not activate a superseded full-corpus profile.
  The bounded history sweep and requested contract merged in #7275; suite
  classification, coverage selection, scheduled repair and live timing
  acceptance remain open.
  The independent CLI-fixture correction merged in #7288; comparable Linux
  timing acceptance remains on #7075, with no production provisioning change.
  The pinned unit-census and actual periodic-execution audit merged in #7506.
  It distinguishes the admitted 236-node packet from the unfinished full unit
  identity map and the still-unproved accepted repair initiation for #7377.
  The Campaign driver initiated and merged #7546 for #7377's Containers
  fixture family, preserving real disk-refusal coverage in a complementary
  non-tmpfs run. Other failure families and a subsequent complete scheduled
  execution remain open; this is not whole-portfolio acceptance.
  The separately retained Windows framing source repair #7264 is also merged
  and its original source worktree finalized. Current-head native framing
  evidence and actual review/required CI passed; the old blanket native-suite
  publication blocker was reconciled with the current unit-only PR policy.
  This neither closes the separately tracked #7387/#7433 native repairs nor
  establishes release, rollout or complete periodic-portfolio acceptance.
- [x] Map the fourteen original shared lazy-dispatch unit bodies into the
  unchanged release packet without copying assertions (#7749). All 408
  previous identities remain, yielding 422 source-qualified nodes with
  actual call coverage. This bounded mapping does not close the complete
  historical census or establish a subsequent Main release's execution.
- [x] Map the 201 original fleet-contracts cases through exact function objects
  and unchanged parameterization, preserving all current packet identities,
  closed source hashes and production-call evidence (#7781). Actual hosted
  source CI executes all 646 admitted cases in 13.666 seconds. This does not
  close the whole census or every-release acceptance.
- [x] Map the 72 original worktree-identity cases through exact function
  objects and unchanged parameterization, preserving the current packet,
  source closure and production-call evidence (#7816). The canonical packet
  and real refusal controls pass; actual hosted source CI executes all 718
  admitted cases in 12.126 seconds. Full-census and every-release acceptance
  remain separate.
- [x] Map the 29 original config-migrate cases without copying assertions,
  retaining synthetic temporary-file operations, unchanged source admission,
  actual production-call coverage and the 300-second packet bound (#7831).
  Actual source CI executes all 747 admitted cases in 11.369 seconds.
  Full-census and every-release acceptance remain separate.
- [x] Land the dev implementation of reviewed, widen-only module-size baseline
  maintenance and remove its obsolete main exception from source (#6438).
- [x] Map the nineteen original drop-in registry cases through exact original
  function objects, retaining actual temporary-file/link/FIFO assertions,
  source closure, production-call coverage and the existing packet budget.
  #7874 and actual hosted execution prove all 778 admitted cases in 15.166
  seconds. This bounded Linux mapping is not full-census or Windows qualification.
- [x] Map 33 original local plugin-resolution cases through 29 exact original
  functions and unchanged parameterization, retaining the subprocess regression
  outside the packet and preserving source closure, call coverage and budget.
  #7881 and actual hosted execution prove all 811 admitted cases in 15.526
  seconds. Full-census, platform and every-release acceptance remain separate.
- [x] Map all 29 original documentation-navigation units through unchanged
  function objects, preserving existing packet identities, source closure,
  actual production-call evidence and the 300-second budget (#7925).
  Actual hosted source CI executes all 840 admitted cases in 15.204 seconds.
  Full-census, native-platform and every-release acceptance remain separate.
- [x] Map all 29 original activation-preservation cases through eleven exact
  function objects and unchanged parameterization, retaining the closed source
  hashes, actual call coverage and 300-second packet budget (#7931).
  Actual hosted source CI executes all 869 admitted cases in 12.971 seconds.
  Real process settlement, native Windows, full census and every-release
  acceptance remain separate.
- [x] Verify a covering promotion activates the dev-owned baseline producer on
  default main; record authorized runtime adoption separately.
  #6631 and hosted job `115706505844` prove activation of #6438; this
  workflow-only capability needs no local plugin/runtime update.
- [ ] Record ownership completion separately; it is not the prerequisite for
  Phase 1 reductions proven safe under existing protection.
- [ ] Establish one exact-input validation contract and compact generated
  candidate gate; preserve distinct heavy/platform/installation coverage.
- [ ] Implement the operator's promotion-lifecycle clarification through the
  canonical [release effort's Phase 9](../dev-branch-release-pipeline/README.md#phase-9---source-owned-release-identity-and-repeat-blocker-prevention):
  retain one active promotion through actual merge, recover run-ended/open-PR
  obligations, and reuse complete exact-input qualification without duplicate
  flaky full-check gates. Mechanisms remain agent-recommended and review-gated;
  independent Main control and existing authority boundaries are unchanged.
- [ ] Remove redundant promotion-PR/main-push validations only after replacement
  gates are required and proven; migrate old contexts/callers before deletion.
- [ ] Reconcile repair ownership with #6142 and existing remediation effort;
  disabled repair automation must escalate to a named owner.
- [x] Route configured Dependabot version-update PRs to dev without weakening
  dependency validation. All eleven uv entries target dev; the existing
  `test_dependency_version_updates_use_the_contribution_branch` guards this
  contract, and live Dependabot #6710 targets dev. Event-normalized invocation
  savings remain part of Phase 1 measurement, not an inferred result here.

### Phase 5a - Opt-in Maintainer merge and rebase stewardship

_(Agent-recommended implementation of the October 18 operator request;
implementation follows review of this amendment. Authority setup and live
enablement require their own explicit acceptance.)_

Execution owner: **PR completion steward**. The
[completion contract](maintainer-pr-completion.md) defines ordered slices,
regressions, retained-owner seams and the completion gate.

- [x] Land the amended vision and completion plan through ordinary review
  before implementation; retain all existing portfolio and activation gates.
  #7219 merged after current-head approval and required CI.
- [x] Repair present-day self-merge readiness and guidance first (#3638,
  #4234): distinguish actor authorization, review evidence, reviewer vetoes,
  required CI and consent. Status, watch and mutation must not contradict one
  another or ask for another approval solely because a permitted review-rule
  exception exists.
  Source repairs merged in #7225 and #7232. Qualified release and actual
  installed behavior retain the separate adoption gates below.
- [x] Preserve every outstanding reviewer change request in the mutation path;
  a repository-authorized exception to missing required approval cannot waive
  a veto, required CI, holds, draft state or head identity.
  #7232 merged with independently reviewed negative/effect-boundary contracts;
  this records the source implementation, not an installed-runtime claim.
- [ ] Close completion-visibility gaps: reuse #7134's descendant-PR projection,
  expose truncated/unresolved claims, and distinguish source merge, qualified
  release and actual installed behavior. Do not recreate an already-merged fix
  or silently copy descendant ownership into an ancestor's ledger.
  Manager completeness/detail implementation merged in #7228; qualified
  release and installed-view acceptance remain open.
- [x] Reuse the existing invited-collaborator plus root CODEOWNERS Maintainer
  boundary. Propose an initial Dev-only, same-repository, nondraft scope:
  exclude Main, generated release candidates and the promotion publisher.
  Require the `auto-merge` consent label and respect configured holds.
  Disabled source and admission contracts merged in #7244; no live consent
  binding or automation identity is activated.
- [x] Evaluate fresh current-head required CI, including required check-source
  identities, and the latest actual current-head Copilot `APPROVED` verdict
  after any reopen or base-change epoch.
  Reject active change requests from every other reviewer. Paginate history;
  a later `COMMENTED` or pending review does not clear a human negative.
  Shared policy and disabled-controller contracts merged in #7232 and #7244;
  actual mutation-identity and event-order qualification remain below.
- [x] Use native GitHub `REBASE` with expected-head compare-and-swap when an
  update is necessary for a clean, policy-compliant merge. Do not substitute a
  merge update, execute PR hooks/code, resolve conflicts or reuse old-head
  approval/CI. Return genuine conflicts to the original submitter with one
  deduplicated notification; races and unknown responses are not conflicts.
  #7244 proves these source contracts without enabling the writer. Current
  Dev policy is non-strict; base movement alone does not require a rebase.
- [ ] Implement a short, event-driven trusted-default-Main reconciler, with
  bounded manual/catch-up recovery for event-order gaps. Treat trigger data as
  hints and re-read actual PR state before each write. Do not hold runners
  waiting for CI/reviews, launch review waves or retry uncertain writes blindly.
  The bounded manual implementation merged in #7244. Automatic subscriptions
  remain unwired pending the separate activation decision.
- [ ] Keep mutation authority out of head-controlled review events and
  untrusted checkouts. Prefer a short-lived, single-repository automation
  identity with only needed permissions. A new App, credential, review-only
  bypass entry or live enablement needs explicit operator approval; existing
  human exemptions and the licensed review-request credential do not imply it.
  CI protections and Main publication requirements remain unchanged.
- [x] Record owner acceptance of the existing repository-only App and the
  completion-token permission subset. The owner confirmed the permission
  additions and approved the updated installation on October 20.
  This is not independent token verification or credential/activation consent.
- [ ] Apply the owner-approved, reviewed CODEOWNER-only policy split:
  preserve the independent one-approval rule without an App bypass and give
  the App PR-only bypass on a separate CODEOWNER-only layer. Preserve all
  existing human actors, CI and Main protections. The
  [completion contract](maintainer-pr-completion.md#codeowner-only-exception-preserve-the-independent-approval-rule)
  owns create-first application, restore-first rollback and canary obligations.
  The October 20 application passed provider readback but exposed a managed
  client compatibility refusal. The original combined rule was restored and
  the added layer disabled. Actor-scoped layered-policy compatibility and
  caller qualification must pass before reapplication; no bypass of the
  independent approval requirement is licensed by this failure.
- [x] Prove external rebase compatibility with the owning managed worktree:
  stale local publication refuses, unpublished work/backups survive, and a
  later verified squash merge supports `pr-complete` and claim settlement.
  Extend existing provider/lifecycle seams only if focused tests expose a gap.
  Three real-Git source-level contracts passed and merged in #7244. These do
  not replace the separately authorized live App canary.
- [ ] Land the implementation disabled through reviewed Dev contribution and
  normal qualified promotion. Keep it Dev-owned unless an independently
  governed critical-control requirement is separately approved; do not expand
  the Main preservation allowlist merely because the job is privileged.
  Reviewed disabled Dev source merged in #7244. Qualified promotion #7391
  includes the readiness, claims, disabled controller and Vault prerequisite
  changes; producing run `39469486744`, attempt 1, passed exact source-unit,
  artifact publication/readback and nonadministrator merge gates.
  Actual installed-runtime and Manager behavior remain unproven. Earlier
  #6947/#6564 failures and any new local installation failure require their own
  acceptance; source publication and a payload refresh are not runtime proof.
- [ ] After authority acceptance, prove report-only operation and one
  explicitly consented rebase/new-CI/new-approval/merge canary. Verify both
  CI-first and review-first event ordering and the actual mutation identity.
  Only then update contribution guidance and the managed consent binding so
  agents opt in, watch the actual merge, and reconcile their worktrees.
  A label or delivered request alone is never completion.

### Phase 6 - Observe and complete

- [ ] Observe two release cycles, supersession, controlled failure/recovery,
  independent main control repair and rejected untrusted admission.
- [ ] Update as-is docs/patterns, audit final policies and record measured
  redundancy reduction with unchanged required coverage.
- [ ] Demonstrate fewer overall runs/checks on comparable event sets and report
  observed Copilot review throughput/latency; state any independent service
  throttling that remains rather than claiming consolidation fixed it.
- [ ] Resolve every validation item before completion and archival.

## Validation Plan

- [ ] Test-portfolio routing: compare collected unit identities with the
  release execution manifest; every unit runs for every Main release.
  Missing/stale/incompatible coverage baselines select full affected suites
  rather than silently omitting unknown coverage.
- [ ] Scheduled Dev integration: prove the retained integration/multiprocess
  inventory is scheduled, its exact Dev input and outcomes remain visible,
  and a failure reaches an accountable agent that initiates repair or
  explicitly escalates. A schedule declaration alone is not acceptance.
- [ ] Test-portfolio timing: report comparable before/after queue and execution
  measurements, sample sizes and reliable failure signatures; distinguish
  cancellation, infrastructure failure and confirmed flakiness. Do not
  manufacture a passing budget by increasing deadlines or deleting witnesses.
- [ ] Unit: exact allowlist, invalid/case-equivalent paths, traversal, symlinks,
  executable modes, submodules, missing files and collisions fail closed.
- [ ] Real Git integration: materialization, preservation, rename/deletion,
  no-op/repeated release, provenance and rollback at pinned commits.
- [ ] Negative admission: outsider/admin mixed source-plus-control PR,
  unapproved metadata, forged check/artifact and self-authorizing allowlist fail.
- [ ] Fork isolation: untrusted head code cannot execute in privileged
  `pull_request_target` jobs, receive release credentials or use trusted
  persistent self-hosted runners; external approval remains required.
- [ ] Final composition: incompatible wrapper/helper, altered recipes and
  missing/skipped/cancelled/mismatched evidence block publication.
- [ ] Race: main advances after candidate creation; stale publication cannot
  overwrite control repair; demonstrate enforced freshness, not polling alone.
- [ ] Clean-room: generated runtime/install behavior on a fresh environment;
  state concrete lane and any unavailable-tier justification per slice.
- [ ] Live Actions: required contexts report for admitted shapes, actual
  merged tree/tag match evidence, and an independent repair survives promotion.
- [ ] Migration: old gates remain until new ones pass; release writer exclusive;
  rollback and policy restoration are demonstrated.
- [ ] Consolidation: distinct stress/Windows/heavy coverage retained and
  failure routing assigned; no unowned required failure.
- [ ] Maintainer steward policy: missing/stale/spoofed/pending/failed CI,
  incomplete review pagination, old-head Copilot approval, persistent human
  change requests, label removal, holds, drafts, forks and Main targets refuse
  without mutation.
- [ ] Maintainer steward races: head movement, base movement, rate limits,
  ambiguous rebase/merge responses and duplicate events never force a write,
  reuse old evidence or silently manufacture success. State the provider's
  head-CAS versus base-freshness guarantees honestly.
- [ ] Maintainer steward isolation/lifecycle: trusted Main code and accepted
  scoped credentials only; no PR-code execution, Main/publisher bypass or
  review-runner route change. Actual canary proves new-head CI/review delivery,
  conflict return and managed local reconciliation before enablement.
- [ ] PR completion readiness: the same evidence produces consistent
  status/watch/merge decisions for approved and clean-comment review shapes,
  absent CODEOWNER approval, unknown authority, stale evidence and persistent
  change requests. Existing contributor human-review requirements remain.
- [ ] Completion visibility: parent/child/grandchild claims are flattened
  before shared ranking and canonical deduplication; bounded or unresolved
  results are visibly incomplete, and the original owner remains discoverable.
  Record release and installed-producer evidence separately from source tests.
- [ ] Bounded review rescue: resolve every obligation in the
  [rescue validation matrix](bounded-review-rescue.md#validation-obligations),
  including fault classification, independent control availability or explicit
  outage hold, restart/dedup, failed restoration and one authorized complete
  native local-backup-to-hosted cycle with enforced repository-owner delegation
  before periodic enablement.

## Proposal

See [design.md](design.md). This is proposed behavior, not an as-is claim.
Plan approval authorizes implementation through review gates, not unbounded
credential changes, broad policy weakening or unreviewed releases.

## Journal

Historical entries are preserved verbatim in dated files; each links back to
this contract. New evidence belongs in its date file, not another peer effort.

- [2026-10-12](journal/2026/10.12.md)
- [2026-10-13](journal/2026/10.13.md)
- [2026-10-14](journal/2026/10.14.md)
- [2026-10-15](journal/2026/10.15.md)
- [2026-10-16](journal/2026/10.16.md)
- [2026-10-18](journal/2026/10.18.md)
- [2026-10-19](journal/2026/10.19.md)
- [2026-10-19 - Metadata source delivery](journal/2026/10.19-metadata-source.md)
- [2026-10-19 - Test burndown contract and measured evidence](journal/2026/10.19-test-burndown.md)
- [2026-10-20](journal/2026/10.20.md)
- [2026-10-20 - Independent upstream test repairs](journal/2026/10.20-test-repairs.md)
- [2026-10-21 - Windows Dispatch base selection](journal/2026/10.21.md)
- [2026-10-21 - Whole-flow CI consolidation](journal/2026/10.21-flow-consolidation.md)
