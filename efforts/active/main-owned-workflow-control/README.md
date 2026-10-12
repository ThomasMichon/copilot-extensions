# Main-Owned Workflow Control

- **Slug:** `main-owned-workflow-control`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees; ordinary changes target `dev`
- **Created:** 2026-10-12
- **Status:** Active; reviewed plan, first consolidation slice in progress
- **Vision:** `visions/main-owned-workflow-control/README.md`
- **Umbrella issue:** #6237
- **Related campaigns:** #3336; `promotion-failure-reactive-fix-agent`; #6142

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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign driver | Own plan, implementation slices, review and release evidence | Umbrella issue #6237 and per-slice managed worktrees |
| Repository owner | Critical-path acceptance and bounded administrative decisions | Owner-authored PRs and issue steering |
| Automated reviewer | Review each plan and implementation PR | Repository Copilot review flow |

## Coordination

- **Topology:** independent per-slice PRs, serial landing.
- **Host (owns PRs):** Campaign driver, explicitly appointed by the operator.
- **Delegates:** no competing implementation assigned.
- **Handoff:** journal the current slice, exact evidence and outstanding gates;
  successors continue this canonical effort, not a peer plan.
- **Reconciliation:** the other planning agent's #6237 comment delegated
  canonical vision/effort publication here and retained a separate ownership
  completion gate. No published competing effort or PR was found.

## Context

The current pattern regenerates main wholesale. Critical workflow repairs can
therefore be delayed by unrelated dev failures and erased by later promotion.
The proposed exception is narrow, not permission to keep arbitrary main drift.
See [design.md](design.md) for the proposed contract and staged cutover.

The October 11 audit is historical evidence, not current failure status. Live
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

The operator confirmed the explicit main-owned control/dev-owned content model.
The detailed decomposition and validation cases below are **agent-recommended**,
reconciled with the other planning agent's public handoff on #6237.

## Plan

