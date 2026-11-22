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
The October 23 rolling follow-up separates fast runner pickup from promotion
admission, actual review execution and late synthetic check refreshes. Its next
investigation is semantic event/helper closure, not another capacity setup.
The November 9 priority extends installed release qualification through the
[Manager Create/Resume/Open lifecycle](installed-manager-lifecycle.md), reusing
the existing #11377 native and #3336/#7529 release owners.

## Goal

**Kind:** completion; a finite campaign under the operator's assignment in
[Request](#request).

**Root goal (operator-defined, verbatim; source: Request):**

> Ultimately, your goal is consolidation and elimination of redundant status
> checks, to produce fewer overall pipeline runs so that Copilot reviews can
> run more often without getting throttled. The main-owned-workflow-control
> is a means to an end, but not the highest priority.

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
The November 9 request explicitly adds the bounded installed-Manager lifecycle
exception to that portfolio policy. It does not restore unrelated integration
suites to fast PR CI or transfer the promotion writer.

The November 12 readiness-compatibility amendment selects
`refuse-unqualified-probes`: return unknown before executing an unqualified
older probe, with the owning updater as recovery. Preserve ordinary commands,
separately authorized installer actions and the older-baseline qualification
requirement. The exact decision and its limits are recorded in the
[installed-lifecycle scope approval](installed-manager-lifecycle.md#november-12-readiness-compatibility-approval).
This adds no rollout, shared-repair or Main-activation authority.

The subsequent census-budget decision selects `allow-query-five-seconds`:
change only the process-census per-query limit from one to five seconds,
add focused controls and require new native proof. Session creation, overall
job/stage deadlines, cadence, caps and fatal-error policy stay unchanged.
The [scope approval](installed-manager-lifecycle.md#november-12-census-query-budget-approval)
preserves the exact prompt and response; it grants no rollout or failure waiver.

The November 16 implementation-strategy decision selects
`migrate-in-current-pr`: complete the newly required strict typing migration
within the current PR, preserving its fixes and review history rather than
splitting typing prerequisites into separate PRs. The
[scope approval](installed-manager-lifecycle.md#november-16-strict-migration-strategy)
retains the exact decision. All validation gates, capture limits and rollout
holds remain unchanged.

The subsequent `fix-grouping-in-current-pr` decision permits the narrow
strict-checker correction for independent standalone programs with same-named
modules. Its [scope approval](installed-manager-lifecycle.md#november-16-strict-checker-program-grouping-approval)
preserves every selected physical file, owner, strict flag, Python/platform
target and fail-closed aggregate. It does not release the Index cleanup hold or
authorize a new qualification profile.

The November 17 `fix-capture-in-current-pr` decision permits bounded HTTP-52
observability/performance repair within the same PR. Its
[scope approval](installed-manager-lifecycle.md#november-17-bounded-http-52-capture-repair-approval)
preserves every probe, the original 240-second aggregate and all prior holds.

**Acceptance:** the Plan and Validation Plan below establish fewer redundant
executions while preserving validation assurance and contribution gates.
Installed-suite qualification must prove the real Manager Create/Open/Resume
flows and the required candidate-bound refusal and publication behavior, not
only source tests or installation success. Every remaining item must be
verified and resolved, or explicitly transferred to a named tracked objective
with accepted ownership; no ownerless remainder is completion.

**Finalization criterion:** this whole effort is explicitly Done, all Plan and
Validation Plan items meet that disposition, required publication/review/merge
gates have passed, and the effort is archived and released through the normal
repository flow. Retained custody follows its existing owners. Finishing a
typing pass, native run, PR or session does not permit premature finalization.

**Now:** #12013 merged at `6c008aa84a1ddede8690390501754170c3386aec`
on November 17 at 10:02 UTC. Exact-head Copilot approval `5936343344`,
required CI `44059828122` and all eight resolved findings precede the managed
merge. This completes the operator's merge-first order, not this parent.
Resume remaining installed-Manager result/refusal, authentic-baseline and
frozen-candidate acceptance work through its existing owners. Preserve the
order's deferred-capture policy and the unavailable #1303 Bash follow-up;
neither is silently qualified. Dev #12995 retires the Bridge registry CI gate,
so do not restore it or demand a replacement capture as a source-merge gate.
The next natural controller run `44063395428` freezes candidate
`8f90be5410d3fe6626a2b7dde804ded31a3c9fba` from Dev `73c8cab85c`, including
#12013. Units pass, but artifact construction, installed TTY and Windows
Resume fail; promotion correctly refuses. The descriptor-layout gap is
subsequently repaired by the existing artifact source fix #13086 at
`965684d48c51dddfb279c337fe8a591ecc1b29be`. On November 18 the operator directs
adoption of that merged implementation and removal of the independently
qualified, unpublished duplicate, retaining the journals and evidence.
Current source is pulled forward without overwriting the broader reviewed
share-data contract. No competing PR, trusted-controller activation, native
rerun or failure waiver accompanies that reconciliation.
The existing owner's later successful release `44397683514` publishes Dev
`f2516d375206cbad6adf0b91d6eacc47a5bcf922` through #13302, merged at
`5163a31ce167f74df563e3b8bae14eaba4b9d555`. Source ancestry and both artifact
helper blobs establish that #13086's correction is now published on Main.
All thirteen executing release jobs pass, including the existing TTY and
Resume contracts; the failure watchdog skips. This is not the larger installed-
suite lifecycle or authentic-baseline qualification, and no operator runtime
is updated. The earlier failed run and cleanup holds remain unchanged.
The subsequently authorized one-run `manager-suite` attempt `44430369826`
at released Main `c9e2c3e68b` fails in all three modes before preparation:
the runner cannot import `agent_procutil`. The #12013 import change removed
the preceding canonical process-library bootstrap. Open #12708 already
contains the explicit bootstrap in its overlapping runner changes, so the
exact failure is shared with its authoring agent rather than pre-empted by a
competing patch. A bootstrap-only cooperation decision is separate from that
PR's broader authentic-source acceptance. No native retry is authorized.
The author subsequently agrees the bootstrap-only handoff on #12708, and
acceptance is posted before editing. The restorative source fix and real cold-
CLI regression now proceed independently through ordinary review and CI,
preserving the broader PR and its existing ownership.
That bounded correction merges in #13496 at `ef88d21b28c87c5cc9d6ce30ec007156650622c5`
after current-head Copilot approval and required CI. The
[November 19 journal](journal/2026/11.19-bootstrap-restoration.md) records the
bounded source milestone; containing release adoption is subsequently verified
through #13527, without native qualification. The consumed one-run grant,
broader #12708 ownership and every retained hold remain unchanged.
After a new explicit one-run grant and verified containing release #13527,
follow-up `44654996590` reaches preparation in all three modes but fails
materialization on ten already-present declared assets. All three diagnostics
are retained; no Manager lifecycle result is qualified and no retry is
authorized. The dated journal records the separate settlement limits and
preserves the materializer and authentic-source owners.
Separately, the [portfolio audit](workflow-portfolio-audit.md#november-17-source-pr-edit-repeated-execution-not-a-reuse-hit)
proves one exact-context repeated source-CI execution after an evidence-only
body edit. The base-retarget/event seam is coordinated on #3336 before shared
CI changes; neither finding authorizes another capture or Main activation.
The independent [lookup-attribution slice](journal/2026/11.20-unit-lookup-attribution.md)
merges in #13603 at `bc68912640affd7e3c106f18f743f3ef6cdea5e5` after fresh
approval and complete current-head Linux CI. Its bounded diagnostic is visibly
flushed before the fresh unit handoff; the original lookup and packet budgets,
proof checks and provider request sequence remain. This is measured diagnostic
delivery, not a workflow-count reduction, a reuse hit or attribution of the
historical exhausted stage. The separate Windows collection gap #13598 remains
open, and native preparation stays with its existing owner.
The [November 20 identifier contract trace](workflow-portfolio-audit.md#november-20-identifier-validation-contract-closure)
then establishes shared scanner implementation but distinct required-enforcement
and detailed-reporting contracts. Direct deletion is not justified. Any
one-scan integration needs an accepted shared CI/release-owner seam and proof
that required enforcement, stale-head refusal, existing findings feedback and
Main-targeting admission survive. No workflow-count reduction or new execution
is claimed for that read-only closure.
The operator retains that shared-owner boundary and selects continuation of the
[scheduled portfolio audit](workflow-portfolio-audit.md#november-20-scheduled-entrypoint-census-and-stress-pair-trace).
Its bounded census distinguishes 78 observed invocations from declared jobs
and failed/cancelled coverage. The two stress suites remain distinct. A later
full-context admission trace observes one executing scheduled job and 80
distinct skipped manual-portfolio check records, not 81 test executions.
Dispatch-preserving reduction and complete helper/repair-owner closure remain
unqualified; no held native selector or shared workflow is changed.
The subsequent
[periodic wrapper trace](workflow-portfolio-audit.md#november-20-periodic-wrapper-selection-and-containment-closure)
closes its default Linux selection, online preparation/offline execution,
containment and refusal boundaries. Preparation is not a second pytest run;
default-full still excludes declared discovery/explicit-tier residuals.
No live wrapper result or equivalent unit/native replacement is claimed.
The [remaining weekly and maintenance traces](workflow-portfolio-audit.md#november-20-weekly-test-callers-and-inventory-only-reduction-candidate)
then distinguish real Handoff/Dispatch coverage from the inventory-only
Payload CWD invocation and separate hourly label policy from weekly branch
cleanup. The concrete trigger-reduction request is with the existing #1303
owner; the observed purge refusal names existing cleanup PR #13117 under
#3336, not a new takeover. Source traces cover all ten scheduled declarations,
but replacement acceptance, live effects, unresolved failure signatures and
comparable saving/coverage measurements do not follow from that inventory.
The [current declaration delta and first-hop map](workflow-portfolio-audit.md#november-20-current-declaration-delta)
extend the historical inventory to 33 YAMLs, including twelve added native/
controller declarations also present byte-identically at the observed Main
snapshot. The actual review embeds its minimal setup rather than adding a
second setup run; the legacy trusted-CI automatic entry is already retired.
Separate native/typed selections and source-missing repair-claimant declarations
remain explicit frontiers, not removal permission or completed qualification.
Ubuntu run
`44028815333` at the prior checkpoint `7ab79f8cfb` passes all
638 controlled cases in both write and independent no-write modes, then all
122 historical-publication controls under the original limits. Its 218
exported outputs are independently receipt-verified, canonically materialized
and validated against both contracts and all 1,141 fixtures. This qualifies
that checkpoint, not later changed inputs, native Copilot or installed Manager
behavior.

The #12013 rebase preserves Dev's then-active packet cohort and both owned capture
histories rather than splicing observations. Twelve prior execution inputs
genuinely change; every original probe remains selected at 229 functions and
638 cases. The manual canonical checker identifies fresh `_peer_launch.py`
evidence that remains unperformed, not required CI after #12995.
All 172 selected physical Python files
in 24 owners pass their declared targets; the former compact-core hunk is now
byte-identical to Dev, not excluded. That PR's complete 5,255-node, 341-target,
821-input unit manifest preserves both prior inventories and its original
budget. A positive Vault fixture is corrected without qualifying older
runtimes: 16 focused controls, 69 configuration controls and seven related
reader controls pass under unchanged limits.

The prior `7ab79f8cfb` provider checks, including Windows publication and both
Borrowed Budget platforms, succeed; they are not current-head gates for the
reconciled source. The earlier `00831b44a4` Windows cleanup
failure and its exact finished/reused-instance hold remain reported on #6237;
a later green job is not record deletion, custody release or relabelling of
that failure. The retained Index cleanup hold, native POSIX proof and hosted Windows
acceptance remain open. Source review and CI are complete for #12013;
no original custody hold changes.

**Return:** after the refresh, re-read this Goal and the linked scope approvals,
reconcile retained implementation/checker custody, and finish that slice under
the unchanged gates. Earn current required CI before new installed Windows
acceptance; then return to the remaining portfolio audit and consolidation
outcome rather than treating the latest child milestone as the root goal.

## Guiding Intent

The former Guiding Intent is retained verbatim in Goal above. The
[Request](#request) preserves the originating operator wording and subsequent
campaign amendments.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign driver | Lead portfolio audit, shared plan, sequencing, measurements, accepted implementation slices and the new installed-lifecycle witness | Umbrella issue #6237 and per-slice managed worktrees |
| CI/release controller | Own active release qualification, promotion writer, CI integration and release admission | #3336 and coordinated #6237 slices |
| Trusted Linux offload owner | Own backend selection and runner-routing source contracts; coordinate CI edits | #7122 and its existing participant |
| Test portfolio owner | Preserve selected coverage, platform/stress boundaries and coverage equivalence | #1303 and component test owners |
| Capacity operator | Qualify the selected image/venue and enforce admitted shared-host limits | #7122 capacity handoff; provider-specific state stays downstream |
| Remediation owner | Own failure signatures, bounded repair and named escalation | #6142 and the existing remediation effort |
| PR completion steward | Own Phase 5a readiness, author guidance, consent automation and completion visibility; coordinate shared provider and release seams | #6237 Phase 5a; #3638, #4234 and the linked completion contract |
| Installed Manager qualification coordinator | Retain authentic installation, native TTY helpers and shared gate interfaces | Existing #11377 effort; coordinate actual shared-file edits |
| Required-readiness contributor | New profile/input companion and focused controls for actual Machines readiness and ordinary-upgrade/New | Accepted bounded contribution on #11377; existing native/runner/workflow files retained |
| Repository owner | Critical-path acceptance and bounded administrative decisions | Owner-authored PRs and issue steering |
| Automated reviewer | Review each plan and implementation PR | Repository Copilot review flow |

## Coordination

- **Topology:** independent per-slice PRs, serial landing.
- **Host (owns PRs):** Campaign driver, explicitly appointed by the operator.
- **Attribution intake:** A7906-P/G and the local P7906/V7906 receiver
  preserve the existing attribution, #6909/#7900 and promotion owners.
  Planned receiving neither appoints an implementer nor authorizes a CI
  rerun, failure injection, probe, renewed attempt or resource operation.
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
- **November 9 installed lifecycle:** the Campaign driver captures the
  CI/promotion requirement and reviewed contract. #11377 retains its native
  fixture and open Main-activation work; #3336/#7529 retain candidate,
  construction and publication authority. Coordinate actual overlapping
  native-helper, runner/result and workflow edits before touching those files.
  Independent new-file witness/profile work does not wait for the whole Main
  PR to merge. A posted handoff is not acceptance or live activation.

The finite #7075 continuation remains with the existing campaign/test-burndown,
Handoff hosted-acceptance and distinct binding/publisher/concurrency/
installation-identity custodians. Preserve #5245 containment, #2660
identity-cost, native-repair, CI/release and capacity owners. No implementer,
runtime action, new validation lane or generic signoff is appointed; the actual
nonexpiring hosted claim remains until completed or authoritatively released.

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

**2026-11-09 - Installed Manager lifecycle (verbatim):**

> Since this worktree is focused on CI and promotion, this is a good place to request that we focus on ensuring that between CI and promotion runs, we validate that worktree-manager can actually launch after install and be able to create and resume worktrees. We regularly encounter issues where after an install, some weird qualification got added that is failing, a "resolve path" fails, PSMux connectivity fails, etc. We need to have e2e tests at some point which set up a representative harness, install our suite, launch worktree manager, create a worktree via manager, start a session (we can mock Copilot's endpoint to avoid token issues), exit Copilot, then open Worktree manager to Resume. We also need a test where we open Worktree Manager in parallel to an active session and Open it.

The [installed lifecycle contract](installed-manager-lifecycle.md) is the
**agent-recommended** realization. It extends existing qualification rather
than treating installed TTY rendering or direct launch-plan tests as proof of
the full requested user flow.

**2026-11-09 - Avoid whole-PR coordination barriers (verbatim):**

> Do what we can to avoid blocking on someone else's PR. Identify the source agent across all our machines if we own it and coordinate with it

The concrete new-file split and shared-interface coordination below are
**agent-recommended**. The existing Main adoption remains with its owner;
its completion is not a prerequisite for independent Dev qualification work.

## Plan

**2026-11-21 - Compact manifest representation (operator-selected behavior):**

The operator selected `compact-versioned-manifest` rather than sharding,
further v1 minification as the long-term approach, or switching away from unit
census work. Implement reader-first support through normal source review while
retaining every existing limit and admission. This selects a source approach,
not writer activation, native execution, rollout or a transfer of existing
CI/release control. Ordered file groups and normalization to the existing
logical manifest are the agent-recommended realization.

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
  The November 20 [environment/name-binding readback](workflow-portfolio-audit.md#november-20-environment-and-credential-binding-metadata)
  complements the required-context readback. A bounded
  [in-flight release-control snapshot](workflow-portfolio-audit.md#november-20-in-flight-release-control-snapshot)
  records the active promotion, two old queued trigger records and unresolved
  API count/row discrepancies. Complete custody is not inferred from that
  inventory; metadata alone does not establish actual token identity, scope
  or custody, and creates no new operational authority.
- [ ] Classify every workflow and its helper/action dependencies, including
  required contexts, event filters, credentials and failure-remediation owner.
- [ ] Measure runs/jobs/checks per contributor update, dev advance and release;
  reviewer queue/start latency and observed throttling. Distinguish GitHub
  Actions concurrency from Copilot service limits; do not assume one shared quota.
- [x] Rank safe redundant trigger/check removals under existing gates ahead of
  ownership work that is not needed for those reductions.
  The November 20 [current candidate ledger](workflow-portfolio-audit.md#prioritized-candidate-ledger)
  puts the echo-only weekly allocation and owner-gated one-scan interface
  first, separates delivered source work from pending activation and does
  not count retired automatic entrypoints as future savings. Ranking is not
  implementation acceptance or measured whole-flow completion.
- [x] Resolve the trusted-check publication limitation explicitly: the common
  Actions app and a check name cannot prevent deliberate Write-actor spoofing.
  Do not claim stronger guarantees than the selected enforceable boundary.
  Operator accepted the existing trusted-collaborator boundary on October 13;
  no cryptographic same-app spoofing guarantee or new publisher is claimed.

### Phase 1 - Earliest safe run and check reductions

- [x] Qualify and land operator-authorized exact-input reuse for repeated
  source PR unit qualification. Bind complete source/workflow/manifest,
  platform/interpreter/dependency and provider run/attempt identities;
  missing or incompatible proof retains fresh execution. Preserve current
  guards/metadata/retarget/aggregate behavior and independent execution for
  every Main release. Source implementation and synthetic refusal tests are
  not real consumer, current-CI or whole-flow performance acceptance.
  #8311 merges with exact-head approval and required CI. A real ordinary
  repeated PR run verifies the original immutable packet and omits all 1,129
  repeated unit executions; current guards and the required aggregate pass.
  Qualification takes 27/8 seconds and the allocated unit job 49/27 seconds
  in one cold/reused observation. Overall CI remains guard-bound (62/69
  seconds); no whole-flow, queue, release-adoption or full-census gate closes.

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
- [x] Land bounded AST-fact admission for the headless guard's later visitors,
  preserving every source read/parse, all six rules, scope/alias semantics and
  ordered diagnostics (#8213). Actual source CI retains all three scanners,
  completes their group in twelve seconds and executes all 1,012 admitted
  units. Empty-pass removal and local paired timing are not controlled
  whole-flow latency; the full source-CI invocation remains.
- [x] Observe qualified release adoption of #8213 separately from source-CI
  execution and comparable whole-flow timing; this changes no controller pin.
- [x] Qualify and land invocation-local fieldless AST dispatch specialization
  for the headless guard, retaining every input read/parse, all six rules,
  allowances, custom handlers and malformed-instance fallback.
  The [bounded source slice](workflow-portfolio-audit.md#november-21-fieldless-visitor-dispatch-candidate)
  merges in #13787 at `d72add0240b7cdd4929cb10c26eee4cac29bda50` after
  current-head Copilot approval and complete required Linux CI, with focused,
  strict-typing and full-corpus equivalence evidence. This is recipe work
  reduction, not fewer workflow invocations, qualified release adoption or
  native lifecycle qualification.
- [x] Qualify and land the original headless-guard units in the closed packet:
  isolate their public allowlist fixture input, retain all 113 original cases,
  parameterization and assertions, and export the original functions/fixture
  with one isolation regression. Require actual guard call coverage for all
  114 additions and complete current-head source CI without changing packet
  budgets, source-admission rules or helper-coverage gates. #13798 merges at
  `83a3973a982ed38f0791f133f5b9389e4a8c2434` after exact-head Copilot approval
  and complete required Linux CI with freshly executed packet evidence.
  This closes only the bounded source admission, not full-census, every-release,
  native or workflow-consolidation acceptance.
- [x] Qualify and land the original sibling guard units through exact test
  objects: 91 tracking-write cases and twelve asyncio analysis cases, preserving
  both original suites and the ten explicit native subprocess residuals.
  Require unchanged previous admission, real per-case production call coverage,
  source closure, helper gates, packet budget and current-head source CI.
  #13801 merges at `5ab627967b0a6a6555c9fbd680cc3b16c01292fc` after
  exact-head Copilot approval and freshly executed complete required Linux CI.
  Native residuals, every-release observation and full-census acceptance remain
  separate.
- [x] Qualify and land all 42 original install-contract guard analysis cases
  through exact original test objects, preserving the original suite and
  production helper. Require closed source admission, real per-case guard call
  coverage, unchanged prior packet contents, helper gates and 300-second
  budget, then complete current-head source review and Linux CI. #13808 merges
  at `cc8196e947b856f7ccb5150974054ae8edc59776` after exact-head Copilot approval
  and complete required Linux CI with freshly executed packet evidence. Broader
  install-contract integration, native qualification, every-release observation
  and full-census acceptance remain separate.
- [x] Qualify and land the original configuration-guard unit batch through
  exact test/fixture objects: 89 cases across six suites, retaining the feed
  Git-path fixture, whole-checkout scan and package CLI smoke outside the packet.
  Preserve all original assertions, production guards, prior packet contents,
  source closure, actual per-case guard call coverage, helper gates and the
  300-second budget. #13814 merges at
  `cfd97a8ba7ca632ed771164d92f18275ee5da010` after exact-head Copilot approval
  and complete required Linux CI with freshly executed packet evidence.
  Broader integration, native, every-release and full-census gates remain open.
- [x] Qualify and land all 32 original Picker inbox-discipline cases through
  exact test/fixture objects, preserving the original suite and AST guard.
  Require closed source admission, real per-case guard call coverage,
  unchanged prior packet contents, helper gates and the 300-second budget,
  then complete current-head source review and Linux CI. #13826 merges at
  `082cc04be63bc7d255a39eff3cd6e4428acfffa2` after exact-head Copilot approval
  and complete required Linux CI with freshly executed packet evidence.
  Native Picker, every-release, workflow-count and full-census acceptance
  remain separate.
- [x] Qualify and land the 24 original temporary-document effort/vision and
  marketplace-isolation guard cases through exact test objects, preserving the
  original Git-history and whole-checkout inventory residuals outside the
  packet. Require actual per-case production call coverage, source closure,
  unchanged prior admission, helper gates and 300-second budget, then complete
  current-head source review and Linux CI. #13829 merges at
  `2fcfa5c63009b5cf6ec5472716cb6ea051878fdc` after exact-head Copilot approval
  and complete required Linux CI with freshly executed packet evidence.
  Broader/native and full-census acceptance remain separate.
- [x] Qualify and land eighteen original vendored-library synchronization
  guard cases through exact test/fixture objects, preserving the original
  native symlink residual outside the packet. Require real per-case guard call
  coverage, closed source admission including existing shared helpers,
  unchanged preceding packet/helper/budget gates, then current-head source
  review and complete Linux CI. #13834 merges at
  `cbcd81463e3768a243a514e2b2aa7ef2fe120f99` after exact-head Copilot approval
  and complete required Linux CI with freshly executed packet evidence.
  Native installation/materialization, every-release and full-census
  acceptance remain separate.
- [x] Qualify and land thirty original large-file Git protocol and source-AST
  subprocess-control cases through exact test objects. Preserve all fifty
  original real Git/process residuals outside the packet, real per-case
  production call coverage, source closure, preceding admission, helper gates
  and the 300-second budget. #13840 merges at
  `7dfefbb42ed36d9238844a52555ad513671c9f9f` after exact-head Copilot approval
  and complete required Linux CI with freshly executed packet evidence.
  Broader native, every-release, workflow-count and full-census gates stay open.
- [x] Qualify and land eleven original mocked local-Git evidence and
  module-size probe controls through exact test objects. Retain all 83
  native/schema/policy residuals and the retired Bridge-registry CI boundary;
  require source closure, actual per-case production call coverage, unchanged
  prior admission/helper/budget gates, current-head review and complete Linux
  CI. #13841 merges at `f43e440735478c62d36b29166ae84f921d077724`
  after fresh exact-head approval and complete required Linux CI following
  real Dev reconciliation. Broader native, every-release, workflow-count and
  full-census gates stay open.
- [x] Qualify and land ten original in-process independent-program typing
  controls through exact test objects and their original autouse scratch
  fixture. Retain four actual MyPy cases and the real toolkit-data case outside
  the packet; require closed sources, actual per-case production call coverage,
  unchanged preceding admission/helper/budget gates, current-head review and
  complete Linux CI. #13879 merges at
  `87d01c5a1ad87b6961afa5fcd789fc6a209903d8` after exact-head Copilot approval
  and complete required Linux CI with freshly executed packet evidence.
  Broader native, every-release and full-census gates stay open.
- [x] Qualify and land twelve original mocked identifier Git-blob protocol
  cases through four exact test objects. Preserve all 54 original residuals,
  source closure, actual per-case production call coverage, preceding
  admission/helper/budget gates, current-head review and complete Linux source
  CI. #13884 merges at `5a2410ebb51119343ee3366a6f87dcfc4bb9ba67`
  after exact-head Copilot approval and complete required Linux CI with freshly
  executed packet evidence. No native Git/process or real identifier sweep is
  admitted or replaced; broader native, every-release and full-census gates stay open.
- [x] Qualify and land 58 original in-process dependency release-age cases
  through thirteen exact test objects. Preserve all 145 original residuals,
  source closure, actual per-case production call coverage, preceding
  admission/helper/budget gates, current-head review and complete Linux source
  CI. #13887 merges at `04510251c0f1551d7478bccc46f86eaef668fa41`
  after exact-head Copilot approval and complete required Linux CI with freshly
  executed packet evidence. No registry, installer or native-toolchain witness
  is replaced; broader native, every-release and full-census gates stay open.
- [x] Qualify and land 26 additional original synthetic-metadata dependency
  cases through thirteen exact test objects in the existing adapter. Preserve
  its preceding 58 cases and all 119 remaining originals outside the packet,
  fixture-owned inventory, source closure, per-case production call coverage,
  admission/helper/budget gates, current-head review and complete Linux source
  CI. #13889 merges at `ffb9f8b130b9c49a2217410863c1599202e483f8`
  after exact-head Copilot approval and complete required Linux CI with freshly
  executed packet evidence. No registry, installer, native-toolchain or
  whole-checkout witness is replaced; broader native, every-release and
  full-census gates stay open.
- [x] Qualify and land 42 additional original fixture-only source-attribution
  cases through thirteen exact test objects in the existing dependency-age
  adapter. Preserve its preceding 84 cases, all 77 remaining originals outside
  the packet, source closure, per-case production call coverage, prior
  admission/helper/budget gates, current-head review and complete Linux source
  CI. #13892 merges at `9f80675a62572da526244ecdf899e701ecbc5192`
  after fresh corrected-head Copilot approval and complete required Linux CI.
  The combined peer cohort is retained under the unchanged cap and budget.
  No preparation fixture text executes, no original assertion is replaced,
  and broader native, every-release and full-census gates stay open.
- [x] Qualify and land seven additional original mocked metadata-transport
  cases through four exact test objects in the existing dependency-age adapter.
  Preserve its preceding 126 cases, all 70 remaining originals outside the
  packet, source closure, per-case production call coverage, prior admission/
  helper/budget gates, current-head review and complete Linux source CI.
  #13895 merges at `4c90091e566dbb0834491972c3a7d7e8c4e64bdc` after
  exact-head Copilot approval and complete required Linux CI with freshly
  executed packet evidence. No npm, registry, installer or native-toolchain witness is replaced;
  broader native, every-release and full-census gates stay open.
- [x] Qualify and land the original fixture-only npm selector case through its
  exact test object, preserving the preceding 133 selections and all 69
  original residuals. Bind the real selector module and require positive
  call-phase coverage of both wrapper and selector, zero prohibited effects,
  unchanged preceding admission/helper/budget gates, current-head review and
  complete Linux source CI. Do not execute fixture scripts or the installer
  entrypoint, or replace native/whole-checkout qualification.
  #13897 merges at `a2541fa0edb89a29321d0a8ca7a5e12e58b6c8b9` after
  exact-head Copilot approval and complete required Linux CI with freshly
  executed packet evidence. Broader parent gates stay open.
- [x] Qualify and land 47 additional original fixture-only dependency cases
  through four exact test objects: the 32-case canonical-preparation branch
  matrix and fifteen local-URL/resolution-input cases. Preserve the preceding
  134 selections and all 22 remaining originals, including the mixed real-UV
  parser case, outside the packet. Require source closure, positive per-case
  call coverage, zero prohibited effects, unchanged preceding admission/
  helper/budget gates, current-head review and complete Linux source CI.
  Keep all entries under the existing cap using whitespace-only source-entry
  indentation compaction; do not execute preparation text or weaken a gate.
  #13899 merges at `da727a4182dad82fcee9a4706020a96c2995dc62` after
  exact-head Copilot approval and complete required Linux CI with freshly
  executed packet evidence. Toolchain-binding timeout leaves that artifact
  unqualified for future reuse; broader parent gates stay open.
- [x] Qualify and land three additional original in-process dependency cases
  through two exact test objects: two mocked CLI outcomes and one fixture-only
  weakened-policy refusal. Preserve the preceding 181 selections and all 19
  artifact/native/checkout residuals. Bind the real CLI module as source and
  positive coverage target; require genuine per-node production call coverage,
  zero prohibited effects, unchanged preceding admission/helper/budget gates,
  current-head review and complete Linux source CI. A test name is not native
  qualification, and no existing witness or cap may be weakened.
  #13900 merges at `6174ac7d9588c33b6162f6e0be22ae5eb55fedbf` after
  exact-head Copilot approval and complete required Linux CI with freshly
  executed packet evidence. Broader parent gates stay open.
- [x] Land reader-first compact source-manifest support with ordered file groups
  and normalization to the unchanged flat logical v1 identity. Preserve every
  preceding selection/hash/target, required assurance and budget; retain v1
  storage and emitted packets. Qualify strict malformed/duplicate/overflow
  refusal, actual source/packet loading, candidate composition and compatibility
  CLI consumers, including old-reader v1 acceptance/v2 refusal. Complete affected
  whole-file strict typing, current-head review and required Linux source CI.
  #13905 merges at `725346e538fc4e1329549ff6f8af7f490e9c4518` after fresh
  corrected-head Copilot approval and complete required Linux CI.
  The Manager packaged-helper patch intent is included. This reader milestone
  retains v1 storage; the subsequent writer milestone is separate.
- [x] Migrate the source manifest writer only after the actual selected readers
  support the new storage contract, raw node-list consumers are reconciled and
  supported rollback or pre-effect refusal is proven. Preserve every original
  unit identity and limit; source reader support alone does not activate a
  separately owned control or qualify full-census/every-release acceptance.
  #13915 merges at `835e608e2280e03f901ade84d866b19792233053` after exact-head
  Copilot approval and complete required Linux CI with all 6,629 units freshly
  executed from compact source storage into unchanged flat-v1 evidence.
  Every logical field/order/digest remains identical, old-runner pre-effect
  refusal and matched-v1 rollback are proven, and broader parent gates stay open.
- [x] Qualify and land eleven original version-bump guard cases through eight
  exact test objects and the original fixture. Preserve all 28 other original
  cases, including real Git/process contracts, unchanged prior admission,
  source closure, positive per-node call coverage, helper decisions and the
  300-second budget. Require whole-file strict typing, current-head review and
  complete Linux source CI; retain compact source storage and flat-v1 evidence.
  #13917 merges at `d3aa1214142b748ff772234867bd0a99084a5c9e` after exact-head
  Copilot approval and complete required Linux CI with all 6,640 units freshly
  executed under the unchanged budget.
  Broader native, release, full-census and whole-flow gates remain separate.
- [x] Qualify and land seven original serial changefile cases and their
  original isolation fixture, preserving the real threaded residual and all existing
  unit identities, source bindings, coverage targets, helper decisions and
  limits. Require actual per-case production call coverage, effect auditing,
  strict typing, current-head review and complete Linux source CI. The ten
  original local Git evidence cases are already mapped; do not duplicate them
  or restore the retired registry CI gate.
  #13920 merges at `02e34a3217bf60736bf6260512d967108b050b87` after
  corrected-head Copilot approval and complete required Linux CI with all
  6,647 units freshly executed. Broader parent gates stay open.
- [x] Qualify and land 37 original pure/fixture-owned version-stamping cases
  through 23 exact test objects and the original isolation fixture. Preserve
  the eight actual-Git cases and canonical checkout-data case outside the
  packet, every preceding identity/hash/target/helper decision and the
  300-second budget. Require source closure, positive per-case production
  coverage, zero prohibited effects, strict typing, current-head review and
  complete Linux source CI; this is not live promotion or full-census proof.
  #13924 merges at `1d33ace36596e1be64e0350d9a6658322689f55a` after
  exact-head Copilot approval and complete required Linux CI with all 6,684
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land the two exact original release-message test objects
  under source closure, positive production call coverage and effect auditing,
  retaining all 46 Git/process originals outside the packet. Preserve every
  prior node/hash/target/helper decision and the 300-second limit; require
  strict typing, current-head review and complete Linux source CI. Formatter
  admission is not promotion, native, full-census or whole-flow acceptance.
  #13927 merges at `76e0a0b850c65fc21ca8c4635ff60467df36113c` after
  exact-head Copilot approval and complete required Linux CI with all 6,686
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land six original preview cases through four exact test
  objects and the original isolation fixture. Retain all eleven real-Git-head
  residuals, unchanged preceding admission/helper/budget gates, exact
  producer/template inputs, positive per-case production call coverage and
  zero prohibited effects. Require strict typing, current-head review and
  complete Linux source CI; this does not qualify an installed preview or
  native/release/full-census/whole-flow behavior.
  #13930 merges at `8faa88c9973499a27675a36ba20a7ff3de1d5014` after
  exact-head Copilot approval and complete required Linux CI with all 6,692
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land thirteen original actor-free materializer cases through
  twelve exact test objects, preserving original fixtures, parameter
  suffixes and assertions. Retain all 152 other originals outside this slice,
  including actual multi-plugin/thread, Git, native-import and symlink
  witnesses. Preserve
  every preceding node/hash/target/helper decision and the 300-second limit;
  require source closure, positive per-case production call coverage, zero
  prohibited effects, strict typing, current-head review and complete Linux
  source CI. This is not installed materialization, release, native,
  full-census or whole-flow acceptance.
  #13932 merges at `34c846bf2294791c5e1673eb50231e3334f9d010` after
  corrected-head Copilot approval and complete required Linux CI with all
  6,705 units freshly executed. The failed initial native-import selection
  remains recorded; no guard or original witness is weakened.
  Broader parent gates stay open.
- [x] Qualify and land thirty additional original fixture-only UV-reference
  cases through 27 exact test objects. Preserve the thirteen mapped pointer
  cases, all 122 remaining originals, exact parameter suffixes/assertions,
  source closure, positive per-case reader/materializer call coverage, effect
  auditing, preceding admission/helper/budget gates and compact/flat-v1
  contracts. Require strict typing, current-head review and complete Linux
  source CI; this is not native, installed, release, full-census or whole-flow
  acceptance.
  #13937 merges at `ad90cdaa21ea958e3ada7e6f169a14fd44f087a4` after
  exact-head Copilot approval and complete required Linux CI with all 6,735
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land fourteen original fixture-only installer and wrapper
  asset cases through twelve exact test objects. Preserve all 43 preceding
  original materializer cases, all 108 remaining originals, original fixtures,
  parameter suffixes and assertions. Retain source closure, positive per-case
  production call coverage, zero prohibited effects, all preceding node/hash/
  target/helper decisions, the compact/flat-v1 contracts and 300-second limit.
  Require strict typing, current-head review and complete Linux source CI.
  Native symlink and broader refresh cases remain outside this slice; this is
  not installed materialization, release, native, full-census or whole-flow
  acceptance.
  #13940 merges at `dc1b8b2e70331d1cbe803144477f59879aafade9` after
  exact-head Copilot approval and complete required Linux CI with all 6,749
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land twenty original fixture-only packaged-asset cases
  through seven exact test objects. Preserve all 57 preceding original
  materializer cases, all 88 remaining originals, original fixtures, parameter
  suffixes and assertions. Retain complete source closure, positive per-case
  production call coverage, zero prohibited effects, all preceding node/hash/
  target/helper decisions, compact/flat-v1 contracts and the 300-second limit.
  Require strict typing, current-head review and complete Linux source CI.
  Native symlink cases remain separate; this is not native, installed,
  release, full-census or whole-flow acceptance.
  #13942 merges at `abcc2aa18809e690ad9af846a30eee047caa4040` after
  exact-head Copilot approval and complete required Linux CI with all 6,769
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land twenty-six original fixture-only local-source closure
  cases through eight exact test objects. Preserve all 77 preceding original
  materializer cases, all 62 remaining originals, original fixtures, expanded
  suffixes and assertions. Retain source closure, positive per-case production
  call coverage, zero prohibited effects, all preceding node/hash/target/helper
  decisions, compact/flat-v1 contracts and the 300-second limit. Require strict
  typing, current-head review and complete Linux source CI. Linked-source and
  aliased-parent native cases remain separate; this is not native, installed,
  release, full-census or whole-flow acceptance.
  #13944 merges at `ee1c7951fa6b1db63e15d42fcadaddeba93b1939` after
  exact-head Copilot approval and complete required Linux CI with all 6,795
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land three exact original invocation-boundary controls:
  foreign-manifest refusal, escaping-output refusal and the materializer's
  invocation-owner callback. Preserve all 103 preceding original cases and
  all 59 residuals; bind copied canonical fixture data without executing it.
  Require unchanged original objects, fixtures and assertions, positive
  per-case production call coverage, zero prohibited effects, all preceding
  node/hash/target/helper decisions, compact/flat-v1 contracts, the 300-second
  limit, strict typing, current-head review and complete Linux source CI.
  Native, installed, release, full-census and whole-flow gates stay open.
  #13947 merges at `8d563d86f9b4d7b4c551f57e85a3e8a7ea8929db` after
  exact-head Copilot approval and complete required Linux CI with all 6,798
  units freshly executed. Broader parent gates stay open.
- [x] Repair the reproduced incomplete invocation fixture with the three
  missing entry modules and eight current build resources, preserving every
  original assertion and parameterization. Satisfy the mandatory whole-file
  strict ratchet without suppressions; retain equivalent runtime module
  targets, native headlessness and the test interpreter floor. Qualify eleven
  exact original output controls with closed sources, real per-case producer
  coverage and zero prohibited effects; rerun all 117 mapped originals.
  Preserve preceding nodes, every other source hash, all target/helper/
  compact/flat-v1 contracts and the 300-second limit, then obtain current-head
  review and complete Linux source CI. All 48 remaining originals stay outside,
  including the four driver-refresh cases refused by the current explicit
  build-data input contract. Do not widen that contract merely for admission.
  Native, installed, release, full-census and whole-flow gates stay open.
  #13950 merges at `e73a6e85ee37973702bd82a816efab82dd4d4315` after
  exact-head Copilot approval and complete required Linux CI with all 6,809
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land only the four original serial pointer-discovery cases
  from the unchanged six-case matrix. Preserve exact original function
  identity, parameter values, suffixes, fixtures and assertions; retain both
  `parallel=True` real-worker cases outside. Require closed sources, actual
  per-case production call coverage, zero prohibited effects, unchanged
  preceding nodes/hashes/targets/helpers/compact/flat-v1 contracts and the
  300-second limit, strict typing, current-head review and complete Linux source
  CI. No native prefilter, token import or thread may be preloaded or replaced
  to qualify a case. All 44 residual originals and broader acceptance gates
  remain outside this finite item.
  #13953 merges at `6ffd1d3094b966d03416a812dbf80dde9184589c` after
  exact-head Copilot approval and complete required Linux CI with all 6,813
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land the 25 original synthetic installer-sync cases, exporting
  their 22 unchanged test functions and exact `fake_repo` fixture. Preserve
  original assertions, parameter identities and all 153 remaining originals
  outside the packet. Require closed Python bindings, actual per-case sync
  owner calls and zero prohibited effects; temporary shell/Python fixture
  content is data, never real installer assets or executed installers. Retain
  all 6,813 preceding nodes/hashes, 89 helper decisions, compact/flat-v1
  contracts and the 300-second limit, with only the sync owner added as a
  coverage target. Obtain strict typing, current-head review and complete
  Linux source CI. This finite item changes no installer behavior and settles
  no native, installed, release, full-census, whole-flow or custody gate.
  #13956 merges at `dbfe919961bb50c22aec72f71a3f556652357d54` after
  exact-head Copilot approval and complete required Linux CI with all 6,838
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land all thirty original synthetic ancestor-metadata cases,
  three original bootstrap-declaration fixture cases and only the original
  `eligible=False` serial plugin-copy case. Preserve exact test/fixture objects,
  all assertions, parameters and existing exclusions; retain the isolated
  bootstrap subprocess and `eligible=True` real workers outside. Require
  actual per-case production call coverage, zero prohibited effects, closed
  source bindings, unchanged preceding nodes/hashes/targets/helpers and
  compact/flat-v1 contracts under the 300-second bound, strict typing,
  current-head review and complete Linux source CI. Original metadata
  simulations are not native-link qualification. Broader native, installed,
  release, full-census, whole-flow and custody gates remain separate.
  #13957 merges at `edb955910d6b64ac57260d9e1a527182fced8e57` after
  exact-head Copilot approval, complete required Linux CI with all 6,872 units
  freshly executed and one explicitly authorized scoped metadata refresh.
  Broader parent gates stay open.
- [x] Qualify and land six original runtime-sync fixture cases through exact
  original functions and the unchanged `repository` fixture, retaining all
  assertions and parameter identities. Admit only the ordinary archive
  destination; real links, POSIX umask publication and real source-archive
  closure matrices stay outside this slice. Preserve the fixture's original
  archive substitute and the two already-bound real Python dependency reads;
  synthetic archive members are data, not native execution. Require positive
  per-case calls to the sync or archive owner, zero prohibited effects, closed
  sources, all preceding nodes/hashes/helpers, compact/flat-v1 contracts and
  the 300-second bound. Add only the two actual coverage owners, obtain strict
  typing, current-head review and complete Linux source CI. This changes no
  installer behavior and settles no broader native, installed, release,
  full-census, whole-flow or custody gate.
  #13961 merges at `eafd884203e894d828a54bdbbcd959a29d801e45` after
  exact-head Copilot approval and complete required Linux CI with all 6,878
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land the three unchanged original source-archive controls
  for deterministic Python-only content, zlib independence and checkout line
  endings. Preserve every original function, fixture, assertion and preceding
  unit identity/hash/target/helper decision. Bind the complete 76-file ordinary
  Python read closure as data; do not import or execute archive members or
  substitute a synthetic closure. Require actual per-case archive-builder
  call coverage, zero prohibited effects, unchanged compact/flat-v1 contracts
  and the 300-second limit, strict typing, current-head review and complete
  Linux source CI. Byte-archive validation is not native lifecycle, installed
  archive execution, release, full-census, whole-flow or custody acceptance.
  #13962 merges at `a7adc7b0eb0ae9c6ce94cdf74e6b8379139782e7` after
  exact-head Copilot approval and complete required Linux CI with all 6,881
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land twenty-nine original ordinary/simulated UV-reference
  cases by exporting the twenty-seven unchanged original functions, retaining
  every fixture, assertion and parameter identity. Leave all five real-link
  functions and their six cases outside; the original fallback/path
  simulations are not native-link qualification. Require positive per-case
  calls to the existing UV-reference coverage owner, zero prohibited effects,
  closed Python reads, all preceding nodes/hashes/targets/helper decisions,
  unchanged compact/flat-v1 contracts and the 300-second bound, strict typing,
  current-head review and complete Linux source CI. This changes no helper,
  installer or workflow behavior and settles no broader native, installed,
  release, full-census, whole-flow or custody gate.
  #13964 merges at `2108c80d318f848e2811cd0896e75a902319c2eb` after
  exact-head Copilot approval and complete required Linux CI with all 6,962
  merged-tree units freshly executed, including 52 concurrent peer additions.
  Only failed guards are retried after a transient public-metadata failure;
  the successful unit execution is retained. Broader parent gates stay open.
- [x] Qualify and land twelve original UV-executable fixture cases through
  seven unchanged original functions, retaining every assertion, parameter
  identity and original callback. Keep all nine ctypes, real-link and Windows
  native-retention cases outside, including the case correctly refused on
  `ctypes.dlopen`; do not replace native ACL work or preload a library for
  admission. Require actual per-case configuration/projection owner calls,
  zero prohibited effects, complete Python read closure, unchanged preceding
  nodes/hashes/targets/helper decisions, compact/flat-v1 contracts and the
  300-second bound, strict typing, current-head review and complete Linux source
  CI. No installer behavior or broader native, installed, release, full-census,
  whole-flow or custody acceptance changes.
  #13968 merges at `028ed693902466b4244b50a57281fe04f88a738d` after
  exact-head Copilot approval and complete required Linux CI with all 6,974
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land sixteen original retention controls through four exact
  unchanged test functions: remaining execution budget, invalid exclusions,
  temporary-path canonicalization and conservative fractional cutoffs. Preserve
  all assertions, parameter identities and original callbacks; copied-module
  cases remain outside because the existing coverage gate requires canonical
  owner calls. Require positive per-case production call coverage, zero
  prohibited effects, complete Python read closure, unchanged preceding
  nodes/hashes/targets/helper decisions, compact/flat-v1 contracts and the
  300-second bound, strict typing, current-head review and complete Linux source
  CI. No installer behavior or broader native, installed, release, full-census,
  whole-flow or custody acceptance changes.
  #13969 merges at `964effefec309524633d0d55c8e4e45dcebcc14e` after
  exact-head Copilot approval and complete required Linux CI with all 6,990
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land forty-two original cutoff-projection cases through
  thirteen exact unchanged functions, retaining their assertions, parameters,
  timestamp precision, malformed-input controls and package/index refusal
  witnesses. Preserve every preceding node/hash/target/helper decision,
  closed Python reads, positive per-case canonical-owner calls, zero prohibited
  effects, compact/flat-v1 contracts and the 300-second bound. Require strict
  typing, current-head review and complete Linux source CI. Do not execute UV,
  change seasoning behavior or treat projection fixtures as native, installed,
  release, full-census, whole-flow or custody acceptance.
  #13971 merges at `934f7105399ea38dc568ac1bc5c83e9dcf782406` after
  exact-head Copilot approval and complete required Linux CI with all 7,032
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land twenty-four original policy-refusal/CLI cases through
  twelve exact unchanged functions, preserving their assertions, parameter
  identities and original callbacks. Require positive per-case canonical-owner
  calls, zero prohibited effects, closed Python reads, unchanged preceding
  nodes/hashes/targets/helper decisions, compact/flat-v1 contracts and the
  300-second bound, strict typing, current-head review and complete Linux source
  CI. No native UV substitution, policy behavior, workflow or broader native,
  installed, release, full-census, whole-flow or custody acceptance changes.
  #13974 merges at `523be8d328ff8b750f610f417644683d0aea65a6` after
  exact-head Copilot approval and complete required Linux CI with all 7,056
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land eight original materialized-graph attribution cases
  through one exact unchanged function. Retain canonical/payload helper owners,
  sibling-library success and unowned/foreign/wrong-identity refusals. Keep the
  separate editable-requirement case outside after its native DLL effect is
  correctly refused; add no mock, preload or provider substitute. Preserve
  all preceding nodes/hashes/targets/helper decisions, closed Python reads,
  positive canonical-owner calls, zero prohibited effects, compact/flat-v1
  contracts and the 300-second bound. Require strict typing, current-head review
  and complete Linux source CI; broader parent acceptance stays open.
  #13976 merges at `c8b72ab5a6951c8a5d16db79b1fbd3dc2651220a` after
  exact-head Copilot approval and complete required Linux CI with all 7,064
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land forty-five original compile/cleanup controls through
  thirteen exact unchanged functions and the original cleanup fixture. Retain
  operation/projection, output-policy and include-remapping refusals plus
  primary-error identity, chaining, diagnostic and success-cleanup witnesses.
  Native compile/retention paths remain outside without replacement callbacks.
  Require closed Python reads, positive per-case canonical-owner calls, zero
  prohibited effects, unchanged preceding nodes/hashes/targets/helper decisions,
  compact/flat-v1 contracts and the 300-second bound, strict typing,
  current-head review and complete Linux source CI. Broader parent acceptance
  remains open.
  #13978 merges at `aa78f9f22d9f8ea8ac485e066e1b8998735382bb` after
  exact-head Copilot approval and complete required Linux CI with all 7,109
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land twenty-nine original source-compile refusal cases through
  six exact unchanged functions. Preserve effective editable-graph, omission,
  unsafe/disabled/context-only mapping and inactive local/registry ambiguity
  witnesses, all assertions, parameter identities and original callbacks.
  Require closed Python reads, positive per-case canonical-owner calls, zero
  prohibited effects, unchanged preceding nodes/hashes/targets/helper decisions,
  compact/flat-v1 contracts and the 300-second bound, strict typing,
  current-head review and complete Linux source CI. Add no native substitute;
  broader parent acceptance remains open.
  #13979 merges at `eb71944ea62770438eebb37a0b8491d7758a7ade` after
  exact-head Copilot approval and complete required Linux CI with all 7,138
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land ten original bundled-artifact exception cases through
  the exact unchanged function and canonical/Manager fixture, plus one focused
  hash-bound-data regression. Admit only the explicit checked-in wheel as data,
  never executable test source or a coverage target; retain its full hash and
  both actual policy owners. Preserve all original assertions/parameters,
  preceding nodes, unrelated hashes/targets, helper decisions, compact/flat-v1
  identity, effect refusals and the 300-second bound. Require closed Python
  reads, positive per-case actual-owner calls, strict typing, current-head
  review and complete Linux source CI. No native, runtime, installation or
  broader parent acceptance is implied.
  #13982 merges at `74c15737c1b64389412dc969a11a352540cacf1e` after
  exact-head Copilot approval and complete required Linux CI with all 7,149
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land seventy-seven original archive-reader cases through
  thirty-two exact unchanged functions and the original fixtures. Retain
  metadata/digest, member/path/RECORD, ZIP64/compression, mutation and deadline
  witnesses. Keep the separate stress, subprocess, external-symlink-fixture,
  timestamp-dependent parameter and machine-environment cases outside without
  replacement witnesses. Require closed Python reads, positive per-case actual
  owner calls, zero prohibited effects, unchanged preceding nodes/hashes,
  targets/helper decisions, compact/flat-v1 identity and the 300-second bound,
  strict typing, current-head review and complete Linux source CI. Broader
  parent acceptance remains open.
  #13984 merges at `27daa8b46dc852b202703af59e78761c1f49ba76` after
  exact-head Copilot approval and complete required Linux CI with all 7,226
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land 704 original source-derived command ownership cases
  through 31 exact unchanged functions, plus three focused hash-bound-data
  controls and four candidate-data origin/refusal controls. Preserve
  writer-body, newline/BOM, peer, root/module/publisher,
  customization, legacy and malformed-source witnesses without launching any
  native program. Bind the 46 actual checked-in command/writer/manifest inputs
  as data only, retaining changed-byte, executable-source and target refusals.
  Keep the separate process-global import assertion outside this mixed packet.
  Require closed Python reads, positive per-case actual-owner calls, zero
  prohibited effects, unchanged preceding nodes and unrelated hashes, 89 helper
  decisions, compact/flat-v1 identity, the 300-second bound, strict typing,
  current-head review and complete Linux source CI. Add the actual command
  parser to coverage targets; native, cleanup and broader parent gates remain.
  The bounded source-only candidate-reader correction follows the existing
  declared canonical-to-local routes for six native data copies. It changes no
  materializer writer, workflow, activation, native-run authority or custody.
  #13986 merges at `2db9de42d9d2464e7a08554c9b3c17d9ff387097` after
  exact-head Copilot approval and complete required Linux CI with all 7,937
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land 56 original footprint-history and recognition cases
  through 19 exact unchanged functions, with the original profile/history data
  hash-bound and never executable. Preserve semantic-transition, retirement,
  read-boundary, corrupt/foreign/stale-input and fresh-construction witnesses.
  Keep the other 33 original cases outside this bounded slice without removing
  their retained lanes or replacing their assertions. Require closed reads,
  positive per-case actual footprint-owner calls, zero prohibited effects,
  unchanged preceding nodes and unrelated hashes, 89 helper decisions,
  compact/flat-v1 identity, the 300-second bound, strict typing, current-head
  review and complete Linux source CI. Native, installed and parent gates remain.
  #14019 merges at `82860fb482d13b402050f1d572a3c56bad1aafef` after
  exact-head Copilot approval and complete required Linux CI with all 7,995
  units freshly executed. Broader parent gates stay open.
- [x] Qualify and land all five original validation-override cases through
  three exact unchanged functions. Preserve list-clearing versus omission,
  empty-map inheritance, empty-path replacement and machine-precedence
  witnesses using the original source-level merge/parser helper, without
  importing or starting the managed runtime. Require closed reads, positive
  per-case actual configuration-owner calls, zero prohibited effects,
  unchanged preceding nodes and hashes, 89 helper decisions, compact/flat-v1
  identity, the 300-second bound, strict typing, current-head review and
  complete Linux source CI. The withdrawn Darwin candidate is not admitted
  or counted as completed coverage; native, installed and parent gates remain.
  #14026 merges at `a0601af38db4fb964ec5781cbe3228d91a75014c` after
  exact-head Copilot approval and complete required Linux CI with all 8,000
  units freshly executed. Broader parent gates stay open.
- [ ] Qualify both original project/resource-overlap cases for normal and
  editable installs, retaining the complete admitted parent project when a
  package resource names its descendant. Preserve the original synthetic
  resolver projection and real fixture copies/hashes without running UV or
  touching operational resources. Require exact original objects and parameter
  identities, closed reads, positive per-case actual policy-owner calls, zero
  prohibited effects, unchanged preceding nodes and target-branch hashes,
  targets/helper decisions, compact/flat-v1 identity, the 300-second bound,
  strict typing, current-head review and complete Linux source CI. Native,
  installed and parent gates remain. Fresh guarded qualification refuses the
  first case on Windows at the original private-root `advapi32` load
  (`ctypes.dlopen`); neither case is admitted. No preload, fixture change or
  guard exception substitutes for that missing qualification.
- [x] Qualify and land both original bundled-wheel release-age cases using
  the exact unchanged function and the already hash-bound canonical wheel.
  Preserve the aged-versus-young canonical-release checks, corrupt-wheel
  refusal and controlled metadata acquisition without installation or network
  access. Require closed reads, positive per-case actual dependency-age owner
  calls, zero prohibited effects, unchanged preceding nodes and target-branch
  hashes, targets/helper decisions, compact/flat-v1 identity, the 300-second
  bound, strict typing, current-head review and complete Linux source CI.
  Keep the separate executable-discovery and resource-retention native
  boundaries unadmitted; native, installed and parent gates remain.
  #14040 merges at `a671fd50402011f38a8db2011156bfaed3557102` after
  exact-head Copilot approval and complete required Linux CI with all 8,002
  units freshly executed. Broader parent gates stay open.
- [ ] Qualify the original deployment CLI editable-requirement refusal using
  its unchanged trusted temporary project, requirements include and forbidden
  UV-selector witness. Extend the existing original CLI adapter without
  another module import. Require exact original identity, closed reads,
  positive actual policy-owner calls and zero prohibited effects before
  admission; retain every preceding node, unrelated hash, target, helper
  decision, compact/flat-v1 identity and the 300-second bound. Normal strict
  typing, current-head review and complete Linux source CI remain required.
  This early-refusal case does not qualify executable discovery, retained
  private-root creation, native execution or installed behavior.
  Fresh guarded qualification refuses the outer configuration wrapper's
  Windows `kernel32` load before the inner source validation. The adapter
  addition is withdrawn and the case remains unadmitted; this confirms the
  previously recorded editable-requirement boundary, not new coverage.
- [ ] Qualify the seven original named-UV lookup and non-native-wrapper
  controls through their four unchanged functions and original lookup doubles.
  Extend the existing UV-selection adapter without rewriting a fixture,
  callback or assertion. Require exact original objects and parameter IDs,
  closed reads, positive per-case actual configuration-owner calls and zero
  prohibited effects before admission. Preserve preceding node order,
  unrelated hashes, targets, helper decisions, compact/flat-v1 identity and
  the 300-second bound. Normal strict typing, current-head review and complete
  Linux source CI remain required. These source-level doubles do not qualify
  actual Windows lookup, private-root creation, links or installed behavior.
  Linux CI refuses the original lazy `ctypes` import at `PyDLL(None)` before
  the lookup doubles are installed. All seven additions are withdrawn;
  the passing Windows development run is not cross-platform admission.
  No preload, guard exception or original-test rewrite is substituted.
- [x] Qualify and land three original artifact-readiness refusal controls:
  changed source refuses before release access, failed preparation emits no
  receipt and final publication does not repair it, and missing premerge
  identity refuses before release access. Preserve the exact original
  synthetic hosted and autouse identity fixtures, callbacks and assertions.
  Require original-object/parameter identity, closed reads, positive per-case
  actual owner calls and zero prohibited effects before admission; retain
  preceding nodes/hashes, targets, helper decisions, compact/flat-v1 identity
  and the 300-second bound. Normal strict typing, current-head review and
  complete fresh Linux CI remain required. Parallel download verification,
  actual hosted publication, native execution, installed and parent gates
  remain separate.
  #14041 merges at `4b7c28faa6fe4120813ff72b2b552dfba94adb41` after
  exact-head Copilot approval and complete required Linux CI with all 8,016
  units freshly executed. The withdrawn lookup cohort and threaded readiness
  proposal remain unadmitted; broader parent gates stay open.
- [x] Qualify and land all 33 cases from six original synthetic bundle
  functions: transfer roundtrip, structured wheel-read failures, output
  directory handling, deterministic bytes and archive-corruption refusal.
  Preserve every original parameter, fixture, callback, clock witness and
  assertion, including the autouse identity fixture. Require closed reads,
  positive per-case actual artifact-owner calls and zero prohibited effects
  before admission; retain preceding nodes/hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Normal strict typing,
  current-head review and complete fresh Linux CI remain required. Actual
  hosted publication, threaded readback, native/installed and parent gates
  remain separate.
  #14043 merges at `a73ea800609c804721d48f48a015405983d24dce` after
  exact-head Copilot approval and complete required Linux CI with all 8,049
  units freshly executed. Broader parent gates stay open.
- [x] Qualify all 39 cases from five adjacent original artifact-schema and
  wheel-validation functions: declared vendored-library acceptance, complete
  provenance and computed identity, forged content-address refusal, real
  wheel metadata/member validation, and structured encoded-metadata refusal.
  Extend the existing bundle adapter with exact original objects and retain
  its autouse fixture, every parameter, callback and assertion. Require
  closed reads, positive per-case actual owner calls and zero prohibited
  effects before admission, without replacing an original witness. Preserve
  preceding node order, unrelated hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Normal strict typing,
  current-head review and complete fresh Linux CI remain required; native,
  installed, hosted publication and parent gates stay separate.
  #14046 merges at `65421209a6a23b5a3ca79d5c7632f7d94744c302` after
  exact-head Copilot approval and complete required Linux CI with all 8,088
  units freshly executed. Broader parent gates stay open.
- [ ] Qualify all 33 cases from eight adjacent original digest, transfer,
  unsafe-wheel, declared-source-path and signer-policy functions. Preserve
  exact original functions, parameters, callbacks, assertions and the autouse
  identity fixture in the existing adapter. Guard every adapter case with
  closed reads, positive per-case actual owner calls and zero prohibited
  effects before admission. Preserve preceding node order and unrelated
  source hashes, targets, helper decisions, compact/flat-v1 identity and the
  300-second bound. Require normal strict typing, current-head review and
  complete fresh Linux CI; native, installed, real publication and broader
  parent gates remain separate.
- [x] Land and observe necessary parsed-root admission for the sibling
  tracking-write guard, retaining every read/parse, alias fixed point, dynamic
  import/reflection boundary and ordered diagnostic (#8227). Actual source CI
  retains all three scanners and all 1,012 admitted units. Local recipe timing
  and omitted analysis are not controlled whole-flow latency.
- [x] Observe qualified release adoption of #8227 separately from source-CI
  execution and comparable whole-flow timing; this changes no controller pin.
  The [November 20 adoption audit](journal/2026/11.20-release-adoption.md)
  verifies both delivered guard blobs, retained optimization branches and the
  already executed source consumer. It establishes no controlled timing gain.
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
- [x] Land source job admission that omits the owner reminder's existing no-op
  before runner allocation (#8029). All reminder JavaScript, independent Main
  admission/identifier jobs and synchronize behavior remain unchanged.
  Native/Linux focused and actual consumer contracts pass, including recorded
  Dev selection with preserved legacy Main bytes. Qualified Main adoption and
  natural no-runner observation remain separate; no live allocation saving is
  claimed from the Dev merge.
- [x] Land the bounded cleanup-publication guard (#8070): completely page open Dev
  metadata before any new commit/push and fail visibly on an outstanding
  same-repository, owner-authored generated cleanup PR. Preserve its existing
  owner, merge gates, cadence and wait budget. Qualified Main adoption and
  natural duplicate-prevention acceptance remain separate.
- [x] Land bounded periodic selection/preparation-failure reporting (#8078) using the
  existing portfolio tracker and owner. Genuine Dev provenance must survive
  missing selection outputs; other manual refs remain inspection-only, and
  preparation failure cannot imply executed Linux or Windows coverage.
- [x] Land omission of the unchanged full AST scanner group on positively closed
  packet-only PR inputs, preserving actual unit execution, every unknown/mixed
  input, all scanner/policy dependencies and full non-PR scans. Keep source
  delivery, natural omission and whole-flow measured savings distinct (#8111).
  Fresh source CI retains all three scans and executes the combined 917-case
  packet in 14.845 seconds after preserving the independently merged cohort.
- [x] Observe a natural packet-only PR omitting that AST group while retaining
  actual unit execution, and report measured work separately from total
  workflow counts and controlled whole-flow latency. #8155's source CI
  `39921653075` skips the AST group and executes all 917 admitted units.
  The workflow takes 50 seconds; its three job pickup intervals are
  2, 3 and 3 seconds. This proves omitted work, not a controlled speedup.
- [ ] Remove redundant release-shaped PR and main-push validation only where
  existing trusted gates and exact-SHA release evidence cover the removed work;
  first prove no generated-output coverage or required context is lost.
- [x] Land source omission of the promotion-filter no-op allocation for
  successful Main-push CI completions (#8189), preserving Main CI, manual
  refs, Dev ancestry and all deferred recovery paths. Native/Linux consumer
  qualification and actual source CI pass; the surrounding invocation remains.
- [ ] Obtain separately owned qualified Main adoption and natural no-runner
  acceptance for #8189. Source delivery is not measured live admission,
  fewer workflow invocations or controlled speedup.
- [x] Land the measured documentation-count suffix prefilters with unchanged
  whole-corpus matches, input reads, ordered diagnostics and error behavior.
  #8199's required CI executes the complete documentation step in two seconds
  and all 1,010 admitted units in 19.780 seconds. The integrated merged packet
  preserves the independent peer's two nodes. Local paired timing remains
  separate; this is reduced recipe work, not fewer workflow invocations.
- [x] Observe qualified release adoption of #8199 separately from source-CI
  execution and comparable whole-flow timing; this changes no controller pin.
  The [same adoption audit](journal/2026/11.20-release-adoption.md) binds the
  retained suffix admission and actual documentation-guard execution to the
  exact released blob without claiming a controlled latency result.
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
  scanning. At that delivery, Main-push CI still ran and its generated-tree
  failures remained open. The
  [November 20 snapshot](journal/2026/11.20-release-adoption.md#main-metadata-is-a-distinct-current-contract)
  records the later metadata-only Main declaration; it does not restore the
  old test lane or silently settle its historical failures.
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

- [ ] Qualify the [installed Manager lifecycle](installed-manager-lifecycle.md):
  representative harness and authentic candidate suite installation, Manager
  Create through a real CLI session and clean exit, later same-history Resume,
  and parallel Manager Open of the active session without a duplicate owner.
  Keep real path resolution, admission, terminal/mux and session persistence;
  substitute only deterministic inference. Existing #11377/#3336/#7529 owners
  retain source and activation custody. Failed or incomplete exact-candidate
  evidence must block promotion, not merely report an informational check.
  Installed-core action/continuity proof merged in #11864 at `fa93ea37c2`,
  backed by exact-source native run 42892225611 and current-head approval.
  Complete frozen-payload acquisition merged in #11972 at `0286b3f715`,
  backed by exact-source native run 42933306312: the actual CLI installed all
  24 private payloads and every selected manifest matched the frozen artifact.
  The separate installed-core lifecycle passed on that same source; the payload
  receipt explicitly leaves runtime installation unobserved.
  Complete-suite construction/actual Machines readiness is now the active slice;
  authentic-baseline update, mandatory enforcement and Main adoption remain open.
- [x] Land the installed-suite source and read-only compatibility corrections,
  complete changed-file strict typing and resolve actual review findings
  through current required CI (#12013). The merge does not qualify subsequent
  native execution, authentic older baselines or frozen-candidate enforcement.
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
- [x] Land the thirteen original remote-shell callable cases through five exact
  function objects and unchanged parameterization, preserving all prior packet
  identities/hashes, actual call coverage and the 300-second budget. Keep the
  original constant-only vocabulary assertion as an explicit unmapped policy
  residual (#8088). Actual hosted source CI executes all 890 admitted cases in
  9.417 seconds; bounded source qualification is not full-census or Main acceptance.
- [x] Map the six original peer-environment units through exact function
  objects, retaining closed canonical source imports, actual call coverage,
  prior packet identities and the 300-second budget. Keep eleven packaging
  cases explicitly unqualified and natural AST omission separate from source
  qualification, full-census and every-Main-release acceptance (#8155).
  Actual hosted source CI executes all 917 current cases in 16.821 seconds.
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

#### Finite faithful-fixture and comparable-measurement continuation - #7075

- [ ] **FI7075-P1:** Retain real copied Handoff CLI mode-off, honest-unavailability
  and non-fabrication behavior, all original eleven cases/assertions,
  deterministic absence of sibling/runtime roots without network/provisioning/
  ambient dependence, real handler/spawn behavior and four-second child bounds.
  Do not disable production provisioning or replace execution with text matching;
  preserve existing item/group/plugin/resource containment.
- [ ] **FI7075-P2:** Retain the distinct inherited binding/lifecycle lane already
  received by #7075: explicit synthetic guest-kind AND identity at real subprocess
  boundaries, private profile/registration, actual publisher/concurrency
  assertions, supported named-direct WSL with observed OR explicit distribution,
  absence-of-registration alone not refusal, no-distribution/no-valid-registration
  refusal, and original foreign/conflicting/native/container controls. Preserve
  read-only admission/deadlines; no host registration, WSL skip or weakened fence.
  Credit prior synthetic-guest/private-registration/native-home corrections,
  with authoritative guest resolution and unrelated marketplace diagnosis kept
  separate.
- [ ] **FI7075-P3:** Keep one evidence record with separate lane rows for selected
  inventory/exclusions, source, environment, outcomes and setup/call/teardown
  timings. For incumbent Handoff retain run38977784933/job117234300776:
  27 files/383 tests/375 pass/8 skip, 175.711s elapsed. The cases in
  `plugins/context-handoff/tests/cli-parity.test.mjs` are
  `CLI handoff commands are never refused for mode=off -- only for unrelated reasons`
  at 100.038409s and
  `list-sessions degrades honestly (never fabricates a chain) without agent-worktrees`
  at 72.827613s. Obtain comparable hosted before/after with EACH named case under
  5s and no lost assertions. Do not sum parallel durations into wall time.
- [ ] **FI7075-P4:** For the inherited lane retain the complete payload-invocation
  suite excluding `test_derived_diagnostic_cli.py` PLUS Index command-catalog
  tests, equivalent complete WSL AND hosted-Linux selection and original
  publisher/concurrency semantics. Preserve original 55 fail/130 pass/24 skip
  in 151.12s versus 185 pass/24 skip in 218.20s as different environments AND
  outcomes, not speedup. Later 139 pass/66 skip or recomposed partitions earn
  only their actual scope; never combine this Python inventory with Node383.
- [ ] **FI7075-P5:** Credit merged source and attributed Windows/native-Linux
  eleven-case results without calling them comparable hosted acceptance.
  Preserve the incumbent nonexpiring hosted-acceptance claim and the under
  10min event-to-required-gate INCLUDING queues program metric; unrelated
  release/native portfolios are not finite-lane prerequisites. Required
  PR/promotion CI remains unit-only; optional/periodic/native/hosted duties stay
  separately owed.

##### Local Validation - #7075

- [ ] **FI7075-LV1:** Read back EVERY P1-P5 and current #7075 original/edit/
  discussion/inherited clause; exercise faithful real consumers, complete
  inventory/assertions and negative controls at original lane tiers and unchanged
  limits. Early refusal, job name, authored fixture or a local-container result
  is not hosted PASS.
- [ ] **FI7075-LV2:** Establish separately scoped valid before/after timing for
  both lanes, each named Handoff case under 5s and truthful queues/program
  metrics. Preserve failed, skipped, unavailable and differently selected
  evidence; source/release/native/installed/hosted/whole completion are distinct.
- [ ] **FI7075-LV3:** Preserve existing Handoff hosted claimant and binding/
  publisher/concurrency/identity custodians. #7075 remains OPEN for unfinished
  acceptance. Already settled subordinate tracking is not reopened, restudied
  or recredited; this receiving amendment is not technical completion or
  claim/resource release.

#### Source-attribution server-failure receiving - #7906

- [ ] **A7906-P:** Retain the four-clause planned source-attribution union in
  [P7906/V7906](workflow-portfolio-audit.md#source-attribution-server-failure---7906):
  only association HTTP 500/502/503/504 admits a complete paginated open-Dev
  census; auth/rate/unclassified/incomplete/duplicate-head/moving-snapshot
  cases fail closed; existing request budgets and the final stable-snapshot
  fence remain. Credit #7907 source37/Main-helper delivery only at its actual
  tier; the trusted affected-current-head failure-path consumer result remains
  unverified. Preserve existing source-attribution, #6909/#7900 and promotion
  owners. This is planned tracking receipt, not implementation, CI retry,
  injected failure, probe, attempt renewal or owner/resource transfer.

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

- [ ] Installed-Manager acceptance satisfies every positive, falsifying,
  isolation, identity and actual promotion-admission case in the
  [lifecycle matrix](installed-manager-lifecycle.md#validation-plan).
  Installed TTY, direct CLI create/resume or a synthetic session is not the
  complete witness. Record source, native execution and live gate activation
  separately; retain existing repair and control owners.
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

- [ ] **FI7075-G:** Resolve EVERY FI7075-P1-P5 AND FI7075-LV1-LV3 at its
  original finite lane tier: real isolated CLI and guest/identity/publisher
  consumers, complete equivalent selectors, exact baseline and valid separately
  scoped hosted/WSL measurements, unchanged unit-only policy/limits, incumbent
  hosted claim and all source/release/native/installed distinctions. No
  whole-parent, every-host, new CI lane or extra acknowledgement prerequisite
  is added; receiving review/merge/readback is not technical acceptance or
  resource release.

- [ ] **A7906-G:** Resolve A7906-P AND the local P7906/V7906 union at its
  original attribution-consumer tier, preserving every admitted/refused HTTP
  class, complete pagination, request bound and final snapshot fence. Keep
  source/Main delivery separate from the actual trusted affected-current-head
  result; unperformed qualification remains unchecked. Planned receiving may
  retain unfinished tracking without claiming technical completion or an
  operational effect. No generic ACK, all-host, unrelated portfolio or new
  issue-OPEN condition is added.

## Proposal

See [design.md](design.md). This is proposed behavior, not an as-is claim.
Plan approval authorizes implementation through review gates, not unbounded
credential changes, broad policy weakening or unreviewed releases.

## Journal

Historical entries are preserved verbatim in dated files; each links back to
this contract. New evidence belongs in its date file, not another peer effort.

- [2026-11-20 UTC - Qualified guard release adoption](journal/2026/11.20-release-adoption.md)
- [2026-11-20 UTC - Bounded source-unit lookup attribution](journal/2026/11.20-unit-lookup-attribution.md)
- [2026-11-19 UTC - Cold CLI bootstrap restoration](journal/2026/11.19-bootstrap-restoration.md)
- [2026-11-14 UTC - Source-attribution receiving](journal/2026/11.14-attribution-receiving.md)
- [2026-11-14 UTC - Finite fixture receiving](journal/2026/11.14-fixture-receiving.md)
- [2026-11-09 - Installed Manager lifecycle contract](journal/2026/11.09-installed-manager-lifecycle.md)
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
