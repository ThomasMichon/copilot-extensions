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

The November 25 storage amendment selects
`reader-first-successor-and-reviewed-source-migration`: implement a successor
manifest representation reader-first, then migrate through normal review,
preserving caps, logical witnesses, guards and deadlines. This is source-only
representation work, not a native run, operational writer/controller change,
runtime update, rollout, cleanup or guard exception.
The [successor phase](manifest-storage-successor.md#scope-approval) retains
the exact question, selected answer and limits separately from the
agent-recommended realization. The root consolidation goal and all other
acceptance and custody requirements remain.

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

### November 25 reader-first storage direction

The operator selects `reader-first-successor-and-reviewed-source-migration`
at the verified 29-byte frontier. The exact public-safe question, selected
option and source-only exclusions are preserved in the
[scope approval](manifest-storage-successor.md#scope-approval).
This supersedes only the prior representation hold needed for that source
successor and reviewed migration; it does not waive limits, witnesses,
eligibility, guards, failed-attempt holds or existing operational ownership.

## Plan

- [ ] Qualify original pure registry projections through eleven complete
  functions and the entire `TestMachineName` and `TestFindMachineEntry`
  classes. Preserve original classes/methods, code/global origins, fixtures,
  parameter types/values, marks, class-qualified IDs and order.
  Exclude the whole file-parser class, entire mixed merge class, fallback
  import/file test and backend-identity constant-only test before execution.
  No YAML file load, backend replacement, hostname/platform probe, SSH,
  environment patch or native/guest process may be introduced.
  Compare full original/adapted metadata before unchanged guarding; require
  real registry-call coverage per selected case before exact admission.
  Retain preceding inventory, hashes, targets, helpers, limits and all holds;
  review, full CI, normal merge and persistent readback remain required.
  Complete metadata collection confirms 88 original and 32 adapted cases,
  retaining all original class/function, fixture, parameter and ID metadata.
  Initial unchanged guarding passes all 32 in 1.60 pytest / 21.469
  observation seconds: all 96 phase reports pass, zero prohibited effects,
  25 bound reads and actual registry-call coverage per case.
  V3 admission adds only 32 nodes, two bindings and one already-bound target:
  10,431 nodes, 1,504 bindings, 549 targets, 92 unchanged helper decisions,
  300 seconds and 702,241 bytes. Ruff and strict Linux/Windows typing pass;
  review, full hosted CI, merge and persistent readback remain.
- [x] Qualify the original pure transport-projection controls through the exact
  ambiguous-profile function and three complete original classes:
  default shell (three), SSH-target selection (six) and command wrapping (three).
  Preserve class/method objects, code/global origins, fixtures, parameter rows,
  marks and class-qualified IDs. Exclude the logical-host profile test, the
  entire owner-platform-detection function and the entire mixed
  `TestGetMachineTransport` class before execution. No SSH, hostname/platform
  observation, environment patch or native/guest process may be introduced.
  Complete metadata comparison precedes unchanged guarded execution of all
  thirteen prospective cases, with real transport-owner call coverage per case.
  Add only the exact adapter/original bindings and already-bound transport
  target after proof; retain all preceding inventory, helpers and limits.
  Complete collection confirms 31 original and thirteen adapted cases with
  unchanged original classes/methods, code/global origins and metadata.
  Initial unchanged guarding passes all thirteen in 1.62 pytest / 16.906
  observation seconds, with all 39 phase reports, zero prohibited effects,
  25 bound reads and actual production-call coverage per case.
  V3 admission retains all preceding inventory and adds only thirteen nodes,
  two bindings and one target: 10,395 nodes, 1,501 bindings, 547 targets,
  92 helper decisions, 300 seconds and 699,500 bytes. Current-head review,
  full hosted CI, normal merge and persistent readback remain.
  Publication safely stops on a concurrent manifest conflict. Reconciliation
  retains all four peer nodes, its new binding/target and both binding
  refreshes; all 25 observed inputs remain exact without trial replay.
  Final inventory is 10,399 nodes, 1,502 bindings, 548 targets and 699,981
  bytes, with unchanged helpers/deadline. Fresh full CI must cover that union.
  Source #14671 merges after current-head clean approval and all required
  checks; natural CI executes all 10,399 cases in 218.581 packet seconds.
  Independent merged state, all six reviewed/merged/working blobs, exact
  reconciled candidate, all current bindings/helpers and all 25 observed
  inputs verify without trial replay. Only this thirteen-case leaf is Done.
- [x] Qualify the remaining two complete original logical-environment functions:
  six invalid/conflicting identities and one native/guest-distinction case.
  Their earlier hold was storage only, not a failed effect observation.
  Preserve the existing eight-row table and original objects, fixtures,
  parameter values/types, marks and IDs. Compare complete metadata before a
  fresh unchanged guarded observation with actual production-call coverage.
  Add only those seven nodes and honestly refresh the existing adapter hash;
  keep every other source, target, helper, budget and refusal unchanged.
  Encode through the merged v3 codec, then use normal review/full CI/merge and
  persistent readback. This is not whole-census or parent completion.
  Complete metadata comparison passes all fifteen original/adapter cases.
  The new seven pass in 1.49 pytest / 21.610 observation seconds with all
  21 phase reports, zero prohibited effects, 25 bound reads and actual owner
  calls per case. V3 admission produces 10,382 nodes, 1,499 bindings,
  546 targets and 698,148 bytes. Source #14667 merges with all 10,382 cases
  freshly passing; current-head review, required checks and independent merged
  bytes, supported pull-forward, all current bindings/helpers and all 25
  observed input hashes verify. Only this leaf is complete.
- [x] Qualify the two complete original core-profile early-refusal functions:
  three registered-manifest schema cases and one expired-deadline/explicit-limit
  case. Metadata-only collection measures 22 originals / four selected cases
  with exact original objects, fixtures, parameter values/types, marks and IDs.
  Their unchanged fixture constructs synthetic wheels through the already-bound
  builder double; both functions refuse before native retained-wheel custody.
  Exclude every other complete function, including both CLI consumers and mixed
  parameterized functions. Require unchanged guarding, per-case production
  coverage, exact observed inputs, strict Linux/Windows typing, review, required
  CI, merge and readback. Preserve the existing profile, proof contract, limits,
  helper decisions, all operational holds and the open parent.
  Local unchanged guarding passes all four cases in 4.27 pytest / 16.500
  observation seconds with zero prohibited effects, twelve phase reports,
  seventy-two bound source reads and call-phase owner coverage for every case.
  Strict Linux/Windows typing, Ruff and staged whitespace checks pass.
  The existing v2 manifest adds only four identities and two source bindings:
  10,240 nodes, 1,475 sources, 536 unchanged targets, 92 unchanged helper
  decisions and 1,047,981 bytes under the unchanged 1 MiB / 300-second limits.
  Source #14595 merges at `5fa91fc40b19966a952a9054fc60127b049a09ff`
  on November 25 at 18:33:05 UTC. Both actual Linux CI executions pass all
  10,240 cases; the final required aggregate passes before ordinary merge.
  All five owned merged blobs match, and all 36 retained cases / 81 distinct
  observed inputs across the four recent cohorts verify at that merged
  revision without replay. The actual Goal, own leaf and full Validation Plan
  are reread before resolving only this source leaf.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records review and synchronization. Later Dev #14583 changes one observed
  helper and adds 22 cases; the retained local observation is not fresh
  qualification of that changed helper. Preserve that distinction.
- [x] Resolve the next manifest-storage formatting decision before further
  original-case admission. Current Dev `08c0404f6f` has 10,264 cases,
  1,479 bound sources and 539 targets in 1,050,585 bytes,
  2,009 above the retained 1 MiB storage envelope. All current source bindings
  and 92 helper decisions verify; no tests are removed, replayed or silently
  requalified. A write-free projection of fully minified existing v2 JSON is
  1,035,899 bytes, leaving 12,677 bytes with identical parsed values, ordered
  witnesses and logical admission. Preserve the schema/profile, cap, parser
  limit, deadlines, proof contract, data admission, native authority and
  existing ownership under the selected formatting-only continuation below.
  The operator now selects `fully-minify-existing-v2-then-continue`:
  fully minify only the existing v2 JSON, preserving every parsed value,
  object/array order, ordered witness and normalized logical admission.
  Retain the schema/profile, 1 MiB storage cap, 128 MiB parser bound,
  300-second packet deadline, all proof checks, eligibility and helper
  decisions. This supersedes only the one-item-per-line formatting selection,
  not any operational hold. Verify exact equivalence before writing, then
  complete ordinary source review, required CI, merge and readback before
  continuing the unchanged original-unit census.
  Exact ordered stored-value comparison and normalized admission comparison
  pass before writing. The whitespace-only serialization shrinks 1,050,585
  bytes to 1,035,899, restoring 12,677 bytes of headroom while retaining
  10,264 cases, 1,479 sources, 539 targets and all 92 helper decisions.
  Every source binding verifies; no test executes or receives admission.
  Source #14600 merges at `47c9b4e098a8fe9a0e729209ae2b16ca2069e665`
  on November 25 at 19:36:03 UTC. Both actual Linux CI executions pass all
  10,264 cases; final required aggregate `139459672873` passes before merge.
  All five owned merged blobs match, supported pull-forward succeeds and
  persisted raw/logical hashes, every source binding and helper decision
  verify without local test replay. The actual Goal, own leaf and entire
  Validation Plan are reread before resolving only this formatting leaf.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records review and synchronization. Continue the unchanged census with
  12,677 bytes of headroom; all parent, owner, native, release and custody
  gates remain open.

- [x] Qualify the eight complete original session-liveness-probe functions.
  Static inspection covers the original module, path-only conftest and
  canonical implementation: script construction and supplied-output parsing
  do not execute the generated script or inspect live processes.
  Preserve exact original function objects, fixtures, parameter values/types,
  marks, IDs and assertions. Require metadata-only comparison before unchanged
  guarding, per-case production call coverage, exact observed inputs, strict
  Linux/Windows typing, normal review, required CI, merge and readback.
  Add no probe execution, native qualification, data admission, guard exception,
  deadline change or operational authority. The application-adapter prospect
  remains excluded at whole-function level because its unchanged fixture path
  reaches native Windows selector reads; do not extract early-refusal rows.
  Metadata-only collection measures eight originals and eight aliases with
  unchanged object/code/global origins and complete matching metadata.
  The fresh guarded trial passes all eight in 1.05 pytest / 35.766 observation
  seconds: 24 passing phase reports, zero prohibited effects, sixteen bound
  source reads and call-phase production coverage for every case.
  Strict Linux/Windows typing and focused Ruff checks pass.
  Only eight nodes, three source bindings and one production target are added:
  10,272 nodes, 1,482 sources, 540 targets, 92 unchanged helper decisions and
  1,036,892 fully minified bytes, leaving 11,684 under the same cap.
  Source #14605 merges at `f863abf64371b9e0bdfeab6eb128259b66214bf3`
  on November 25 at 20:17:24 UTC. Natural Linux CI passes all 10,272 cases
  and required aggregate `139480601043` passes before ordinary managed merge.
  All five owned merged blobs match the reviewed head. Supported pull-forward,
  every source/helper check and all sixteen retained observed inputs verify
  without test replay. The actual Goal, own leaf and full Validation Plan
  are reread before resolving only this source leaf.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records review and synchronization. Continue the unchanged census;
  broader native, every-release, timing, owner and custody gates remain.

- [x] Qualify the complete original control-plane parser suite and five
  complete pure execution-identity functions as one coherent library cohort.
  Preserve original objects, fixtures, parameter values/types, marks, IDs and
  assertions. Metadata-only comparison must precede unchanged guarding and
  actual per-case production call coverage. The complete package import
  closure and selected function helpers are statically inspected; all
  production inputs are already bound. Tests use supplied topology objects
  or temporary YAML, not live machine/configuration state.
  Exclude the other complete identity functions and the entire platform suite:
  no environment/OS substitution or repository-test inspection is executed.
  Retain every existing witness, helper decision, profile, proof, limit and
  operational hold. Require typing, lint, normal review, required CI, merge
  and readback before resolving only this source leaf.
  Metadata-only collection measures 29/29 control-plane cases and 5/14
  identity cases across thirteen complete selected functions, with exact
  object/code/global origins and matching fixtures, parameters/types, marks
  and IDs. The other nine identity cases remain excluded as three complete
  functions. The fresh unchanged guarded trial passes all 34 selected cases
  in 2.64 pytest / 25.375 observation seconds, with 102 phase reports,
  zero prohibited effects, 26 bound source reads and call-phase production
  coverage for every case. Strict Linux/Windows typing and Ruff pass.
  Add only 34 nodes, three source bindings and two existing production
  modules as targets: 10,306 nodes, 1,485 sources, 542 targets,
  92 unchanged helper decisions and 1,039,616 minified bytes, leaving 8,960
  under the same cap.
  Source #14611 merges at `15caba6c46a4071d0bd3f54e5a95f6c3ef5fc8d3`
  on November 25 at 20:43:14 UTC. Natural Linux CI passes all 10,306 cases;
  required aggregate `139495385151` passes before ordinary managed merge.
  All five owned merged blobs match the reviewed head. Supported pull-forward,
  all source/helper checks and all 26 retained observed inputs verify without
  test replay. The actual Goal, own leaf and full Validation Plan are reread
  before resolving only this source leaf.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records review and synchronization. Broader parent gates remain open.
- [x] Qualify the complete original pure PR-filter suite without provider I/O.
  The original module and both canonical production files are fully inspected:
  bounded parsing, matching and acquisition planning use supplied data only.
  Preserve every complete function and parameter row, including long integer
  IDs, exact original object/code/global origins, fixtures, types, marks and
  assertions. Metadata-only comparison must precede unchanged guarding.
  Require per-case production call coverage, exact observed inputs, typing,
  lint, normal review, required CI, merge and readback. Add no provider call,
  live acquisition, data-admission change, profile/limit/guard exception,
  native execution or operational authority.
  Metadata-only collection measures all 34 original cases across twelve
  complete functions with exact object/code/global origins, fixtures,
  parameter values/types, marks and IDs; the longest original suffix is
  484 UTF-8 bytes and remains unchanged. The fresh guarded trial passes all
  34 in 1.26 pytest / 17.453 observation seconds, with 102 phase reports,
  zero prohibited effects, seventeen bound source reads and call-phase
  production coverage for every case. Strict Linux/Windows typing and Ruff
  pass. Add only 34 nodes, four bindings and two targets: 10,340 nodes,
  1,489 sources, 544 targets, 92 unchanged helper decisions and 1,043,757
  fully minified bytes, leaving 4,819 under the same cap.
  Source #14616 merges at `603d45229a60ef39e790c22e22b4bcd2613c6add`
  on November 25 at 21:01:45 UTC. Natural Linux CI passes all 10,340 cases;
  required aggregate `139507070985` passes before ordinary managed merge.
  All five owned merged blobs match the reviewed head. Supported pull-forward,
  all source/helper checks and all seventeen retained observed inputs verify
  without test replay. The actual Goal, own leaf and full Validation Plan
  are reread before resolving only this source leaf.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records review and synchronization. Broader parent gates remain open.
- [x] Qualify the complete original byte-stream HTTP framing suite only if
  exact metadata and its unchanged import closure fit the existing envelope.
  The original five functions use in-memory buffered bytes, not a daemon,
  socket, subprocess or resource mutation. The actual parser, ordinary error
  class, package import statements, eager class bodies/defaults and path-only
  conftest are inspected; do not execute the conftest.
  Preserve every original object, fixture, parameter row/type, mark, ID and
  assertion. Measure the complete existing-v2 minified addition before any
  guarded trial or admission. Require unchanged guarding, per-case production
  coverage, exact observed inputs, typing, lint, normal review, required CI,
  merge and readback. Add no client construction, provider effect, import
  bypass, native qualification, data-admission change or guard/limit exception.
  Metadata-only collection measures all 27 original cases across five complete
  functions with exact object/code/global origins, fixtures, parameter
  rows/types, marks and IDs; the longest unchanged suffix is 110 UTF-8 bytes.
  The measured full addition fits before any trial. The fresh unchanged
  guarded trial then passes all 27 in 0.90 pytest / 12.218 observation seconds,
  with 81 passing phase reports, zero prohibited effects, 21 bound source
  reads and parser call coverage for every case. Strict Linux/Windows typing
  and Ruff pass. Add only 27 nodes, eight bindings and one parser target:
  10,367 nodes, 1,497 sources, 545 targets, 92 unchanged helper decisions and
  1,047,467 minified bytes, leaving 1,109 under the same cap.
  Source #14620 merges at `16ec3d7aece6db998f14384718d61f53859d9efe`
  on November 25 at 21:21:14 UTC. Natural Linux CI passes all 10,367 cases;
  required aggregate `139517963323` passes before ordinary managed merge.
  All five owned merged blobs match the reviewed head. Supported pull-forward,
  all source/helper checks and all 21 retained observed inputs verify without
  test replay. The actual Goal, own leaf and full Validation Plan are reread
  before resolving only this source leaf.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records review and synchronization. Broader parent gates remain open.
- [x] Disposition the complete original installed-inventory prospect under
  unchanged metadata, storage and eligibility requirements.
  Static inspection closes the original fixture and its actual readers:
  supplied temporary JSON and `.git/config` text, not Git execution,
  installed-user state, registration mutation or native locking.
  Preserve the exact original `inventory` fixture object, test functions,
  code/global origins, parameter values/types, marks, IDs and assertions.
  The path-only conftest is inspected but not executed.
  A write-free length-only storage projection fits all five with the adapter,
  original and three newly bound production files in 1,048,513 bytes,
  leaving 63 under the same cap; this is not collection or qualification.
  Actual metadata confirms all five cases, exact original fixture/function
  objects and the same 1,048,513-byte fit. The first unchanged guarded case
  then refuses the original helper's fixture `.git/config` write as
  `actual Git storage access`; it is not passing evidence or a Git subprocess.
  Every whole function calls that same helper, so all five remain excluded;
  the other four are not executed. Withdraw the unpublished adapter and
  admit nothing. Preserve the metadata/candidate/refusal evidence without
  replay, fixture relocation/substitution, preload or guard exception.
  The manifest remains at 10,367 nodes and 1,047,467 bytes with 1,109 free.
  This resolves only the prospect's eligibility disposition, not any parent
  or retained native/owner/coverage requirement.
- [x] Qualify the complete eight-row original logical-environment tuple
  function without admitting a partial parameter table.
  The original module and pure string-decomposition owner are inspected;
  package import closure is already bound. Metadata-only original collection
  measures fifteen cases in three whole functions. The full addition would
  be 1,049,014 bytes, 438 over the unchanged cap. The complete eight-row
  `test_logical_and_tuple_forms` function fits at 1,048,547 bytes, leaving 29.
  The other two complete functions/seven cases remain storage-held, not
  technically refused or deleted.
  Compare the exact adapter/original objects, code/global origins, fixtures,
  parameter rows/types, marks and IDs before unchanged guarding.
  Require per-case production call coverage, exact observed inputs, typing,
  lint, ordinary review, required CI, merge and readback. Add no OS or
  environment-variable substitution, native call, new admission policy,
  source/profile/schema change, guard exception or limit increase.
  Exact adapter/original comparison measures fifteen originals and eight
  aliases, retaining every row of the selected function. The fresh unchanged
  guarded trial passes all eight in 1.09 pytest / 11.422 observation seconds:
  24 passing phase reports, zero prohibited effects, 25 bound source reads
  and production call coverage for every case. Strict Linux/Windows typing
  and Ruff pass. Add only eight identities, two bindings and one already-bound
  owner as target: 10,375 nodes, 1,499 sources, 546 targets,
  92 unchanged helper decisions and 1,048,547 minified bytes, leaving 29.
  Source #14625 merges at `6e2637ccbb65bfc0e4295f52358142c571ade943`
  on November 25 at 21:56:32 UTC. Natural Linux CI passes all 10,375 cases;
  required aggregate `139535763823` passes before ordinary managed merge.
  All five owned merged blobs match the reviewed head. Supported pull-forward,
  all source/helper checks and all 25 retained observed inputs verify without
  test replay. The actual Goal, own leaf and full Validation Plan are reread
  before resolving only this source leaf.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records review and synchronization. Broader parent gates remain open.
- [x] Resolve the next storage direction before further census admission in
  this campaign. The fully minified existing-v2 manifest is 1,048,547 bytes
  under the unchanged 1,048,576-byte cap, leaving 29.
  Even a shortest possible new source binding requires 73 bytes.
  The two known storage-held functions require at least 51 or 68 bytes per
  case before parameters or a new group; neither fits.
  No further whitespace removal is available under the approved format.
  Preserve every current witness, binding, target, helper decision, proof,
  eligibility rule, parser bound, deadline and operational hold.
  The operator now selects the reader-first successor and reviewed source
  migration recorded in the [scope approval](manifest-storage-successor.md#scope-approval).
  No larger cap, test omission, eligibility/guard exception or operational
  authority follows. The original-census, every-release and parent gates stay open.
- [x] Deliver the [reader-first manifest successor](manifest-storage-successor.md)
  through reviewed intent, actual consumer/bootstrap closure, compatible reader
  delivery and a separately reviewed source-data migration. Complete that
  phase's Plan and Validation before resuming further original-unit admission.
  Reader #14658 is merged with the full 10,375-case packet; persistent reviewed
  blobs and all current bindings/helpers match. The separate format-only
  migration preserves that exact post-reader inventory in 697,907 bytes.
  Data #14662 is independently confirmed merged; current-head review,
  complete fresh packet, required checks and persistent blob/binding/helper
  readback complete that bounded phase. Resume original census admission
  with v3 storage and unchanged eligibility/effect gates.
  Preserve the parent completion gate and all retained owners and holds.

**2026-11-21 - Compact manifest representation (operator-selected behavior):**

The operator selected `compact-versioned-manifest` rather than sharding,
further v1 minification as the long-term approach, or switching away from unit
census work. Implement reader-first support through normal source review while
retaining every existing limit and admission. This selects a source approach,
not writer activation, native execution, rollout or a transfer of existing
CI/release control. Ordered file groups and normalization to the existing
logical manifest are the agent-recommended realization.

**2026-11-23 - Compact existing v2 formatting (operator-selected behavior):**

At the next byte frontier, the operator selects
`compact-existing-v2-formatting`, rather than switching to packet-verification
CPU optimization or pausing source changes. Change only the existing v2 JSON
formatting, then continue the unchanged original-unit census. Preserve the
schema/profile, every value and ordered witness, logical identity, proof
checks, 1 MiB cap, deadlines and all operational holds. This is not a new
profile, phase contract, writer activation, native grant or rollout.

**2026-11-23 - Pure packet-processing CPU correction (operator-selected behavior):**

After #14241's exact-head CI expires before upload, the operator selects:

> optimize pure packet-processing CPU in this PR, then review/test a new
> head, preserving all guards, witnesses, contracts and limits.

Keep the failed attempt intact; do not blindly retry it or bypass CI.
An independent owner is landing a ten-minute outer unit-job envelope through
normal review, retaining the packet's 300-second bound. Rebase onto that
change after it merges rather than duplicating it or changing a workflow here.
This decision authorizes pure packet-CPU source work and fresh qualification
of a corrected head in #14241, not a phase/profile/schema change, witness
reduction, native action, rollout or transfer of the independent owner's work.
After the correction passes local validation, the operator clarifies: do not
wait for that unpublished envelope PR; publish the corrected head now through
normal gates and rebase if the independent envelope change later lands.

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
- [x] Qualify all 33 cases from eight adjacent original digest, transfer,
  unsafe-wheel, declared-source-path and signer-policy functions. Preserve
  exact original functions, parameters, callbacks, assertions and the autouse
  identity fixture in the existing adapter. Guard every adapter case with
  closed reads, positive per-case actual owner calls and zero prohibited
  effects before admission. Preserve preceding node order and unrelated
  source hashes, targets, helper decisions, compact/flat-v1 identity and the
  300-second bound. Require normal strict typing, current-head review and
  complete fresh Linux CI; native, installed, real publication and broader
  parent gates remain separate.
  #14050 merges at `fbe2276af774d799e5eddf9cd282f09c7736d6fe` after
  exact-head Copilot approval and complete required Linux CI with all 8,121
  units freshly executed. Broader parent gates stay open.
- [ ] Resolve the original successful bundle-attestation control's thread-start
  refusal through its existing integration owner. The 54-case development
  prototype passes the preceding 105 adapter cases but refuses
  `test_attestation_checks_exact_workflow_ref_and_digests` at the production
  verifier's thread-pool submission. It is withdrawn from the adapter and is
  not admitted; the original function, guard and threading boundary remain
  unchanged. Do not substitute a serial pool or weaker witness.
- [x] Qualify the other 53 cases from fourteen original attestation-failure,
  publication, explicit-404, tag-resolution and candidate-source controls. Preserve exact
  original functions, callbacks, parameters, assertions and autouse fixture,
  including synthetic command and token-isolation witnesses. Require closed
  reads, positive per-case actual owner calls and zero prohibited effects
  before admission. Preserve preceding node order and unrelated source hashes,
  coverage targets, helper decisions, compact/flat-v1 identity and the
  300-second bound. Normal strict typing, current-head review and complete
  fresh Linux CI remain required; real publication, credentials, native,
  installed and broader parent gates remain separate.
  #14055 merges at `d7e8fe3c9d7ef7582aada56b3537a0b3571450fc` after
  exact-head Copilot approval and complete required Linux CI with all 8,174
  units freshly executed. The distinct successful-attestation thread refusal
  and broader parent gates stay open.
- [x] Qualify all thirteen cases from five original candidate-retry clock,
  environment, exact remote identity and CLI output functions. Export exact
  original functions and the identity fixture, preserving every parameter,
  callback, clock witness and assertion. Keep the three native Git cases with
  their existing owner, unchanged. Require closed reads, positive per-case
  actual owner calls and zero prohibited effects before admission. Preserve
  all preceding nodes and hashes, targets, helper decisions, compact/flat-v1
  identity and the 300-second bound. Require normal strict typing, current-head
  review and complete fresh Linux CI; native, installed, publication and
  broader parent gates remain separate.
  #14056 merges at `447c6d4e6f6d1554894f6e36bf79d4a204c77a4d` after
  exact-head Copilot approval and complete required Linux CI with all 8,187
  units freshly executed. Broader parent gates stay open.
- [x] Qualify all 27 cases from six original subject-attestation verifier
  retry functions, preserving the original subject fixture, mock callbacks,
  parameter identities, monotonic deadline witnesses and assertions. This
  independent subject-verifier cohort is not a replay or replacement of
  the refused threaded bundle-attestation witness. Require closed reads,
  positive per-case actual owner calls and zero prohibited effects before
  admission. Preserve preceding nodes and hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI; native, installed,
  publication and broader parent gates remain separate.
  #14057 merges at `eddb35122c52195acbd12bba835d8d53d090854c` after
  exact-head Copilot approval and complete required Linux CI with all 8,214
  units freshly executed. The distinct threaded witness and broader parent
  gates remain open.
- [ ] Resolve the six original core manager-wheel exception cases' undeclared
  build-data input with the existing data-admission owner. Review of #14058
  identifies `worktree-manager/pyproject.toml` outside the admitted source/data
  closure. Withdraw the entire original function without changing its inputs
  or assertions; a green Python-read effect audit does not bind that TOML.
  Do not widen the data allowlist to admit this cohort.
- [x] Qualify the other 23 synthetic cases from twelve original core-artifact
  functions, preserving their exact functions, fixture helpers, build
  callbacks, parameters and assertions. Keep the original isolated builder
  CLI smoke with its existing integration owner. Require closed reads,
  positive per-case actual owner calls and zero prohibited effects before
  admission. Preserve preceding nodes and hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI; native, installed,
  real builds and broader parent gates remain separate.
  #14058 merges at `39433b011c1decd03f4681455df3c41a44df5a55` after
  exact-head Copilot approval and complete required Linux CI with all 8,237
  units freshly executed. The six undeclared-data cases, original isolated
  CLI smoke and broader parent gates remain open.
- [ ] Qualify the 52 synthetic cases from thirteen original runtime-profile
  class methods, preserving the exact original class, methods, parameters,
  helpers and assertions. Admit only those positive selectors; keep the
  original real fresh-process smoke unchanged with its integration owner.
  Require complete original/adapter collection identity, closed reads,
  positive per-case actual owner calls and zero prohibited effects before
  admission. Preserve preceding nodes and hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI; native, installed,
  data-admission and broader parent gates remain separate.
  Initial qualification preserves all 53 original/adapter class, method and
  parameter identities but stops on the first selected case: dependency-marker
  evaluation reaches `platform.uname()` and `socket.gethostname`, refused by the
  unchanged effect auditor. None of the 52 cases is admitted. Retain the
  candidate and failed helper; do not preload host state or retry variants.
- [x] Qualify the three original bounded-hash worker-count refusal cases,
  preserving their exact function, parameters and assertions. Keep the two
  original threaded hashing witnesses unchanged with their integration owner.
  Require closed reads, positive per-case actual owner calls and zero
  prohibited effects before admission. Preserve preceding nodes and hashes,
  targets, helper decisions, compact/flat-v1 identity and the 300-second bound.
  Require normal strict typing, current-head review and complete fresh Linux
  CI; the refused runtime-profile class and broader parent gates remain open.
  #14061 merges at `55479aa6685d8536b530a4e132de09aa11bfe7be` after
  exact-head Copilot approval and complete required Linux CI with all 8,240
  units freshly executed. Both threaded witnesses, the 52-case host-query
  refusal and broader parent gates remain open.
- [x] Qualify seven original deterministic Windows-rotation selector cases,
  preserving their exact functions, fixed dates, CLI arguments and assertions.
  Keep the original tier-disjointness assertion outside this call-coverage
  admission without reclassifying it or inventing an owner call.
  Require complete original/adapter collection identity, closed reads,
  positive per-case actual owner calls and zero prohibited effects before
  admission. Preserve preceding nodes and hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI; no actual Windows
  workflow, platform qualification, runtime or native contract changes.
  #14062 merges at `0a27a31e0369c47c87c6db1277fde61e788cf22f` after
  exact-head Copilot approval and complete required Linux CI with all 8,247
  units freshly executed. The original tier-disjointness assertion and
  broader Windows, scheduled-integration and parent gates remain open.
- [x] Qualify all 104 original fallback-equivalence cases, preserving all three
  original functions, the greedy reference implementation, all 100 seeds,
  nine budgets per seed, parameters and assertions without reducing the matrix.
  Require complete original/adapter collection identity, closed reads,
  positive per-case actual owner calls and zero prohibited effects before
  admission. Preserve preceding nodes and hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI. This exercises the
  existing pure algorithm, not portfolio routing, baseline adoption, workflow
  policy or activation; all such owners and broader parent gates remain separate.
  Complete collection preserves all 104 original selectors, original function
  objects and parameter objects. Fresh guarded qualification passes all 104
  in 2.562 seconds with zero prohibited effects, four bound Python reads and
  positive actual call-phase algorithm-owner lines per case. The compact-v2
  candidate contains 8,351 nodes, 1,189 sources and 450 targets; all 8,247
  preceding nodes, source hashes and 89 helper decisions remain intact.
  #14063 merges at `d56f481a236cacf0ed4090401e50ff8b125fce1f` after
  exact-head Copilot approval and complete required Linux CI with all 8,351
  units freshly executed. Only this finite leaf is complete; portfolio
  activation, shared-owner seams and broader parent gates remain open.
- [x] Qualify the 20 original pure coverage-policy methods in the complete
  `TestSelectTests`, `TestComputeFallbackSet` and
  `TestDefaultTierEligibleTests` classes. Preserve the exact class and method
  objects, shared synthetic baseline, class helpers and assertions.
  Keep all other original-module methods and real integration witnesses
  unchanged with their existing owners. Require complete selected
  original/adapter collection identity, a closed source-read import closure,
  positive per-case actual owner calls and zero prohibited effects before
  admission. Preserve preceding nodes and hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI; no portfolio
  activation, baseline adoption, workflow or native contract changes.
  Complete collection maps all 20 exact class/method objects from 157 original
  cases. Fresh guarded qualification passes all 20 in 1.500 seconds, with zero
  prohibited effects, 23 bound Python reads and positive actual call-phase
  owner lines per case. Nine bindings close the original module's import
  closure; no target or admission-policy change is needed. The compact-v2
  candidate contains 8,371 nodes, 1,198 sources and the existing 450 targets.
  #14064 merges at `2d4fa14c7fc471e1638c0704a1cc1381c7365762` after
  exact-head Copilot approval and complete required Linux CI with all 8,371
  units freshly executed. Only this finite leaf is complete; other original
  cases and all broader parent gates remain open.
- [x] Qualify the 27 original methods in the complete `TestCorrelation`
  and `TestFetchBaselineAsset` classes, retaining exact class/method objects,
  helpers, synthetic payloads, existing subprocess stubs and assertions.
  Do not add mocks, replace witnesses, download assets or execute native
  processes. Keep other original-module cases unchanged and separately owned.
  Require complete selected original/adapter collection identity, closed
  source reads, positive per-case actual owner calls and zero prohibited
  effects before admission. Preserve preceding nodes and hashes, targets,
  helper decisions, compact/flat-v1 identity and the 300-second bound.
  Require normal strict typing, current-head review and complete fresh Linux
  CI; no baseline publication/adoption, workflow, runtime or native contract
  changes.
  Complete collection maps all 27 exact class/method objects from 157 original
  cases. Initial guarded qualification passes all 27 in 1.328 seconds, with
  zero prohibited effects, 23 bound Python reads and actual correlation-owner
  call-phase lines per case. Only the adapter needs a new source binding;
  original helpers, payloads, subprocess stubs and assertions are unchanged.
  The compact-v2 candidate contains 8,398 nodes, 1,199 sources and the existing
  450 targets. #14065 merges at
  `bbd747b48cbab03f82c117a253d0d1c1a4a79c62` after exact-head Copilot approval
  and complete required Linux CI with all 8,398 units freshly executed.
  Only this finite leaf is complete; all broader parent gates remain open.
- [x] Qualify the complete original `TestPlanChunks` and
  `TestMergeChunkResults` classes, retaining every class/method object,
  parameter, temporary-file fixture, existing subprocess stub and assertion.
  Do not add mocks, preloads, real process/thread execution or network access.
  Require complete selected original/adapter collection and parameter identity,
  closed source reads, positive per-case actual owner calls and zero prohibited
  effects before admission. Preserve preceding nodes and hashes, helper
  decisions, compact/flat-v1 identity and the 300-second bound.
  Require normal strict typing, current-head review and complete fresh Linux
  CI; no baseline publication/adoption, workflow, runtime or native contract
  changes.
  Complete collection maps all 15 expanded cases with exact class/method and
  parameter objects. Initial guarded qualification passes all 15 in 2.453
  seconds, with zero prohibited effects, 23 bound Python reads and actual
  baseline-owner call-phase lines per case. Only the adapter needs a new source
  binding; baseline and chunk-scheduler modules become coverage targets.
  The compact-v2 candidate contains 8,413 nodes, 1,200 sources and 452 targets.
  #14066 merges at `8b999f63eb36bbb5ab0a20cd4ab9be02cd71c878` after
  exact-head Copilot approval and complete required Linux CI with all 8,413
  units freshly executed. Only this finite leaf is complete; all broader
  parent gates remain open.
- [x] Qualify all eleven original `TestDecide` methods, retaining the exact
  class/method objects, helper baselines, original collaborator stubs and
  assertions. Keep actual Git debt/ancestry, asset-fetch and integration cases
  unchanged with their existing owners. Do not add mocks, preloads, real
  process/thread execution or network access. Require complete selected
  original/adapter collection identity, closed source reads, positive per-case
  actual decision-owner calls and zero prohibited effects before admission.
  Preserve preceding nodes, hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI; no portfolio
  activation, workflow, runtime or native contract changes.
  Complete collection maps all eleven exact class/method objects from 157
  original cases. Initial guarded qualification passes all eleven in 1.515
  seconds, with zero prohibited effects, 23 bound Python reads and actual
  decision-owner call-phase lines per case. Only the adapter needs a new source
  binding; the already-bound decision module becomes a coverage target.
  The current base includes #14054's six independently owned bootstrap
  bindings and sixteen refreshed hashes; these are preserved, not reauthored
  or attributed to this slice. The compact-v2 candidate contains 8,424 nodes,
  1,207 sources and 453 targets. #14067 merges at
  `2966cd1a1688c49756e51439f9d3dd2adc56cef2` after exact-head Copilot approval
  and complete required Linux CI with all 8,424 selected units freshly
  executed. The merged base also carries #14059's independently owned
  additional node and five refreshed hashes, yielding 8,425 current nodes.
  Only this eleven-case leaf is complete; broader parent gates remain open.
- [x] Qualify the two original synthetic sandbox/diagnostic functions and
  eight non-integration `TestBaselineCollectionErrorContract` cases.
  Preserve the exact function/class/method and parameter objects, fixtures,
  helpers, existing collaborator stubs and assertions. The class's opt-in
  `test_real_collection_error_is_not_hidden_by_coverage_warning` remains
  unchanged and outside the exact manifest; the whole nine-case class is
  not unit-qualified. Require complete original/adapter collection identity,
  explicit ten-case selection, closed source reads, positive per-case actual
  owner calls and zero prohibited effects before admission.
  Preserve preceding nodes, current-base hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI; no new mocks,
  preloads, process/thread execution, network access, workflow, runtime or
  native contract changes.
  Complete collection verifies all 157 original cases, eleven adapter cases
  and the exact ten-case selection, including original parameter-object
  identity. Initial unchanged-guard qualification passes all ten in 2.281
  seconds, with zero prohibited effects, 23 bound Python reads and actual
  baseline-owner call-phase lines per case. The real-process case is
  deselected, never executed or counted as a skipped unit. Only the adapter
  needs a new source binding; no coverage target changes. The compact-v2
  candidate contains 8,435 nodes, 1,208 sources and 453 targets. #14068
  merges at `d29e522962d577f539d44b98859546b5003fffb4` after exact-head
  Copilot approval and complete required Linux CI with all 8,435 selected
  units freshly executed. Only this ten-case leaf is complete; broader
  parent gates remain open.
- [x] Qualify thirteen original synthetic chunk-control cases and the pure
  original CLI-summary case. Preserve the four original imported function
  objects, class/method and parameter objects, fixtures, helpers, existing
  collaborator stubs and assertions. Keep actual concurrency/process cases,
  workflow inspection and the three real-Git CLI methods unchanged outside
  the exact manifest; the whole CLI class is not unit-qualified.
  Require complete original/adapter collection identity, explicit fourteen-case
  selection, closed source reads, positive per-case actual owner calls and
  zero prohibited effects before admission. Preserve preceding nodes,
  current-base hashes, targets, helper decisions, compact/flat-v1 identity
  and the 300-second bound. Require normal strict typing, current-head review
  and complete fresh Linux CI; no new mocks, preloads, process/thread
  execution, network access, workflow, runtime or native contract changes.
  Complete collection verifies all 157 original cases, seventeen adapter
  cases and the exact fourteen-case selection, including original parameter
  objects. Initial unchanged-guard qualification passes all fourteen in
  2.188 seconds, with zero prohibited effects, 23 bound Python reads and
  actual baseline/chunk/summary-owner call-phase lines per case. The three
  real-Git CLI cases are deselected, never executed or counted as skipped
  units. Only the adapter needs a new source binding; the already-bound CLI
  module becomes a coverage target. The compact-v2 candidate contains 8,449
  nodes, 1,209 sources and 454 targets. #14069 merges at
  `e6f58dd9792676c21f2813b8aa19ef374c332f44` after exact-head Copilot approval
  and complete required Linux CI with all 8,449 selected units freshly
  executed. Only this fourteen-case leaf is complete; broader parent gates
  remain open.
- [ ] Qualify the six original auditability-declaration refusal cases and
  three existing stubbed selected-execution CLI cases. Preserve original
  function/parameter objects, fixtures, helpers, collaborator stubs and
  assertions. Keep actual Git/process execution, workflow inspection and
  host-profile-dependent auditability cases unchanged outside the packet.
  Require complete original/adapter collection identity, closed source reads,
  positive per-case actual owner calls and zero prohibited effects before
  admission. Preserve preceding nodes, current-base hashes, targets, helper
  decisions, compact/flat-v1 identity and the 300-second bound. Require normal
  strict typing, current-head review and complete fresh Linux CI; no new
  mocks, preloads, process/thread execution, network access, workflow, runtime
  or native contract changes.
  Complete collection verifies all sixty original cases and nine exact
  adapter cases, including original parameter objects and the explicit Python
  import closure. Initial unchanged-guard qualification refuses during
  collection because collected nodes differ from the exact admitted
  inventory. No tests execute and no case is admitted. The underlying
  collection cause is not exposed by that inventory refusal; it remains
  unknown. Retain the failed adapter and candidate without replay, preload,
  loader exception or guard change. This leaf remains open.
- [x] Qualify the eleven original pure CI-telemetry cases, preserving exact
  function and module-fixture objects, synthetic records, helpers and
  assertions. Keep the original persistence test and all API/fetch paths
  unchanged outside the packet. Require complete original/adapter collection
  identity, closed source reads, positive per-case actual telemetry-owner
  calls and zero prohibited effects before admission. Preserve preceding
  nodes, current-base hashes, targets, helper decisions, compact/flat-v1
  identity and the 300-second bound. Require normal strict typing,
  current-head review and complete fresh Linux CI; no new mocks, preloads,
  process/thread execution, network access, workflow, runtime or native
  contract changes.
  Complete collection verifies twelve original cases and eleven exact
  adapter cases, including the unchanged module fixture. Initial unchanged
  guard qualification passes all eleven in 1.171 seconds, with zero
  prohibited effects, four bound Python reads and actual telemetry-owner
  call-phase lines per case. The compact-v2 candidate contains 8,460 nodes,
  1,213 sources and 455 targets. #14071 merges at
  `de9a977f150f983f64615c4dcf4144bcdec135b4` after exact-head Copilot approval
  and complete required Linux CI with all 8,460 selected units freshly
  executed. Only this eleven-case leaf is complete; the refused nine-case
  candidate contributes no nodes or source bindings and broader gates remain
  open.
- [x] Qualify the complete original CI-failure-watchdog test module:
  47 original functions and fifty expanded cases, including its unchanged
  watchdog fixture, synthetic logs, helpers, existing subprocess/API stubs,
  parameter objects, temporary output files and assertions. No actual GitHub,
  subprocess, thread or network execution is selected.
  Require complete original/adapter collection identity, closed source reads,
  positive per-case actual watchdog-owner calls and zero prohibited effects
  before admission. Preserve preceding nodes, current-base hashes, targets,
  helper decisions, compact/flat-v1 identity and the 300-second bound.
  Require normal strict typing, current-head review and complete fresh Linux
  CI; no new mocks, preloads, wrappers, effect exceptions, workflow, runtime
  or native contract changes.
  Complete collection verifies all fifty original and adapter cases, exact
  function/fixture identity and the original four parameter objects.
  Initial unchanged-guard qualification passes all fifty in 1.984 seconds,
  with zero prohibited effects, three bound Python reads and actual
  watchdog-owner call-phase lines per case. The compact-v2 candidate contains
  8,510 nodes, 1,215 sources and 456 targets. #14072 merges at
  `62aac085ea9728ed4b29111214c62d7a858427a6` after exact-head Copilot approval
  and complete required Linux CI with all 8,510 selected units freshly
  executed. Only this fifty-case leaf is complete; broader gates remain open.
- [x] Qualify the fifty original pure CI-job-admission cases: seven original
  functions covering path selection, unknown-input widening, non-PR full
  coverage, path validation, union/empty diff and incomplete-commit refusal.
  Preserve original function and parameter objects, helpers and assertions.
  Leave actual Git/CLI, workflow inspection and aggregate subprocess cases
  unchanged outside the packet. Require complete original/adapter collection,
  closed source reads, positive per-case actual admission-owner calls and zero
  prohibited effects before admission. Preserve preceding nodes, current-base
  hashes, targets, helper decisions, compact/flat-v1 identity and the 300-second
  bound. Require normal strict typing, current-head review and complete fresh
  Linux CI; no new mocks, preloads, wrappers, effect exceptions, workflow,
  routing, runtime or native contract changes.
  Complete collection verifies all 67 original cases and fifty exact adapter
  cases, preserving original parameter-row objects. Initial unchanged-guard
  qualification passes all fifty in 2.750 seconds, with zero prohibited
  effects, three bound Python reads and actual admission-owner call-phase
  lines per case. The compact-v2 candidate contains 8,560 nodes, 1,218 sources
  and 457 targets. #14073 merges at
  `9a6a8f56bf3c972b1fb9012c60fa854a98cc47b5` after exact-head Copilot approval
  and complete required Linux CI with all 8,560 selected units freshly
  executed. Only this fifty-case leaf is complete; broader gates remain open.
- [x] Qualify 109 original pure CI-partition matrix/backend-decision cases:
  four matrix functions and all sixteen backend functions, preserving exact
  original functions, parameter rows, both backend fixtures, helpers and
  assertions. Keep actual CLI, Git, workflow/generated-checkout and real
  repository-inventory cases unchanged outside the packet.
  Require complete original/adapter collection, closed Python reads, positive
  actual owner calls per case and zero prohibited effects before admission.
  Preserve preceding nodes/order, current-base hashes, targets, helper
  decisions, compact/flat-v1 identity and the 300-second bound. Require normal
  strict typing, current-head review and complete fresh Linux CI. No new
  mocks, preloads, wrappers, effect exceptions, data admission, workflow,
  routing, runtime, native reserve proof or runner allocation is authorized.
  Complete collection verifies all 124 original cases and exactly 109 selected
  cases across twenty unchanged original function objects, both fixture
  objects and every selected parameter value. Initial unchanged-guard
  qualification passes all 109 in 3.219 seconds, with zero prohibited effects,
  closed Python reads and actual partition-owner call-phase lines per case.
  The compact-v2 candidate contains 8,669 nodes, 1,221 sources and 458 targets.
  #14074 merges at `dc8ca620e2b007fcd75dc388b5203e7edf5b8e95` after
  exact-head Copilot approval and complete required Linux CI with all 8,669
  selected units freshly executed. Only this 109-case leaf is complete;
  broader gates remain open.
- [x] Qualify the three original module-health lookup-outcome cases, retaining
  exact original function objects, their existing subprocess stubs, helpers
  and assertions. Keep all seven actual Git, CLI and main-entrypoint cases
  unchanged outside the packet. Require complete original/adapter collection,
  closed Python reads, positive actual owner calls per case and zero prohibited
  effects before admission. Preserve preceding nodes/order, current-base hashes,
  targets, helper decisions, compact/flat-v1 identity and the 300-second bound.
  Require normal strict typing, current-head review and complete fresh Linux CI.
  No new mock, preload, wrapper, effect exception, data admission, workflow,
  runtime, actual GitHub lookup or issue filing is authorized.
  Complete collection verifies ten original and three selected function
  objects in original order. Initial unchanged-guard qualification passes all
  three in 1.375 seconds, with zero prohibited effects, three bound Python
  reads and actual watchdog-owner call-phase lines per case. The compact-v2
  candidate contains 8,672 nodes, 1,224 sources and 459 targets. #14076 merges
  at `e2cdc012fdd91f9a0d96f5434d6a56092eb0984e` after exact-head Copilot approval
  and complete required Linux CI with all 8,672 selected units freshly
  executed. Only this three-case leaf is complete; broader gates remain open.
- [x] Qualify 95 original installer-guard cases across seventeen original
  functions: engine/policy call parsing, temporary plugin scope validation,
  temporary stderr-redirection scanning and completion-marker status parsing.
  Preserve original function and parameter objects, helpers and assertions.
  Keep actual native execution, repository scans, imported runtime/profile
  suites and the constant/AST-only scope comparison outside the selection.
  Require complete original/adapter collection, closed Python reads, positive
  actual owner calls per case and zero prohibited effects before admission.
  Preserve preceding nodes/order, current-base hashes, targets, helper
  decisions, compact/flat-v1 identity and the 300-second bound. Require normal
  strict typing, current-head review and complete fresh Linux CI. No new mock,
  preload, wrapper, effect exception, data admission, installer/native
  execution, workflow, runtime or cleanup action is authorized.
  Complete collection verifies all 336 original cases and exactly 95 selected
  cases across seventeen unchanged original functions and original parameter
  values. Initial unchanged-guard qualification passes all 95 in 4.047 seconds,
  with zero prohibited effects, closed Python reads and actual guard-owner
  call-phase lines per case. The compact-v2 candidate contains 8,767 nodes,
  1,227 sources and 460 targets. #14078 merges at
  `e25c69750ab737de649b7f4357eb65a40ce4e62b` after exact-head Copilot approval
  and complete required Linux CI with all 8,767 selected units freshly
  executed. Only this 95-case leaf is complete; broader gates remain open.
- [ ] Qualify 21 original synthetic machine-gate contract cases across four
  original functions: actual source conformance, source mutations, planner
  bypasses and missing-source refusals. Preserve the original source-copy
  helper, private synthetic package, AST function extraction, doubles,
  parameter objects and assertions. No original contract mechanism is replaced.
  Require complete original/adapter collection, closed Python reads, positive
  actual owner calls per case and zero prohibited effects before admission.
  Preserve preceding nodes/order, current-base hashes, targets, helper
  decisions, compact/flat-v1 identity and the 300-second bound. Require normal
  strict typing, current-head review and complete fresh Linux CI. No installer,
  native process, installed metadata probe, policy/routing change, new mock,
  preload, wrapper, effect exception, data admission or cleanup is authorized.
  Complete collection verifies 336 original cases and exactly 21 selected
  cases with original function/parameter identity. Initial unchanged-guard
  execution stops after nine passes at the original mutation
  `update_gate.py: return machine in allowed -> return True`: the mutation
  source text is absent, so the original `assert before in text` fails before
  that contract check. No complete observation report or admission follows.
  All 21 remain unadmitted; the exact adapter, candidate, selector inventory
  and helper are retained without replay or assertion changes.
- [x] Qualify twenty original strict-checker inventory/manifest/matrix controls
  across eight original functions. Preserve original functions, parameter
  values, both autouse fixtures, helpers, source-custody controls and assertions.
  Keep actual Git/MyPy/frontdoor/native execution and repository-data cases
  unchanged outside this slice. Require complete original/adapter collection,
  closed Python reads, positive actual owner calls per case and zero prohibited
  effects before admission. Preserve preceding nodes/order, current-base hashes,
  targets, helper decisions, compact/flat-v1 identity and the 300-second bound.
  Require normal strict typing, current-head review and complete fresh Linux CI.
  No new mock, preload, wrapper, effect exception, data admission, workflow,
  runtime or cleanup action is authorized.
  Complete collection verifies 236 original cases and exactly twenty selected
  cases across eight original functions, both autouse fixtures and every
  selected parameter value. Initial unchanged-guard qualification passes all
  twenty in 1.906 seconds, with zero prohibited effects, closed Python reads
  and actual checker-owner call-phase lines per case. The compact-v2 candidate
  contains 8,787 nodes, 1,228 sources and 460 targets. #14079 merges at
  `bdaf43f00ad3653ab8cc9129c0d125acf2e2bdc4` after exact-head Copilot approval
  and complete required Linux CI with all 8,787 selected units freshly
  executed. Only this twenty-case leaf is complete; broader gates remain open.
- [x] Qualify thirty additional original strict-checker import-custody cases
  across eight original functions: import-root manifests, native path-list
  separator refusal, ordered temporary import delivery, namespace/WIP members,
  temporary extension aliases and input/root-change refusals. Extend the
  existing original-function adapter in original order, preserving both autouse
  fixtures, helpers, assertions and original parameter values. Qualify all fifty
  adapter cases before admitting only the thirty new nodes. Keep actual
  Git/MyPy/frontdoor execution, repository scans and symlink fixtures excluded.
  Preserve preceding nodes/order, every other current-base source hash, targets,
  helper decisions, compact/flat-v1 identity and the 300-second bound. Require
  closed Python reads, positive actual owner calls per case, zero prohibited
  effects, normal strict typing, current-head review and complete fresh Linux CI.
  No new mock, preload, wrapper, effect exception, data admission, workflow,
  runtime, native process or cleanup action is authorized.
  Complete collection verifies all 236 original cases and fifty adapter cases,
  exact function/fixture identity and every selected parameter value. Initial
  unchanged-guard qualification passes all fifty in 3.047 seconds, with zero
  prohibited effects, 24 bound Python reads and actual checker-owner call-phase
  lines per case. Only thirty new nodes are appended within the existing first
  group; all 8,787 preceding nodes retain their relative order and all 1,227
  other source hashes remain intact. The compact-v2 candidate contains 8,817
  nodes, 1,228 sources and 460 targets. #14080 merges at
  `c492d6e5d9ea3bbdc087e91747b82d6251e75a46` after exact-head Copilot approval
  and complete required Linux CI with all 8,817 selected units freshly
  executed. Only this thirty-case leaf is complete; broader gates remain open.
  The merged Dev base also contains independently landed #14007 at
  `9f2293251a342c62d8e2f557b6673a8aaaa2db19`: its seventeen added source
  bindings and four refreshed hashes bring the merged manifest to 1,245
  sources in 987,506 bytes without changing nodes, targets or the bound.
  The 1,228-source/985,329-byte qualification above describes this leaf's
  reviewed head, not the combined merged base.
- [x] Qualify fourteen additional original strict-checker lifecycle controls
  across eleven original functions: input-drift cleanup, conservative consumer
  selection, missing-preparation refusal, pending-probe non-admission,
  dependency-policy refusal, captured-lock installation, failed-cache repair,
  unqualified include refusal, replaced-directory refusal, lease-time readiness
  and checker-timeout cleanup. Preserve exact original functions, parameter
  values, both autouse fixtures, helpers, existing collaborator stubs and
  assertions. Keep actual Git/MyPy/frontdoor, repository-catalog, symlink,
  nonordinary-global-metadata and platform-specific fallback cases excluded.
  Qualify all 64 adapter cases before admitting only the fourteen new nodes
  after the current fifty in the same first group. Preserve preceding node
  order, every other current source hash, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require closed Python
  reads, positive actual owner calls per case, zero prohibited effects, normal
  strict typing, current-head review and complete fresh Linux CI. No new mock,
  preload, wrapper, effect exception, data admission, workflow, runtime,
  native process or cleanup action is authorized.
  Complete collection verifies all 236 original cases and 64 adapter cases,
  exact function/fixture identity and every selected parameter value.
  Initial unchanged-guard qualification passes all 64 in 3.250 seconds, with
  zero prohibited effects, 24 bound Python reads and actual checker-owner
  call-phase lines per case. Only fourteen new nodes are appended within the
  first group; all 8,817 preceding nodes retain their relative order and all
  1,244 other current-base hashes remain intact. The compact-v2 candidate
  contains 8,831 nodes, 1,245 sources and 460 targets. #14081 merges at
  `cb2cfa00afdec9e52c3a69437a59918e8a747de3` after exact-head Copilot approval
  and complete required Linux CI with all 8,831 selected units freshly
  executed. Only this fourteen-case leaf is complete; broader gates remain open.
- [x] Qualify 46 additional original strict-checker source-view controls across
  eleven original functions: guard-alias scope/mapping/drift, physical line
  mapping, native-loader source preservation, copied input custody,
  loader-mutation and directive refusals, declared fragment order, stale
  composition, code/suppression preservation and imported-view drift cleanup.
  Preserve exact original functions, parameter values, both autouse fixtures,
  helpers, existing collaborator stubs and assertions. Keep actual MyPy,
  native/process, symlink, repository-catalog, platform-specific guard-shadow
  and all other excluded cases unchanged outside the selection.
  Qualify all 110 adapter cases before admitting only 46 new nodes after the
  current 64 in the same first group. Bind the original guard helper's one
  canonical Python read without adding a coverage target; preserve preceding
  node order, all other current source hashes, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require closed Python
  reads, positive actual owner calls per case, zero prohibited effects, normal
  strict typing, current-head review and complete fresh Linux CI. No new mock,
  preload, wrapper, effect exception, data admission, workflow, runtime,
  native process or cleanup action is authorized.
  Complete collection verifies all 236 original cases and 110 adapter cases,
  exact function/fixture identity and every selected parameter value.
  Initial unchanged-guard qualification passes all 110 in 9.000 seconds, with
  zero prohibited effects, 27 bound Python reads and actual checker-owner
  call-phase lines per case. Only 46 new nodes are appended within the first
  group; all 8,831 preceding nodes retain their relative order. Only the
  existing adapter hash changes and the canonical guard source binding is
  added, preserving all other 1,244 current-base hashes. The compact-v2
  candidate contains 8,877 nodes, 1,246 sources and 460 targets. #14083 merges at
  `630385013436e640dd72fa3940cbc67ece00571a` after exact-head Copilot approval
  and complete required Linux CI with all 8,877 selected units freshly
  executed. Only this 46-case leaf is complete; broader gates remain open.
- [ ] Qualify seven original in-process promotion source-identity controls
  across six complete original functions: equivalent-composition index
  acceptance, changed-source/composition refusal, legacy parent binding,
  second-container refusal, immutable-set reuse and changed-asset refusal.
  Preserve exact original functions, parameter values, helpers, assertions
  and existing collaborator stubs. Keep real Git/history/native cases outside.
  Require complete original/adapter collection identity, unchanged effect
  auditing, closed Python reads and actual production calls per case before
  admission. Add only the canonical original/adapter Python source bindings;
  preserve every prior node and source hash, targets, helper decisions,
  compact/flat-v1 identity and the 300-second bound. Require normal strict
  typing, current-head review and complete fresh Linux CI. No new mock,
  preload, wrapper, effect exception, data admission, workflow, runtime,
  native process or cleanup action is authorized.
  Complete collection verifies sixteen original cases and seven adapter
  cases with exact function/parameter identities and no native repository
  fixture. Initial unchanged auditing stops after five passes when the
  original immutable-set reuse case reaches `verify_asset_downloads` and its
  thread pool; the guard refuses `thread start` before launch. No complete
  observation report or admission follows. All seven remain outside the
  packet, with the exact adapter, candidate and helper retained without
  replay, subset admission, test changes or guard exceptions.
- [ ] Qualify both original dependency-resource overlap cases, preserving
  original editable/noneditable parameters, synthetic UV projection probe,
  actual retained source copies, metadata and hash checks. Require complete
  original/adapter collection identity, unchanged effect auditing, closed
  Python reads and actual policy-owner calls before admission. Preserve all
  prior nodes/hashes, targets, helper decisions and the 300-second bound.
  Require normal strict typing, current-head review and complete fresh Linux
  CI. No new mock, preload, wrapper, effect exception, data admission, native
  process, workflow, runtime or cleanup action is authorized.
  Complete collection verifies both original and adapter cases with exact
  function/parameter identity. Initial unchanged auditing stops at the first,
  noneditable case: Windows `private_retained_root` attempts `ctypes.dlopen`
  for `advapi32`, which the guard refuses before loading. No complete
  observation report or admission follows. Both remain outside the packet,
  with exact adapter, candidate and helper retained without replay, subset
  admission, preloading, test changes or guard exceptions.
- [x] Qualify the original in-process checker procutil-provider control:
  candidate-local precedence, foreign winning provider refusal and package,
  module and distribution-stub refusal. Preserve the complete original
  function and assertions, with both unchanged autouse fixtures. Qualify all
  111 adapter cases before admitting only this one node at the front of the
  same checker group, retaining original source order and all prior node
  order/hashes except the changed adapter binding. Preserve targets, helper
  decisions, compact/flat-v1 identity and the 300-second bound. Require closed
  Python reads, positive actual owner calls, zero prohibited effects, normal
  strict typing, current-head review and complete fresh Linux CI. No native
  launch, preload, new mock, guard exception, data, workflow or runtime change.
  Complete collection verifies all 236 original cases and 111 adapter cases,
  exact function/fixture identity and every selected parameter value.
  Initial unchanged auditing passes all 111 in 8.844 seconds, with zero
  prohibited effects, 27 bound Python reads and actual checker-owner
  call-phase lines per case. Only the provider node is inserted at the front
  of the same checker group; all 8,877 prior nodes retain relative order.
  Only the existing adapter hash changes, preserving the other 1,245 source
  hashes. The compact-v2 candidate contains 8,878 nodes, 1,246 sources and
  460 targets. #14085 merges at
  `1cd4836f9a0c18106aee8db6507df205ba72f47e` after exact-head Copilot approval
  and complete required Linux CI with all 8,878 selected units freshly
  executed. Only this provider-control leaf is complete; broader gates remain open.
- [x] Qualify the three original optional H1 validator malformed-shape
  controls from one complete original function. Preserve exact original
  function, parameter values and assertions; call only `validate_packets`
  with synthetic malformed fixture declarations and verify deferral to the
  existing registry owner. Exclude every vector-read, asynchronous observation,
  host/client, packet-capture and historical-record case. Require complete
  original/adapter collection identity, unchanged effect auditing, closed
  canonical Python reads and actual validator calls before admission.
  Bind only the new adapter/original/validator/imported-publication Python
  sources and the actual validator target; preserve all prior nodes and hashes,
  helper decisions, compact/flat-v1 identity and the 300-second bound.
  Require normal strict typing, current-head review and complete fresh Linux
  CI. No capture, listener, native process, publication, cleanup, new mock,
  preload, guard exception, data, workflow, runtime or routing change.
  Complete collection verifies all 14 original cases and the three selected
  adapter cases, exact original function identity and every parameter object.
  Initial unchanged auditing passes all three in 1.032 seconds, with zero
  prohibited effects, seven bound Python reads and actual validator call-phase
  lines per case. Only the adapter, original, validator and imported-publication
  source bindings and validator target are added; all 8,878 prior nodes,
  1,246 hashes, 460 targets, 89 helper decisions and the 300-second bound
  remain intact. The compact-v2 candidate contains 8,881 nodes, 1,250 sources
  and 461 targets in 993,848 bytes. #14087 merges at
  `63cd3dd4acf78d12aa5b3820b7153c355adab9fb` after exact-head approval,
  complete fresh Linux CI and independent merged-tree verification.
  Malformed-shape deferral is not full H1 owner validation; only this leaf
  is complete.
- [x] Qualify the four original pytest temporary-link controls from both
  complete original functions. Preserve all Windows alias and Linux/macOS
  parameter objects, original monkeypatches, assertions and restoration.
  Verify only the optional pytest helper is suppressed and restored, while
  `os.symlink` remains unchanged; do not create an actual symlink.
  Require complete original/adapter collection identity, initial unchanged
  auditing, closed canonical Python reads, actual guard calls and zero
  prohibited effects before admission. Add only the adapter/original source
  bindings and existing guard target; preserve all 8,881 prior nodes,
  1,250 source hashes, 461 targets, 89 helper decisions and the 300-second
  bound. Require normal strict typing, committed changefile presence,
  exact-head review and complete fresh Linux CI. No native/process effect,
  new mock, preload, guard exception, data, workflow, runtime or cleanup change.
  Complete collection verifies all four original and adapter cases, both
  original functions and every selected parameter object. Initial unchanged
  auditing passes all four in 1.032 seconds, with zero prohibited effects,
  fourteen bound Python reads and actual guard call-phase lines per case.
  Only the adapter/original bindings and existing guard target are added.
  The compact-v2 candidate contains 8,885 nodes, 1,252 sources and 462 targets
  in 994,539 bytes, preserving prior node order and all previous source hashes.
  #14089 merges at `bd213fdfe08692746c8665361c972b193e2b696a` after
  exact-head approval and complete fresh Linux CI with all 8,885 selected
  units executed. Only this four-case leaf is complete.
- [x] Qualify the three original noncontained private build-tools-root
  refusals from one complete original function: sibling, outside and relative
  paths. Preserve exact original function, parameter objects and assertions.
  Leave the separate native path-equivalence function and its Windows-only
  case outside this selection; no native equivalence qualification is claimed.
  Require complete original/adapter collection identity, initial unchanged
  auditing, closed canonical Python reads, actual owner calls and zero
  prohibited effects before admission. Add only the adapter/original Python
  bindings and existing source-build owner target; preserve all 8,885 prior
  nodes, 1,252 source hashes, 462 targets, 89 helper decisions and the
  300-second bound. Require normal strict typing, committed changefile
  presence, exact-head review and complete fresh Linux CI. No native loader,
  thread, new mock, preload, guard exception, data, production, workflow,
  runtime, routing or cleanup change.
  Complete collection verifies all five original cases and the three selected
  adapter cases, with exact original function and parameter-object identity.
  Initial unchanged auditing passes all three in 1.671 seconds, with zero
  prohibited effects, twelve bound Python reads and actual source-build
  call-phase lines per case. Only the two Python bindings and existing
  source-build owner target are added. The compact-v2 candidate contains
  8,888 nodes, 1,254 sources and 463 targets in 995,204 bytes, preserving prior
  node order and all previous source hashes. #14090 merges at
  `37633fdcd039ec661180ddc97317e25fb8834f56` after exact-head approval and
  complete fresh Linux CI executes all 8,888 selected units. Only this
  three-case leaf is complete.
- [x] Qualify three original in-process source-phase controls: independent
  phase allowances and deadline cause, preservation of an integrity failure's
  original cause, and progress-output coalescing without losing completed work.
  Preserve the complete original functions, clocks, monkeypatches and assertions.
  Exclude the native path checks, file-batch/tree/thread cases and ordinary
  installer/feed fixtures. Bind the canonical original-test import closure
  without exercising those fixtures or adding mocks, preloads, data or guard
  exceptions. Require complete original/adapter collection identity, initial
  unchanged effect/source auditing and actual owner calls before admission.
  Preserve all 8,888 nodes, 1,254 hashes, 463 targets, 89 helper decisions and
  the 300-second bound. Normal strict checks, committed changefile presence,
  exact-head review and complete fresh Linux CI remain required. No runtime,
  native, thread, network, workflow, routing or cleanup authority is added.
  Complete collection verifies all 47 original cases and exactly three adapter
  cases with original function-object identity. Initial unchanged auditing
  passes all three in 2.063 seconds, with zero prohibited effects, seventeen
  bound canonical Python reads and actual source-build call-phase lines per
  case. Only the adapter, original test and its two direct original-test import
  bindings are added; no coverage target changes. The compact-v2 candidate
  contains 8,891 nodes, 1,258 sources and 463 targets in 996,013 bytes.
  Full fresh Linux CI and normal review/merge remain required.
  At #14092's initial head, fresh Linux CI `45031811112` refuses
  `ctypes.dlopen` during collection through the original test's imported
  wheel-acquisition module. No selected case executes in that run.
  The local audit is not portable qualification; retain the failed exact
  head and do not rerun, preload, subset or relax the guard. The import is
  used only by the separate Windows `_running` helper; relocating it to
  that helper is a possible source correction, not an accepted replay.
  The explicit `correct-import-and-requalify` decision permits only that
  ordinary source correction and qualification of a distinct corrected head.
  It does not permit replay of the failed head or native execution.
  The import now resides in its sole Windows helper branch; source comparison
  verifies no other original-test edit. Fresh corrected collection retains
  47 original and three adapter cases. Unchanged auditing passes all three in
  2.422 seconds with zero effects, the same seventeen bound reads and actual
  owner calls. Only that source hash changes; all nodes, targets, helper
  decisions and bounds remain unchanged. The distinct corrected head receives
  fresh exact-head approval and complete Linux CI executes all 8,891 units.
  #14092 merges at `1701f179a4f2e4ffdd985b7fc0101089c68fdf06`;
  reviewed and merged trees match. Only this three-case leaf is complete;
  the failed initial head remains retained and was not replayed.
- [ ] Qualify the separate original source-wheel verification case that
  requires a fresh bounded I/O budget on repeated verification.
  Export the complete original function, retaining both clock values,
  wheel bytes, real archive digest and original assertions. Require complete
  original/adapter collection identity, initial unchanged source/effect
  auditing and actual source-build owner calls before admission.
  Reuse the now-bound original import closure without invoking installer,
  feed, threaded-tree or native fixtures. Add only its adapter binding;
  preserve all 8,891 nodes, 1,258 source hashes, 463 targets, 89 helper
  decisions and the 300-second bound. Normal strict checks, committed
  changefile presence, exact-head review and complete fresh Linux CI remain
  required. No replay of the failed phase head, new mock, preload, data,
  guard exception, native/runtime/routing or cleanup authority is added.
  Complete collection verifies all 47 original cases and exactly one
  adapter case with original function-object identity. Initial unchanged
  auditing refuses `ctypes.dlopen`: `SourceWheelInputs.verify` calls
  `_digest_file`, whose Windows retained `_open` loads `kernel32`.
  No complete observation report is written and this case is not admitted.
  Retain the exact adapter outside the source tree, helper, candidate and
  selector inventory without replay, preload, substitution or guard change.
  The packet remains 8,891 nodes, 1,258 sources and 463 targets.
- [x] Qualify the separate original aggregate source-tree budget refusal
  function with both original file/byte parameters. Preserve its temporary
  inputs, preexisting digest-failure callback and original assertions.
  The selected refusal must occur before pool construction or file dispatch;
  do not export any successful tree/thread or wheel-open case.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before adding only the new
  adapter binding. Preserve all 8,891 nodes, 1,258 hashes, 463 targets,
  89 helper decisions and the 300-second bound. Normal strict checks,
  committed changefile presence, exact-head review and complete fresh Linux
  CI remain required. No replay of any refused case, new mock, preload,
  data admission, guard exception, native/runtime/routing or cleanup
  authority is added.
  Complete collection verifies all 47 original cases and exactly two adapter
  cases with original function and parameter-object identity. Initial
  unchanged auditing passes both in 1.969 seconds with zero prohibited
  effects, seventeen bound canonical reads and actual owner call-phase
  lines per case. Only the adapter binding is added; no target changes.
  Compact-v2 contains 8,893 nodes, 1,259 sources and 463 targets in 996,431
  bytes, preserving prior node order and all previous source hashes.
  #14095 merges at `bcc3242e7c0f5e7da778bb44b86b1a209b080f10`
  after exact-head approval and complete fresh Linux CI executes all 8,893
  selected units. Reviewed and merged trees match; only this two-case
  leaf is complete. The separate native-load refusal remains unadmitted.
- [x] Qualify the separate original ZIP32/ZIP64 directory-count boundary
  function with all three original entry counts: 65,534, 65,535 and 65,536.
  Preserve actual in-memory ZIP_STORED archives, the original limits fixture,
  twenty-second preflight deadlines, stream-reset assertion and reduced-count
  refusal. Do not shrink archives, change assertions or waive elapsed bounds.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before adding only the new
  adapter binding. Preserve all 8,893 nodes, 1,259 hashes, 463 targets,
  89 helper decisions and the 300-second packet bound. Normal strict checks,
  committed changefile presence, exact-head review and complete fresh Linux
  CI remain required. No replay of refused candidates, new mock, preload,
  guard exception, data admission, native/runtime/routing or cleanup
  authority is added.
  Complete collection verifies all 104 original cases and exactly three
  adapter cases with original function, fixture and parameter-object identity.
  Initial unchanged auditing passes all three in 11.734 seconds, with zero
  prohibited effects, nine bound canonical Python reads and actual
  preflight owner call-phase lines per case. Only the adapter binding is
  added; no coverage target changes. Compact-v2 contains 8,896 nodes,
  1,260 sources and 463 targets in 996,926 bytes, preserving prior node order
  and every previous source hash. #14097 merges at
  `85b93019f19c090f32a3bb8e1c5d0cc008244aef` after exact-head approval and
  complete fresh Linux CI executes all 8,896 selected units within the
  unchanged bound. Reviewed and merged trees match; only this three-case
  leaf is complete. All separate refusals and parent gates remain open.
- [x] Qualify the separate original historical-argv inspection function
  with all six combinations of `venv`/`.venv` and the three original products.
  Preserve the literal argument vectors, unused temporary-path fixture and
  original positive, foreign-root and unrelated-module assertions.
  Export only that pure `admits_argv` inspection, not stop, capture, process
  inventory, signaling, native pidfd, unit-writer or Bash execution cases.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before admission.
  Preserve all 8,896 nodes, 1,260 source hashes, 463 targets, 89 helper
  decisions and the 300-second bound. Normal strict checks, committed
  changefile presence, exact-head review and complete fresh Linux CI remain
  required. No replay of refused candidates, new mock, preload, guard
  exception, data admission, native/runtime/routing or cleanup authority
  is added.
  Complete collection verifies all 110 original cases and exactly six adapter
  cases with original function and parameter-object identity. Initial unchanged
  auditing passes all six in 1.094 seconds with zero prohibited effects,
  three bound canonical reads and actual `admits_argv` call-phase lines per
  case. Two Python bindings and the already hash-bound owner as a coverage
  target are added; no original test changes. Compact-v2 contains 8,902 nodes,
  1,262 sources and 464 targets in 997,820 bytes, preserving prior node order
  and every previous source hash. #14100 merges at
  `3b02f94b566ba051ff340b869bc0f951e81cc914` after exact-head approval and
  complete fresh Linux CI executes all 8,902 selected units within the
  unchanged bound. Concurrent #14098 advances the base and two independent
  typecheck source bindings; the reviewed and merged leaf have identical
  stable patch IDs. Only this six-case leaf is complete; no whole merged-tree
  CI identity or parent completion is inferred.
- [x] Qualify the separate original vault cache-policy inspection function
  with all three original `no-write`/`shared`/`legacy` parameters. Preserve
  literal argument vectors, hash-derived cache identity and the positive,
  foreign-root, unexpected-option, relative-root and foreign-cache assertions.
  Export only that pure `admits_argv` inspection, not stop, capture, process
  inventory, signaling, native pidfd, unit-writer or Bash execution cases.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before adding only the new
  adapter binding. Preserve all 8,902 nodes, the current 1,262 source hashes
  including #14098's independent rebindings, 464 targets, 89 helper decisions
  and the 300-second bound. Normal strict checks, committed changefile
  presence, exact-head review and complete fresh Linux CI remain required.
  No replay of refused candidates, new mock, preload, guard exception,
  data admission, native/runtime/routing or cleanup authority is added.
  Complete collection verifies all 110 original cases and exactly three
  adapter cases with original function and parameter-object identity.
  Initial unchanged auditing passes all three in 1.172 seconds, with zero
  prohibited effects, three bound canonical reads and actual `admits_argv`
  call-phase lines per case. Only the adapter binding is added; no coverage
  target changes. Compact-v2 contains 8,905 nodes, 1,263 sources and
  464 targets in 998,353 bytes, preserving prior node order and all current
  source hashes. #14102 merges at
  `e28321eb6c31746423a2546c571fdffdd332c907` after exact-head approval and
  complete fresh Linux CI executes all 8,905 selected units within the
  unchanged bound. Reviewed and merged trees match. Only this three-case
  leaf is complete; all parent acceptance and separate refusal gates remain.
- [x] Qualify the two separate original Python reconciliation early-return
  functions: implicit contained execution and missing runtime scope.
  Preserve their existing environment/subprocess monkeypatches, temporary-path
  and capture fixtures, return-value assertions and finite deferral diagnostic.
  Only the source adapter's early returns are selected; no shell, native
  wrapper, registry, runtime, daemon or cleanup execution is permitted.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before adding the adapter,
  original test binding and the already hash-bound owner as a target.
  Preserve all 8,905 nodes, 1,263 source hashes, 464 targets, 89 helper
  decisions and the 300-second bound. Normal strict checks, committed
  changefile presence, exact-head review and complete fresh Linux CI remain
  required. No replay of refused candidates, new mock, preload, guard
  exception, data admission, native/runtime/routing or cleanup authority
  is added.
  Complete collection verifies all 66 original cases and exactly two adapter
  cases with original function-object identity. Initial unchanged auditing
  passes both in 0.968 seconds, with zero prohibited effects, three bound
  canonical reads and owner call-phase coverage limited to early-return
  lines 16-23. Two Python bindings and one already hash-bound owner target
  are added. Compact-v2 contains 8,907 nodes, 1,265 sources and 465 targets
  in 998,922 bytes, preserving prior node order and all current source hashes.
  #14103 merges at `20812d3d5866f4908c4455cfc0a2c42b74d6082b` after
  exact-head approval and complete fresh Linux CI executes all 8,907 selected
  units within the unchanged bound. Concurrent #14086 advances the base:
  the reviewed and merged leaf retain identical stable patch IDs, and exact
  manifest composition preserves both this leaf and the peer's changes.
  The current combined packet has 1,267 sources and 467 targets in 999,376
  bytes. Only this two-case leaf is complete; the earlier exact-input CI
  is not claimed to have executed the entire later merged tree.
- [ ] Qualify the separate complete original invalid-freeze-deadline function,
  retaining all three original `0`/infinite/boolean parameters, the original
  engine fixture, temporary paths and absent-source/destination assertions.
  Require refusal before input discovery, tree reads, file creation, copying,
  native calls or candidate construction; do not select ordinary/plugin or
  guardian fixtures. Complete original/adapter collection identity, initial
  auditing under the unmodified current source guard and actual owner calls
  precede admission. Preserve all 8,907 nodes, the current 1,267 source hashes
  including #14086's independent rebindings, 467 targets, 89 helper decisions
  and the 300-second bound. Normal strict checks, committed changefile
  presence, exact-head review and complete fresh Linux CI remain required.
  No replay of refused candidates, new mock, preload, guard exception,
  data admission, native/runtime/routing or cleanup authority is added.
  Complete collection verifies all 65 original cases and exactly three
  adapter cases with unchanged original function, fixture and parameters.
  The initial current-guard execution refuses setup of the first case:
  the original fixture imports the unadmitted canonical source
  `plugins/agent-worktrees/scripts/versioned_runtime.py` through
  `installer_engine.py:31`. The other two cases remain unexecuted.
  No manifest or guard is changed. The refused adapter, helper, candidate,
  node list and refusal evidence are retained without replay; this leaf
  remains blocked under the existing data-admission owner.
- [x] Qualify the separate three complete original deferred-dispatch recovery
  controls: verified merge completion recovers a suppressed advance, a resume
  merge drains paused source without another push, and retirement never
  automatically retries unchanged source. Preserve the original in-memory
  backend, both retirement assertions and all decision/reason assertions.
  Complete collection verifies all 102 original cases and exactly three
  adapter cases with original function-object identity. Initial unchanged
  auditing passes all three in 0.953 seconds with zero prohibited effects,
  six bound canonical Python reads and actual owner call-phase coverage.
  Add only the adapter and original-module bindings, retaining 8,907 prior
  nodes in order, all 1,267 existing source hashes, 467 targets, 89 helper
  decisions and the 300-second bound. Compact-v2 contains 8,910 nodes and
  1,269 sources in 999,982 bytes. Normal strict checks, committed release
  intent, exact-head review and complete fresh Linux CI remain required.
  This source-only leaf makes no dispatch, Main, workflow, runtime, native,
  guard or cleanup change and does not settle retained refusals.
  #14105 merges at `870876e40c683d6fef438d8c2b747786aeebfb41` after
  exact-head approval and complete fresh Linux CI executes all 8,910 selected
  units in 184.659 packet seconds within the unchanged bound. Reviewed and
  merged trees match. Only this three-case leaf is complete; the parent and
  the separate invalid-deadline import refusal remain open.
- [x] Qualify the separate five complete original deferred-dispatch
  admission/history controls: actual Gate attempt binding, all job-attempt
  pages, closed-history pagination and candidate binding, readback mismatch
  refusal, and the ten-page inventory ceiling. Preserve the existing
  in-memory backend/request callbacks, literal 101-row inventories,
  both attempt assertions, exact page/cursor sequences, mismatch diagnostic
  and ten-call bound. Require complete original/adapter collection identity,
  initial unchanged source/effect auditing and actual owner calls before
  adding only a new adapter binding. Preserve all 8,910 nodes, 1,269 current
  source hashes, 467 targets, 89 helper decisions and the 300-second bound.
  Normal strict checks, committed release intent, exact-head review and
  complete fresh Linux CI remain required. This is source-only qualification,
  not actual workflow dispatch, Main writes or artifact admission; it adds
  no new mock, preload, data/guard exception, native/runtime/routing or
  cleanup authority and never replays retained refused candidates.
  Complete collection verifies all 102 original cases and exactly five
  adapter cases with original function-object identity. Initial unchanged
  auditing passes all five in 1.015 seconds with zero prohibited effects,
  six bound canonical Python reads and actual admission/dispatch owner
  call-phase coverage. Only the adapter binding is added; no target changes.
  Compact-v2 contains 8,915 nodes and 1,270 sources in 1,000,644 bytes,
  preserving prior node order and all current source hashes. #14106 merges
  at `f80b5c109f43c5b82e3bec919dd68a47f502e865` after exact-head approval
  and complete fresh Linux CI executes all 8,915 selected units in 227.499
  packet seconds within the unchanged bound. Reviewed and merged trees
  match. Only this five-case leaf is complete; all parent acceptance and
  separate refusal gates remain open.
- [x] Qualify three separate complete original admission/history parameterized
  functions: recorded Gate status/conclusion decisions (six cases),
  unavailable/malformed admission evidence (five cases), and incomplete or
  malformed retirement connections (eight cases). Preserve all 19 original
  parameters, in-memory backend/request callbacks, diagnostic assertions and
  the entire errors/missing/truncated/state/merge/duplicate/cursor coverage.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before adding only a new
  adapter binding. Preserve all 8,915 nodes, 1,270 current source hashes,
  467 targets, 89 helper decisions and the 300-second bound. Normal strict
  checks, committed release intent, exact-head review and complete fresh Linux
  CI remain required. No original assertion, fixture or OS skip is changed;
  no new mock, preload, data/guard exception, actual Main/workflow/runtime,
  native/routing or cleanup authority is added. Never replay a retained
  refused candidate.
  Complete collection verifies all 102 original cases and exactly 19 adapter
  cases, including exact function objects and parameters from each original
  parametrization. Initial unchanged auditing passes all 19 in 1.109 seconds
  with zero prohibited effects, six bound canonical Python reads and actual
  admission/dispatch owner call-phase coverage per case. Only the adapter
  binding is added; no target changes. Compact-v2 contains 8,934 nodes and
  1,271 sources in 1,002,798 bytes, preserving prior node order and all current
  source hashes. #14107 merges at
  `63923a0cebe59c4b0c2c4b6fcc19268dc994d2cd` after exact-head approval and
  complete fresh Linux CI executes all 8,934 selected units in 141.097 packet
  seconds within the unchanged bound. An explicitly authorized, one-PR
  metadata refresh reconciles a stale direct attribution result before the
  required-check verifier and normal merge. Intervening independently merged
  source/hash changes are retained; the reviewed adapter and node additions
  match. Only this 19-case leaf is complete; all parent gates remain open.
- [x] Qualify two separate complete original artifact-admission integrity
  functions: six timestamp/workflow/attempt/duplicate/upload-evidence mutations
  and three foreign-branch/workflow/legacy-step decisions. Preserve all nine
  original parameters, in-memory backend/request callbacks and assertions.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before adding only a new
  adapter binding. Preserve all 8,934 nodes, 1,271 current source hashes,
  467 targets, 89 helper decisions and the 300-second bound. Normal strict
  checks, committed release intent, exact-head review and complete fresh Linux
  CI remain required. No original assertion, fixture or OS skip is changed;
  no new mock, preload, data/guard exception, actual Main/workflow/runtime,
  native/routing or cleanup authority is added. Never replay a retained
  refused candidate or reuse the consumed metadata-refresh authorization.
  Complete collection verifies all 102 original cases and exactly nine adapter
  cases with original function-object and parameter identity. Initial unchanged
  auditing passes all nine in 1.437 seconds with zero prohibited effects,
  six bound canonical Python reads and actual admission-owner call-phase
  coverage per case. Only the adapter binding is added; no target changes.
  Compact-v2 contains 8,943 nodes and 1,272 sources in 1,003,943 bytes,
  preserving prior node order and all current source hashes. #14112 merges at
  `fc2f362081ce09a58e7d84429c70970c33a08c8b` after exact-head approval and
  complete fresh Linux CI executes all 8,943 selected units in 164.193 packet
  seconds within the unchanged bound. Reviewed and merged trees match.
  Only this nine-case leaf is complete; all parent gates remain open.
- [x] Qualify five separate complete original source-event refusal functions:
  pending/failed current CI (four cases), untrusted or unrelated CI (three),
  noncanonical promoter completion (four), drifting or incomplete closed-PR
  evidence (five), and malformed pipeline state (three). Preserve all 19
  original parameters, in-memory backend/request callbacks and assertions.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before adding only a new
  adapter binding. Preserve all 8,943 nodes, 1,272 current source hashes,
  467 targets, 89 helper decisions and the 300-second bound. Normal strict
  checks, committed release intent, exact-head review and complete fresh Linux
  CI remain required. No original assertion, fixture or OS skip is changed;
  no new mock, preload, data/guard exception, actual Main/workflow/runtime,
  native/routing or cleanup authority is added. Never replay a retained
  refused candidate or reuse the consumed metadata-refresh authorization.
  Complete collection verifies all 102 original cases and exactly 19 adapter
  cases with original function-object and parameter identity. Initial unchanged
  auditing passes all 19 in 1.093 seconds with zero prohibited effects,
  six bound canonical Python reads and actual dispatch-owner call-phase
  coverage per case. Only the adapter binding is added; no target changes.
  Compact-v2 contains 8,962 nodes and 1,273 sources in 1,005,736 bytes,
  preserving prior node order and all current source hashes. #14114 merges at
  `9aea321ce3dc00afd1064498504e78dda0f966bd` after exact-head approval and
  complete fresh Linux CI executes all 8,962 selected units in 178.437 packet
  seconds within the unchanged bound. Reviewed and merged trees match.
  Only this 19-case leaf is complete; all parent gates remain open.
- [x] Qualify 17 separate complete original non-CLI dispatch-decision
  functions (45 cases): source-CI evidence and ordering, terminal-promoter
  reconciliation, retry/no-op/coalescing decisions, pause/resume behavior,
  retirement history, invalid input, fork provenance and exact source/base
  ownership across automatic wakes. Preserve all original function objects,
  multiple parameter decorators, in-memory backend/request callbacks and
  assertions. Keep both original CLI functions outside this selection.
  Require complete original/adapter collection identity, initial unchanged
  source/effect auditing and actual owner calls before adding only a new
  adapter binding. Preserve all 8,962 nodes, 1,273 current source hashes,
  467 targets, 89 helper decisions and the 300-second bound. Normal strict
  checks, committed release intent, exact-head review and complete fresh Linux
  CI remain required. No original assertion, fixture or OS skip is changed;
  no new mock, preload, data/guard exception, actual Main/workflow/runtime,
  native/routing or cleanup authority is added. Never replay a retained
  refused candidate or reuse the consumed metadata-refresh authorization.
  Complete collection verifies all 102 original cases and exactly 45 adapter
  cases with original function-object and parameter identity, including every
  stacked decorator. Initial unchanged auditing passes all 45 in 1.531 seconds
  with zero prohibited effects, six bound canonical Python reads and actual
  dispatch-owner call-phase coverage per case. Only the adapter binding is
  added; no target changes. Compact-v2 contains 9,007 nodes and 1,274 sources
  in 1,010,396 bytes, preserving prior node order and all current source hashes.
  #14116 merges at `ffe137243bf345c9cf1ee57d20a4aed6b10e977b` after exact-head
  approval and complete fresh Linux CI executes all 9,007 selected units in
  134.840 packet seconds within the unchanged bound. Reviewed and merged trees
  match. Only this 45-case leaf is complete; all parent gates remain open.
- [x] Qualify the two complete original dispatch CLI functions alongside
  eleven complete original lifecycle inventory/admission functions (30 cases):
  complete/empty inventory, producing-run independence, exact candidate reuse,
  foreign/nonpromotion/publisher rules, invalid rows/pages/repositories,
  complete bounded pagination and moving-history refusal. Preserve all 32
  original cases, function objects, parameters, original request callbacks,
  fixture-local outputs and assertions. Keep lifecycle CLI and real isolated
  subprocess tests outside this selection. Require complete original/adapter
  collection identity, initial unchanged source/effect auditing and actual
  lifecycle-owner call-phase coverage before adding only the two adapter
  bindings and original lifecycle-test source binding. Preserve all 9,007 nodes,
  1,274 current source hashes, 467 targets, 89 helper decisions and the
  300-second bound. Normal strict checks, committed release intent, exact-head
  review and complete fresh Linux CI remain required. No original assertion,
  fixture, OS skip, mock, preload, data/guard exception, actual Main/workflow,
  runtime/native/routing or cleanup change is authorized. Never replay a
  retained refusal or reuse the consumed metadata-refresh authorization.
  Complete collection verifies all 102 original dispatch and 34 original
  lifecycle cases, with exactly two and 30 adapter cases retaining original
  function-object, parameter-object, value/type and node identity. Initial
  unchanged auditing passes all 32 in 1.234 seconds, with zero prohibited
  effects, eight bound canonical Python reads and actual lifecycle-owner
  call-phase coverage per case; all 96 phase reports pass. Only the three
  planned source bindings are added, with no target changes. Compact-v2
  contains 9,039 nodes and 1,277 sources in 1,013,461 bytes, preserving prior
  node order and current hashes. Fatal Ruff and whole-file strict Linux/Win32
  typing pass. #14117 merges at `e186fb46ca4c4fe969a973041404c1da2727fe8e`
  after exact-head approval and complete fresh Linux CI executes all 9,039
  selected units in 180.472 packet seconds within the unchanged bound. One
  explicitly authorized, PR-scoped metadata refresh resolves a superseded
  source-attribution failure before all seven verifier reads and managed merge.
  Independent base #14119 contributes the complete reviewed/merged-tree delta;
  all 9,039 nodes and this slice's five nonmanifest blobs remain identical.
  Its one additional source and one existing-source rebind yield 1,278 current
  bindings in 1,013,579 bytes. Only this 32-case leaf is complete; all parent
  gates and retained refusals remain open.
- [x] Qualify the three complete original lifecycle CLI functions: status
  blocks dispatch while an open promotion retains ownership, exact existing
  candidate opening remains non-idle, and transport failure is visible without
  writing success output. Preserve original functions, callbacks, parameters,
  monkeypatch, fixture-local files, capture assertions and complete 34-case
  original collection identity. Keep the real isolated subprocess test outside
  this selection. Require initial unchanged source/effect auditing and actual
  lifecycle-owner call-phase coverage before adding only a new adapter binding.
  Preserve all 9,039 nodes, 1,278 current source hashes, 467 targets, 89 helper
  decisions and the 300-second bound. Normal strict checks, committed release
  intent, exact-head review and complete fresh Linux CI remain required.
  No original assertion, fixture, OS skip, new mock, preload, data/guard
  exception, workflow, runtime/native/routing or cleanup change is authorized.
  Both prior PR-specific metadata-refresh authorizations are consumed; neither
  licenses a later dispatch or refusal replay.
  Complete collection verifies all 34 original cases and exactly three adapter
  cases with original function and unparameterized node identity. Initial
  unchanged auditing passes all three in 1.359 seconds, with zero prohibited
  effects, six bound canonical Python reads and actual lifecycle-owner
  call-phase coverage per case; all nine phase reports pass. Only the adapter
  binding is added. Compact-v2 contains 9,042 nodes and 1,279 sources in
  1,014,063 bytes, preserving prior order and every current hash. Fatal Ruff
  and whole-file strict Linux/Win32 typing pass. #14121 merges at
  `a21bd7205552bd0de714bacfd6c6a2ac39f6a5a8` after exact-head approval and
  complete fresh Linux CI executes all 9,042 selected units in 143.895 packet
  seconds within the unchanged bound. Reviewed and merged trees match.
  Only this three-case leaf is complete; the parent, separate refusals and
  native qualifications remain open.
- [x] Qualify the two complete original control-manifest refusal functions:
  fourteen nonportable/noncontrol path cases and nine malformed/ambiguous
  schema cases. Preserve original functions, parameter objects, literal
  malformed bytes, helper calls and assertions; compare complete 46-case
  original collection with exactly 23 adapter nodes. Keep all real-Git,
  candidate mutation and workflow-recipe cases outside the selection.
  Require initial unchanged source/effect auditing and actual control-owner
  call-phase coverage before adding only a new adapter and original-test
  source binding. Preserve all 9,042 nodes, 1,279 current source hashes,
  467 targets, 89 helper decisions and the 300-second bound. Normal strict
  checks, committed release intent, exact-head review and complete fresh Linux
  CI remain required. No original assertion, fixture, OS skip, new mock,
  preload, data/guard exception, workflow, runtime/native/routing or cleanup
  change is authorized. Do not replay a retained refusal or reuse either
  consumed PR-specific metadata-refresh grant.
  Complete collection verifies all 46 original cases and exactly 23 adapter
  cases with original function, parameter-row object, type and node identity.
  Initial unchanged auditing passes all 23 in 1.140 seconds, with zero
  prohibited effects, eight bound canonical Python reads and actual
  control-owner call-phase coverage per case; all 69 phase reports pass.
  Only the adapter and original-test source bindings are added. Compact-v2
  contains 9,065 nodes and 1,281 sources in 1,016,683 bytes, preserving prior
  node order, source hashes and all targets. Fatal Ruff, whole-file strict
  Linux/Win32 typing and documentation checks pass. #14123 merges at
  `b315b0441dc0feb091cb908bd226af196f33e2f2` after exact-head approval and
  complete fresh Linux CI executes all 9,065 selected units in 235.563 packet
  seconds within the unchanged bound. Reviewed and merged trees match.
  Only this 23-case leaf is complete; the parent, separate refusals and
  native qualifications remain open.
- [x] Qualify the four complete original source-owned artifact-index tests:
  equivalent composition across Git containers, changed source/composition
  refusal, legacy parent binding and rejection of a second container identity.
  Preserve original functions, parameter objects, temporary wheel/bundle/index
  helpers and assertions; compare complete 16-case original collection with
  exactly five adapter nodes. Keep all real-Git, Windows-native and publisher
  calls outside this selection. Require initial unchanged source/effect
  auditing and actual artifact-owner call-phase coverage before adding only
  a new adapter and original-test source binding. Preserve all 9,065 nodes,
  1,281 current source hashes, 467 targets, 89 helper decisions and the
  300-second bound. Normal strict checks, committed release intent, exact-head
  review and complete fresh Linux CI remain required. No original assertion,
  fixture, OS skip, new mock, preload, data/guard exception, workflow,
  runtime/native/routing or cleanup change is authorized. Do not replay a
  retained refusal or reuse either consumed metadata-refresh grant.
  Complete collection verifies all 16 original cases and exactly five adapter
  cases with original function, parameter-row object, type and node identity.
  Initial unchanged auditing passes all five in 2.656 seconds, with zero
  prohibited effects, forty bound canonical Python reads and actual
  artifact-owner call-phase coverage per case; all fifteen phase reports pass.
  Only the adapter and original-test source bindings are added. Compact-v2
  contains 9,070 nodes and 1,283 sources in 1,017,444 bytes, preserving prior
  node order, source hashes and all targets. Fatal Ruff, whole-file strict
  Linux/Win32 typing and documentation checks pass. Initial CI executes all
  9,070 cases within the packet limit but exceeds the separate five-minute
  job limit before evidence upload; that cancellation remains recorded.
  One explicitly authorized job-scoped retry at unchanged head executes all
  9,070 cases afresh in 145.023 packet seconds and passes complete required CI,
  without changing either limit. #14125 merges at
  `165e6e206994266da8bb842fefcf5e07244d8933` after exact-head approval;
  reviewed and merged trees match. Only this five-case leaf is complete;
  the parent, separate refusals and native qualifications remain open.
- [ ] Qualify the complete original thirteen-method construction-assessment
  class, preserving its original class, methods, setup, synthetic in-memory
  helpers, loops, subtests and assertions. Compare complete 22-case original
  collection with exactly thirteen adapter nodes. Keep the nine-method
  canonical-policy-reading native-matrix sibling outside this selection.
  Require initial unchanged source/effect auditing and actual assessment-owner
  call-phase coverage before adding only adapter, original-test and production
  source bindings plus the production coverage target. Preserve all 9,070
  nodes, 1,283 current source hashes, 467 targets, 89 helper decisions and the
  300-second bound. Normal strict checks, committed release intent, exact-head
  review and complete fresh Linux CI remain required. Synthetic native labels
  do not execute or qualify native behavior. No original assertion, fixture,
  OS skip, new mock, preload, data/guard exception, workflow, runtime/native/
  routing or cleanup change is authorized. All prior retry and metadata-refresh
  grants are consumed; a retained refusal is not replayed.
  Complete collection verifies all 22 original cases and exactly thirteen
  adapter cases with original class, method and setup identity. Initial
  unchanged execution reports thirteen passing tests and 121 passing subtests,
  then the existing evidence verifier refuses the phase stream as
  `duplicate or unexpected unit phase`. Test success is not packet admission:
  no completed observation or per-node owner-coverage proof is produced.
  All thirteen remain unqualified; the manifest and guard remain unchanged.
  The exact adapter is retained outside source discovery with its candidate,
  helper and refusal evidence. No subtest filtering, assertion change,
  phase-contract waiver or replay is authorized.
- [x] Qualify the 23 original fixture-only construction-inventory methods,
  exporting the complete original thirty-method class without subclassing or
  method substitution. Use exact positive node selectors; leave the five
  subtest-emitting methods and the real symlink and real Git/CLI methods
  outside the initial selection. Preserve original setup, cleanup, fixture
  writes, patches and assertions. Compare all thirty original and adapter
  collection identities before the initial unchanged source/effect audit.
  Require actual inventory-owner call-phase coverage for all 23 nodes before
  adding only adapter, original-test and production source bindings plus the
  production target. Preserve all 9,070 prior nodes, 1,283 source hashes,
  467 targets, 89 helper decisions and the 300-second bound. Normal strict
  checks, committed release intent, exact-head review and complete fresh Linux
  CI remain required. This is not a replay or repair of the separately
  refused assessment class, native qualification or a phase-contract change.
  Complete collection verifies all thirty original and adapter nodes with
  original class, method, setup and fixture-helper identity; exactly 23 are
  selected. Initial unchanged auditing passes all 23 in 4.454 seconds, with
  zero prohibited effects, only three bound canonical Python reads and actual
  inventory-owner call-phase coverage per case. All 69 phase reports pass.
  Compact-v2 contains 9,093 nodes and 1,286 sources in 1,020,285 bytes,
  retaining all prior node order and source hashes and adding only the
  production target ahead of the prior 467. Fatal Ruff, whole-file strict
  Linux/Win32 typing and documentation checks pass. #14128 merges at
  `75df360b0ca23286d29702006d467bff35ae1127` after exact-head approval and
  complete fresh Linux CI executes all 9,093 selected units in 141.700 packet
  seconds within the unchanged bound. Reviewed and merged trees match.
  Only this 23-case leaf is complete; the separate assessment refusal,
  native qualifications, release/owner/custody gates and parent remain open.
- [x] Qualify the complete original 22-case compatibility-root migration
  scanner tests against their synthetic plugin trees. Preserve original
  functions, the original repository-redirection fixture, helper bodies,
  captured diagnostics and assertions. Compare complete original and adapter
  collection identity before initial unchanged source/effect auditing and
  actual scanner-owner call-phase coverage for every case. Add only adapter,
  original-test and production source bindings plus the production target;
  preserve all 9,093 prior nodes, 1,286 source hashes, 468 prior targets,
  89 helper decisions and the 300-second bound. Normal strict checks,
  committed release intent, exact-head review and complete fresh Linux CI
  remain required. No real repository scan, assertion/fixture change, phase
  contract change, retained-case replay, native execution or workflow/runtime/
  routing/cleanup change is authorized.
  Complete collection verifies all 22 original and adapter nodes with original
  function and repository-redirection fixture identity. Initial unchanged
  auditing passes all 22 in 1.844 seconds, with zero prohibited effects,
  three bound canonical Python reads and actual scanner-owner call-phase
  coverage per case; all 66 phase reports pass. Compact-v2 contains 9,115
  nodes and 1,289 sources in 1,022,181 bytes, retaining all prior node order,
  source hashes and targets and adding one production target. Fatal Ruff,
  whole-file strict Linux/Win32 typing and documentation checks pass. #14131
  merges at `15912163e4020dd1c11b896c16e2354d882782c7` after exact-head
  approval and complete fresh Linux CI executes all 9,115 selected units in
  154.522 packet seconds within the unchanged bound. Reviewed and merged
  trees match. Only this 22-case leaf is complete; the parent, separate
  refusals and native qualifications remain open.
- [x] Qualify the complete original static reproducer-boundary test file:
  all 21 original functions, parameter-mark rows, synthetic dictionaries,
  helpers, loops and assertions remain unchanged. Compare complete original
  and adapter collection and exact function/parameter identity. Static
  declaration parsing and inspection of supplied dictionaries is not Docker
  execution, provisioning, measured capacity or resource custody. Require
  initial unchanged source/effect auditing and actual boundary-owner
  call-phase coverage per selected case before adding only adapter,
  original-test and production Python source bindings plus the production
  target. Preserve all 9,115 nodes, 1,289 current source hashes, 469 prior
  targets, 89 helper decisions and the 300-second bound. Normal strict checks,
  committed release intent, exact-head review and complete fresh Linux CI
  remain required. No existing test, phase contract, effect guard, data
  admission, workflow, runtime, native policy, routing or cleanup changes;
  no retained refusal or consumed retry is replayed.
  Complete original/adapter collection confirms 170 cases, all 21 original
  function objects, each original stacked-mark parameter-row object and equal
  cross-module parameter values/types. Initial unchanged auditing passes all
  170 in 2.312 seconds with zero prohibited effects, only three bound canonical
  Python reads and actual boundary-owner call-phase coverage per case; all
  510 setup/call/teardown reports pass. Compact-v2 contains 9,285 nodes and
  1,292 sources in 1,035,062 bytes. All prior nodes retain order, all current
  source hashes remain intact and one production target precedes the 469
  unchanged targets. Fatal Ruff, whole-file strict Linux/Win32 typing and
  documentation consistency pass. #14132 merges at
  `8adad1841eab283bc4b6411c3118ece658313044` after exact-head approval and
  complete fresh Linux CI executes all 9,285 selected units in 199.759 packet
  seconds within the unchanged bound. Reviewed and merged trees match.
  Only this 170-case leaf is complete; the parent, separate refusals and
  native qualifications remain open.
- [x] Qualify seven complete original synthetic network-inventory functions:
  exact physical inventory, single subject, unsafe network, missing fields,
  peer attachments, bounded distinct selections and malformed inventory.
  Compare all 47 original cases with the expected 43 selected cases, retaining
  original functions, parameter rows, helpers and assertions. Exclude the four
  reader cases before execution: their POSIX-shaped selected executable paths
  do not satisfy Windows absolute-path validation. Do not patch those paths
  or execute an observer. Require initial unchanged effect/source auditing and
  actual network-owner call-phase coverage per selected case before adding
  only the adapter, original test and its canonical import-source closure.
  Preserve all 9,285 nodes, 1,292 source hashes, 470 prior targets, 89 helper
  decisions and the 300-second bound. Require strict checks, committed release
  intent, exact-head review and complete fresh Linux CI. Synthetic attachment
  dictionaries are not Docker, network, egress, custody or capacity proof;
  no retained refusal, guard exception, native lane or consumed retry is replayed.
  Complete collection confirms all 47 original cases, exactly 43 adapter
  cases, original function/parameter-row identity and equal cross-module
  parameter values/types. Initial unchanged auditing passes all 43 in 1.562
  seconds with zero prohibited effects, thirteen bound canonical Python reads
  and actual network-owner call-phase coverage per case; all 129 reports pass.
  Eleven source bindings and one production target are added; Compact-v2
  contains 9,328 nodes and 1,303 sources in 1,039,347 bytes, preserving prior
  node/target order and every current hash. Fatal Ruff, whole-file strict
  Linux/Win32 typing and documentation consistency pass. #14134 merges at
  `842e6ac8aaa9257308341c01897eaac7a89b3dad` after exact-head approval and
  complete fresh Linux CI executes all 9,328 selected units in 183.918 packet
  seconds within the unchanged bound. Reviewed and merged trees match.
  Only this 43-case leaf is complete; the parent, separate refusals and
  native qualifications remain open.
- [x] Qualify five complete original in-memory storage-monitor functions:
  resume freshness, exact thresholds, cadence/lifetime/loss, invalid
  measurement invalidation and finite bounded policy. Compare all 64 original
  cases with the expected 19 selected cases, retaining original functions,
  parameter rows, helpers and assertions. Exclude all reader, guest, native
  and process cases before execution. Require initial unchanged effect/source
  auditing and actual storage-owner call-phase coverage per selected case.
  Add only one adapter source binding and the already-bound production
  storage module as a coverage target; preserve all 9,328 nodes, 1,303 current
  hashes, 471 targets, 89 helper decisions and the 300-second bound.
  Require strict checks, committed release intent, exact-head review and
  complete fresh Linux CI. Supplied timestamps and usage dataclasses are not
  real storage measurement, quota, custody or capacity proof. No native lane,
  held refusal, guard exception or consumed retry is replayed.
  Complete collection confirms all 64 original cases, exactly 19 adapter
  cases, original function/parameter-row identity and equal cross-module
  values/types, treating the unchanged NaN input as NaN rather than equality
  to itself. Initial unchanged auditing passes all 19 in 1.562 seconds with
  zero prohibited effects, twelve bound canonical Python reads and actual
  storage-owner call-phase coverage per case; all 57 phase reports pass.
  One adapter binding and one already-bound production coverage target are
  added. Compact-v2 contains 9,347 nodes and 1,304 sources in 1,040,977 bytes,
  preserving prior node/target order, every current hash, all 89 helper
  decisions and the 300-second bound. Fatal Ruff, whole-file strict Linux/Win32
  typing and documentation consistency pass. #14136 merges at
  `66c6a452e4ede024aea4df5be24458f0188bd046` after exact-head approval and
  complete fresh Linux CI executes all 9,347 selected units in 144.159 packet
  seconds within the unchanged bound. Reviewed and merged trees match.
  Only this 19-case leaf is complete; the parent, separate refusals and
  native qualifications remain open.
- [x] Qualify eight complete original pure mount-posture functions:
  effective evidence, unsafe/ambiguous posture, shadowed/nested mounts,
  exact record ceilings, wire/root fields, exact capacities and duplicate
  roots/extra fields. Compare all 60 original cases with the expected 43
  selected cases, retaining function/parameter-row identity, helpers,
  synthetic tables, loops and assertions. Exclude all reader/guest/native
  cases and the complete four-case long-table function before execution;
  its unchanged case identifiers do not fit the remaining manifest bound.
  Never truncate or relabel its rows. Require initial unchanged effect/source
  auditing and actual mounts-owner call-phase coverage per selected case.
  Add only adapter/original-test bindings and the already-bound production
  mounts module as a coverage target; preserve all 9,347 nodes, 1,304 current
  hashes, 472 targets, 89 helper decisions, the 300-second deadline and the
  existing manifest byte bound. Require strict checks, committed release
  intent, exact-head review and complete fresh Linux CI. Supplied mount
  tables and dictionaries are not real mount, quota, custody or capacity
  proof; no native lane, held refusal, guard exception or consumed retry
  is replayed.
  Complete collection confirms all 60 original cases, exactly 43 adapter
  cases, original function/parameter-row identity and equal cross-module
  parameter values/types. Initial unchanged auditing passes all 43 in 1.422
  seconds with zero prohibited effects, thirteen bound canonical Python
  reads and actual mounts-owner call-phase coverage per case; all 129
  phase reports pass. Two source bindings and one production target are
  added. Compact-v2 contains 9,390 nodes and 1,306 sources in 1,044,996 bytes,
  preserving prior node/target order, every current hash, all 89 helper
  decisions and the 300-second bound. Fatal Ruff, whole-file strict Linux/Win32
  typing and documentation consistency pass. #14137 merges at
  `6ef3cf8a01df7e03a3ef9e97ce4ced65d2cd8c6d` after exact-head approval and
  complete fresh Linux CI executes all 9,390 selected units in 155.482 packet
  seconds within the unchanged bound. A late attribution result refuses
  merge; one newly operator-authorized, PR-scoped metadata refresh passes
  before fresh final verification and normal managed merge. That grant is
  consumed. Independent #14035/#14044 base changes are retained separately:
  our non-manifest files and all ordered nodes/targets match the reviewed
  head; only their native-daemon source binding changes in the manifest.
  Every current hash verifies, but our earlier CI does not qualify those
  peer changes. Only this 43-case leaf is complete; the parent, separate
  refusals and native qualifications remain open.
- [x] Qualify six complete original pure offline-inspection functions:
  evidence-only observation, kernel-record ceilings, loopback host routes,
  volatile counters, actual no-route results and incomplete/changed fields.
  Compare all 63 original cases with the expected nine selected cases,
  preserving function/mark-row identity, helpers, supplied dictionaries,
  routes, loops and assertions. Exclude boundary-only, reader, guest, socket,
  file/snapshot and long-parameter functions before execution. Require
  initial unchanged effect/source auditing and actual offline-owner
  call-phase coverage per case. Add only adapter/original-test bindings
  and the already-bound production offline module as a coverage target;
  preserve all 9,390 nodes, 1,306 current hashes, 473 targets, 89 helper
  decisions, the 300-second deadline and manifest byte bound. Require strict
  checks, committed release intent, exact-head review and complete fresh
  Linux CI, including the independent source rebinding. Supplied route and
  namespace dictionaries are not real network, offline isolation, socket,
  custody or native proof. No held refusal, guard exception, native lane or
  consumed retry/metadata grant is replayed.
  Complete collection confirms all 63 original cases, exactly nine adapter
  cases, original function/mark-row identity and equal cross-module parameter
  values/types. Initial unchanged auditing passes all nine in 1.297 seconds
  with zero prohibited effects, thirteen bound canonical Python reads and
  actual offline-owner call-phase coverage per case; all 27 phase reports
  pass. Two source bindings and one production target are added. Compact-v2
  contains 9,399 nodes and 1,308 sources in 1,046,192 bytes, preserving prior
  node/target order, every current hash including the peer rebinding, all
  89 helper decisions and the 300-second bound. Fatal Ruff, strict Linux/Win32
  typing and documentation consistency pass. #14141 merges at
  `c09c89ae89a68d5c9053dc3c52f32a42fcf7dc4d` after exact-head approval and
  complete fresh Linux CI executes all 9,399 selected units in 145.027 packet
  seconds. Attempt one passes its tests but hits the five-minute job limit
  before evidence upload; one explicitly authorized single-job retry passes
  with normal upload and aggregate. That grant is consumed. Our non-manifest
  files and ordered nodes/targets match the reviewed head; four source hashes
  are independently rebound by peer #14145. All current hashes verify, but
  prior CI does not qualify those peer changes. Only this nine-case leaf is
  complete; the parent, separate refusals and native qualifications stay open.
- [x] Qualify three complete original offline declaration/static-boundary
  functions as eleven cases: explicit/default modes, all unknown modes and
  retained unresolved gates. These functions call the boundary owner, not the
  offline observation owner; they were excluded from that separate nine-case
  selection before execution, not refused or rewritten. Compare all 63
  original cases with eleven adapter cases, preserving function/mark-row
  identity, helpers, parameters and assertions. Exclude the other sixteen
  functions / 52 cases before execution. Require initial unchanged
  effect/source auditing and actual boundary-owner call-phase coverage per
  case. Add only the adapter binding, with no new target: the original test,
  production boundary and its target are already bound. Preserve all 9,399
  nodes, 1,308 current hashes including the four peer rebindings, 474 targets,
  89 helper decisions, the 300-second deadline and manifest byte bound.
  Require strict checks, committed release intent, exact-head review and
  complete fresh Linux CI. Supplied declarations/inspection dictionaries
  establish neither real Docker posture nor network isolation, capacity,
  custody or native proof. No held refusal, guard exception, native lane or
  consumed retry/metadata grant is replayed.
  Complete collection confirms all 63 original cases, exactly eleven adapter
  cases, original function/mark-row identity and equal cross-module parameter
  values/types. Initial unchanged auditing passes all eleven in 1.250 seconds
  with zero prohibited effects, thirteen bound canonical Python reads and
  actual boundary-owner call-phase coverage per case; all 33 phase reports
  pass. One source binding and no target are added. Compact-v2 contains
  9,410 nodes and 1,309 sources in 1,047,313 bytes, preserving prior order,
  every current hash including the four peer rebindings, 474 targets,
  89 helper decisions and the 300-second bound. Fatal Ruff, strict Linux/Win32
  typing and documentation consistency pass. #14151 merges at
  `ef16da00f849c6386b25ec18661afdb933cff439` after exact-head approval and
  complete fresh Linux CI executes all 9,410 selected cases in 189.817 packet
  seconds on attempt one, with normal upload and required aggregate.
  Independent peer #14144 adds four cases, three source bindings, one target
  and five source rebindings. All current hashes verify and all preceding
  nodes retain order, but this leaf's earlier CI does not qualify those peer
  changes. Only this eleven-case leaf is complete; the parent, separate
  refusals and native qualifications stay open.
- [x] Resolve the compact-v2 storage frontier before the next original
  state-mount selection. At this boundary the merged packet contains 9,414 nodes,
  1,312 sources and 475 targets in 1,048,109 bytes, leaving 467 bytes under
  the unchanged 1 MiB cap. Collect-only research identifies three complete
  original pure mount functions / six cases, but their adapter and canonical
  import closure would require 1,048,931 bytes, 355 above that cap.
  No adapter, qualification execution or admission is started. Preserve all
  peer additions and existing gates; obtain the operator's next source
  approach rather than increasing limits, truncating identifiers, dropping
  witnesses or silently changing the selected representation.
  The operator subsequently selects compacting only existing v2 formatting.
  Replacing two-space with one-space leading indentation reduces the manifest
  from 1,048,109 to 1,001,489 bytes, reclaiming 46,620 bytes and leaving
  47,087 bytes under the unchanged cap. Direct line comparison proves every
  non-leading byte unchanged; actual reader normalization preserves all
  9,414 nodes, 1,312 source bindings, 475 targets, every value/order and
  logical digest. All current hashes and 89 helper decisions verify and
  the 300-second bound is unchanged. All twelve existing pure compact-manifest
  controls pass. #14157 merges at
  `6e2ee85c6a6c983002aaf61be8df485055b48c25` after exact-head approval and
  complete fresh Linux CI executes all 9,414 selected cases in 181.251 packet
  seconds on attempt one, with normal upload and required aggregate.
  All four changed files match the reviewed head; the only additional merged
  changes are independent peer documentation. Only this storage leaf is
  complete. Future census additions retain one-space formatting rather than
  reinflating the file; all broader parent and operational gates stay open.
- [x] Qualify three complete original pure state-mount functions as six cases:
  evidence-only state mount, four supported nonvolatile filesystems and exact
  device matching. Compare all 50 original cases with six adapter cases,
  preserving function/mark-row identity, helpers, supplied tables, dataclasses,
  parameters and assertions. Exclude the other sixteen functions / 44 cases
  before execution, including all directory-descriptor, lock, guest-operation
  and native fixtures. Add only the adapter, original operation test and
  canonical guest-operation import-module bindings, with no new target.
  Require initial unchanged effect/source auditing and actual mounts-owner
  call-phase coverage per case. Preserve all 9,414 nodes, 1,312 current hashes,
  475 targets, 89 helper decisions, the 300-second deadline and 1 MiB cap.
  Keep one-space v2 formatting; require strict checks, committed release
  intent, exact-head review and complete fresh Linux CI. Supplied mount
  tables do not establish real mount state, operation custody or native proof.
  No held refusal, guard exception, native lane or consumed grant is replayed.
  Complete collection confirms all 50 originals / six adapter cases, exact
  original functions and mark-row objects, and equal cross-module parameter
  values/types. Initial unchanged guarding passes all six in 0.65 pytest /
  1.500 observation seconds with zero prohibited effects, fourteen bound
  source reads and real mounts-owner call-phase coverage for every case.
  Admission adds exactly three source bindings and no target, yielding
  9,420 nodes, 1,315 sources and 475 targets in 1,002,268 one-space v2 bytes.
  All previous nodes/order/hashes, 89 helpers and the 300-second bound remain
  intact. #14160 merges at
  `eab18c28651bb39fb3b6845e6c1852fe1f62e43d` after strict checks,
  exact-head approval and complete fresh CI executes all 9,420 cases in
  192.423 packet seconds on attempt one, with normal upload and aggregate.
  All four non-manifest files match the reviewed head. Independent
  #14155/#14159 add 35 peer files and seven source-hash rebindings, preserving
  every selected node/order, target and bound. All current hashes and
  89 helper decisions verify; this leaf's earlier CI does not qualify those
  peer changes. Only this six-case leaf is complete. Parent, separate
  refusals, ownership, native and custody gates remain open.
- [x] Qualify two complete original pure state-mount validation functions
  as thirteen cases: unavailable/volatile mount posture and ancestor
  overmounts on either device. Preserve the original functions, mark-row
  objects, supplied tables, parameters and assertions. Compare all fifty
  original cases with thirteen adapter cases; exclude the other seventeen
  functions / 37 cases before execution, including the long-table cases and
  all guest-operation, descriptor, lock and native fixtures. Add only the
  adapter's source binding, with no new production target. Require initial
  unchanged effect/source auditing and actual mounts-owner call-phase
  coverage per case before admission. Preserve all 9,420 preceding nodes,
  1,315 current source hashes including seven peer rebindings, 475 targets,
  89 helper decisions, one-space v2 storage, the 1 MiB cap and 300-second
  bound. Require strict checks, committed release intent, exact-head review
  and complete fresh Linux CI; no held refusal or operational lane is replayed.
  Complete collection confirms fifty originals / thirteen adapter cases,
  exact functions and mark-row objects, and cross-module parameter
  values/types. Initial unchanged guarding passes all thirteen in 0.66 pytest
  / 1.266 observation seconds with zero prohibited effects, fourteen bound
  reads, all 39 phase reports and actual mounts-owner call-phase coverage.
  Admission adds one source binding and no target, yielding 9,433 nodes,
  1,316 sources and 475 targets in 1,003,508 one-space v2 bytes. All current
  hashes, preceding node/target order, 89 helpers and bounds remain unchanged.
  #14162 merges at `bd48788101c6334671acf544fec0287e500e442b`
  after exact-head approval and complete fresh CI executes all 9,433 cases
  in 201.175 packet seconds on attempt one, with normal upload and aggregate.
  The entire merged source tree equals the reviewed head, including all
  seven preceding peer rebindings. Only this thirteen-case leaf is complete;
  broader parent, ownership, refusal, native and custody gates stay open.
- [x] Qualify the complete original pure shadowed/nested state-mount function
  as two cases. Preserve the function, both original suffix rows, mark-row
  identities, full parameter IDs and assertions. Complete collection compares
  fifty original cases with two adapter cases and equal cross-module parameter
  values/types; the other eighteen functions / 48 cases remain outside before
  execution. Initial unchanged guarding passes both in 0.65 pytest / 1.344
  observation seconds with zero prohibited effects, fourteen bound reads,
  all six phase reports and actual mounts-owner call-phase coverage.
  Admission adds one adapter binding and no target, yielding 9,435 nodes,
  1,317 sources and 475 targets in 1,004,014 one-space v2 bytes. All 9,433
  preceding nodes/order, source hashes, targets, 89 helpers, the 1 MiB cap
  and 300-second bound remain intact. #14165 merges at
  `56f47e28b3ea68eae598e166d4cc8bdadebf4da0` after strict checks,
  committed release intent, exact-head approval and complete fresh CI executes
  all 9,435 cases in 145.455 packet seconds on attempt one, with normal upload
  and aggregate. The entire merged tree equals the reviewed head.
  Only this two-case leaf is complete. No guest operation, host inspection,
  native lane or held refusal executes; broader parent, ownership, release
  and custody gates remain open.
- [x] Qualify four complete original injected lock-posture functions as nine
  cases: expected plain-file metadata, six altered metadata fields, missing-lock
  posture before/after acquisition, and the injected shared-lock constructor at
  its fixed key. Preserve original functions, callbacks, parameter rows, IDs
  and assertions. Complete collection compares fifty originals with nine
  adapter cases and equal cross-module parameter values/types; fifteen other
  functions / 41 cases remain excluded before execution. Initial unchanged
  guarding passes all nine in 0.58 pytest / 1.094 observation seconds with zero
  prohibited effects, fourteen bound reads, all 27 phase reports and actual
  guest-operation-owner call-phase coverage per case. Add only the adapter
  binding and the already-bound guest-operation coverage target, yielding
  9,444 nodes, 1,318 sources and 476 targets in 1,004,813 one-space v2 bytes.
  Preserve every preceding node/order/hash/target, 89 helpers, the 1 MiB cap
  and 300-second bound. #14167 merges at
  `88f73e86f636f93f8cd461d5b6791b03ecd28c23` after strict checks,
  committed release intent, exact-head approval and complete fresh CI executes
  all 9,444 cases in 231.571 packet seconds on attempt one, with normal upload
  and aggregate. The entire merged tree equals the reviewed head.
  Only this nine-case leaf is complete. No real lock, descriptor, guest
  operation, host inspection or native lane executes; broader parent, refusal,
  ownership and custody gates remain open.
- [x] Qualify four complete original injected operation-custody functions as
  eight cases: checks before yield/each effect/exit, same-guest contention, five
  failure paths, and state replacement at exit. Preserve the original fixture,
  fake holder, callbacks, parameter rows, IDs and assertions. Complete
  collection compares fifty originals with eight adapter cases and equal
  cross-module parameter values/types; fifteen other functions / 42 cases
  remain excluded before execution. Initial unchanged guarding passes all
  eight in 0.82 pytest / 1.672 observation seconds with zero prohibited
  effects, fourteen bound reads, all 24 phase reports and actual
  guest-operation-owner call-phase coverage per case. Add only the adapter
  binding, yielding 9,452 nodes, 1,319 sources and the same 476 targets in
  1,005,593 one-space v2 bytes. Preserve every preceding node/order/hash/target,
  89 helpers, the 1 MiB cap and 300-second bound. #14169 merges at
  `f2c988e45ccf8ff4ff2ae7412ee2ef0cec344e9f` after strict checks,
  committed release intent, exact-head approval and complete fresh CI executes
  all 9,452 cases in 138.026 packet seconds on attempt one, with normal upload
  and aggregate. Managed sync preserves four independent upstream source
  rebindings; the observed closure is unchanged. The entire merged tree equals
  the reviewed head. Only this eight-case leaf is complete. No real state
  observation, lock, descriptor, host inspection or native lane executes;
  broader parent, refusal, ownership and custody gates remain open.
- [x] Qualify six complete original synthetic runtime-origin probe functions
  as nineteen cases: main-package attribution, four interpreter refusals,
  eleven source/metadata refusals, preimport refusal, explicit noneditable
  metadata and bounded file-growth reading. Preserve the original paths
  fixture, temporary source/runtime trees, callbacks, parameter rows, IDs and
  assertions. Complete collection compares 36 originals with nineteen adapter
  cases and equal cross-module parameter values/types; six other functions /
  seventeen cases remain excluded before execution, including the complete
  symlink matrix and every guest/caller/event-loop case. Initial unchanged
  guarding passes all nineteen in 1.11 pytest / 1.703 observation seconds with
  zero prohibited effects, eighteen bound reads, all 57 phase reports and
  actual origin-probe-owner call-phase coverage per case. Add six source
  bindings and one production target, yielding 9,471 nodes, 1,325 sources and
  477 targets in 1,007,465 one-space v2 bytes. Preserve every preceding
  node/order/hash/target, 89 helpers, the 1 MiB cap and 300-second bound.
  #14172 merges at `1c62b7dbb11043e7a67e5860201c1379810e863d`
  after strict checks, committed release intent, exact-head approval and fresh
  CI executes all 9,471 cases in 180.030 packet seconds on attempt one, with
  normal upload and aggregate. Whole-tree composition checks preserve the
  concurrent #14170 delta and its single installer binding; the observed
  nineteen-case closure is unchanged. Earlier CI is not execution evidence for
  that later peer delta. Only this nineteen-case leaf is complete. Temporary
  synthetic files and injected metadata are not installed-runtime, guest,
  native, rollout or custody proof; broader gates remain open.
- [x] Qualify four complete original injected runtime-origin caller functions
  as ten cases: finite isolated command construction, seven observation
  refusals, custody loss and runtime-root replacement. Preserve the original
  caller fixture, fake run callbacks, temporary runtime identity, rows, IDs
  and assertions. Collection compares 36 originals with ten adapter cases,
  exact function/fixture/mark-row objects and equal cross-module parameter
  values/types; eight other functions / 26 cases remain excluded before
  execution, including the complete symlink matrix and event-loop case.
  Initial unchanged guarding passes all ten in 0.75 pytest / 1.453 observation
  seconds with zero prohibited effects, eighteen bound reads, all thirty
  phase reports and actual guest-origins-owner call-phase coverage per case.
  Add only the adapter binding and already-bound caller production target,
  yielding 9,481 nodes, 1,326 sources and 478 targets in 1,008,480 one-space
  v2 bytes. Preserve all preceding nodes/order/hashes/targets, including the
  concurrent installer binding, 89 helpers, 1 MiB cap and 300-second bound.
  Normal pull-forward then preserves the independent Vault slice's six nodes,
  five new sources, seven rebindings and four targets: 9,487 nodes, 1,331
  sources, 482 targets and 1,009,900 bytes. All eighteen observed sources and
  preceding order remain unchanged; fresh CI must execute the composition.
  Strict checks, committed release intent, exact-head review and fresh full
  source CI pass; this is not native/guest/installed execution, release,
  rollout, custody settlement or whole-parent completion.
  #14175 receives exact-head approval, but fresh CI attempt one times out at
  the unchanged five-minute job limit during evidence upload. All 9,487 cases
  pass in 250.279 packet seconds; no artifact completes and the required
  aggregate correctly fails. Source guards and metadata pass independently.
  The operator subsequently authorizes exactly one
  unit-job retry on the unchanged approved head; that grant is consumed by
  attempt two. No further retry, deadline change or bypass is authorized.
  Attempt two executes all 9,487 cases in 141.137 packet seconds, completes
  the artifact upload and passes the required aggregate. #14175 merges at
  `fffd9f792d02228f87627af9afb819e4ba850292`. Whole-tree composition checks
  preserve both concurrent peer changes, their one added case/source and five
  rebindings: 9,488 nodes, 1,332 sources, 482 targets and 1,010,114 bytes.
  All eighteen observed sources remain unchanged. Earlier-head CI is not
  execution evidence for those later peer deltas. Only this ten-case leaf
  is complete; the next normal functional slice needs fresh complete CI.
- [x] Qualify ten complete original pure source-tree inventory functions as
  25 cases: regular tree metadata, the entire fifteen-row unsafe-tree matrix,
  aggregate limits, shared prefixes, prefix memory ceilings, deep zero-byte
  paths, listing byte limits, exact UTF-8 ceilings, exact tree/blob ceilings
  and SHA-256 identity. Preserve original functions, parameter rows, IDs and
  assertions. Collection compares 56 originals with 25 adapter cases, exact
  function/own-mark-row objects and equal cross-module parameter values/types;
  eight other functions / 31 cases remain excluded before execution.
  Initial unchanged guarding passes all 25 in 0.77 pytest / 1.343 observation
  seconds with zero prohibited effects, fourteen bound reads, all 75 phase
  reports and actual source-inventory-owner call-phase coverage per case.
  Add three bindings and one production target, yielding 9,513 nodes,
  1,335 sources and 483 targets in 1,014,275 one-space v2 bytes. Preserve
  every preceding node/order/hash/target, including both concurrent peer
  slices, 89 helpers, 1 MiB cap and 300-second bound. #14181 merges at
  `e4368cde009fbbe86dafb39cf4d4cd699e6b6c66` after strict checks,
  committed release intent, exact-head approval and fresh CI executes all
  9,513 cases in 135.801 packet seconds on attempt one, with normal upload
  and required aggregate. The entire merged tree equals the reviewed head.
  Only this 25-case leaf is complete. No SourceReader/materialization, actual
  Git, native, private-root/symlink fixture, installed runtime or custody
  operation executes; broader acceptance gates remain open.
- [x] Qualify three complete original pure capture-cohort selector functions
  as ten cases: both file orders and platform flags, unchanged unrelated
  selections, and either missing source-context marker. Preserve original
  functions, the autouse scratch fixture, parameter rows, IDs and assertions.
  Collection compares sixteen originals with ten adapter cases, exact
  function/fixture/own-mark-row objects and equal cross-module parameter
  values/types. Two complete runner functions / six cases remain excluded
  before execution. Initial unchanged guarding passes all ten in 1.90 pytest /
  3.016 observation seconds with zero prohibited effects, 49 bound reads,
  all thirty phase reports and actual selection-owner call-phase coverage.
  Add ten Python bindings and one production target, yielding 9,523 nodes,
  1,345 sources and 484 targets in 1,016,379 one-space v2 bytes. Preserve all
  preceding entries/order/hashes/targets, 89 helpers, 1 MiB cap and
  300-second bound. Strict checks, committed release intent, exact-head
  review and fresh full-packet CI remain required. No runner, capture,
  process, native, installed-runtime or custody operation is qualified.
  Managed pull-forward preserves concurrent #14177 and its four existing
  source rebindings without changing counts, node order or target order.
  All 49 initially observed Python sources remain unchanged. Authored and
  post-sync Linux/Win32 strict checks pass; earlier observations do not
  qualify those later peer deltas. #14184 receives exact-head approval and
  fresh attempt-one CI passes all 9,523 cases in 135.014 packet seconds,
  completes the three-file upload and passes the required aggregate.
  Normal managed merge yields `a199575e1d8f9fcbead8831d9a76f2cd738a76f0`.
  Whole-tree composition preserves concurrent #14182, its ten cases,
  two sources, four rebindings and one target: 9,533 nodes, 1,347 sources,
  485 targets and 1,017,512 bytes. All 49 observed sources remain unchanged.
  Earlier-head CI is not evidence for the later peer deltas. Only this
  ten-case source leaf is complete; the next normal functional slice needs
  fresh full-packet CI.
- [x] Qualify three complete original private-fixture file-selection
  functions as ten cases: recursive-discovery directory patterns versus
  file names, interactive path normalization and invalid explicit paths
  refused before preparation. Preserve original functions, both fixtures,
  parameter rows, IDs and assertions. Collection compares 267 originals with
  ten adapter cases, exact function/fixture/own-mark-row objects and equal
  cross-module parameter values/types. The other 104 functions / 257 cases
  remain excluded before execution. Initial unchanged guarding passes all
  ten in 1.88 pytest / 2.500 observation seconds with zero prohibited
  effects, 48 bound reads, all thirty phase reports and actual file-selection
  owner call-phase coverage. Add one adapter binding and the existing runner
  source as a production target, yielding 9,543 nodes, 1,348 sources and
  486 targets in 1,018,924 one-space v2 bytes. Preserve preceding entries,
  order, hashes and targets, including both concurrent peer slices,
  89 helpers, 1 MiB cap and 300-second bound. Require strict checks,
  committed release intent, exact-head review and fresh full-packet CI.
  Private source fixtures and early refusal are not runner preparation,
  threaded cleanup, subprocess/native execution, installed acceptance,
  release, rollout or custody proof. #14188 receives exact-head approval;
  fresh attempt-one CI passes all 9,543 cases in 199.736 packet seconds,
  completes the three-file upload and passes the required aggregate.
  Normal managed merge yields `256e09550bcfccf29d1981d24e0601144eb86254`.
  Whole-tree composition preserves concurrent #14127 and its one existing
  source rebinding; counts and bytes stay unchanged. All 48 initially
  observed sources remain unchanged. Earlier-head CI is not evidence for
  that later peer delta. Only this ten-case source leaf is complete;
  the next normal functional slice needs fresh full-packet CI.
- [x] Qualify four complete original explicit-file selection functions as
  eleven cases: sorted/deduplicated selections, invalid relative paths,
  absolute paths and invalid CLI target combinations rejected before
  preparation. Preserve original functions, autouse scratch fixture,
  parameter rows, IDs and assertions. Collection compares 267 originals
  with eleven adapter cases, exact function/fixture/own-mark-row objects
  and equal cross-module parameter values/types. The other 103 functions /
  256 cases remain excluded before execution. Initial unchanged guarding
  passes all eleven in 1.51 pytest / 2.219 observation seconds with zero
  prohibited effects, 48 bound reads, all thirty-three phase reports and
  actual runner-owner call-phase coverage. Add only one adapter binding,
  yielding 9,554 nodes, 1,349 sources and unchanged 486 targets in
  1,019,988 one-space v2 bytes. Preserve preceding entries/order/hashes,
  89 helpers, 1 MiB cap and 300-second bound. Require strict checks,
  committed release intent, exact-head review and fresh full-packet CI.
  Selection and early CLI refusal do not qualify preparation, execution,
  threads/processes, native behavior, installation, release or custody.
  #14191 receives exact-head approval; fresh attempt-one CI executes all
  9,554 cases in 191.546 packet seconds, completes the three-file upload
  and passes the required aggregate. Normal managed merge yields
  `0dd3ada551cfbb7e9c4b06f1aca63b1f19c4cf95`. Whole-tree composition
  preserves concurrent #14189 and #14187, including six existing source
  rebindings; counts and bytes stay unchanged. All 48 initially observed
  sources remain unchanged. Earlier-head CI is not evidence for those
  later peer deltas. Only this eleven-case source leaf is complete;
  the next normal functional slice needs fresh full-packet CI.
- [x] Qualify four complete original independent-file grouping functions
  as five cases: declared Bridge groups, filtered/unfiltered preservation,
  missing independent-file refusal and explicit selection without unrelated
  native files. Preserve original functions, autouse scratch fixture,
  parameter rows, IDs and assertions. Collection compares 267 originals
  with five adapter cases, exact function/fixture/own-mark-row objects
  and equal cross-module parameter values/types. The other 103 functions /
  262 cases remain excluded before execution. Initial unchanged guarding
  passes all five in 1.85 pytest / 2.469 observation seconds with zero
  prohibited effects, 48 bound reads, all fifteen phase reports and actual
  grouping-owner call-phase coverage. Add only one adapter binding,
  yielding 9,559 nodes, 1,350 sources and unchanged 486 targets in
  1,020,604 one-space v2 bytes. Preserve preceding entries/order/hashes,
  89 helpers, 1 MiB cap and 300-second bound. Require strict checks,
  committed release intent, exact-head review and fresh full-packet CI.
  Source fixture filenames do not qualify native execution, preparation,
  process/thread cleanup, installation, release or custody. #14195 receives
  exact-head approval; fresh attempt-one CI executes all 9,559 cases in
  216.533 packet seconds, completes the three-file upload and passes the
  required aggregate. Normal managed merge yields
  `71421f9b3de099952ab5dcd7235de5f9931d06ff`. Whole-tree composition
  preserves concurrent #14194 and #14196, including three peer cases and
  six source rebindings: 9,562 nodes, 1,350 sources, 486 targets and
  1,020,833 bytes. All 48 initially observed sources remain unchanged.
  Earlier-head CI is not evidence for those later peer deltas. Only this
  five-case source leaf is complete; the next normal functional slice
  needs fresh full-packet CI.
- [x] Qualify five complete original isolated-environment functions as five
  cases: mutable-root detachment, optional-file containment, explicit synthetic
  host-state preservation without session affinity, explicit-tier requirement
  and escaped-temp refusal. Preserve original functions, autouse fixture and
  assertions. Collection compares 267 originals with five unparametrized
  adapter cases; the other 102 functions / 262 cases remain excluded before
  execution. Initial unchanged guarding passes all five in 2.70 pytest /
  3.766 observation seconds with zero prohibited effects, 48 bound reads,
  all fifteen phase reports and actual environment/portfolio-owner call
  coverage. Add only the adapter binding: 9,567 nodes, 1,351 sources,
  unchanged 486 targets, 89 helpers, 300-second bound and 1 MiB cap in
  1,021,416 one-space v2 bytes. Normal managed pull-forward preserves
  #14193/#14198/#14200; the last peer replaces five cases with four and
  rebinds two existing sources. Exact composition yields 9,566 nodes,
  1,351 sources and 1,021,330 bytes; all 48 initially observed sources
  remain unchanged. Normal `origin/dev` strict checks pass on Linux/Windows
  after pull-forward. Require committed release intent, exact-head review
  and fresh full CI. No real credentials, host-state access, native
  execution or rollout are qualified by synthetic environment maps.
  #14203 receives exact-head approval; fresh attempt-one CI executes all
  9,566 cases in 228.891 packet seconds, completes the three-file upload
  and passes the required aggregate. Normal managed merge yields
  `fc17a3b3506c9b39850c06f7a94d64df3dc4682c`; the complete reviewed and
  merged trees are equal. Actual parent Goal, Plan and Validation Plan
  are reread before resolving only this five-case source leaf.
- [x] Qualify five complete original runner admission functions:
  busy-holder refusal, one mocked environment lease at a time, unexpected
  runner-error continuation, guard admission and collect-only admission.
  Preserve exact functions, the autouse scratch fixture and assertions.
  All use private fixture directories and mocked lease/runner dispatch;
  no real lease, service, native runner or subprocess is selected.
  Admission and runner owners are already source-bound production targets.
  Require exact collection comparison, unchanged effect/source observation,
  actual owner calls, normal strict checks, release intent, exact-head
  review and fresh full-packet CI. Do not select the adjacent clock/sleep
  retry test by implication. Exact collection compares 267 originals with
  five unparametrized adapter cases and the original autouse fixture;
  102 functions / 262 cases remain excluded before execution. Initial
  unchanged guarding passes all five in 5.81 pytest / 7.297 observation
  seconds with zero prohibited effects, 48 bound reads, all fifteen phase
  reports and actual admission/runner-owner call coverage. Add only the
  adapter binding: 9,571 nodes, 1,352 sources and unchanged 486 targets in
  1,021,859 one-space v2 bytes. Preserve preceding order/hashes,
  89 helpers, 1 MiB cap and 300-second bound. Normal Linux/Windows strict
  checks and Ruff pass. #14205 receives exact-head approval; fresh
  attempt-one CI executes all 9,571 cases in 239.424 packet seconds,
  completes the three-file upload and passes the required aggregate.
  Normal managed merge yields
  `139e4a8a55c06b2211f4e3cd7dfd8923cdd6ab9a`; the complete reviewed and
  merged trees are equal. Actual parent Goal, Plan and Validation Plan
  are reread before resolving only this five-case source leaf.
- [x] Qualify five complete original runner argument-control functions,
  expected as 31 cases: existing native-owner default/explicit values,
  positive file-group limits, existing functional-profile dispatch values,
  unqualified-scope/override refusal and unknown-profile refusal. Preserve
  complete functions, autouse fixture, parameter rows, IDs and assertions.
  All calls use mocked admission/runner dispatch or fail argument parsing
  before admission. This changes no profile, limit or native contract.
  Require exact collection comparison, unchanged observation and actual
  runner calls, normal strict checks, release intent, exact-head review
  and fresh full-packet CI. Do not select adjacent preparation or
  partition-execution cases by implication. Exact collection compares
  267 originals with 31 adapter cases and equal parameter values/types,
  IDs and indices; 102 functions / 236 cases remain excluded before
  execution. Initial unchanged guarding passes all 31 in 3.01 pytest /
  4.094 observation seconds with zero prohibited effects, 48 bound reads,
  all 93 phase reports and actual runner calls in every case. Only the
  adapter binding is added: 9,602 nodes, 1,353 sources and unchanged
  486 targets in 1,025,149 one-space v2 bytes. Preserve preceding order/
  hashes, 89 helpers, 1 MiB cap and 300-second bound. Ruff and normal
  Linux/Windows strict checks pass; release intent is recorded.
  #14211 receives exact-head approval; fresh attempt-one CI executes all
  9,602 cases in 240.401 packet seconds, completes the three-file upload
  and passes the required aggregate. Normal managed merge yields
  `8d15e686059af661de573b8fd145f5dd15955f6e`; the complete reviewed and
  merged trees are equal. Actual parent Goal, Plan and Validation Plan
  are reread before resolving only this 31-case source leaf.
- [x] Qualify six complete original runner CLI admission/selection functions:
  mocked prepare-only admission, explicit host-state tier refusal,
  infinite/NaN admission-wait refusal and changed-plugin contract selection
  for real/empty mocked diffs. Preserve complete functions, the autouse
  scratch fixture and assertions. All use mocked admission/runner dispatch,
  argument failure before admission or mocked subprocess responses.
  Require exact collection comparison, unchanged observation and actual
  runner calls, normal strict checks, release intent, exact-head review
  and fresh full-packet CI. Do not select adjacent real preparation,
  threaded cleanup or clock/sleep retry cases by implication. Exact
  collection compares 267 originals with six unparametrized adapter
  cases and original function/fixture objects; 101 functions / 261 cases
  remain excluded before execution. Initial unchanged guarding passes
  all six in 3.56 pytest / 4.625 observation seconds with zero prohibited
  effects, 48 bound reads, all eighteen phase reports and actual runner
  calls in every case. Only the adapter binding is added: 9,608 nodes,
  1,354 sources and unchanged 486 targets in 1,025,740 one-space v2 bytes.
  Preserve preceding order/hashes, 89 helpers, 1 MiB cap and 300-second
  bound. Ruff and normal Linux/Windows strict checks pass; release intent
  is recorded. #14214 receives exact-head approval; fresh attempt-one CI
  executes the composed 9,619 cases in 145.803 packet seconds, completes
  the three-file upload and passes the required aggregate. Normal managed
  merge yields `73816b7c69f2d93fe05bac334491c42e8f541924`.
  The slice is exact over the actual merge parent; concurrent #14212
  changes two imported runtime sources, so whole-tree equality is not
  claimed. Requalification of these six against the merged inputs passes
  in 1.62 pytest / 2.562 observation seconds with zero prohibited effects,
  all eighteen reports and real runner calls. No CI retry is requested.
  Actual parent Goal, Plan and Validation Plan are reread before resolving
  only this six-case source leaf.
- [x] Qualify two complete original tooling-declaration functions, expected
  as thirteen cases: flat dev-group fallback and malformed metadata refusal
  before install. Preserve complete functions, the autouse scratch fixture,
  parameter rows, IDs and assertions. All metadata is fixture-owned;
  malformed declarations fail before generation, cache clearing, install
  or subprocess execution. The imported retirement helper only checks
  a missing fixture marker; no retirement action is selected. Require exact
  collection, unchanged effect/source observation, real runner calls,
  normal strict checks, release intent, exact-head review and fresh full
  CI. Keep canonical metadata/native-proof and real-install cases separate.
  Exact collection compares 267 originals with thirteen adapter cases,
  preserving function/fixture identity, parameter values/types, own marks
  and complete IDs; 105 functions / 254 cases are excluded before execution.
  Initial unchanged guarding passes all thirteen in 2.56 pytest / 3.609
  observation seconds with zero prohibited effects, 49 bound reads,
  all thirty-nine phase reports and actual runner calls in every case.
  Add only the adapter and imported retirement-helper bindings, no target:
  9,632 nodes, 1,359 sources and unchanged 488 targets in 1,029,032
  one-space v2 bytes. Preserve preceding order/hashes, 89 helpers, 1 MiB
  cap and 300-second bound. Ruff and normal Linux/Windows strict checks
  pass; release intent is recorded. #14216 receives exact-head approval;
  fresh attempt-one CI executes all 9,632 cases in 235.119 packet seconds,
  completes the three-file upload and passes the required aggregate.
  Normal managed merge yields `74ffa6d0a90c75e9fdd920415a989e9ecc7f740e`;
  the complete reviewed and merged trees are equal. Actual parent Goal,
  Plan and Validation Plan are reread before resolving only this thirteen-
  case source leaf. The parent remains open.
- [x] Qualify one complete original mocked preparation-argv function as two
  cases: separate and equals-form profile flags are removed together with
  test-file flags from preparation arguments, without mutating caller input.
  Preserve the original function, assertions, autouse scratch fixture,
  parameter rows, IDs and mocked subprocess. Require exact collection,
  unchanged effect/source observation, real network-scope helper calls,
  normal strict checks, release intent, exact-head review and fresh full CI.
  Existing network-scope and selection helpers are already bound; any added
  production target must be established by actual per-node call coverage.
  No container, network operation or real dependency preparation is selected.
  Exact collection compares 267 originals with two adapter cases, preserving
  function/fixture identity, parameter values/types, own marks and complete
  IDs; 106 functions / 265 cases are excluded before execution. Initial
  unchanged guarding passes both in 1.63 pytest / 2.297 observation seconds
  with zero prohibited effects, 48 bound reads, all six phase reports and
  actual network-scope helper calls in both cases. Add only the adapter
  binding and that established production target: 9,634 nodes, 1,360
  sources and 489 targets in 1,029,513 one-space v2 bytes. Preserve preceding
  order/hashes, 89 helpers, 1 MiB cap and 300-second bound. Ruff and normal
  Linux/Windows strict checks pass; release intent is recorded. #14218
  receives exact-head approval; fresh attempt-one CI executes all 9,634
  cases in 142.529 packet seconds, completes the three-file upload and
  passes the required aggregate. Normal managed merge yields
  `111165accb659ca344b182f947c6084d2f93c10b`; the complete reviewed and
  merged trees are equal. Actual parent Goal, Plan and Validation Plan
  are reread before resolving only this two-case source leaf.
- [x] Qualify three complete original preflight/preparation early-return
  functions, expected as four cases: unsafe child cleanup refuses before
  preparation/collection, preflight consumes the original plugin budget,
  and mocked prepare-only never invokes pytest. Preserve original
  functions, assertions, the autouse scratch fixture and parameter rows.
  Require exact collection, unchanged effect/source observation and actual
  runner calls, strict checks, release intent, exact-head review and fresh
  full CI. These paths stop before the unmocked cleanup thread pool.
  Keep positive run/partition/profile tests that submit real cleanup work
  separate; a mocked contained runner does not remove that thread effect.
  Exact collection compares 267 originals with four adapter cases,
  preserving function/fixture identity, parameter values/types, own marks
  and complete IDs; 104 functions / 263 cases are excluded before execution.
  Initial unchanged guarding passes all four in 2.10 pytest / 2.953
  observation seconds with zero prohibited effects, 48 bound Python reads,
  all twelve phase reports and actual runner calls in every case. The
  canonical fake `alpha` plugin is absent; the prepare-only lookup does not
  open a real plugin's configuration. Add only the adapter binding: 9,638
  nodes, 1,361 sources and the same 489 targets in 1,030,063 one-space v2
  bytes. Preserve preceding order/hashes, 89 helpers, 1 MiB cap and
  300-second bound. Ruff and normal changed-code Linux/Windows strict checks
  pass; a worktree-manager patch changefile records release intent.
  Published composition preserves eight peer cases and changed dependency
  imports. Final-byte observation passes all four again in 1.71 pytest /
  2.375 observation seconds with zero prohibited effects and all 48 source
  reads bound. Published head receives exact Copilot approval; both the
  initial full CI and the already-triggered edited-event CI pass all 9,646
  cases. The latter repeats execution after lookup exhausts its unchanged
  eight-second budget; this is not a reuse hit or consolidation saving.
  The provider first refuses while that superseding gate is expected, then
  normal managed merge after fresh green verification yields #14222 at
  `0e7648251066dff4d1a5f99d52976217b1384b57`. The entire reviewed and merged
  trees are equal. Actual parent Goal, Plan, own leaf and Validation Plan
  are reread before resolving only this four-case source leaf.
- [x] Qualify four complete original cache-metadata functions, expected as
  nine cases: missing transitive manifest refusal, stable script-only keys
  that still bind runner requirements, selected-library extras and refusal
  of source activation selectors. Preserve original functions, assertions,
  the exact `graph` and `released_graph` fixtures, parameter rows, marks,
  IDs and fixture-owned filesystem roots. Require exact collection,
  unchanged effect/source observation, real cache-policy calls, normal
  strict checks, release intent, exact-head review and fresh complete CI.
  Exclude adjacent real metadata scans, symlink/native paths, preparation,
  cleanup and actual UV execution before running selected tests.
  The source trace rejects the prospective body-drift/generated-file
  controls before execution: their real multi-file fingerprint helper
  submits to a thread pool. Do not patch that helper or original tests,
  widen the effect guard or claim those paths were qualified.
  Exact collection compares 24 functions / 51 original cases with nine
  adapter cases; 20 functions / 42 cases are excluded before execution.
  Function/fixture identities, parameter values/types, own marks, full IDs
  and fixture names match. Initial unchanged guarding passes all nine in
  1.64 pytest / 2.094 observation seconds with zero prohibited effects,
  49 bound Python reads, all 27 phase reports and actual cache-policy calls
  per case. Add only the adapter/original bindings and that established
  production target: 9,655 nodes, 1,363 sources and 490 targets in 1,032,055
  one-space v2 bytes. Preserve preceding entries/order/hashes, 89 helpers,
  1 MiB cap and 300-second bound. Ruff and normal changed-code Linux/Windows
  strict checks pass; a worktree-manager patch changefile records release
  intent. #14225 receives exact-head approval; fresh attempt-one CI
  executes all 9,655 cases in 233.200 packet seconds, completes the three-
  file upload and passes the required aggregate. Normal managed merge
  yields `802d038e0086c6779aa87ab1ee2fbc13e930e519`; the complete reviewed
  and merged trees are equal. Actual parent Goal, Plan, own leaf and
  Validation Plan are reread before resolving only this nine-case leaf.
- [x] Qualify three complete original exact-selection load/refusal functions,
  expected as fifteen cases: legacy loading without controller TOML
  support, thirteen malformed/invalid selections and missing-manifest
  refusal before preparation. Preserve original functions, assertions,
  selection fixture, parameter dictionaries/types, marks and full IDs.
  The fixture defines its native preparation helper but selected paths
  never call it. Require exact collection, unchanged effect/source
  observation, real selection-helper calls, normal strict checks, release
  intent, exact-head review and fresh full CI. Keep actual contained
  execution, venv creation, subprocesses and other native paths excluded.
  Exact collection compares sixteen functions / 51 original cases with
  fifteen adapter cases; thirteen functions / 36 cases are excluded before
  execution. Function/fixture identities, fixture names, recursive parameter
  values/types, own marks and complete IDs match. Initial unchanged guarding
  passes all fifteen in 1.74 pytest / 2.407 observation seconds with zero
  prohibited effects, 43 bound Python reads, all 45 phase reports and actual
  selection-helper calls per case. Add only the original/adapter bindings:
  9,670 nodes, 1,365 sources and the existing 490 targets in 1,033,365
  one-space v2 bytes. Preserve preceding entries/order/hashes, 89 helpers,
  1 MiB cap and 300-second bound. Ruff and normal changed-code Linux/Windows
  strict checks pass; a worktree-manager patch changefile records release
  intent. #14228 receives exact-head approval; fresh attempt-one CI executes
  all 9,670 cases in 145.840 packet seconds, completes the three-file upload
  and passes the required aggregate. Normal managed merge yields
  `db110c00bdb96d7b7d87fa8e89dc0bbca9465c1e`. Concurrent #14221 advances
  the actual parent with eight Vault cases; the complete expected merged
  composition preserves every peer tree entry and all owned non-manifest
  blobs. All 43 observed inputs remain unchanged. The resulting 9,678-case
  composition is not claimed executed by this PR's 9,670-case CI.
  Actual parent Goal, Plan, own leaf and Validation Plan are reread before
  resolving only this fifteen-case leaf.
- [x] Qualify four complete original exact-selection diagnostic/CLI functions,
  expected as sixteen cases: missing target-plugin refusal, bounded missing-
  node diagnostics, shared invalid/empty-file diagnostic bounds and eleven
  conflicting CLI modes. Preserve original functions, assertions, the exact
  selection fixture, fixture names, recursive parameter values/types, marks
  and full IDs. Direct callback calls do not perform real pytest collection;
  CLI refusals precede UV/environment admission. Require exact collection,
  unchanged effect/source observation, real per-node owner calls, normal
  strict checks, release intent, exact-head review and fresh complete CI.
  Bind any newly imported callback and establish any new production target
  before admission. Keep native preparation and actual execution excluded.
  Exact collection compares sixteen functions / 51 originals with sixteen
  adapter cases; twelve functions / 35 cases are excluded before execution.
  Function/fixture identities, fixture names, recursive parameter values/
  types, own marks and complete IDs match. Initial unchanged guarding passes
  all sixteen in 1.82 pytest / 2.469 observation seconds with zero prohibited
  effects, 46 bound Python reads, all 48 phase reports and actual owner calls
  per case. The observed owner union covers the callback, selection helper
  and runner. Add only the adapter/callback bindings and established callback
  production target: 9,694 nodes, 1,369 sources and 492 targets in 1,035,905
  one-space v2 bytes. Preserve preceding entries/order/hashes, 89 helpers,
  1 MiB cap and 300-second bound. Ruff passes and release intent is recorded.
  Initial normal strict checking encounters an advanced remote base selecting
  peer projects and an unprepared Budget Guidance environment. Supported
  commit/sync reconciliation preserves #14223's three source rebindings;
  none of the 46 observed inputs changes. The unchanged normal strict
  command then passes on Linux/Windows for the actual one-file source delta.
  No dependency preparation, override or unrelated source repair is used.
  #14230 receives exact-head approval; fresh attempt-one CI executes all
  9,694 cases in 164.789 packet seconds, completes the three-file upload and
  passes the required aggregate. Normal managed merge yields
  `f667e57ac21c72b051e1d182b3716c35cf7c982a`; the entire reviewed and merged
  trees are equal. A concurrent fetch ref race is inspected read-only before
  the supported source sync completes. Actual parent Goal, Plan, own leaf
  and Validation Plan are reread before resolving only this sixteen-case leaf.
- [x] Qualify one complete original nonrelative-path refusal function as six
  string-only drive/UNC cases. Preserve original assertions, the exact
  selection fixture, parameter strings/types, marks and full IDs, including
  the global failing `Path.resolve` patch. The selected validator must refuse
  before filesystem resolution; no actual Windows/native action is selected.
  Require exact collection and positive unchanged guard observation, real
  owner calls, normal strict checks, release intent, exact-head review and
  fresh complete CI. Any incompatibility remains a refusal, not permission
  to patch the original, fixture or guard or preload around an observation.
  Exact collection compares sixteen functions / 51 originals with six
  adapter cases; fifteen functions / 45 cases are excluded before execution.
  Function/fixture identities, fixture names, recursive parameter values/
  types, own marks and complete IDs match. Positive observation under the
  unchanged guard passes all six in 1.60 pytest / 2.203 observation seconds
  with zero prohibited effects, 43 bound Python reads, all eighteen phase
  reports and actual validator calls per case. The failing resolution
  assertion remains intact and no compatibility workaround is needed.
  Add only the adapter binding: 9,700 nodes, 1,370 sources and the existing
  492 targets in 1,036,848 one-space v2 bytes. Preserve preceding entries/
  order/hashes, 89 helpers, 1 MiB cap and 300-second bound. Ruff and normal
  changed-code Linux/Windows strict checks pass; a worktree-manager patch
  changefile records release intent. #14232 receives exact-head approval;
  fresh attempt-one CI executes all 9,700 cases in 145.421 packet seconds,
  completes the three-file upload and passes the required aggregate.
  Normal managed merge yields `544a1e5757275ab709881cd350ce2049775a5117`;
  the entire reviewed and merged trees are equal. Actual parent Goal, Plan,
  own leaf and Validation Plan are reread before resolving only this six-
  case leaf. The three selection adapters cover 37 of the 51 originals;
  fourteen preparation/contained-execution cases remain unselected. The
  first invokes unredirected source-bound preparation helpers despite its
  subprocess mock; do not execute it or add an unrelated fixture workaround.
- [x] Qualify three complete original cache path-spelling functions as seven
  pure string/path cases: private fixture UNC/native-prefix preservation,
  drive/UNC/existing-prefix spelling and non-Windows identity. Preserve
  original functions, assertions, fixture names, recursive parameter values/
  types, marks and full IDs. Only the module's OS policy attribute is mocked;
  no actual native filesystem, cleanup or preparation action is selected.
  Require exact collection, unchanged effect/source observation, real helper
  calls, normal strict checks, release intent, exact-head review and fresh
  complete CI. Keep fixture-length-only, threaded fingerprint, symlink,
  cleanup, preparation and actual UV cases outside this slice.
  Exact collection now preserves all three original function objects,
  fixture names, recursive parameter values/types, own marks and complete
  IDs: 24 functions / 51 originals versus seven adapter cases, excluding
  21 functions / 44 cases before execution. Positive unchanged-guard
  observation passes seven in 2.60 pytest / 3.859 observation seconds,
  with zero prohibited effects, 49 bound Python reads, all 21 phase reports
  and real spelling-helper call lines per case. Add only the adapter binding:
  9,707 nodes, 1,371 sources and the same 492 targets in 1,037,881 one-space
  v2 bytes; retain 89 helpers, 1 MiB cap and 300-second bound. Ruff and normal
  changed-code Linux/Windows strict checks pass. A worktree-manager patch
  changefile records release intent. Managed publication preserves the
  independent vault change: actual parent has 9,701 cases and 493 targets;
  final packet has 9,708 cases, 1,371 sources and 493 targets in 1,038,020
  bytes. Exact five-file composition preserves the peer changes and all 49
  observed inputs. #14235 receives exact-head approval; fresh attempt-one CI
  executes all 9,708 cases in 195.386 packet seconds, completes the three-
  file upload and passes the required aggregate. Normal managed merge yields
  `3159e63925b4be7131461268d21e67ef5f6ecc6f`; the entire reviewed and merged
  trees are equal. Actual parent Goal, Plan, own leaf and Validation Plan
  are reread before resolving only this seven-case source leaf.
- [x] Qualify seven complete original command-safety classification functions,
  provisionally 21 cases: bounded inspection, NUL refusal, redirected-home
  nonqualification, runner-bound namespaces, Windows private-name freshness,
  POSIX private sockets and child-argument namespace refusal. Select only
  in-process classification and fixture-owned paths, not actual commands,
  native parsing, backend control or audit-hook activation. Preserve original
  functions/assertions/fixture names and recursive parameter values/types,
  marks and full IDs. Exact collection, unchanged positive observation, real
  helper calls, normal strict checks, release intent, exact-head review and
  fresh complete CI still precede admission and source completion.
  Exact collection now verifies 43 functions / 272 originals against
  seven functions / 21 adapter cases; 36 functions / 251 cases are excluded
  before execution. Original function identities, fixture names, recursive
  parameter values/types, own marks and full IDs match. Unchanged positive
  observation passes 21 in 1.29 pytest / 2.110 observation seconds, with
  zero prohibited effects, three bound Python reads, all 63 phase reports
  and real command-safety call lines per case. No native Windows parser
  call lines occur. Add only adapter/original bindings and the existing
  command-safety production target: 9,729 nodes, 1,373 sources, 494 targets
  and 1,040,021 one-space v2 bytes. Preserve all prior entries/order/hashes,
  89 helpers, 1 MiB cap and 300-second bound. Ruff and normal changed-code
  Linux/Windows strict checks pass; a worktree-manager patch changefile
  records release intent. #14237 receives exact-head approval; fresh
  attempt-one CI executes all 9,729 cases in 193.019 packet seconds,
  completes the three-file upload and passes the required aggregate.
  Normal managed merge yields `a539a408ff81469583784d539ac7860212fff156`.
  The independent Driver change #14224 advances Dev during review: all
  five owned blobs and the exact manifest equal the reviewed head, but the
  entire merged tree differs by that peer change. Source CI qualifies the
  reviewed head, not an asserted combined-tree execution. Managed
  pull-forward succeeds after a verified concurrent-fetch ref race; no
  ref deletion, reset, stash or repair is used. All three observed inputs
  and complete source/helper bindings verify. Actual parent Goal, Plan,
  own leaf and Validation Plan are reread before resolving only this leaf.
- [x] Qualify six complete original shell/environment policy functions as
  provisionally 35 cases: environment mutation and split-string handling,
  GNU split-extension refusal, shell-state invalidation, explicit private
  transport and namespace expansion. Preserve the original module-local
  POSIX policy patches, functions/assertions/fixture names and recursive
  parameter values/types, marks and full IDs. Original callback cases
  collect `addaudithook` into a list and directly invoke that callback;
  no actual hook activation, audit event, native parser or backend operation
  is selected. Require exact collection, unchanged positive observation,
  real helper calls, normal strict checks, release intent, exact-head review,
  fresh complete CI, normal merge and actual parent readback.
  Exact collection verifies 43 functions / 272 originals against six
  functions / 35 adapter cases; 37 functions / 237 cases are excluded
  before execution. Original function identities, fixture names, recursive
  parameter values/types, own marks and complete IDs match. Unchanged
  observation passes 35 in 1.38 pytest / 2.109 observation seconds, with
  zero prohibited effects, three bound Python reads, all 105 phase reports
  and real command-safety call lines per case. Native parser/Windows token
  body lines are absent. Add only the adapter binding: 9,764 nodes, 1,374
  sources and the same 494 targets in 1,043,839 one-space v2 bytes. Preserve
  all prior entries/order/hashes, 89 helpers, 1 MiB cap and 300-second bound.
  Ruff and normal changed-code Linux/Windows strict checks pass; a
  worktree-manager patch changefile records release intent. Exact-head
  review, fresh full CI, normal merge and actual parent readback remain.
  #14241 receives exact-head approval `6021004306`, but fresh attempt-one
  CI `45166572452` is cancelled at the unchanged five-minute job limit.
  Unit job `137664941138` records all 9,764 cases passing in 224.64 pytest /
  250.609 packet seconds and computes executed-packet metadata, but the
  qualification step is cancelled before artifact upload. The provider
  explicitly reports exceeding `5m0s`; artifact inventory is empty.
  Guards pass, required aggregate `137667524743` fails and that source head
  remains unmerged. This is not a qualified packet or a known-flake waiver.
  No retry, deadline increase, evidence substitution, reduced selection or
  automatic CPU-optimization assignment follows from the failure.
- [x] Complete the separately selected pure packet-CPU correction in #14241.
  Replace phase-proof validation's per-report full inventory scan with one
  local membership set, retaining every collection, report shape/type,
  duplicate/unexpected phase, positive-outcome, effect and elapsed-bound check.
  A focused comparison-work regression fails on the original code and passes
  after correction; all 166 phase/packet acceptance and refusal cases pass.
  At the unchanged 9,764-node / 29,292-report synthetic workload, three-trial
  median validation time changes from 1.545238 to 0.080254 seconds. This is
  pure function CPU measurement, not full packet or CI timing.
  Unchanged guarded observation of all 166 cases passes in 3.77 pytest /
  4.500 observation seconds with zero prohibited effects, four bound Python
  reads, all 498 reports and actual owner calls. Add one regression witness
  and refresh only the two changed source bindings; preserve all 9,764 prior
  witnesses/order, source keys, 494 targets, 89 helpers, 1 MiB cap and
  300-second bound. Candidate totals are 9,765 / 1,374 / 494 / 1,043,986 bytes.
  Ruff and normal changed-code Linux/Windows strict checks pass.
  The operator subsequently directs publication now without waiting for the
  unpublished independent envelope PR, with managed rebase if it later lands.
  Corrected-head review and complete fresh CI, normal merge and actual
  parent readback remain.
  Corrected publication `84890980dc5051988e6daddcd1b2bb84026e9340` then
  reports a Dev-base conflict before ordinary unit CI starts. A retained
  backup precedes managed sync and its safe abort; the understood manual
  rebase keeps both independent manifest groups. The combined inventory
  preserves every node and relative order from both branches, all peer
  bindings/targets and our three unchanged Python blobs: 9,786 cases,
  1,377 sources, 495 targets and 1,046,031 bytes, still with 89 helpers,
  the 1 MiB cap and 300 seconds. This reconciliation is not new-head CI.
  New exact-head approval `6022276028` names
  `6a56d862d0503f6e94119bf09508b2f8d5a33ccb`; prior approvals are dismissed.
  Fresh attempt-one CI `45181064013` executes all 9,786 cases in 244.386
  packet seconds and completes three-file artifact `14821507812`; guards
  and required aggregate `137717573099` pass. Normal managed merge yields
  `6ae8d269bc691236c09eb45fc9f05f5f8d135141`; the entire reviewed and merged
  trees are equal. A safe post-merge sync abort is reconciled only after
  clean-state/tree-equality checks, retaining backups and using an empty-range
  local rebase before supported sync succeeds. All source/helper bindings
  and four CPU/three shell observed inputs verify. Actual parent Goal,
  Plan, both own leaves and Validation Plan are reread before resolving
  only these source leaves. The original cancelled attempt remains distinct;
  no whole-CI savings, native, rollout or outer-envelope completion is claimed.
- [x] Continue the original source-inspection census with four complete
  command-safety functions, provisionally 23 cases: child-source preflight
  without importing it, harmless exec/spawn/lookup source inspection,
  non-call mock/comment handling and benign-source AST avoidance.
  Preserve every original assertion and fixture, require exact collection
  and unchanged positive observation, and measure prospective storage before
  execution/admission. The current packet has only 2,545 bytes below its
  unchanged 1 MiB cap; do not omit witnesses or widen limits to fit.
  Exact collection verifies 43 functions / 272 originals against four
  functions / 23 adapter cases, excluding 39 functions / 249 cases before
  execution. Original function identities, fixture names, recursive parameter
  values/types, own marks and full IDs match. Initial one-space v2 storage
  would be 1,048,977 bytes, 401 over the cap; no case is executed or packet
  written at that refusal. Within the existing v2-formatting direction,
  remove only JSON separator spaces, retaining one-space indentation.
  Whitespace-only storage is 1,044,649 bytes with identical admitted values
  and logical identity. The 23-case candidate fits at 1,047,594 bytes.
  Unchanged positive guarding then passes all 23 in 0.86 pytest / 1.406
  observation seconds, with zero prohibited effects, three bound Python
  reads, all 69 reports and actual command-safety call lines per case.
  Add only the adapter binding: 9,809 cases, 1,378 sources and the same
  495 targets; preserve every preceding witness/order/hash, 89 helpers,
  1 MiB cap and 300 seconds. Three compact-reader/identity controls, Ruff
  and normal changed-code Linux/Windows strict checks pass. A
  worktree-manager patch changefile records release intent. Fresh complete
  CI, exact-head review, normal merge and actual parent readback remain.
  #14250 receives exact-head approval `6022548848`; fresh attempt-one CI
  `45184257701` executes all 9,809 cases in 231.575 packet seconds,
  completes artifact `14823481305` and passes required aggregate
  `137728670844`. Normal managed merge yields
  `ec4db6cc3c188711ef1c7439a0a5fea96791d2be`; the entire reviewed and merged
  trees are equal. Managed pull-forward succeeds; all source/helper bindings
  and three observed inputs verify. Actual parent Goal, Plan, own leaf
  and Validation Plan are reread before resolving only this source leaf.
  The three command-safety adapters cover 79 of 272 originals; 193 remain
  unselected, not deleted or qualified. No whole-flow saving is inferred.
- [x] Qualify three complete original launcher/cwd-classification functions,
  twenty-one cases: local launcher/private-namespace admission (twelve),
  refusal of cleanup following an executable lookup (one), and already-traced
  PowerShell working-directory invalidation (eight), batched before
  implementation to avoid a separate PR/CI cycle.
  Preserve original functions/assertions/fixture names and recursive
  parameter values/types, marks and full IDs. List-argument recursion and
  fixture-owned namespace values select no actual command, backend, audit
  activation or native string parsing. Measure prospective storage first;
  only 982 bytes remain below the unchanged cap. Require exact collection,
  unchanged positive observation, actual helper calls, normal strict checks,
  release intent, fresh full CI, exact-head review, merge and parent readback.
  Exact collection verifies 43 functions / 272 originals against three
  functions / 21 adapter cases; 40 functions / 251 cases are excluded
  before execution. Original function identities, fixture names, recursive
  parameter values/types, own marks and full IDs match. Prospective existing
  storage is 1,049,577 bytes, 1,001 over the cap. Within the existing
  v2-formatting direction, remove only presentation indentation while keeping
  one item per line and no separator spaces: existing storage becomes
  998,780 bytes with identical admitted values/logical identity. The
  21-case candidate fits at 1,000,664 bytes. Unchanged positive guarding
  passes all 21 in 0.77 pytest / 1.453 observation seconds with zero
  prohibited effects, three bound Python reads, all 63 reports and actual
  command-safety call lines per case. Add only the adapter binding: 9,830
  cases, 1,379 sources and the same 495 targets. Preserve all prior ordered
  witnesses/hashes, 89 helpers, 1 MiB cap and 300 seconds. Three compact-
  reader/identity controls, Ruff and normal changed-code Linux/Windows strict
  checks pass; a worktree-manager patch changefile records release intent.
  Fresh full CI, exact-head review, normal merge and parent readback remain.
  #14252 receives exact-head approval `6022841523`; fresh attempt-one CI
  `45187415198` executes all 9,830 cases in 245.382 packet seconds,
  completes artifact `14824197015` and passes required aggregate
  `137739650369`. Normal managed merge yields
  `b0fb7d69efeb40edf4d02b9b8d16e8afbc2b3c35`; the entire reviewed and merged
  trees are equal. Managed pull-forward succeeds; all source/helper bindings
  and three observed inputs verify. Actual parent Goal, Plan, own leaf
  and Validation Plan are reread before resolving only this source leaf.
  Four command-safety adapters cover 100 of 272 originals; 172 remain
  unselected, not deleted or qualified. No whole-flow saving is inferred.
- [x] Assess five complete original AST-fixture control functions as six
  source-only unit candidates, preserving every assertion and original double.
  They extract selected helper bodies as AST data; never import or execute the
  native fixture module. Prove closed source binding and actual helper-body
  call coverage under unchanged positive observation. Original signal,
  command, observer and timer doubles must remain; no real backend, native
  lifecycle, audit activation or qualification is selected. Stop on any
  admission/effect refusal rather than changing the original, fixture, guard,
  preload or phase contract. Normal source review/CI/merge still apply.
  Exact collection verifies 43 functions / 272 originals against five
  functions / six adapter cases, excluding 38 functions / 266 cases before
  execution. Original function identities, fixture names, recursive parameter
  values/types, marks and full IDs match. Prospective admission binds the
  actual fixture source as an input/helper-body target, not a module to run.
  Unchanged positive guarding passes all six in 0.94 pytest / 1.734
  observation seconds, with zero prohibited effects, four bound Python
  reads, all eighteen reports and actual expected helper-body calls per
  case, not definition-only coverage. Original signal/command/timer/observer
  doubles remain intact. Add only adapter/fixture bindings and that helper
  target: 9,836 cases, 1,381 sources, 496 targets and 1,001,540 zero-indent
  v2 bytes. Preserve all prior ordered witnesses/hashes, 89 helpers,
  1 MiB cap and 300 seconds. Ruff and normal changed-code Linux/Windows strict
  checks pass; a worktree-manager patch changefile records release intent.
  Fresh full CI, exact-head review, normal merge and parent readback remain.
  Initial #14253 head `787637c562ded9bf9545a6d3bbf80e23562cd667`
  receives exact approval and fresh attempt-one CI `45190875919` executes
  all 9,836 cases in 142.976 packet seconds with a complete three-file
  upload. The final fence then observes a conflicting Dev-base advance,
  so that head is not merged. A retained backup and safe managed abort
  precede the understood target-union rebase onto
  `ce6d8b9c1fed4dfe862986736116df2c00c11a5e`. Preserve the peer's current
  witness rename/new case, all peer bindings/targets and our exact six
  cases/two inputs. All four observed inputs remain unchanged. Actual
  combined totals are 9,837 / 1,383 / 497 / 1,001,889 bytes, with 89 helpers
  and existing bounds retained. The prior CI is not combined-head
  qualification; fresh review and complete CI remain required.
  New exact-head approval `6023331545` names
  `c17638e0a49ebdd6b76d5de3b4a4faca2228c1f9`; the old approval is dismissed.
  Fresh attempt-one CI `45193464085` executes all 9,837 cases in 237.198
  packet seconds, completes artifact `14826523924` and passes required
  aggregate `137760627518`. The local watcher exits on a jobs-list HTTP 500,
  but independent job/run reads confirm success; no CI retry is triggered.
  Normal managed merge yields `dfb7961ee2ef0a986706694e7e6c3b8e02b60105`;
  the entire reviewed and merged trees are equal. After a safe sync abort
  replaying the two squash-merged commits, clean-state/tree-equality checks
  and retained backups precede empty-range local rebase and successful
  supported sync. All bindings and four observed inputs verify. Actual
  parent Goal, Plan, own leaf and Validation Plan are reread before
  resolving only this source-only leaf. Five adapters cover 106 of 272
  original command-safety cases; 166 remain unselected, not deleted or
  qualified. No actual native/lifecycle/custody acceptance follows.
- [x] Assess two original portable containment functions, provisionally
  five cases: sandbox cleanup signature compatibility (two) and private
  PowerShell module-path identity (three). Preserve original assertions and
  fixtures; bound cleanup to the test's own temporary files. Complete the
  original module's top-level/import/decorator audit before collection.
  Require closed input binding, actual helper calls and unchanged positive
  observation before normal strict/review/CI/merge. Select no real child,
  preparation, registry, service, native lifecycle or retained-resource cleanup.
  Static inventory completes before collection: the original module has only
  imports, function definitions and static parameter/platform decorators;
  the portable containment imports defer Darwin/native binding loads.
  Exact collection finds 60 original functions / 118 cases and two unchanged
  adapter functions / five cases, excluding 58 functions / 113 cases before
  execution. Function identity, fixtures, recursive parameter values/types,
  marks and full IDs match. Unchanged positive guarding passes all five in
  1.30 pytest / 2.375 observation seconds, with zero prohibited effects,
  ten bound Python reads and all fifteen phase reports. Each case contributes
  actual body-call coverage to `native_test_paths.py::_rmtree` or
  `plugin_test_environment.py::isolated_environment`, respectively. Cleanup
  reaches only the original fixture's own files; no TestSandbox instance,
  retained-resource cleanup, native loader or backend is invoked.
  Add only adapter/original bindings: 9,842 cases, 1,385 sources, unchanged
  497 targets / 89 helpers / 300 seconds and 1,002,610 zero-indent v2 bytes
  under the unchanged 1 MiB cap. No original, fixture, guard, preload or
  input-contract workaround is used. Fresh full CI, exact-head review,
  normal merge and parent readback remain required.
  Ruff and three compact/packet reader controls pass. The first normal
  strict check also selects an untouched Dispatch test against stale local
  Dev and reports two errors there; the adapter itself passes both targets.
  Preserve that result. Supported sync incorporates #14255; the refreshed
  normal `origin/dev` check selects only this adapter and passes Linux and
  Windows without changing any source/checker admission.
  Final comparison identifies #14255's one existing Dispatch installer
  source-hash refresh, which is preserved. All ten observed inputs remain
  unchanged; the final composition must equal current Dev plus only this
  slice's five ordered nodes/two source bindings, not the pre-sync candidate.
  #14259 receives exact-head approval `6024055181` with zero active findings,
  comments or unresolved threads and complete pagination. Fresh attempt-one
  CI `45201249013` executes all 9,842 cases with 55 deselections in 222.77
  pytest / 245.969 packet seconds, finalizes the actual three-file artifact
  `14832920311`, and passes guards and aggregate `137788850367`.
  Seven fresh required-check reads and final head/base/hold/parent-reference
  fences precede normal managed merge at `2026-11-23T15:14:23Z` as
  `483fc02d286905ba8760da75ecf2e635afc66498`.
  Independent provider readback confirms merged state and open parent;
  supported source sync succeeds. The entire reviewed and merged trees
  match; all bindings, 89 helpers and ten observed inputs verify unchanged.
  The actual Goal, Plan/own leaf and Validation Plan are reread before
  resolving only this leaf. Broader native, release, owner and custody gates
  remain open; this is no whole-flow savings or parent-completion claim.
- [x] Assess the original synthetic environment-selection function (six
  prospective cases) and patched-OS path-spelling function (three) from
  the same containment module. Keep explicit fixture dictionaries and
  the original per-module OS double; select no actual host-state capture,
  backend or native lifecycle. Require exact original objects/fixtures,
  parameters/types, marks and full IDs, closed inputs, actual production
  body calls and unchanged positive guarding before normal gates/merge.
  Do not select the two test-helper-only MAX_PATH assertions as production
  call coverage or manufacture a target for them.
  Exact collection verifies 60 original functions / 118 cases against two
  original functions / nine adapter cases; 58 functions / 109 cases are
  excluded before execution. Exact objects, fixture names, recursive
  parameter values/types, marks and full IDs match. Unchanged positive
  guarding passes all nine in 1.38 pytest / 2.297 observation seconds with
  zero prohibited effects, ten bound Python reads and all 27 phase reports.
  Each case contributes actual `isolated_environment` or `native_test_path`
  body calls. Add only the adapter binding: 9,851 cases, 1,386 sources,
  unchanged 497 targets / 89 helpers / 300 seconds, and 1,003,638 compact
  v2 bytes under the unchanged 1 MiB cap. Four functions / fourteen cases
  across both containment adapters are selected; 104 originals remain
  unselected, not deleted or qualified. Normal strict, fresh full CI,
  exact-head review, merge and parent readback remain required.
  Ruff and three reader controls pass. An initial normal strict selection
  also reaches untouched Vault files and stops for missing prepared tooling;
  no preparation or peer edit is attempted. Supported sync incorporates
  #14260 at `57a97c20a112018eb1f7c8ccd572bdefe7831857`; the refreshed
  normal `origin/dev` check selects only the adapter and passes Linux and
  Windows. Final composition preserves the peer inventory and equals current
  Dev plus exactly nine ordered nodes / one binding: 9,856 cases, 1,389
  sources, 500 targets and 1,004,604 bytes, with 89 helpers and existing
  bounds retained. All ten observed inputs remain unchanged.
  #14261 head `144fcf512e2124b890b3d32d2679e6422c65daf2` receives exact
  approval `6024362787`, zero active findings/comments/threads and complete
  pagination. Attempt-one CI `45204956081` executes all 9,856 cases with
  55 deselections in 223.19 pytest / 247.066 packet seconds, but the outer
  five-minute job limit cancels the three-file upload after bytes transfer
  and before artifact finalization. Guards pass; required aggregate
  `137801668132` fails. This is not complete packet qualification and the
  PR remains open/unmerged. Do not retry the same head or bypass CI.
  The independent ten-minute outer-envelope change is not present at this
  source; the earlier pure-CPU decision applied to #14241, not a new
  blanket performance or workflow grant. A bounded recovery decision is
  required before changing this PR or executing another attempt.
  The operator selects `wait-for-existing-envelope-owner`: wait for
  #13234's existing ten-minute outer CI-envelope change to merge through
  normal gates, then rebase #14261 and requalify a new head. No retry or
  bypass. #13234 is currently an open draft and owns its broader work;
  waiting does not authorize changing, superseding or merging it here.
  Verify the merged envelope and retained 300-second packet bound before
  the supported rebase; preserve all peer/current inventories and require
  fresh exact-head review, complete CI/upload and parent readback.
  The subsequent role clarification keeps this campaign's driver accountable
  for its own #14261 rather than treating another owner's draft as its whole
  assignment. Provider attribution and passive workspace inspection identify
  #13234's existing controller; no takeover or replacement is attempted.
  [Coordination request 7155413748](https://github.com/ThomasMichon/copilot-extensions/pull/13234#issuecomment-7155413748)
  asks that owner to separate the already-written enclosing-job correction,
  matching guard, focused controls and necessary documentation into an
  independently reviewed change, preserving the 300-second packet and all
  native gates. Exact comment readback matches; posting is not acceptance.
  Live-conversation inspection is unavailable before any prompt admission;
  no message replay, service repair or new peer is attempted. The failed
  attempt and the operator's ordinary-merge/rebase/requalification order
  remain intact.
  November 25 delivery reconciliation finds the exact enclosing-job change
  already merged independently in #11248 at
  `d441fbd689278ae7605c0b102ad80eb4861d7f4a`, with successful required source
  CI, unit execution/upload and ordinary review. Its five-to-ten-minute job
  change retains the 300-second packet. This satisfies the specific
  publication prerequisite without accepting or taking over #13234's wider
  native work. The original failed attempt remains failed.
  A backed-up rebase onto Dev `e306259aa1564c4c416a2e177ffca59ef24dde93`
  retains every upstream ordered node and source binding, plus all seventy
  owned additions across the eight adapters. The combined compact-v2 manifest
  has 10,145 cases, 1,453 verified sources, 529 targets and 92 helper decisions
  in 1,037,932 bytes. The 1 MiB storage cap and 300-second packet remain.
  All seventy-five selected containment cases across nine adapters pass fresh
  current-source guarding in 2.53 pytest / 17.390 observation seconds, with
  zero prohibited effects, twenty-six admitted Python reads and actual
  per-case production call coverage. Whole-file strict typing passes twelve
  physical files in two projects on Linux and Windows; focused Ruff and
  whitespace checks pass. This is local requalification, not complete hosted
  packet/upload evidence, native acceptance, a runtime update or parent
  completion. Fresh exact-head CI and review remain required.
  Published candidate `d9ff3e2d89d44453cc6387b2c29b55f4c85cbe10` receives
  Copilot approval `6059164492` with no active issues. CI `45629607523`
  passes all 10,145 units in 159.74 pytest / 181.612 packet seconds and
  finalizes artifact `15029509815`; however, its required source aggregate
  fails because the newly merged base workflow invokes #14535's validator,
  absent from that earlier source head. No merge or retry follows.
  Supported sync onto Dev `bdf770ddc2a301d393306940d503fafb33d0515a`
  incorporates that guard and two independent source changes. The node
  inventory and targets remain; 1,454 sources verify in 1,038,052 bytes,
  with 92 helpers and unchanged limits. All twenty-six observed inputs of
  the local seventy-five-case cohort retain exact hashes, so that settled
  local test is not replayed. The inherited hook guard passes, as does
  whole-file strict Linux/Windows checking of all eight changed Python
  files. Fresh hosted CI/review for the pulled-forward head remains required;
  neither the prior approval nor successful units erase its failed aggregate.
  #14261 then merges at `96cd5c06ee0c18089a0261f6202436caf5c4c5f0`
  on November 25 at 14:34:31 UTC. Current-head approval `6059554092`
  reports no active issues; complete comment pagination is empty and seven
  fresh required-check reads verify exact head `700709a09a`. CI
  `45631884784` passes all 10,145 units in 175.42 pytest / 196.083 packet
  seconds, finalizes artifact `15032425883`, passes source guards and
  required aggregate `139270418719`, and precedes normal Maintainer merge.
  The parent remains open. Actual merge inspection preserves all eleven
  other owned files byte-for-byte; the manifest is exactly its current
  merge parent plus seventy ordered nodes / eight adapter bindings, retaining
  all peer hashes and targets. Its 1,455 sources include the independently
  advanced parent input, not a transfer of old unit evidence to that input.
  The initial squash pull-forward conflict is retained; after this explicit
  content proof, the already-merged commits are advanced past and supported
  sync succeeds without reset, stash, deletion or a new source session.
  Goal, Plan and Validation Plan are reread, and only these eight finite
  source leaves are resolved. Next is the source-bound metadata census at
  the changed original module, without executing held consumers. The
  separate legacy-monitor run `45629597791` retains Linux and Windows
  setup failures: `installer_engine` cannot import `retained_procutil`
  from the plugin-local `installer_wheel_snapshot`. Both inputs are outside
  this source diff. This is not a native pass, retry, accepted flake, repair,
  release, rollout, custody settlement or whole-parent completion.
- [x] Qualify the fifteen original callback-routing and fake-owner close
  cases locally while the accepted enclosing-job correction remains a
  publication prerequisite. Keep all five complete original functions,
  assertions, fixture names, recursive parameter values/types, marks and full
  IDs. The routing test retains its original module-local OS and reader
  callbacks; close tests use `object.__new__`, original fake owners,
  retirement callbacks, kernel functions and clocks, never a native Job
  constructor or backend. Do not select the four actual-thread tree-size
  functions or replace their platform/guard/fixture behavior.
  Exact collection verifies 60 original functions / 118 cases against five
  functions / fifteen cases, excluding 55 functions / 103 cases before
  execution. Unchanged positive guarding passes all fifteen in 1.31 pytest /
  2.578 observation seconds, with zero prohibited effects, ten admitted
  Python reads and all 45 reports. Every case intersects the actual
  `_tree_size` or `_WindowsJob.close` body during its call phase.
  Add only the adapter binding: local candidate 9,871 cases, 1,390 sources,
  unchanged 500 targets / 89 helpers / 300 seconds, and 1,006,035 compact
  v2 bytes under the unchanged 1 MiB cap. Preserve all prior ordered values
  and hashes. Across three adapters, nine functions / 29 of 118 originals
  are selected; 89 cases remain unselected, not deleted or qualified.
  Local preparation is not publication or full-packet qualification.
  No hosted run, retry, rebase or push is performed. Current-Dev strict
  typing after the supported rebase, fresh complete CI/upload, exact-head
  review, ordinary merge and parent readback remain required.
  Ruff passes. Development-isolated whole-file strict typing selects exactly
  the new adapter against `HEAD` and passes Linux and Windows; this is
  explicitly not current-Dev publication qualification. The final local
  candidate equals the published inventory plus exactly fifteen ordered
  nodes / one binding. All ten observed inputs, original tests, production
  modules and effect guard remain byte-identical; every old ordered source
  value/target and all 89 helper decisions verify. The existing changefile
  keeps the same worktree-manager patch intent.
- [x] Qualify sixteen original POSIX-double cases locally, preserving twelve
  complete functions and every existing assertion, fixture, parameter
  value/type, mark and full ID. The originals already double identity,
  membership, signals, clocks, descriptor reads/allocation/close, process
  launch and reaping. The two orchestration controls write only their
  fixture-owned ready file. No actual process, signal, native pipe, reaper,
  backend or retained-resource cleanup is selected. Keep the zombie-heavy
  platform-resource case outside this selection.
  Collect-only initially stops because a development comparison helper
  compares independently constructed PermissionError values by identity;
  no test executes and no candidate is admitted. Correct only that helper
  to compare exact exception types, arguments, custom and OS-error
  attributes, additionally checking the adapter retains the original
  parameter instance. Original tests, fixtures and the effect guard remain
  unchanged. Exact comparison then verifies 60 functions / 118 originals
  versus twelve functions / sixteen cases, excluding 48 functions / 102
  cases before execution. Unchanged positive guarding passes all sixteen
  in 1.26 pytest / 2.203 observation seconds, zero prohibited effects, ten
  admitted Python reads and all 48 reports. Each case contributes its
  expected production-body call lines, not merely a definition.
  Add only the adapter binding and the already-bound POSIX parser as a
  coverage target: local candidate 9,887 cases, 1,391 sources, 501 targets,
  89 unchanged helpers, 300 seconds and 1,007,290 compact-v2 bytes under the
  unchanged 1 MiB cap. Across four containment adapters, 21 original
  functions / 45 of 118 cases are selected; 73 cases remain unselected,
  not deleted or qualified. This remains unpublished local preparation.
  Accepted enclosing-job correction, supported rebase/current-Dev strict
  checks, fresh complete CI/upload, exact-head review, ordinary merge and
  parent readback remain required; no hosted retry, push or rebase here.
  Ruff passes. Development-isolated whole-file strict typing against `HEAD`
  selects exactly the new adapter and passes Linux and Windows; current-Dev
  publication typing remains pending. Final local comparison proves the
  prior candidate plus exactly sixteen ordered nodes / one binding / one
  already-bound production target. All ten observed inputs, original tests,
  production modules and effect guard remain byte-identical; every prior
  ordered value/hash/target and all 89 helper decisions verify.
- [x] Qualify six original worker-double cases locally, preserving four
  complete functions and every assertion, fixture, recursive parameter
  value/type, mark and full ID. The controller/worker functions retain
  original process, signal, descriptor and reaper doubles; the Linux
  membership parser reads only original fixture files through its existing
  Path redirect and synthetic page-size callback. No real process, signal,
  descriptor or reaper operation is selected. Actual owner-death tests
  remain outside selection.
  Exact collection verifies 60 functions / 118 originals versus four
  functions / six cases, excluding 56 functions / 112 cases before execution.
  Unchanged positive guarding passes all six in 1.10 pytest / 1.938
  observation seconds, zero prohibited effects, ten admitted Python reads
  and all eighteen reports. Each case contributes its expected production
  body calls, including both worker entry and worker loop for the controller
  case. The two abandoned-worker cases do not reach the unpatched descriptor
  close line.
  Add only the adapter binding: local candidate 9,893 cases, 1,392 sources,
  unchanged 501 targets / 89 helpers / 300 seconds, and 1,008,010 compact-v2
  bytes under the unchanged 1 MiB cap. Across five containment adapters,
  25 functions / 51 of 118 cases are selected; 67 remain unselected, not
  deleted or qualified. This is unpublished local preparation; accepted
  enclosing-job correction, supported rebase/current-Dev strict checks,
  fresh complete CI/upload, exact-head review, ordinary merge and parent
  readback remain required. No hosted retry, push or rebase here.
  Ruff passes. Development-isolated whole-file strict typing against `HEAD`
  selects exactly this adapter and passes Linux and Windows; current-Dev
  publication typing remains pending. Final local comparison proves the
  previous candidate plus exactly six ordered nodes / one binding, with
  every old value/hash/target, all ten observed inputs, original and
  production/effect-guard bytes, and all 89 helper decisions preserved.
- [x] Qualify six original reaping/assignment-double cases locally, keeping
  two complete functions and every existing assertion, fixture, recursive
  parameter value/type, mark and full ID. The original Process wait/kill/
  terminate methods, fake Job constructor/assign/close, module-local OS,
  launch and stdio doubles remain. No actual process, Job, native handle,
  descriptor or reaper operation is selected.
  Exact collection verifies 60 functions / 118 originals versus two
  functions / six cases, excluding 58 functions / 112 cases before execution.
  Unchanged positive guarding passes all six in 0.88 pytest / 1.500
  observation seconds, zero prohibited effects, ten admitted Python reads
  and all eighteen reports. Each case contributes actual `_reap_controller`
  body calls; the assignment cases also contribute `_run_contained_process`
  body calls.
  Add only the adapter binding: local candidate 9,899 cases, 1,393 sources,
  unchanged 501 targets / 89 helpers / 300 seconds, and 1,008,665 compact-v2
  bytes under the unchanged 1 MiB cap. Across six containment adapters,
  27 functions / 57 of 118 cases are selected; 61 remain unselected, not
  deleted or qualified. Local preparation is unpublished; accepted enclosing-
  job correction, supported rebase/current-Dev strict checks, fresh complete
  CI/upload, exact-head review, ordinary merge and parent readback remain.
  Ruff passes. Development-isolated whole-file strict typing against `HEAD`
  selects exactly the new adapter and passes Linux and Windows; current-Dev
  publication typing remains pending. Final local comparison proves the
  previous candidate plus exactly six ordered nodes / one binding, with
  every old value/hash/target, all ten observed inputs, original and
  production/effect-guard bytes, and all 89 helper decisions preserved.
- [x] Qualify the original native-application preflight refusal function's
  four cases locally. Preserve the explicit original fixture environment,
  failure sentinel, assertions, fixtures, recursive parameter values/types,
  marks and full IDs. Host, self-hosted, outside-root and already-contained
  inputs refuse before any registry, native scope, pin or lifecycle entry.
  The flag is a fixture input, not a native-execution grant.
  Exact collection verifies 60 functions / 118 originals versus one
  complete function / four cases, excluding 59 functions / 114 cases before
  execution. Unchanged guarding passes all four in 1.11 pytest / 2.047
  observation seconds, zero prohibited effects, eleven admitted Python
  reads and all twelve reports. Each case contributes actual public-wrapper
  and lifecycle-preflight body calls. Registry-read, native-scope and pin
  method bodies are not entered; the original lifecycle failure sentinel
  remains unchanged.
  Add only the adapter binding: local candidate 9,903 cases, 1,394 sources,
  unchanged 501 targets / 89 helpers / 300 seconds, and 1,009,199 compact-v2
  bytes under the unchanged 1 MiB cap. Across seven containment adapters,
  28 functions / 61 of 118 cases are selected; 57 remain unselected, not
  deleted or qualified. This is unpublished refusal-only preparation.
  Accepted enclosing-job correction, supported rebase/current-Dev strict
  checks, fresh complete CI/upload, exact-head review, ordinary merge and
  parent readback remain required; no hosted retry, push or rebase here.
  Ruff passes. Development-isolated whole-file strict typing against `HEAD`
  selects exactly the new adapter and passes Linux and Windows; current-Dev
  publication typing remains pending. Final local comparison proves the
  previous candidate plus exactly four ordered nodes / one binding, with
  every old value/hash/target, all eleven observed inputs, original and
  production/effect-guard bytes, and all 89 helper decisions preserved.
- [x] Qualify four original fake-pin cases locally, preserving three complete
  functions and every assertion, fixture, recursive parameter value/type,
  mark and full ID. Synthetic records, Mock processes and patched birth/
  handle functions remain; reads stay in the original fixture directory.
  No actual process, birth, native handle, registry or lifecycle operation
  is selected. Inventory refusal retains the original no-decoding sentinel.
  Exact collection verifies 60 functions / 118 originals versus three
  functions / four cases, excluding 57 functions / 114 cases before
  execution. Unchanged guarding passes all four in 0.78 pytest / 1.485
  observation seconds, zero prohibited effects, twelve admitted Python
  reads and all twelve reports. Each case contributes actual pin-object
  constructor/observe body calls, plus close for the three applicable
  cases. Binding, lifecycle, native-scope and real inspection-proxy bodies
  are not called.
  Add only the adapter binding: local candidate 9,907 cases, 1,395 sources,
  unchanged 501 targets / 89 helpers / 300 seconds, and 1,009,736 compact-v2
  bytes under the unchanged 1 MiB cap. Across eight containment adapters,
  31 functions / 65 of 118 cases are selected; 53 remain unselected, not
  deleted or qualified. This is unpublished local preparation; accepted
  correction, supported rebase/current-Dev strict checks, fresh complete
  CI/upload, exact-head review, ordinary merge and parent readback remain.
  Ruff passes. Development-isolated whole-file strict typing against `HEAD`
  selects exactly the new adapter and passes Linux and Windows; current-Dev
  publication typing remains pending. Final local comparison proves the
  previous candidate plus exactly four ordered nodes / one binding, with
  every old value/hash/target, all twelve observed inputs, original and
  production/effect-guard bytes, and all 89 helper decisions preserved.
- [x] Qualify ten original lifecycle-outcome/user-bus cases locally, keeping
  two complete functions and every assertion, fixture, recursive parameter
  value/type, mark and full ID. The six outcome cases already replace
  scope selection, pin and lease contexts, process execution, and registry
  hooks with original doubles. Their settled/held/error outcomes are
  synthetic, not evidence about real cleanup or retained custody. The four
  bus cases retain original fake platform, UID, Path/lstat and caller-unit
  callbacks; no real process scope, socket or user bus is inspected.
  Exact collection verifies 60 functions / 118 originals versus two
  functions / ten cases, excluding 58 functions / 108 cases before execution.
  Unchanged guarding passes all ten in 1.24 pytest / 2.313 observation
  seconds, zero prohibited effects, twelve admitted Python reads and all
  thirty reports. Outcome cases contribute actual wrapper bodies and bus
  cases contribute actual scope-selection body calls. Real lease, pin,
  process, registry and Linux scope/probe bodies are not called.
  Add only the adapter binding: local candidate 9,917 cases, 1,396 sources,
  unchanged 501 targets / 89 helpers / 300 seconds, and 1,010,762 compact-v2
  bytes under the unchanged 1 MiB cap. Across nine containment adapters,
  33 functions / 75 of 118 cases are selected; 43 remain unselected, not
  deleted or qualified. This is unpublished local preparation; accepted
  correction, supported rebase/current-Dev strict checks, fresh complete
  CI/upload, exact-head review, ordinary merge and parent readback remain.
  Ruff passes. Development-isolated whole-file strict typing against `HEAD`
  selects exactly the new adapter and passes Linux and Windows; current-Dev
  publication typing remains pending. Final local comparison proves the
  previous candidate plus exactly ten ordered nodes / one binding, with
  every old value/hash/target, all twelve observed inputs, original and
  production/effect-guard bytes, and all 89 helper decisions preserved.
- [x] Account for the original containment module's current selected and
  unselected identities without executing held consumers. Collect-only
  comparison verifies 60 original functions / 118 cases and nine adapters /
  33 complete functions / 75 cases. No duplicate original identities or
  partial-function selections occur; the adapter IDs equal the manifest's
  exact cohort. The remaining 27 functions / 43 cases map exactly once to
  the thirteen source-audited disposition groups in the portfolio audit.
  Original/adapters' source hashes and manifest bytes remain unchanged.
  Metadata enumeration is not execution, fixture setup, current-Dev CI,
  live scheduling, accepted ownership transfer or whole-file qualification.
  All original assertions remain; publication, native/platform and parent
  acceptance gates stay open. Source changes invalidate this snapshot.
  The subsequent Dev rebase changes the original module through independently
  landed typing and cleanup controls, so the 118/75/43 census above remains
  a historical source-bound map, not a current whole-file census. All
  thirty-three selected functions and seventy-five adapter identities remain
  and pass fresh guarding; no added or previously excluded original consumer
  receives qualification from that result.
- [x] Refresh the changed original module's source-bound metadata census.
  At original SHA-256
  `a4ebac1c7089a68ee09532742e59724961943e16fdc3269baee8f5c36fc87bd5`,
  collection only finds 65 functions / 128 cases, with the same nine
  adapters / 33 complete functions / 75 cases and no duplicate or partial
  selections. All 118 prior originals and 43 prior residuals remain.
  The ten new originals comprise four fixture-only cleanup-retry functions /
  nine cases, plus one actual Windows handle/thread case. All 32 unselected
  functions / 53 cases remain outside execution. Source hashes, exact
  manifest bytes and every binding verify unchanged; no fixture setup,
  test body, native action or admission occurs during this census.
- [x] Qualify seven original synthetic cleanup-retry cases, keeping all
  three complete functions, original `tmp_path`, monkeypatch/capture fixtures,
  parameter identities/types, assertions and clock/sleep callbacks.
  `TestSandbox(dir=tmp_path)` scopes real filesystem work to caller-owned
  synthetic storage; its exit method delegates only to the existing cleanup
  retry helper or preserves the supplied incomplete/expired exception.
  Require exact collection, positive exit-body calls for every case,
  positive retry-body calls for five cases and absence for the two original
  ownership-refusal cases, closed inputs and unchanged effect guards.
  The cleanup helper is already source-bound; admit it as a production target
  only after actual body coverage. Keep the actual Windows handle/thread
  case outside, with no platform/fixture replacement or real sleep.
  Preserve every existing ordered node/hash/target, compact-v2 semantics,
  1 MiB cap, 300-second packet and all native/parent gates.
  The initial four-function / nine-case candidate collects exactly, then
  unchanged guarding stops after three passes at original
  `test_sandbox_cleanup_does_not_retry_other_errors[linux-5]`. Its original
  shared `sys.platform` patch prevents Windows extended-path normalization
  during finally-cleanup; the guard refuses `os.rmdir` as outside synthetic
  storage. Five later cases are unexecuted, not passing. No guard, fixture,
  parameter, OS workaround or failed-case replay follows. Exclude that whole
  two-case function, never only its failing row.
  Fresh exact collection of the other three complete functions preserves
  every fixture, recursive parameter value/type, mark and full ID. All seven
  pass in 0.92 pytest / 13.969 observation seconds with zero prohibited
  effects, fifteen admitted Python reads and all twenty-one phase reports.
  Every case calls the actual sandbox exit body; five call the cleanup
  retry body and both ownership-refusal cases do not. Add only the adapter
  binding and that already-bound cleanup helper's covered target:
  10,152 nodes / 1,456 sources / 530 targets / 92 helpers / 1,038,944
  compact-v2 bytes, preserving prior order, hashes and limits. Strict
  whole-file Linux/Windows checking and Ruff pass. Across ten adapters,
  36 functions / 82 of 128 originals are selected; 46 remain unselected.
  Fresh combined-head CI/upload, review, ordinary merge and parent
  readback remain required; no native, release or rollout claim follows.
  #14574 merges at `4ac43b76c4f7dd64c1cefb13de6647769ed0ae6e`
  on November 25 at 15:04:43 UTC after exact-head approval `6060032026`
  for `f49b21f987`. The full review has no active issues; complete comment
  pagination is empty. CI `45638967650` executes all 10,152 units in
  140.87 pytest / 158.549 packet seconds, finalizes artifact `15033944036`,
  and passes guards plus required aggregate `139295808993`. Seven fresh
  required-check reads and live head/base/hold/closing-reference checks
  precede normal managed Maintainer merge. Provider readback confirms merged
  state and open parent; supported source sync succeeds. The reviewed and
  merged trees match exactly; every source binding and helper decision
  verifies unchanged. Goal, this Plan leaf and the Validation Plan are
  reread before resolving only the seven-case source leaf.
  Continue the unchanged original-unit census without replaying retained
  refusals. The complete cross-platform function, actual Windows handle/
  thread case, old residuals and every native/release/custody gate remain.
- [x] Qualify the original hook-architecture string and fixture-only tests,
  retaining all eleven whole functions, original assertions, fixture names,
  recursive parameter values/types, marks and full IDs. Exact collect-only
  comparison measures 49 originals / 48 selected cases; only the complete
  real-repository allowlist scan remains excluded. Forty cases parse supplied
  strings; eight use original `tmp_path` plugin, hooks, script-text and JSON
  allowlist fixtures without executing script contents. Unchanged guarded
  execution passes all 48 in 1.34 pytest / 13.703 observation seconds, with
  zero prohibited effects, sixteen admitted Python reads and all 144 phase
  reports. Every case contributes actual checker call-body lines.
  Add only the adapter, original test and checker bindings, plus that
  checker's covered target: 10,200 nodes / 1,459 sources / 531 targets /
  92 helpers / 1,042,344 compact-v2 bytes, preserving prior ordered nodes,
  bindings, targets, the 1 MiB cap and 300-second packet. Supported sync
  incorporates #14556's three existing binding updates; all sixteen local
  observation inputs are unchanged, so settled tests are not replayed.
  Composition equals current Dev plus exactly this leaf. All bindings and
  helper contracts verify; normal current-Dev typing selects just the adapter
  and passes whole-file strict Linux/Windows checks, as does Ruff.
  Complete hosted CI/upload, review, ordinary merge and parent readback
  remain required. No hook policy, allowlist, workflow, production,
  native, release or rollout behavior is changed or newly qualified.
  #14577 merges at `8e72471e6af2c9ecf89ac316c7e7144b983c40f8`
  on November 25 at 15:41:28 UTC. Exact-head approval `6060637614`
  covers `11e598e936`, with no active issues and complete empty review/
  discussion-comment pagination. Initial CI `45646685813` passes all
  10,200 cases with 55 deselections in 120.26 pytest / 134.915 packet
  seconds, finalizes artifact `15039370152`, and passes guards and
  required aggregate `139321855077`. Seven fresh required-check reads
  and live head/base/hold/closing-reference checks precede normal managed
  Maintainer merge. Provider readback confirms merged state and open parent;
  supported source sync succeeds. The actual merge adds exactly the five
  reviewed owned files, whose blobs equal the reviewed head, atop concurrent
  base `0c6ee7c324`; its unrelated Index changes remain. This is not a
  whole-tree identity claim. Every binding and helper decision verifies.
  Goal, this leaf and the complete Validation Plan are reread before
  resolving only this source leaf. Continue the unchanged original-unit
  census; the real-repository scan and all native/release/custody gates remain.
- [x] Qualify the two eligible original pre-uv fixture functions, preserving
  original `tmp_path`, the `_tree` helper, assertions and complete function
  identities. Exact collection measures four originals / two selected cases:
  listed-fragment success and missing-fragment failure. The original checker
  reads synthetic manifest/entry/fragment bytes and compiles them in memory;
  no compiled content is executed. All two cases pass unchanged guarding in
  0.93 pytest / 13.125 observation seconds, with zero prohibited effects,
  sixteen admitted Python reads, six phase reports and actual checker-body
  coverage per case. Add only adapter/original/checker bindings and the
  checker's covered target: 10,202 nodes / 1,462 sources / 532 targets /
  92 helpers / 1,042,885 compact-v2 bytes. All prior ordered nodes, bindings,
  targets, the 1 MiB cap and 300-second packet remain. Whole-file strict
  checking, Ruff, complete hosted CI/upload, current-head review, normal
  merge and parent readback remain required; no native or runtime claim follows.
  The shared #14584 source delivery and final readback below resolve only
  this eligible pair, not either excluded pre-uv function.
- [x] Qualify both complete original release-scope fixture tests in the same
  functional publication as the pre-uv pair, avoiding a separate CI/review
  cycle. Exact collection measures two originals / two adapter cases with
  identical objects, fixtures, marks and full IDs. Original synthetic policy,
  manifest and Markdown files exercise the real scope parser and bound docs
  link parser; the real Git-listing entrypoint is never called. Both pass
  unchanged guarding in 1.03 pytest / 17.953 observation seconds with zero
  prohibited effects, eighteen admitted Python reads, six phase reports
  and actual checker call-body coverage per case.
  Add only adapter/original/checker bindings and that checker's covered target.
  The combined four-case candidate has 10,204 nodes / 1,465 sources /
  533 targets / 92 helpers / 1,043,474 compact-v2 bytes; preserve every prior
  ordered node, binding, target, parser/proof contract, the 1 MiB cap and
  300-second packet. Current-head typing/lint, complete hosted CI/upload,
  review, ordinary merge and parent readback remain required. No policy,
  workflow, production, native, release or rollout behavior changes.
  Normal current-Dev typing selects exactly both new adapters and passes
  whole-file strict checks on Linux and Windows; Ruff and whitespace checks
  pass. These source results do not replace fresh combined-head hosted CI.
  Publication is blocked on November 25: the managed flow pushes
  `688debd1ee4141878e69b3645cbbeeba7a8499e3`, but initial GraphQL creation
  and one absence-checked retry fail with server errors. Attributed REST
  creation also returns HTTP 502; a subsequent exact-head REST listing is
  empty. Stop creation attempts and retain the pushed branch, local evidence
  and both unchecked leaves. All observed inputs, 1,465 bindings and 92 helper
  decisions still verify. No PR, hosted result or completion is inferred.
  At 16:36 UTC GitHub reports the affecting Pull Requests/Actions degradation
  mitigated, establishing a changed provider boundary. Exact-branch REST
  deduplication remains empty before supported sync onto #14578. The result
  equals current Dev plus exactly four nodes, six bindings and two targets;
  all 21 combined observation inputs are unchanged. Every binding/helper
  decision verifies, and current-Dev strict Linux/Windows checking passes
  both adapters. Resume the same publication normally; do not replay tests,
  resend an owner message or infer hosted/review acceptance from recovery.
  #14584 merges at `f5f871fd120dd8b989aadc306cee391abcd84dca`
  on November 25 at 16:53:04 UTC after exact-head approval `6061449043`
  for `11716db82d`. The full review has no active issues and complete review/
  discussion-comment pagination is empty. Initial CI `45656844022` passes
  all 10,204 cases with 55 deselections in 145.32 pytest / 166.366 packet
  seconds, finalizes artifact `15044122066`, and passes guards and required
  aggregate `139359094093`. Toolchain binding exhausts its separate reuse
  budget, so this is fresh passing execution, not reusable evidence.
  Fresh required-check verification and live identity/hold checks precede
  managed merge. Its first authorized review-exception attempt hits a TLS
  handshake timeout; confirmed open state at the same head and a second fresh
  seven-read verification precede the successful bounded retry.
  Provider readback confirms merged state and open parent. Local sync
  safely aborts while replaying the already squash-merged commits. Exact
  comparison verifies all five nonmanifest owned blobs and proves the actual
  merged manifest equals parent `3ce4ce3261` plus precisely this four-case
  addition, preserving the peer's existing binding update. Backup-retaining
  empty-range reconciliation and subsequent supported sync succeed without
  reset, stash or dropped unmerged changes. Every binding/helper decision
  verifies and both cohorts' observed inputs remain exact.
  The actual Goal, both Plan leaves and complete Validation Plan are reread
  before resolving only these source leaves. Continue the unchanged
  original-unit census; parent, excluded functions, native/release and custody
  remain open. No owner seam, reuse, rollout or whole-flow saving is inferred.
- [x] Qualify the three complete original Python-wrapper fixture functions:
  leading requirement parsing, explicit fixture resolver configuration and
  uncached local requirements. Metadata-only comparison measures eight
  original Windows cases and three selected cases with exact function objects,
  fixtures, marks and IDs. Preserve the originals' build/subprocess doubles,
  two local iterations and cleanup; do not invoke real uv or a shell.
  All three pass unchanged effect guarding in 1.06 pytest / 17.922 observation
  seconds with zero prohibited effects, sixteen admitted Python reads,
  nine phase reports and actual production call coverage per case.
  Exclude both complete ambient-config functions and the entire three-row
  native argument-round-trip function; no environment or fixture workaround.
  The shared #14588 source delivery below resolves only this selected cohort.
- [x] Qualify all fifteen original launcher path-rendering cases from three
  whole functions; exclude the complete two-row native CMD function.
  Exact metadata comparison measures seventeen originals / fifteen selected
  cases, preserving function objects, fixtures, parameter values/types,
  marks and full IDs. Real rendering covers equivalent extended paths,
  other device namespaces and extended-only names without filesystem or
  shell execution. All fifteen pass unchanged effect guarding in 1.20 pytest /
  19.219 observation seconds with zero prohibited effects, sixteen admitted
  Python reads, forty-five phase reports and production call coverage per case.
  The combined eighteen-case candidate adds only two adapters, both originals,
  the previously unbound wrapper helper and two covered production targets;
  the existing launcher-helper binding stays unchanged. All prior ordered
  nodes, bindings and targets remain: 10,222 nodes / 1,470 sources /
  535 targets / 92 helpers / 1,045,979 compact-v2 bytes, under the unchanged
  1 MiB cap and 300-second packet. Current-Dev whole-file strict typing
  selects both adapters and passes Linux and Windows; Ruff and whitespace
  checks pass. Current-head hosted CI/upload, actual review, ordinary merge
  and parent readback remain required. This is unchanged-test admission,
  not production behavior, workflow, runtime, native or rollout qualification.
  #14588 merges at `3299b2eab49be817b5a321e016d52d5c0624ad0c`
  on November 25 at 17:26:00 UTC, from source
  `a4ab797e2991d968962ff2295d5bd3b8373cb513`.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records the review evidence.
  Initial CI `45663437907` passes all 10,222 cases with 55 deselections
  in 156.81 pytest / 177.134 packet seconds, finalizes qualified artifact
  `15044884497` and passes guards and required aggregate `139381504137`.
  This is fresh qualified execution, not a reuse hit.
  Seven fresh scoped provider reads and live identity/hold checks precede
  normal managed Maintainer merge; no failed CI is bypassed.
  Independent provider readback confirms merged state and open parent.
  Backup-retaining supported source sync succeeds, all five owned merged
  blobs equal the reviewed head, every binding/helper decision verifies
  and all nineteen combined observation inputs remain exact without replay.
  Goal, both Plan leaves and the complete Validation Plan are reread before
  resolving only these source leaves. Continue unchanged original-unit
  qualification within the remaining 2,597 manifest bytes; retain all native,
  ambient-config, owner-seam, release and custody boundaries.
- [x] Qualify the five complete original Docker identity scope-selection
  functions without invoking Git or Docker. Metadata-only collection measures
  sixteen originals / fourteen selected cases with exact function objects,
  fixture lists, parameter values/types, marks and full IDs. Original path
  tables exercise relevant/unrelated selection; the other three functions
  retain their original `_git` doubles for manual dispatch, absent-base
  fallback and diff-error refusal. Exclude the whole real-Git function and
  actual workflow/manifest inventory function.
  All fourteen pass unchanged guarding in 1.32 pytest / 14.235 observation
  seconds with zero prohibited effects, sixteen admitted Python reads,
  forty-two phase reports and actual scope-helper call coverage per case.
  Admission adds only fourteen ordered nodes, three source bindings and
  that covered helper: 10,236 nodes / 1,473 sources / 536 targets /
  92 helpers / 1,047,392 compact-v2 bytes, leaving 1,184 bytes under the
  unchanged 1 MiB cap. Preserve all prior ordered nodes/bindings/targets,
  parser/proof contracts and 300-second packet. Current-head typing, lint,
  full hosted CI/upload, review, ordinary merge and parent readback remain.
  No workflow, production, native, runtime or cleanup behavior changes.
  Whole-file strict checking against current Dev selects exactly the new
  adapter and passes Linux and Windows; Ruff and whitespace checks pass.
  All 1,473 bindings and 92 helper decisions verify, and all twenty-two
  combined observed inputs for this cohort and the prior batch remain exact
  without replay. Fresh hosted execution, review and delivery remain required.
  #14591 merges at `2d89c0c2a5755920eedc2cb90239b65b3b2d0b89`
  on November 25 at 17:51:52 UTC, from source
  `d10704af078cf98a8d6c236df422122c903cef1a`.
  [Dated delivery history](journal/2026/11.25-original-unit-delivery.md)
  records the description assessment, review and thread resolution.
  Initial CI `45666696644` passes all 10,236 cases in 127.41 pytest /
  146.792 packet seconds and finalizes qualified artifact `15047112689`.
  The necessary body-only correction triggers CI `45667737365`, which passes
  the same 10,236 cases again in 153.21 pytest / 173.203 packet seconds
  and finalizes artifact `15048925699`; toolchain binding exhausts its
  reuse budget, so this second artifact is unqualified for reuse.
  Required aggregate `139396577779`, guards and metadata gates pass.
  Seven fresh scoped provider reads and live head/base/hold checks precede
  normal managed Maintainer merge, with no failed-CI bypass.
  Independent provider readback confirms merged state and open parent;
  backup-retaining supported sync succeeds. All four owned merged blobs
  equal the reviewed head, every binding/helper decision verifies and all
  twenty-two observed inputs remain exact without local test replay.
  Goal, this leaf and the complete Validation Plan are reread before resolving
  only this source leaf. The two actual CI executions are not a reuse hit
  or a workflow-count reduction. Continue the census within 1,184 remaining
  bytes; preserve every parent, owner-seam, native, release and custody hold.
- [ ] Retain the complete pre-uv invalid-syntax fixture function's source-read
  boundary. The initial three-function candidate reports three pytest passes
  but effect verification refuses `unadmitted source read: pkg/_frag_b.py`.
  That is not qualification. Exclude the whole invalid-syntax function,
  preserve the original source/fixture/assertions and effect guard, and do
  not replay it or bind a guessed nonexistent repository source.
  The original real-repository manifest/fragment scan also remains excluded;
  neither case is silently qualified by the two-case result.
- [ ] Retain the complete cross-platform cleanup-error function's unit-guard
  boundary. Its two original cases remain outside selection after the
  Windows `[linux-5]` guard refusal above; the other row is not silently
  qualified. Do not patch the guard, original `sys.platform` fixture or
  path spelling to manufacture eligibility. The refusal is during original
  synthetic finally-cleanup, not evidence that an external resource remains
  unreaped; original native/custody obligations stay with their own owners.
- [ ] Retain the pause-tolerant-policy test's Windows pin-binding boundary.
  Its original fake daemon environment and runner do not replace the real
  Windows pin context. That route calls binding and may fall back to an
  ambient lifecycle declaration and real identity validation. No candidate
  adapter, execution or admission is performed for that function; metadata
  enumeration is not execution. A Linux route might skip those pins but is
  not qualified here. Do not patch platform, preload context or alter the
  original fixture/guard to manufacture a local pass. Retain the
  portability/admission disposition and all real owner/custody obligations.
- [ ] Retain the interactive-policy refusal contract finding. Static tracing
  reaches the original daemon validator's missing-policy ValueError before
  registry creation, birth lookup or monitor start. The public wrapper
  then marks that context-entry failure as incomplete teardown and replaces
  it with UnreapedProcessError, unlike the original test's expected
  ValueError. This is a static contract mismatch, not a runtime-confirmed
  test result or evidence of an actual unreaped resource.
  No candidate adapter, execution or admission is performed for that
  function; the comparison inventory still enumerates its metadata.
  Do not change cleanup classification, the original assertion, fixture or
  guard simply to make the case eligible. Retain the finding for explicit
  contract disposition; no new lifecycle/phase authority is inferred.
- [ ] Retain the original environment-audit lifecycle boundary. Its six
  parameter cases call the public `run_contained` wrapper, which imports
  `plugin_test_lifecycle` and enters the unpatched daemon lifecycle and,
  on Windows, native-instance pin context. The original registry/process
  doubles do not replace those owners. They remain unselected, unexecuted
  and unadmitted: no TestSandbox instantiation, runner/lifecycle call or
  candidate adapter for that function. The original-module inventory still
  enumerates all 118 cases as metadata; that is not execution or eligibility.
  Do not preload a fake lifecycle, edit the original fixture, or weaken
  guarding to manufacture a unit result. Existing lifecycle/native ownership
  and all retained-resource holds remain.
- [ ] Retain the original retirement early-refusal input boundary: two
  prospective original functions, provisionally six cases, use the unchanged
  environment fixture that copies actual `tools/test-environment-capabilities.json`.
  That input exists but is outside the current explicit build-data contract;
  `relative_file` refuses it. No retirement module is imported/called, test
  collected/executed, adapter written or packet admitted. Revisit only through
  a separately authorized input-contract decision, not a synthetic
  replacement, omitted input, preload, original/fixture edit or guard exception.
- [ ] Retain the declaration-planner import-data refusal: collect-only
  comparison reaches twelve original / eleven selected cases, but
  prospective admission rejects the two real HTTP-52 import-time asset
  manifests because the unchanged source schema cannot bind them. No
  selected test execution, packet admission or publication occurs. Do not
  omit these inputs, preload the dependency, widen the schema or alter the
  original functions to manufacture eligibility. Revisit only through an
  independently authorized input-contract decision.
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

- [x] Manifest successor satisfies the
  [phase validation](manifest-storage-successor.md#validation): existing-format
  compatibility, exact ordered logical equivalence, unchanged bounds and
  eligibility, actual consumer readiness before migration, focused fresh
  execution and the complete admitted packet. No representation-only milestone
  establishes the historical census, every-release or parent acceptance.
  Reader #14658 and separate data #14662 pass each source-only phase item;
  final baseline preserves all thirteen intervening peer binding refreshes.
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

- [2026-11-26 UTC - Manifest successor implementation](journal/2026/11.26-manifest-successor.md)
- [2026-11-25 UTC - Original unit delivery and documentation assessment](journal/2026/11.25-original-unit-delivery.md)
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