All slices use the execution gates in [design.md](design.md#phase-execution-gates).
The campaign driver owns implementation/review; the repository owner alone
authorizes critical main-path acceptance or a bounded administrative change.

### Phase 0 - Reviewed contract and baseline

- [x] Land this vision/effort through ordinary dev plan review (#6284).
- [ ] Snapshot branch protection, rulesets, environments, workflow catalog,
  credentials and in-flight releases; identify enforced freshness and admission.
- [ ] Classify every workflow and its helper/action dependencies, including
  required contexts, event filters, credentials and failure-remediation owner.
- [ ] Measure runs/jobs/checks per contributor update, dev advance and release;
  reviewer queue/start latency and observed throttling. Distinguish GitHub
  Actions concurrency from Copilot service limits; do not assume one shared quota.
- [ ] Rank safe redundant trigger/check removals under existing gates ahead of
  ownership work that is not needed for those reductions.
- [ ] Resolve the trusted-check publication limitation explicitly: the common
  Actions app and a check name cannot prevent deliberate Write-actor spoofing.
  Do not claim stronger guarantees than the selected enforceable boundary.

### Phase 1 - Earliest safe run and check reductions

- [ ] Remove redundant release-shaped PR and main-push validation only where
  existing trusted gates and exact-SHA release evidence cover the removed work;
  first prove no generated-output coverage or required context is lost.
- [ ] Coalesce or prevent redundant event-triggered runs, not merely mark all
  jobs skipped; preserve timely contributor feedback and review delivery.
- [ ] Measure before/after run and check counts and review latency over comparable
  events; retain distinct heavy/platform/installation coverage.
- [ ] Deploy only the minimal critical control repair required to unblock the
  next safe consolidation slice, through the narrow owner-reviewed main path.

### Phase 2 - Inert preservation support where needed

- [ ] Extend the existing composer with strict, exact-path preservation from
  pinned main; absent manifest retains existing behavior.
- [ ] Reject unsafe/missing files, collisions and conflicting ownership; record
  control provenance and test intentional deletion.
- [ ] Land implementation, tests and related documentation via dev, then promote
  while no preservation manifest is active.

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
  before resuming ordinary publication; no new bypass actor or broad credential.

### Phase 5 - Finish validation and automation consolidation

- [ ] Record ownership completion separately; it is not the prerequisite for
  Phase 1 reductions proven safe under existing protection.
- [ ] Establish one exact-input validation contract and compact generated
  candidate gate; preserve distinct heavy/platform/installation coverage.
- [ ] Remove redundant promotion-PR/main-push validations only after replacement
  gates are required and proven; migrate old contexts/callers before deletion.
- [ ] Reconcile repair ownership with #6142 and existing remediation effort;
  disabled repair automation must escalate to a named owner.
- [ ] Route Dependabot contribution PRs to dev; remove redundant rejected
  release-branch contribution runs without weakening dependency validation.

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

## Proposal

See [design.md](design.md). This is proposed behavior, not an as-is claim.
Plan approval authorizes implementation through review gates, not unbounded
credential changes, broad policy weakening or unreviewed releases.

## Journal

### 2026-10-12 - Ownership and reconciliation

- Operator appointed the campaign driver and explicitly restricted direct-main
  contributions to critical workflow/metadata, including admins.
- Reconciled the other planning agent's #6237 handoff: publish one canonical
  effort, use same-composed-commit implementations and preserve exact paths.
- Current migration has made no workflow, secret, environment or policy changes.

### 2026-10-12 - Operator priority correction

- Primary completion gate is consolidation and elimination of redundant runs and
  status checks to improve review capacity. Ownership is an enabling mechanism.
- Reordered the proposed plan so safe reductions under existing gates come first;
  preserved all validation, isolation and direct-main scope restrictions.

### 2026-10-12 - Reviewed plan and first consolidation slice

- #6284 received Copilot approval and merged after the required PR aggregate and
  workflow lockdown checks passed. Native auto-merge still awaited codeowner
  review; the owner-profile merge bypassed only the accepted review gate.
- First slice combines the existing trusted review fallback and lockdown into
  one `pull_request_target` invocation, without changing their scripts or the
  required check identity. Review concurrency/permissions remain job-scoped;
  the guard cannot wait behind the five-minute review fallback.
- Baseline recent 100 runs had 23 review-fallback and 23 lockdown runs. The
  intended reduction is one invocation per shared PR event, not fewer coverage
  tiers. Live post-promotion proof and final counts remain outstanding.

### 2026-10-12 - First slice merged; queue-time grace

- #6308 merged as `567488f973da` after current-head Copilot approval and both
  required checks passed. The two-to-one trusted invocation reduction is merged
  to dev, not yet claimed live on main.
- Next slice counts time already spent queued toward the review fallback's
  five-minute event grace. A recent manual request still retains its separate
  five-minute suppression; latest-head and billing admission are unchanged.
- This targets held runner time without adding a workflow or status check.
  Live queue/start timestamps and actual script logs must confirm the saving.

### 2026-10-12 - Queue grace and CI-completion consolidation merged

- #6313 merged as `ff5b1daebb9b` and #6314 as `17bab4665219`, serially after
  current-head Copilot approval and explicit required-check passes.
- CI completions now have one proposed trusted follow-up invocation instead of
  two, with the original ancestry/filter and inert scanner jobs unchanged and
  original permissions isolated per job. Real Git/Bash tests exercised exact
  dev dispatch, unrelated feature rejection and fetch failure.
- The earlier failed release snapshots still contained the handoff fixture
  regression; its existing owner fixed it through #6309. New green promotion
  and default-main live behavior are still required, not presumed.
- Next metadata reduction routes all eleven dependency version-update groups
  to dev. Security updates retain GitHub's default-branch behavior and must
  still follow the dev contribution/release boundary, never a source exception.
- These changes reduce workflow invocations and held review-grace time.
  Required/job check counts and test coverage have not been pruned in this wave.

### 2026-10-12 - New coverage-evidence release blocker

- Run `38250657887` passed canonical source suites but failed promotion because
  the enrolled agent-worktrees coverage artifact was missing. Its separate
  coverage rerun exited 2; optional-step handling hid that failure until the
  required promotion evidence check correctly refused publication.
- #6319 owns the repair. The collector exception rendered stderr *or* stdout,
  so a no-data coverage warning hid the pytest collection cause. First preserve
  both diagnostic streams, then fix the real cause using the live Linux lane.
- Local Docker has no reachable daemon; no shared service is repaired or
  started as a side effect. A contained synthetic real-collector failure covers
  diagnostic behavior locally; this is not the actual worktrees reproduction.
- No partial baseline, enrollment removal, forced retry or evidence bypass is
  an acceptable release recovery. The duplicate full/coverage execution remains
  a consolidation seam after the failure is understood.

### 2026-10-12 - Diagnostic overlap reconciled

- Another driver landed the equivalent diagnostic implementation in #6321.
  The operator authorized retaining only this campaign's unique regression
  coverage and journal, adopting that implementation unchanged.
- #6323 therefore contributes no production diagnostic change; its actual
  collector failure regression and native-path test expectations remain useful.
  The real missing-baseline repair stays open under #6319.

### 2026-10-12 - Repair ownership and evidence refreshed

- #6315 merged as `8fdb86fe39f6` after current-head approval and required
  checks passed. Dependency routing is dev-landed, not yet claimed released.
- Duplicate diagnostic tracker #6319 transferred to the already-owned #6320.
  The later run `38289187893` produced all nine baseline artifacts, including
  worktrees artifact `11766276761`; its publication instead failed the strict
  no-overwrite materialization check at three migrated vendor destinations.
- Existing fix #6335 under #6331/#6334 owns that layout repair. Later stale-tip
  test failures have a merged owner repair #6353. No competing fix or gate
  bypass is introduced. Observe a new green release before claiming live proof.

### 2026-10-12 - First-wave live publication verified

- Normal release run `38305079883` passed full source suites, all nine enrolled
  baselines, final promotion, exact PR wait and actual post-merge tagging.
  Generated #6358 merged at `21:01:09Z` as main `67661d853e8f`; recorded dev
  input `1fe40334e3e0` includes #6308, #6313 and #6314 by Git ancestry.
- Old standalone reviewer and identifier workflow files are absent from main.
  Natural PR event `38308273017` has both guard and review jobs in one run.
  Natural CI completions `38308478256` and `38308078379` have both scanner and
  ancestry jobs in one run, with successful independent outcomes.
- This proves workflow-invocation consolidation is live; it does not prove
  overall Copilot throughput from a few samples or eliminate job/check records.
  #6359 separately targets unnecessary executed ancestry jobs on PR completions.
- #6319 is closed as duplicate, transferred to #6320. The production diagnostic
  implementation remains #6321's, with only unique regression coverage here.
  Main optional validation duplication and complete campaign measurement remain
  unresolved gates; no completion declaration or policy bypass is implied.

### 2026-10-12 - Verified peer repair and next runner reduction

- #6337, not the still-open #6335, supplied the released vendor-layout repair;
  merged source is `4b920e008227`. #6320 is closed after the real green release.
  Keep historical owner pointers above as history, not the current blocker.
- #6359 merged as `dbe9037692dc` after current-head Copilot approval and both
  required checks passed. It prevents PR CI completions from allocating an
  ancestry-filter runner, retaining success/ancestry checks for push/manual
  events. Release and live skip evidence for that latest slice remain pending.
