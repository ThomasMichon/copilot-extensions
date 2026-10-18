# Main-Owned Workflow Control

- **Slug:** `main-owned-workflow-control`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees; ordinary changes target `dev`
- **Created:** 2026-10-12
- **Status:** Active; main-PR CI invocation cutover live-proven, generated-main validation failures and remaining consolidation open
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

> Yes, noting we always still need an escape hatch. The Copilot reviewer still
> isn't perfect, so we as maintainers always need a way to forcbly self-merge as
> a worst-case, but we don't want agents reaching for that button prematurely.

**2026-10-18 - Maintainer completion automation (verbatim, with an unrelated
implementation reference generalized):**

> One thing we can do is develop one more background automation in GitHub which, similar to request-review-if-maintainer, operates on maintainer-owned PRs, and helps admin-merge PRs form maintainers which have passed CI, have an Approved stamp from Copilot (and no change requests from anyone else), and are tagged with auto-merge. It should also take care of auto-rebase, similar to [the existing review automation], leaving only conflict resolution to originals submitters. If we add this, then hopefully we can make agents know to just set auto-merge and let PRs go through.

Only the implementation reference is generalized; the requested merge gates,
label consent, automatic rebase and submitter conflict ownership are preserved.
The implementation and activation decomposition below is agent-recommended.

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
- [x] Resolve the trusted-check publication limitation explicitly: the common
  Actions app and a check name cannot prevent deliberate Write-actor spoofing.
  Do not claim stronger guarantees than the selected enforceable boundary.
  Operator accepted the existing trusted-collaborator boundary on October 13;
  no cryptographic same-app spoofing guarantee or new publisher is claimed.

### Phase 1 - Earliest safe run and check reductions

- [ ] Remove redundant release-shaped PR and main-push validation only where
  existing trusted gates and exact-SHA release evidence cover the removed work;
  first prove no generated-output coverage or required context is lost.
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

- [x] Land the dev implementation of reviewed, widen-only module-size baseline
  maintenance and remove its obsolete main exception from source (#6438).
- [x] Verify a covering promotion activates the dev-owned baseline producer on
  default main; record authorized runtime adoption separately.
  #6631 and hosted job `115706505844` prove activation of #6438; this
  workflow-only capability needs no local plugin/runtime update.
- [ ] Record ownership completion separately; it is not the prerequisite for
  Phase 1 reductions proven safe under existing protection.
- [ ] Establish one exact-input validation contract and compact generated
  candidate gate; preserve distinct heavy/platform/installation coverage.
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

- [ ] Reuse the existing invited-collaborator plus root CODEOWNERS Maintainer
  boundary. Propose an initial Dev-only, same-repository, nondraft scope:
  exclude Main, generated release candidates and the promotion publisher.
  Require the `auto-merge` consent label and respect configured holds.
- [ ] Evaluate fresh current-head required CI, including required check-source
  identities, and the latest actual current-head Copilot `APPROVED` verdict
  after any reopen or base-change epoch.
  Reject active change requests from every other reviewer. Paginate history;
  a later `COMMENTED` or pending review does not clear a human negative.
- [ ] Use native GitHub `REBASE` with expected-head compare-and-swap when an
  update is necessary for a clean, policy-compliant merge. Do not substitute a
  merge update, execute PR hooks/code, resolve conflicts or reuse old-head
  approval/CI. Return genuine conflicts to the original submitter with one
  deduplicated notification; races and unknown responses are not conflicts.
- [ ] Implement a short, event-driven trusted-default-Main reconciler, with
  bounded manual/catch-up recovery for event-order gaps. Treat trigger data as
  hints and re-read actual PR state before each write. Do not hold runners
  waiting for CI/reviews, launch review waves or retry uncertain writes blindly.
- [ ] Keep mutation authority out of head-controlled review events and
  untrusted checkouts. Prefer a short-lived, single-repository automation
  identity with only needed permissions. A new App, credential, review-only
  bypass entry or live enablement needs explicit operator approval; existing
  human exemptions and the licensed review-request credential do not imply it.
  CI protections and Main publication requirements remain unchanged.
- [ ] Prove external rebase compatibility with the owning managed worktree:
  stale local publication refuses, unpublished work/backups survive, and a
  later verified squash merge supports `pr-complete` and claim settlement.
  Extend existing provider/lifecycle seams only if focused tests expose a gap.
- [ ] Land the implementation disabled through reviewed Dev contribution and
  normal qualified promotion. Keep it Dev-owned unless an independently
  governed critical-control requirement is separately approved; do not expand
  the Main preservation allowlist merely because the job is privileged.
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

### 2026-10-12 - Missing-check incident and operator-authorized recovery

- #6323 was prematurely merged as `145f6dfb01cf`: the watcher treated reported
  green checks as complete while the required PR aggregate had not registered.
  The driver also failed to treat its absence as blocking. Owner-profile merge
  used admin escalation, and classic protection still exempted admins.
- The driver disclosed the incident immediately; the operator directed actual
  CI/promotion observation and repair of any failures. Exact-head CI
  `38310113011` later passed with both named required contexts explicitly green.
  Covering dev CI passed, and release `38313645096` validated dev
  `cbb7256268e6`, including `145f6dfb01cf` by Git ancestry.
- Generated #6377 merged at `22:40:12Z` as main `677cc12041a4`, with exact PR
  wait and post-merge tagging successful. No CI failure needed fixing in this
  recovery. Later success does not retroactively justify the premature merge.
- #6367 tracks the tooling defect. Pending or absent required contexts now block
  the driving session independently of its watcher rollup.
- The operator authorized enabling `dev`'s existing `enforce_admins` setting.
  Live readback confirmed true, the same required aggregate and strict=false;
  review-required ruleset and its existing Maintainer bypass actors unchanged.
  No additional workflow/check, main policy or credential was introduced.
- The operator explicitly retained a worst-case review self-merge escape hatch,
  without premature agent use. CI enforcement changes require separate bounded
  human recovery authorization, never an inferred routine exemption.
- The first-wave publication/recovery watches are complete. Remaining main
  duplicate validation, stronger candidate assurance and normalized review
  capacity measurement remain open campaign gates.

### 2026-10-12 - Safety documentation landed; software preflight

- #6386 merged as `78a236501b89` after current-head Copilot approval and both
  explicit required contexts passed, with admin CI enforcement active. A
  mechanical index conflict was resolved by retaining the peer's new row.
- #6367's software follow-up adds required-context completeness only at the
  explicit GitHub admin merge boundary, preserving the review escape hatch and
  adding no watcher polling, workflow or check. The verified head is pinned;
  missing, unsuccessful or unreadable requirements refuse the mutation.
- Contained targeted tests exercise both classic and ruleset contexts, missing
  aggregate, permission failures, malformed evidence and head/base races.
  A live read-only call against #6332 refused its not-yet-passing PR aggregate;
  no merge/write operation was invoked in that consumer check.
- Candidate assurance will reuse the active attestation contract in #6332,
  not a competing receipt framework. Its builder/signing/publication files
  remain with their existing owner. Main duplicate validation cannot be pruned
  until that final-tree assurance and required admission gate are proven.

### 2026-10-13 - Required-rule pagination review fix

- #6402's review found that later effective-rule pages could add required
  contexts omitted by the initial preflight. The merge boundary now consumes
  all pages with `gh api --paginate --slurp`, validates every page and refuses
  partial output on a pagination error.
- The contained provider/guard selection passed 162 cases, including missing
  and passing later-page contexts, malformed pages and a later-page read
  failure. Focused Python lint and the 13-plugin install contract passed.
  A read-only native API invocation confirmed the outer-page response shape.
- This changes no first-provision behavior, so a clean-room install lane is
  not applicable. Live required PR checks and a fresh review remain pending;
  no review or CI bypass is authorized by this local evidence.

### 2026-10-13 - Integration identity and measured first-wave reduction

- The next #6402 review correctly found that context names alone cannot
  distinguish two required integrations. Classic numeric app declarations and
  ruleset integration declarations are now preserved; bound contexts require
  successful, complete, paginated exact-head check-run evidence from each app.
  Unknown integration identity fails closed. Same-app deliberate spoofing
  remains outside this preflight's documented assurance boundary.
- The contained provider/guard selection passed 178 cases; focused lint and
  changed-module limits passed. The actual read-only provider boundary refused
  #6402 while its required PR aggregate was still absent. Fresh current-head
  review and CI remain necessary; no mutation was attempted by that probe.
- Complete, equally sized three-hour Actions windows show 60 paired PR events
  producing 120 guard/reviewer runs and 52 CI completions producing 104
  identifier/filter runs before cutover. After cutover, 100 and 109 comparable
  events produced 100 and 109 combined runs: 50% fewer invocations per event in
  each consolidated family, or 209 removed invocations in that latter window
  compared with the former layout at the same event volume.
- Windows were `2026-10-12T18:00:00Z..21:00:00Z` (367 total runs) and
  `2026-10-12T21:05:00Z..2026-10-13T00:05:00Z` (495 total runs), with API
  reported counts matching complete pagination. Higher later traffic prevents
  comparing raw totals as throughput. The later window had 79 successful
  Copilot review runs; this does not establish a causal quota improvement.
  Distinct job coverage and required-check counts were not removed by that wave.
- #6409 owns the next independent reduction: migrate the exact dev-PR
  payload-CWD smoke into existing native CI jobs, retaining standalone
  main-target, weekly and manual coverage. The unchanged Windows selection
  passed 10 cases in 51 seconds before migration; candidate/promotion files
  remain with #6332's owner.

### 2026-10-13 - Guard merged and native-smoke consolidation verified

- #6402 merged as `06a5bb4e0aa6` after current-head approval `5497694992`,
  zero open findings and both explicitly required checks passed. The real
  read-only preflight additionally verified every configured integration at
  head `45347c28c338` before the managed merge. Source is dev-landed; release
  and authorized runtime adoption remain separate follow-through gates.
- #6414 received current-head approval `5497722970` with zero findings.
  CI `38330285814` succeeded; the consolidated native jobs finished in 108
  seconds on Windows and 73 on Linux, within their unchanged five-minute
  budgets. Its affected opening event had no standalone payload-CWD run.
- #6414 merged externally as `8d33901962c7` while the read-only preflight was
  rechecking PR identity. The preflight refused the now-closed PR and no
  duplicate merge mutation was attempted. Full release/default-main proof
  remains pending; neither dev merge alone completes this campaign.
- Seven deep/platform workflow event/platform rosters were independently
  confirmed, including full Windows rotation and both native stress/race
  lanes. Six untouched schedules remain unchanged. Failure routing is not
  declared in six of those workflow files; that catalog gap remains open.
- The optional trusted self-hosted workflow is a further invocation-consolidation
  seam, but deployment consumers may pin its exact blob. Consumer pins and
  non-dev event coverage must be reconciled before changing it; no self-hosted
  admission or deployment control was altered.
- A tightly coupled preflight follow-up rejects missing/non-string/empty rule
  discriminators rather than treating malformed policy as a non-status rule.
  Legitimate unrelated rule types remain unchanged.

### 2026-10-13 - Dev-owned baseline correction and release queue recovery

- #6426 merged as `fd73fdcff233` after current-head approval `5498079093`,
  zero findings, both explicit requirements and the actual read-only preflight
  passed. Malformed-policy behavior is dev-landed, not claimed deployed.
- Generated promotion #6408 was conflicted despite green checks: main's only
  intervening commit was automated baseline update #6071 (`53be0fa787d3`).
  The operator identified main-targeted baseline maintenance as a bug and chose
  a versioned baseline updated on dev, not a release-artifact redesign.
- The operator explicitly authorized closing #6408. Only its recorded head
  `c2e9bf25a7fe` was retired; branch and validation evidence were preserved.
  Run `38324591183` ended with the expected closed-candidate failure, releasing
  the queue. Replacement `38334102000` started; no new candidate is yet claimed
  merged, and no CI or branch-policy bypass was used.
- #6430 moves baseline maintenance into the existing trusted CI-completion
  invocation. Pinned trusted tooling reads committed dev as data with isolated
  Python and credential-free checkouts; publication is baseline-only to dev,
  under ordinary CI/review. The release PAT is confined to publication.
- The obsolete main-writing and manual-verification workflows are removed;
  main admission no longer permits their baseline branch. Hard caps, existing
  ceiling refresh and unrelated contribution scoping remain unchanged.
  Sixty-one targeted tests passed, including poisoned candidate tooling/imports,
  new-offender rejection, real stale-input/diff fencing and main rejection.
- Activation/default-main proof remains pending review, CI and release. The
  independent #6332 writer/attestation owner retains its files and open findings;
  main validation is not pruned on the strength of this correction.

### 2026-10-13 - Replacement release and baseline review follow-through

- Replacement #6441 merged at `03:25:21Z` as `9539eda8f9b3`; promotion
  `38334102000` completed successfully, including its exact-candidate waiter
  and post-merge tag `promote-20261013032543-9539eda8`. The recorded validated dev input
  `a27d57dc2ddc83fc1a8e92a0a026e5f64b5cb7b2` covers #6402 and #6414 by
  Git ancestry, but not later #6426 or the still-open #6438. Runtime adoption
  and the later covering release remain separate obligations.
- Current-head review of #6438 found a real stale-baseline gap after the
  pre-publication SHA fence: a subsequent dev shrink could leave an excessive
  ceiling that the ordinary upper-bound check accepted.
- The existing required PR guards now run read-only automatic-baseline
  canonicality on the automation branch's merged dev tree. Recomputing the
  refresh must produce the same baseline; a stale result fails without
  rewriting the PR. Ordinary reviewed manual ceilings remain unchanged.
  This validates the tree CI exercises, not a claim of atomic merge-time CAS.
- Fifty-three focused tests passed, including an actual Git merge after dev
  shrinks beyond the publication fence, refusal without file mutation,
  successful regenerated policy, new-offender rejection and required-job
  wiring. Focused lint and documentation consistency passed.
  No new workflow, required check or credential was introduced.

### 2026-10-13 - Operator-selected widen-only maintenance

- The operator explicitly prioritizes avoiding an excessively low ceiling
  that blocks code over avoiding a stale, unnecessarily high ceiling.
  Confirmed automated maintenance must widen only; tightening remains a
  deliberate componentization/manual refresh, not a subsequent automatic pass.
- Automation now invokes `--refresh-baseline --widen-only`. It raises an
  existing entry when necessary but never lowers, removes or creates an
  entry. Manual refresh and `--allow-widen` retain their existing behavior;
  the hard cap for new offenders remains enforced.
- Removed the earlier experimental exact-baseline canonicality gate:
  it rejected the stale-high proposals the operator accepts, and it could
  not establish atomic base freshness under the non-strict status policy.
  Ordinary PR ceiling checks and both required contexts remain intact.
  No global strict-base policy, merge queue or additional CI run was introduced.
- Sixty-two focused cases passed, including real Git races: a stale high
  proposal remains admissible after a shrink, while widen-only maintenance
  cannot publish a tightening that blocks subsequent growth within the
  existing ceiling. Shrunk/graduated entries are preserved, new offenders
  still fail, and deliberate manual tightening is unchanged.
- Updated the reviewer contract and remaining live references to the retired
  workflows. Historical incident references remain historical; the independent
  attestation/control implementation is not changed.

### 2026-10-13 - Widen-only correction merged; next composed-validation stretch

- #6438 merged at `09:52:15Z` as `87c5f5ddcca1`. The final reviewed head was
  `00d9cad9b9a6`, with Copilot approval `5501468799` and zero open findings.
  CI `38373310592` and lockdown `38373311117` passed; the real read-only
  source preflight verified every required context, complete policy pages,
  integration identity and unchanged exact head before the managed merge.
- Review hardening bounded the target-root probe, disabled diff helpers and
  inherited system/global Git configuration at the publication fence, and made
  workflow assertions select named steps while preserving required ordering.
  Sixty-seven focused cases and Python lint passed. Widen-only maintenance,
  deliberate manual tightening and ordinary new-offender checks are unchanged.
- This is merged to dev, not yet claimed activated on default main. A covering
  promotion must include #6426 and #6438 before release proof is recorded;
  authorized runtime adoption remains separately outstanding.
- The next stretch establishes the exact-input/final-composed-tree validation
  contract using #6332's existing attestation producer, then proves a required
  compact candidate gate before pruning promotion-PR or main-push validation.
  No competing signer/receipt framework, main source exception, new credential
  or premature removal of existing gates is authorized.
- The campaign remains Active. First-wave measurement, independent-control
  preservation/admission, platform coverage and the two-release-cycle
  validation plan still require their own completion evidence.

### 2026-10-13 - Inert preservation prerequisite prepared

- #6332's signer transitively depends on the full validation jobs through its
  builder. Its signed package index already binds dev/main/content, but does
  not expose an authoritative suite/recipe evidence contract. It is not enough
  by itself to remove candidate validation; the existing gates remain intact.
- Prepared strict pinned-main preservation in the existing composer, shared
  by trusted artifact recomposition. No manifest is active. Version 1 admits
  only exact top-level workflow files and automatically preserves its own
  declaration; dev cannot introduce or redefine that declaration.
- Real-Git cases prove pinned rather than moving-main authority, exact blobs
  despite candidate text/filemode policy, provenance, intentional retirement
  and equivalent artifact recomposition. Negative cases cover unsafe Git
  types, paths, aliases, missing targets and ambiguous declarations.
- The contracts join existing path-gated Linux/Windows artifact jobs, not a
  new workflow or required context. Source review and covering release remain
  outstanding; atomic freshness, trusted admission, helper compatibility and
  control-preserving rollback are still prerequisites to later activation.
- This realizes the explicit-preservation vision item without changing its
  intent. The repo's fail-loud, smallest-surface and cross-platform patterns
  apply; no runtime, dependency, credential or branch-policy change is needed.
- Native PR CI exposed #6322's previously unexplained Windows exit mismatch:
  captured diagnostics show cold PowerShell compilation exceeding the fixture's
  ten-second child timeout. Two such children also had only a twenty-second
  enclosing budget. Repaired the ordinary fixture's nested deadline hierarchy,
  without weakening any console, stream or exit assertion; the real five-second
  timeout/tree-reap contract is unchanged. Both native local cases passed.
  Current-head hosted Windows proof is still required, not waived as a flake.

### 2026-10-13 - Inert preservation source merged; admission decision next

- #6521 merged at `11:59:40Z` as `5f4ce4d8926f`. Final head `d3aae9957781`
  received Copilot approval `5503062211` with zero open threads. Current-head CI
  `38391167703` and lockdown `38391414726` passed, and the real source preflight
  verified every configured requirement, applicable integration binding and
  unchanged head before the managed merge. No bypass of CI was used.
- Hosted Linux exercised 598 artifact/composer cases. The dedicated Windows
  artifact job explicitly executed `test_promotion_control.py` with the shared
  artifact suites: 259 passed. The separate Windows runner's 85-case native
  containment selection also passed, proving #6322's diagnosed fixture recovery.
  That issue is closed; no assertion or five-second tree-timeout was suppressed.
- Preservation is dev-landed, not activated or claimed released. No manifest
  exists. The full Plan/Validation Plan remains open for admission, atomic
  freshness, compatibility, rollback, covering release and observed cycles.
- A read-only live policy snapshot found `main source gate` required by ruleset
  `18553911`, with strict=false and an Admin always-bypass actor on that combined
  rule. Native GraphQL schema confirms merge accepts an expected head OID but no
  expected base OID. A last-minute main read cannot supply atomic freshness.
- The next cutover needs a separately accepted provider freshness mechanism and
  explicit trust boundary. Dev's loose policy remains unchanged; no new App,
  token, service, merge queue or policy mutation is authorized by this journal.

### 2026-10-13 - Operator keeps current process; consolidation resumes

- The operator reaffirmed that the current release process works and the task
  is consolidation/deduplication, not making new ownership/freshness machinery
  a prerequisite. Accepted the existing trusted-collaborator boundary.
  No strict-base policy or new infrastructure was authorized.
- Independent main-control activation remains dormant. Its hypothetical base
  race is not a blocker for reductions covered by today's existing process.
- The next staged reduction reuses the existing main-PR reminder invocation
  for the unchanged `main source gate`. The temporary legacy CI producer stays
  until trusted default-main deployment is proven; removing it earlier would
  deadlock the promotion that deploys its replacement.
- Main-push validation, contribution review/CI, fork approval and platform/deep
  lanes remain intact. The final cutover removes a redundant main-PR workflow
  invocation and its optional duplicate guards, not distinct coverage.
- Coverage tracing found that main-PR identifier scanning follows the CI
  completion and does not run on main pushes. Preserve and prove that caller
  too before disabling main-PR CI; admission staging alone is not cutover.
- Native Git/Bash tests exercise admitted/rejected shapes, actual rollback
  trees and read-only head-object fetching without switching the trusted
  working tree. Gate permissions are contents-read only; PR code never runs.
- Prior resolver repair #6508 and standalone alias repair #6506 are merged.
  New artifact-validation recovery stays with its existing #6535/#4876 owner;
  no competing signer or publication fix is introduced here.
- Later recovery run `38457217400` failed two project-scalar config-reuse
  assertions in the full worktrees suite. Existing #6556/#6557 owners are
  already validating a receiving-config reuse fix and coupled fixture repairs.
  Preserve that ownership rather than loosen assertions or fork the repair.

### 2026-10-13 - Trusted main admission staging merged

- #6566 merged at `21:08:28Z` as `1f57609f76f6`. Final head `db2e7e1fd99a`
  received native Copilot approval `5509925281` with zero open threads.
  CI `38466307933` and lockdown `38466308104` passed; the real source preflight
  verified all configured requirements and the unchanged head before merge.
- The new producer remains staging until a covering promotion. The legacy CI
  gate and scanner remain, so no pipeline reduction is claimed live yet.
- The next code slice shares the existing trusted identifier scan/report
  implementation across its current completion caller and the main-PR host.
  Preserve explicit failure reporting, stale-head handling and denied/removed
  path behavior. Uninvited main PRs must not gain a secret-denylist scan oracle.
  Only after both new producers are released and proven may main-PR CI stop.

### 2026-10-13 - Shared identifier scan staged without coverage removal

- The post-CI scanner and the main-PR host now share one owner-protected
  composite action and reporter under `.github/actions/identifier-leak-guard/`.
  The trusted scanner still reads paginated non-removed changed paths from the
  pinned PR commit as Git data; no PR checkout or PR executable code is involved.
- Main-host scanning requires both successful unchanged main admission and
  owner authorship. Rejected and non-owner PRs gain no secret-backed scan oracle.
  The existing post-CI caller and all CI event/coverage lanes remain in place.
- Stale events and heads advancing during a scan publish nothing. Both callers
  retain thin always-failure reporting that works without a loaded local action.
  An unconfigured but operational feature remains neutral; infrastructure
  failures and missing/malformed reports cannot masquerade as that feature gate.
- Local contracts exercise the real reporter, API delivery ordering and
  authenticated comment markers, bounded full finding detail, every prerequisite
  failure, stale heads, caller fallback without local files, and the actual
  Bash/Python scanner against generated Git blobs distinct from the working tree.
  The legacy scanner/policy regression selection remains green.
- This realizes the consolidation vision without changing ownership or branch
  policy. Smallest-surface and fail-loud patterns apply; no plugin payload,
  runtime, dependency, credential or installer changes require a changefile or
  fresh-machine install lane. Fresh temporary fixtures exercise first action use.
- Review/current-head hosted checks and a covering promotion remain outstanding.
  Default-main producer proof precedes removal of main-PR CI; source staging
  is not live cutover. The full worktrees release failure retains its existing
  repair owner rather than being bypassed or repaired by a competing PR.

### 2026-10-13 - Scanner staging native CI blocker repaired

- #6577 received current-head approval with no findings, but required CI
  exposed #6532's real-helper read-only fixture on Windows: the first hosted
  helper call exceeded its unchanged fifteen-second boundary.
- The contained native reproduction independently found host-generated
  `PowerShell/telemetry.uuid` in both full-home snapshots. #6581 owns this
  bounded fixture repair: opt out of PowerShell telemetry only in the test
  environment, retaining the real helper calls, production deadline and
  byte/mtime assertions. Both native cases now pass locally.
- No production resolver behavior changed. Hosted current-head validation
  remains mandatory; local telemetry proof alone is not claimed to explain
  or clear every hosted timeout. Review and required CI must run again after
  this correction. No gate is bypassed and no competing installation work
  is taken over.

### 2026-10-13 - Review closes filename-transport bypass

- Current-head review of #6577 identified a real inherited line-delimited
  filename bypass: a valid newline-containing Git path became two nonexistent
  paths and its contents were skipped. Merge stayed blocked despite green CI.
- The shared action now emits one JSON-string filename per record; scanner and
  reporter consume that lossless representation. The old `--paths-file` API
  remains compatible, while `--paths-jsonl` rejects malformed/non-string/NUL
  records and cannot be combined with the other explicit scope selectors.
- Actual passive Git trees cover newline, carriage return, Unicode line
  separators, whitespace and backslash filenames even on Windows, where some
  cannot be checked out. Binary tree creation and a NUL-delimited byte assertion
  prove exact fixture filenames before the real scanner command runs.
- The telemetry-corrected head's required hosted Windows runner and launch
  selections passed. Its subsequent low-severity documentation advisory exposed
  a CLI stdin-body transport failure; the full PR description was restored from
  an explicit file and read back, including Documentation impact and attribution.
  Review must evaluate that complete description and the filename fix together.

### 2026-10-13 - Shared scanner staging merged; release proof next

- #6577 merged at `22:43:02Z` as `5d229af2eca8`. Final head `1ad14bfff2aa`
  received native review `5510850988`: a clean owner-authored Comment with zero
  open findings under the repository's documented contract. The real filename
  finding was fixed and resolved; the generic recommendation for human security
  review did not identify another defect or introduce a new merge requirement.
- Current-head CI `38477671946` and lockdown passed. Before the managed merge,
  the actual source preflight verified all configured requirements, complete
  policy/evidence pages, bound integration identities and the unchanged head.
  No CI or required-check bypass was used.
- The final local workflow/scanner/policy selection passed 124 cases, including
  the real Git filename boundary. Current-head native Windows and Linux lanes
  passed; #6581's telemetry fixture repair is merged and that issue is closed.
- Admission and scanner migration are source-landed, not yet proven on default
  main. Keep the legacy gate and completion caller until a covering promotion
  exercises both replacement producers; then remove main-PR CI allocation while
  retaining full main-push, contributor and deep/platform coverage.
- Release run `38479483177` is queued behind failed full-suite attempts. Existing
  artifact/configuration repair owners retain their blockers. The campaign
  remains Active; this source merge closes neither cutover nor observation.

### 2026-10-13 - Full suites green; coverage evidence still blocks release

- Containing run `38479483177` passed all nine full plugin suites, including
  Agent Worktrees and Agent Bridge. The canonical #6587/#6588 fixture repair is
  merged; do not reopen or replace that completed source correction.
- Promotion job `115586929142` then refused because enrolled Agent Bridge
  coverage had not been collected/transferred into that run. This is a distinct
  evidence blocker, not another unit-test failure. The explicit evidence gate
  remains intact.
- Relayed the exact run/job/signature to the existing #6535/#4876 release-repair
  owner in #6535 comment `6157132946`, without a competing implementation or
  manual rerun. Run `38480618519` was already pending; its result alone cannot
  be predicted from the prior source merges.
- Default main still records validated dev `ca7b3e563067` from `07:32:25Z`, not
  the new admission/scanner source. The next consolidation step remains blocked
  on normal covering release and live producer proof. Legacy CI stays enabled.

### 2026-10-13 - Review lifecycle measurement and canonical collector repair

- Extended the existing complete three-hour event windows with review-workflow
timing, using all Actions API pages rather than the latest-run sample.
The earlier window contains 45 successful Copilot review runs; the later
contains 79. Median recorded lifecycle increased from 227 to 262 seconds,
and p90 from 389 to 446 seconds. These are `updated_at - run_started_at`
workflow metadata durations, not measured inference or review-engine latency.
- Every review run in both windows reports `run_started_at == created_at`.
That field therefore provides no independent queue-delay observation here;
zero recorded delay is not proof of an unqueued service. No causal latency,
throughput or quota improvement follows from the invocation reduction.
- The missing Bridge evidence is now traced by the existing release-repair
drivers to repository-root collection of `test_workspace_creation.py`:
`tests.test_acquisition_runtime` cannot be imported. #6597 owns the bounded
test-import correction and retains the full evidence requirement. Another
driver's proposed transfer was withdrawn after that ownership was confirmed.
- Run `38481804293` independently reproduces the collection failure in Bridge
job `115587540155`; an apparently successful optional baseline step alone
does not prove an artifact was produced. Await the canonical repair's normal
covering promotion. No competing patch, extra rerun or CI removal is made.

### 2026-10-13 - Canonical repair merged; containing validation pinned

- #6597 merged at `23:27:55Z` as `41615b8c601f`. Normal validation run
`38484939290` selects that exact source: completed full-suite job
`115596669721` records both checkout ref and actual HEAD as the merge SHA.
A dispatch's triggering commit or run-level default-main SHA is not the
selected validation input; authoritative job evidence resolves it.
- Older run `38481804293` failed the unchanged evidence gate in promotion job
`115596045700`. The containing run is still executing; complete baseline
collection, artifact validation and actual main publication remain gates.
- Shared the pinned run/job/SHA with the existing release owners in #6535
comment `6157573705`, including explicit pending stages. Main still records
the older `ca7b3e563067` release. The legacy CI producer remains untouched.

### 2026-10-14 - Collector proof clears; residual artifact mismatch blocks release

- Containing run `38484939290` completed all nine full plugin suites and
produced all nine enrolled baseline artifacts, including Bridge artifact
`11858944868`. Promotion's explicit collected-baseline requirement passed.
This is the hosted proof of #6597's collector repair, not merely local
validation or an optional step's reported success.
- Unsigned Python artifact build `115605421711` and validation/attestation
`115605705366` both passed. Promotion job `115605893690` then refused with
`promote-release: manifest payload hash differs from candidate` while
composing against exact dev `41615b8c601f`, with both baseline and signed
Python artifact directories supplied. No candidate merge or publication
occurred; successful attestation alone does not prove final composition.
- Relayed the residual contract mismatch and the now-passing collection gate
to the existing #6535/#4876 artifact owners in comment `6157878020`.
Normal run `38487345996` was already active; no extra dispatch, parallel
artifact implementation or evidence bypass was introduced.
- Default-main admission/scanner proof and CI cutover remain outstanding.
Keep every legacy producer until a genuine covering release clears them.

### 2026-10-14 - Repeated composition refusal; original publisher reached

- Normal run `38487345996`, on exact dev `d97acffbeab7`, passed its other
jobs but promotion `115613853426` repeated the payload-hash refusal at
`00:29:43Z`. There is still no covering release or replacement-producer proof.
- Resolved the original #6332 publisher's actual source-attribution root and
verified its live session before sending the exact two-run evidence directly.
The existing Fleet Control V2 consumer's #6596 released-only rollout blocker
was included. Delivery succeeded; repair acceptance or a canonical fix is
not yet confirmed. The newer bootstrap-repair root had no live session, so
no replacement session or service restart was attempted.
- A peer's LF/CRLF scratch-canonicalization trace is a candidate diagnosis,
not reproduced proof or permission to waive source/hash/wheel binding.
Await the retained artifact owner's acknowledgement and normal repair.
- The exact final-consumer repair is now reserved on #6535 as
`battered-toolbox-promotion-candidate` (comment `6158147260`). Relayed that
reservation to the original publisher and the Fleet Control V2 consumer
to prevent overlap; broader #4876 custody remains unchanged.
- A third normal run, `38492036670` on dev `f790097ec762`, repeated only the
same promotion refusal in job `115627083122` at `01:14:31Z`. Requested the
reserved worker's concrete reproduction/PR/next gate in comment `6158761173`;
quiet time does not expire the reservation or authorize another repair.
- Refreshed the eventual cutover's live required-context inventory: main has
no classic required-status-check protection, and its complete effective
rules include only `main source gate` as a required context, with strict
freshness false. No required-workflow rule was present. The existing trusted
host preserves that context name; no policy mutation or orphaned main PR
aggregate is needed for this slice. Release and live producer proof still
precede any removal of the old CI invocation.

### 2026-10-14 - Source repair confirmed; independent clock fixture repaired

- Provider evidence confirms #6604 merged at `00:54:06Z` as `1097e7f401de`.
An open-PR-only progress lookup missed that completed publication; the earlier
"no repair PR yet" status is corrected. The shared canonical artifact reader
is source-landed, not released. #6611 separately prepares its dev workflow
mirror; neither change authorizes this campaign to perform the distinct
trusted-main bootstrap. Main still carries the older #6558 workflow.
- Later validation `38495860924` instead fails the full worktrees suite at
`test_identity_lookup_shares_overall_budget_without_two_second_cutoff`.
Existing issue #6610 had no exact repair reservation or PR; reserved only
this fixture family as `spry-cog-local-cache-clock`.
- The actual contained native test reproduced the hosted StopIteration
failure. Its three-value clock was patched onto the shared standard-library
time module, so real launch-stage instrumentation exhausted those values.
Bind the fake clock only to the module under test, including the three
neighboring deadline fixtures of the same class. The regression explicitly
verifies the standard-library monotonic clock is unchanged.
- The complete local-cache/launch-progress selection now passes 71 tests,
with two explicit platform skips. Exact ten-second overall, ten-second
lookup and seven-second remaining-render assertions are retained; no
production deadline, renderer, authority or fallback code changes.
- The existing finite-clock regression joins the cheap deterministic guard
selection, so required PR CI executes it rather than only collecting it.
No new test case, matrix, workflow or subprocess is introduced.
- This is a test-only, below-altitude release repair with the required
agent-worktrees patch changefile. The real refresh and launch instrumentation
supply the simple integration contract. Fresh-install/runtime rollout lanes
do not apply because no install or runtime behavior changes. Current-head
hosted checks, review and a containing full validation remain required.

### 2026-10-14 - Clock repair merged; trusted-main recovery remains separate

- #6612 merged at `02:34:51Z` as `0d28a69bdc20`, after exact-head native
  approval `5512325368` with zero findings and successful required CI.
  The actual source preflight verified complete paginated requirements,
  integration-bound evidence and unchanged head before the managed merge.
- Initial Windows CI repeated the separately tracked #6266 seven-console
  signature. One recorded unchanged-head diagnostic, requested only after the
  parent run completed, passed both real integration cases without skips.
  No safety assertion or required check was changed or bypassed. #6266 stays
  open with #6274's owner; this observation does not declare that issue fixed.
- Closed only #6610 after current-head proof and merge, released its bounded
  fixture reservation, and verified #6237 remains open. The canonical effort
  still requires a normal containing full validation and release.
- #6611's reviewed dev adapter mirror merged at `02:29:04Z` as `4a1b15c99fbd`.
  The latest observed trusted main still contains #6558, not the new promoter
  adapter. Asked the retained artifact owner to identify the distinct
  authorized bootstrap execution gate; no main patch or release dispatch
  is inferred from the dev merge.
- Next: observe that owner's trusted-main recovery and a normal covering
  promotion, then prove default-main admission/scanning before removing
  legacy main-PR CI. Main-push, contributor and distinct deep/platform
  coverage remain untouched.

### 2026-10-14 - Containing suite clears; authorized bootstrap is already claimed

- Normal run `38502638232` pins exact dev `0d28a69bdc20` and passes every
  full plugin suite, guards, baseline checks and artifact attestation. Only
  the old trusted-main promoter fails, in job `115661812252`, with the same
  payload-hash refusal. This closes the clock repair's containing full-suite
  validation, not the release or producer-migration gate.
- The maintainer explicitly authorized this campaign to drive the narrow
  workflow-only main bootstrap using #6611's reviewed adapter, preserving
  ordinary review, required checks and artifact validation. No helper-source,
  token/ruleset, runtime-update or broader control-activation authority follows.
- The required fresh coordination check found prior stewardship notice
  `6160133003` and explicit `placid-pendulum-promoter-main-bootstrap` reservation
  `6160291385`. No competing main patch or workspace was created. Requested
  one canonical bootstrap PR and execution gate in #6535 comment `6160342204`;
  this campaign retains the consolidation objective and consumes that same
  authorized repair rather than preempting its existing executor.
- The canonical execution is now #6619, acknowledged by the original artifact
  campaign in comment `6160373797`. Exact head `47113c401948` changes only
  the one authorized workflow. Its fresh native review approves with zero
  findings and the main source gate passes; remaining CI and actual merge
  are still observed gates, not completion. Follow this PR rather than
  creating another bootstrap.

### 2026-10-14 - Canonical trusted-main bootstrap merged

- #6619 actually merged at `03:52:34Z` as
  `e1566a2a31a97bc5f084da123da149c88102ae8b`. The exact reviewed head remains
  `47113c401948`; its native approval, main source gate, guards and existing
  identifier check all pass. Only the authorized release workflow changed.
- This deploys the temporary trusted-main compatibility adapter, not the
  staged admission/scanner producers or a complete consumable release.
  The passing source gate on this bootstrap was the legacy CI producer.
- Normal run `38507249476` was created before the bootstrap and still names
  old main `9c4d6acade64`; do not mistake its later start for the repaired
  workflow definition. The retained executor owns the next normal full
  promotion. Observe a fresh trusted-main recipe and exact selected dev
  input before claiming the artifact refusal is cleared.
- Keep the release, live producer proof and CI-removal gates open. No
  additional publication run, updater or temporary-adapter cleanup was
  performed by this coordinator.

### 2026-10-14 - Promoted source verified; post-merge publisher has a distinct blocker

- Fresh run `38508930963` passed all full suites, coverage requirements,
  artifact build/attestation and the repaired promoter. Generated #6631
  merged at `05:24:52Z` as `f5e4e30353a076aec74781e6ef5cfe5df474e939`,
  from validated dev `e22e05cc54be` and prior main `e1566a2a31a9`.
- The real annotated tag `promote-20261014052516-f5e4e303` resolves to that
  exact merged commit. The state file's earlier candidate-time tag prefix
  is not the actual post-merge tag; verify the real ref rather than infer it.
- The trusted host, shared scanner action and reporter on actual main have
  exactly the reviewed blob identities. Their definitions are promoted,
  but no post-promotion host event has run yet. Live execution proof and
  legacy-CI removal remain open.
- Only post-merge publisher job `115696521430` fails: temporary-index
  `git rm --cached` refuses the new release-state bytes against its older
  checkout. #6632 and the explicit `placid-pendulum-publisher-index`
  reservation retain that repair; the owner has reproduced the exact
  stderr with real Git and will use the normal dev flow. No competing
  helper patch, retry, asset replacement or integrity waiver is introduced.
- Source promotion and tagging are confirmed; complete first-party artifact
  publication is not. Continue read-only producer verification while that
  separate recovery proceeds, without weakening any cutover gate.

### 2026-10-14 - Dev-owned baseline producer exercised on default main

- Natural post-CI run `38519754668`, job `115706505844`, uses trusted main
  `f5e4e30353a0` and committed dev input `697df793f896`. Its actual command
  is `python -I trusted/tools/check-module-size.py --repository candidate
  --refresh-baseline --widen-only`, followed by the ordinary ceiling check.
- The real output reports the baseline already current and all source
  modules within their 1000-line cap/ceiling. Dev advances before publication,
  so the stale-input fence correctly skips the baseline-only dev PR.
  No baseline change or PR is invented to make this a positive test.
- The live post-CI workflow blob exactly matches the reviewed dev-owned
  publisher configuration; both obsolete main-baseline workflow files are
  absent from the promoted tree. PR-only completions correctly skip this job.
- This closes #6438's GitHub-hosted activation proof. It does not claim a
  fresh baseline PR, local runtime installation, complete artifact
  publication, or completion of the parent consolidation campaign.

### 2026-10-14 - Both default-main replacements proven; invocation cutover reserved

- Natural #6642, exact head `96193a99e3f7`, exercises trusted host run
  `38523711315`. Its native main source gate job `115719048291` passes on
  that exact PR head under the GitHub Actions integration.
- Scanner job `115719481024` actually checks out promoted main
  `f5e4e30353a0`, not PR code. Its event is non-stale, denylist configured,
  and every prerequisite plus actual scan succeeds. The unavailable-scanner
  fallback is skipped.
- Successful identifier check `115720880437` completes inside that host's
  publication step at `06:47:30Z`, scanning 322 changed generated-head files.
  The legacy caller's successful check `115719481029` scans the same 322
  files on the same head. This proves active scanning and head-bound
  reporting, not merely a green job or an unconfigured neutral result.
- Source promotion and the replacement consumer contract are now proven.
  Existing #6632/#6633 publication recovery remains with the artifact owner;
  no signature, immutable-asset or runtime-adoption requirement is waived.
- Reserved only the planned main-PR invocation cutover in #6237 comment
  `6162851913`, after fresh open-PR/thread deduplication. Next remove the
  redundant main-PR CI allocation and duplicate gate through normal dev
  review, retaining full main/dev push, other PR targets, fork approval
  and distinct deep/platform coverage. The cutover checkbox stays open
  until the change and its actual invocation outcome are proven.

### 2026-10-14 - Main-PR CI allocation removed from the candidate

- After the default-main producer proof above, `ci.yml` excludes exactly
  `main` as a PR base and removes its duplicate admission job. All other
  PR bases, including a `main` head targeting dev, retain CI. Pushes to
  main/dev and manual validation retain their complete existing job graph,
  permissions and selection settings; a parsed before/after comparison
  confirms no surviving CI job changed.
- The trusted-CI guard now requires that exact base exclusion, rejecting
  missing triggers, path/type filtering, dev-only selection and additional
  excluded branches. Real-Git admission and rollback tests execute the
  retained trusted host rather than the deleted duplicate.
- Local validation passes 189 focused CI, admission, scanner, attribution,
  fleet, installation-context and payload-CWD cases. Ruff F/E9, trusted-CI,
  documentation, install-contract and whitespace guards pass. The real-Git
  tests exercise actual shell/subprocess and ancestry boundaries.
- This closes the vision's gated-reversible-migration slice without
  extending ownership or activating preservation. No plugin payload,
  installer, fresh-machine behavior or resident daemon changes, so a
  clean-room install and local runtime update are not applicable.
- Current-head native review, configured required checks and the source
  integration-bound preflight still precede merge. A normal containing
  promotion must then prove actual main-PR CI invocation absence while
  the trusted admission/scanner and main-push CI remain. This journal
  records candidate validation, not release or parent completion.

### 2026-10-14 - Review caught retarget admission

- #6661's first current-head native review identified a real missing event:
  retargeting main to dev emits `edited`, while the default PR activity
  set would leave the required CI absent. Added that activity alongside
  opened/reopened/synchronize and made the exact set a guarded contract.
  Other edits also invoke CI because GitHub has no workflow-admission
  filter for base edits alone. This bounded cost preserves immediate
  retarget feedback without adding a separate dispatcher or privileged
  PR-code path.
- Corrected the remaining current producer pointers in rollback and
  promotion comments. No runtime program or release-job behavior changed.
  The earlier structural-equality proof now has one additional intentional
  event difference; all surviving jobs and push/manual paths remain intact.
- The revised candidate passes 192 focused cases, Ruff F/E9, trusted-CI,
  documentation and whitespace checks. Parsed CI equality confirms only
  the stated base/activity changes and duplicate-gate deletion.

### 2026-10-14 - Invocation cutover merged; containing release pending

- #6661 merged at `08:37:50Z` as
  `e82d8d94a11a7f4ab9d25544c73e6c321bdd397a`. Exact reviewed head
  `fc979a4b4e7a` received native approval `5514884572` with zero open
  findings. Both review threads were fixed and resolved.
- The first merge attempt correctly failed while a later same-head CI
  suite was still pending, despite the earlier suite's successful aggregate.
  Waited for latest edited-event CI `38531058695` to succeed, then reran
  complete integration-bound required-check verification and the managed
  merge. No status check, policy or review gate was bypassed to resolve
  that refusal.
- Confirmed the provider's merged state and #6237's open state. The
  published two-commit patch exactly matches the upstream squash patch.
  After the ordinary pull-forward auto-aborted that multi-commit replay,
  retained a backup and dropped only that proven-published history before
  the supported sync succeeded.
- Normal dev CI `38536473092` was created for the actual merge. Continue
  through its containing promotion, verify the promoted workflow bytes,
  and prove main-PR CI invocation absence alongside the retained trusted
  admission/scanner and full main-push CI. The cutover checkbox and parent
  effort remain open until those live outcomes are recorded.

### 2026-10-14 - Containing release meets an already-owned fixture blocker

- The cutover's first dev CI `38536473092` was superseded by natural
  dev advance `834e7936ad20`; follow that normal flow rather than dispatch
  another release.
- Active full validation `38532443234` tests snapshot `80f3d86b8`.
  Completed worktrees job `115754166934` fails only
  `test_exact_session_fallback_scans_record_names_deterministically`:
  its synthetic project has no receiving-side registration authority.
  This is not evidence that the production authority fence should change.
- Exact issue #6675 already reserves that fixture repair and names the
  existing shared configuration fixture as its source. Do not build a
  competing repair. #6671 separately tracks the earlier profile-assignment
  fixture family; prior failures are not accepted flakes or a waiver.
- Published the actual #6661 merge and pending-release gate on #6237 in
  comment `6164474879`, retaining this slice's proof reservation. The
  parent campaign remains active and no complete artifact release or
  runtime rollout is claimed.

### 2026-10-14 - Inert support publication gate resolved

- Verified the already-confirmed #6631 main commit
  `f5e4e30353a076aec74781e6ef5cfe5df474e939` carries #6521's exact
  `tools/promotion_control.py` blob
  `3c73e80e82a307c4c3a8ea359fa138f02a907a73`.
- Its real composer imports and calls that support with the pinned main
  input. The committed tree has no `.github/promotion-control.json`, and
  the release-state receipt has no `control_preserved` entry.
- This closes only Phase 2's previously open source-publication proof.
  Preservation remains inactive; no allowlist, main-control rollout,
  concurrent-repair behavior or rollback-under-activation is claimed.

### 2026-10-14 - Retained registration-fixture repair merged

- Normal validation `38534938481` checks out actual dev
  `834e7936ad204f06bb7a5572e677707e21c173a2`, which includes #6661.
  Worktrees job `115765163594` repeats the same #6675 exact-session
  fixture failure, not a new post-repair failure.
- The retained executor's #6682 merged at `09:24:13Z` as
  `be910de4f60b4e24a0c77e4d8971c46baa0d9330`. Its shared-fixture repair
  retains real registration and the original deterministic selection;
  the owner's 186-case contained portfolio and cheap guard selection pass.
- Normal dev CI `38542087880` now covers that repair. Follow the next
  containing full validation and promotion; no retry, additional dispatch,
  competing patch or authority-fence relaxation is needed.

### 2026-10-14 - Coupled budget-hook contracts repaired locally

- Normal dev CI `38539344925` on `0976e89aa0ed` fails only the
  customizing-copilot suite: its explicit inline roster omits the reviewed
  budget advisory boundary, and the output-free stack proof does not
  recognize the new Bash wrapper. Reserved the unowned pair as #6699
  after exact issue/open-PR deduplication; #6646's feature is already merged.
- Reuse the existing suite-standard empty-JSON EXIT trap in that wrapper,
  retaining its child-output suppression, redacted diagnostics and deadlines.
  Reconcile the owned inline roster rather than weaken scanner classification
  or introduce a new semantic policy. Stack assertion failures now show the
  responsible inventory. The missing-writer regression requires exactly one
  empty envelope.
- Full native validation passes 493 customizing cases and 296 budget cases,
  with 40 explicit platform/opt-in skips in the latter. Windows exposed an
  oversized parametrized ID overflowing `PYTEST_CURRENT_TEST`; short case
  labels fix it without shrinking the 65537-byte input or its assertions.
  The full budget suite uses admitted Git Bash, not the unavailable WSL
  launcher; no persistent host setting or test coverage was suppressed.
- A separate actual-wrapper check on a fresh temporary home verifies startup
  and resume cue creation, malformed input and missing-writer envelopes,
  retention of previous cue bytes and no runtime provisioning. It explicitly
  admits a working Python rather than accepting a WindowsApps alias's
  success-shaped empty run. Scratch is removed afterward.
- Ruff F/E9, docs, install-contract, headless and whitespace guards pass.
  Both affected plugins have tool-generated patch changefiles. Linux
  SIGALRM and the new POSIX regression remain hosted-CI obligations;
  this is not the separate native-desktop acceptance deferred under #6644.
  The repair still needs ordinary dev review/checks, merge and a containing
  release before the original main-PR invocation proof can finish.

### 2026-10-14 - Coupled hook repair merged; containing release pending

- #6701 merged at `10:29:26Z` as
  `b03852f590f1443fd33a0849b22cd1b287c312d8`. Current-head Copilot
  review `5516515382` approved with zero findings; all three same-head
  check suites completed successfully. The source required-check verifier
  independently accepted the exact published head immediately before merge.
- PR CI `38547642918` passes the actual Linux customizing-copilot and
  budget-guidance suites (`115798946602` and `115798946951`), closing the
  hosted obligations recorded above. Windows runner contracts and the
  required aggregate also pass. No review or CI bypass was used.
- Normal dev CI `38549929226` starts on the repair's merge commit.
  Follow normal containing validation and promotion, then prove that main
  PR events allocate no legacy CI invocation while the trusted admission
  and identifier checks remain and full main-push CI still runs.
  This source merge does not complete that release proof or the parent effort.

### 2026-10-14 - Containing validation admitted; policy snapshot refreshed

- The merge-tip CI was superseded without allocating jobs; it was cancelled,
  not a failing validation. Normal validation `38551356685` is admitted and
  its actual guards job `115810220872` checks out dev
  `2ebdc595c00f97508bbad632e367e1bbdac540c8`, which contains both #6661
  and #6701. The workflow API's `f327cf44793c` head identifies the main
  workflow definition, not the validated dev snapshot. No extra dispatch
  or retry was needed.
- Read-only policy snapshot: main ruleset `18553911` requires `main source
  gate`, non-strict; dev classic protection requires `PR gate (required
  check)` from Actions app `15368`, non-strict and admin-enforced.
  Dev ruleset `24152679` independently requires `workflow-lockdown-guard`
  and `source-attribution`, with no bypass actors. Ruleset `23904550`
  requires a PR without bypass; `24069919` requires one approval and
  code-owner review with the existing maintainer pull-request bypass.
  `24412577` blocks creation of `worktree/**` branches on origin.
- Both branches also retain non-fast-forward/deletion ruleset protection.
  The classic force-push setting alone does not describe that effective
  policy. Nothing here adds strict freshness or changes any rule.
- The current workflow catalog has 22 entries: 21 active, including three
  dynamic platform entries, and the existing manually disabled failure-fix
  workflow. The `copilot` environment has no protection rules;
  `main-promotion` uses protected-branch deployment policy only.
  This snapshot does not claim a credential audit or complete dependency
  classification, and it does not authorize a policy mutation.

### 2026-10-14 - Later fixture group has a retained repair owner

- Containing full validation `38551356685` reaches sub-suite 15/15, then
  agent-worktrees job `115810221047` fails three receiving-authority
  fixture cases: the terminal-managed registration assertion (#6212)
  and two owning-tracking-directory cases (#6693). That group reports
  3 failed, 721 passed and 2 skipped. The previously repaired exact-session
  group passes 634 cases with one skip; the old #6675 failure is not repeated.
- The existing #6696 executor already owns all three corrections and has
  published the narrow fixture updates with 405 contained cases passing.
  Confirmed that custody on #6212 rather than opening a competing repair,
  weakening authority, retrying the failed run or waiving its result.
- #6701 remains merged and its hosted checks remain proven. The release
  proof is now waiting for #6696 and the next normally admitted containing
  validation; this is a distinct, tracked blocker, not a release success.

### 2026-10-14 - Authorized fixture split deduplicated to existing break-fix

- The retained #6696 API PR is still open with two Medium findings in its
  latest review body; its zero headline count does not make that a clean
  verdict. Repeated bounded waits and cooperative comments did not establish
  advancement. No verified live owner contact was found; that does not
  establish abandonment.
- The operator explicitly authorized a separately reviewed repair of only
  the three fixtures, without changing or superseding the API work. Fresh
  pre-edit deduplication found #6726 already implements exactly that split,
  under its own explicit operator authorization and current-head approval.
  Retained #6726 as the sole writer rather than publishing a duplicate.
- Its two fixture builders use the registration-specific authority resolver
  introduced by #6586, keeping all original assertions and production source
  unchanged. The owner's isolated reproduction reports three failures before
  the repair and 76 passing cases afterward; hosted checks and the normal
  containing release remain required.
- Shared the deduplication and separate #6237 release-proof ownership on
  #6726. Main is still `f327cf44793c60321bc205eddadccaeda3d5ee43`;
  no containing release or main-PR invocation reduction is claimed yet.

### 2026-10-14 - Fixture-only repair merged; distinct scanner repair retained

- #6726 merged at `18:25:48Z` as
  `028fc2e08803ca446d64b163b7795e4f8bb53924`. No competing fixture PR was
  created here. Normal merge-tip dev CI is `38615086423`.
- Earlier dev CI `38607818951` exposes a distinct scanner failure in job
  `116017320517`: the complete stack classifies agent-worktrees as a
  possible output after #6716 adds a stderr-only diagnostic. Budget-guidance
  remains proven output-free, so this does not repeat #6699.
- Fresh deduplication finds #6729 already owns that scanner correction.
  Shared the exact failure and retained its normal review/check flow;
  no second scanner implementation or retry was started here.
- Also notified the concurrent #6730 fixture driver that the three
  overlapping cases have already merged through #6726. Its distinct fast
  guards and negative-Resume work remain with that driver; this session
  neither closes nor mutates their PR.
- Follow the normal containing release after both repairs reach dev.
  Main-push coverage and trusted admission/scanner publication remain
  required; no partial release or invocation-reduction success is claimed.

### 2026-10-14 - Authorized producer-only compatibility repair

- #6729 still has a Medium finding: its scanner exemption can admit compound
  or nested stdout prints. Do not waive that finding or take over its proposal.
- The operator explicitly authorized a smaller producer-only break-fix:
  use hook_client.py's existing stderr-write convention for the new catalog
  diagnostic, retaining its exact message, newline, empty catalog fallback
  and stdout behavior. Reserved #6740 and explained the non-overlapping scope
  on #6729 before editing.
- The unchanged whole-stack contract reproduces the current dev failure
  locally. The repair changes no scanner code, classifier rule, stdout
  refusal, ownership/admission behavior, shell path or installer lifecycle.
  A strengthened existing regression checks exact stderr, empty stdout
  and restoration of the bytecode setting on the missing-helper path.
- The complete contained hook IPC module passes 119 cases; the complete
  unchanged customizing-copilot suite passes 496. A fresh subprocess/home
  and partial payload with the actual binding helper absent reproduces the
  exact stderr diagnostic with empty stdout, no runtime provisioning and
  no bytecode. Ruff, docs, install-contract, headless and whitespace
  checks pass. The required patch changefile is tool-generated.
- Ordinary current-head review/required CI, merge and a containing release
  remain outstanding. This below-altitude compatibility repair does not
  complete #6729's separate scanner-improvement proposal or #6237.

### 2026-10-14 - Producer-only break-fix merged through normal gates

- #6741 merged at `20:20:21Z` as
  `ee4e6b0063dff7eafed04942feec10974de4f52c`. Native Copilot review
  `5524099241` approves its exact head with zero findings. CI
  `38629746629`, all three same-head suites and the fresh source
  required-check verifier pass before managed merge; no bypass was used.
- The actual publication contains only the hook diagnostic, its existing
  regression, reference documentation, required patch changefile and this
  chronological journal. The scanner and #6729 remain unchanged.
- Normal dev CI `38631216916` is queued on that merge. Continue through its
  containing full validation and promotion, then the original #6661 main-PR
  invocation proof. The effort stays Active; a source merge is not release
  or runtime-rollout evidence.

### 2026-10-14 - Dev contracts pass; next full gate exposes startup timeout

- Normal dev CI `38631216916` passes all 36 jobs on #6741's merge, including
  customizing-copilot job `116083334574`. Natural validation `38633055933`
  then checks out that exact dev commit in guards job `116089425298`.
- The full gate exposes two different failures. The late-owner Resume
  test mock rejects the production `verdict` keyword in both modes; the
  retained #6730 writer has already merged that correction afterward as
  `3fc5f5b1812c3cab05d26e33eb8d3551be30437c`. Do not duplicate it.
- Logger job `116089425533` times out after its unchanged 20-second
  PowerShell classifier-child budget, before any stdout or stderr, with
  475 cases passing and 29 skipped. Reserved the unowned exact signature
  as #6747; the broader historical #5245 is not this failure's custody
  or an acceptance of a flake.
- The exact two shell cases pass unchanged on Windows, so the Linux timeout
  cause is not established. The harness currently inherits ambient startup
  state. Its bounded correction uses the existing isolated-home helper,
  noninteractive startup, no telemetry/update checks and headless Windows
  execution, preserving both classifier outcomes and every existing deadline.
  An initial stderr marker makes any future pre-script stall distinguishable.
- Both existing shell cases now join the fast guard lane. The complete
  installer-binstub module passes 17 cases with one platform skip; Ruff,
  docs and whitespace checks pass. No production installer changed.
  Linux current-head guard/full validation remains outstanding, and #6747
  remains an investigation rather than a claimed root cause or accepted flake.

### 2026-10-14 - Harness isolation reviewed; later scoping fixture blocks release

- #6756 merged at `21:18:05Z` as
  `4578b90c131e0b7bdd58282da125b439d0ac7a01`, after exact-head Copilot
  approval `5524739132`, all three successful same-head suites and the fresh
  source required-check verifier. No bypass was used.
- Its Linux CI logger job `116105413372` runs the complete suite, not merely
  guards: 476 passed/29 skipped and 823 passed/32 skipped across both groups.
  The original unexplained #6747 timeout remains open. The next full run's
  unmodified logger also passes before this harness change is included, so
  neither result establishes the original cause or licenses flake acceptance.
- Normal full validation `38637663769` actually checks out dev
  `c45b1430b53a387ddb588ab5c9708d35273393e5`, containing the Resume
  correction but predating #6756. Its later Worktrees group now fails
  `test_create_worktree_core_plan_project_matches_repo_name_not_ambient_global`
  with an incomplete resolved-config namespace (`yaml_path`, then `repo`).
  That group reports 511 passed and 26 skipped; the remaining groups are not
  established by that failed job.
- Deduplication finds no existing writer for this exact scoping fixture.
  Continue bounded diagnosis while preserving the real project-selection
  assertion; no publication rerun or production authority relaxation.

### 2026-10-14 - Scoping failure traced to incomplete tracking-record double

- Reserved #6758 and reproduced the exact failure locally. The provisional
  resolved-config description was imprecise: the helper returns a four-field
  namespace from `tracking.create_new_record`. The real deferred-command
  constructor now reads `record.yaml_path`; its failure wrapper also needs
  `record.repo`.
- Reuse the existing complete `WorktreeRecord` fixture through
  `dataclasses.replace`, keeping the creation factory's requested identity,
  repository, machine, platform and kind. No production field fallback or
  authority check changes. The actual core/deferred-command path and both
  selected-project assertions remain intact.
- The complete scoping module passes four cases. Its existing behavioral
  create-plan case now joins fast guards. Expand contained local coverage
  before publication because earlier full gates stopped at successive stale
  fixtures; do not mistake an earlier group's pass for the remaining groups.

### 2026-10-14 - Equivalent scoping PR discovered before publication

- A second writer independently claimed #6759 and published #6763 while this
  session was broadening native coverage. Its exact patch is the same typed
  record/real deferred-plan repair, with the requested codename and owning
  tracking-directory argument also preserved. It has current-head approval.
- Retain #6763 as the sole published repair rather than compete over claim
  timing. Withdrew only this session's unpublished duplicate fixture,
  documentation and generated changefile; the local four-case result remains
  evidence, not a claim that this session's code shipped.
- The optional full native Windows run stopped at the unchanged 300-second
  bound in group 1/16 without reporting an assertion failure. It is not a
  full-suite pass, and no limit was raised or bypassed. Existing required
  Windows lanes and actual containing Linux full validation remain distinct
  obligations. Follow #6763's normal merge and the next containing release.

### 2026-10-14 - Canonical scoping repair merged; next failures already owned

- #6763 merged as `95d9d6b92208fa79f49fc424cface20602deab57` at
  `21:54:09Z`. Closed this session's duplicate #6758 implementation claim
  into #6759/#6763; #6237 release-proof ownership remains here.
- Natural full validation `38645036098` actually checks out dev
  `5d08c74d7185dcaf3b3f5c61af97c997576e427f`. The repaired scoping group
  now passes 512 cases with 26 skips. Logger also passes; this does not
  establish #6747's original timeout cause or accept it as a flake.
- The full gate fails in the next Worktrees group on the exclude-FIFO
  refusal (`timeout` instead of `failed`, #6770). Existing #6769 already
  owns the unsafe-input-before-Git diagnosis. Dispatch separately fails
  startup/reconnect delivery timing (#6771), whose retained writer has
  claimed the actual TCP/SSE diagnosis. Do not compete with either repair.
- The promoter job's green status is only its early refusal/no-op: candidate,
  signing, merge and artifact-publication steps are skipped after failed
  validation. No release occurred. Follow the retained repairs and next
  normal containing validation; no retry, skip, timeout increase or CI waiver.

### 2026-10-14 - Reconcile the next reuse and repair-ownership gate

- Both #6769 and #6771 retain their original repair owners; no published
  containing fix was found at this refresh. Natural validation `38647658802`
  is queued. No competing diagnosis, manual dispatch or release waiver.
- The configured Dependabot version-update route is already implemented,
  regression-protected and observed on live #6710. Close only that scoped Plan
  item; this does not prove security-update routing or a measured run reduction.
- Made the next exact-input reuse boundary explicit in `design.md`: actual
  checkout, execution recipe, platform, run/attempt/jobs and composed output
  must be reconciled before any further validation removal. The current signed
  artifact index does not itself contain suite/recipe evidence. Main-push CI
  and the distinct coverage collection remain intact.
- #6142 supplies offline construction, not a deployed repair service. The
  optional cloud fixer is currently `disabled_manually`; an explicit disabled
  skip is not a named repair owner or acceptance of the underlying failure.
  Failure routing/escalation therefore remains an open consolidation gate.
- The retained #6697 publisher is separately changing the routine merge event
  contract. Its proposed explicit post-merge CI/lockdown dispatch must be
  recorded as such, not substituted for this campaign's outstanding natural
  main-push proof. No competing bootstrap or policy mutation is introduced.

### 2026-10-14 - Reviewed evidence reconciliation merged; FIFO repair follows

- #6774 merged as `2635c295c5095a8e1d4e64c5abcf367b570b57e8` at
  `23:18:08Z`. Exact head `c9df18ad404b` received native Copilot approval
  with zero findings. All three same-head check suites and the fresh source
  required-check verifier passed before managed merge; no CI bypass.
- Reread the Plan and Validation Plan after merge. Only the scoped existing
  version-update routing item closes; the proposed reuse boundary is not an
  implemented compact gate, and #6237 remains Active.
- Retained FIFO repair #6773 merged independently as
  `23367596f6caabeee879df9bfb1813cdc6ad895c` at `23:13:09Z` after native
  approval. No competing patch or merge was performed here.
- Earlier natural full `38647658802` validates the unchanged `5d08c74d7185`
  input and repeats the FIFO refusal. Dispatch job `116141175848` passes all
  eight ordinary groups and emits baseline `11934069896`; the original
  #6771 cause remains open, not repaired or accepted as a flake. Shared that
  unchanged-input evidence with its retained owner.
- Natural full `38652751643` is queued. Establish its actual checkout and
  containing release before claiming the #6661 main-PR invocation cutover;
  no manual dispatch, deadline increase or local rollout.

### 2026-10-14 - FIFO cleared; optional-assignment plan assertion repaired

- Natural full `38652751643` actually checks out
  `23367596f6caabeee879df9bfb1813cdc6ad895c`, including #6773. The
  FIFO group's 498 passing cases and five skips clear that earlier blocker.
  Worktrees then reaches group 10/16 and exposes
  `test_new_worktree_survives_optional_assignment_state_failure`.
- Exact issue/PR searches find no existing repair. Reserved only this family
  under #6237 and reproduced the same failure in the contained native runner.
  The test strips execution admission but then mistakes the still-required
  setup launcher for Copilot. The production two-layer plan is intentional;
  the fixture's unrelated fake-Git metadata warning does not cause that wrapper.
- The existing case now requires the exact admission module, record and
  admission-only flag before checking the real setup command's underlying model
  arguments. Both real launch builders, profile environment and absence-of-
  assignment assertions remain; no production behavior or deadline changes.
  This case also joins fast guards.
- The complete assignment, setup-launcher and creation-seed/admission modules
  pass 161 cases in the contained native runner. The actual guard selector
  executes and passes the repaired case. Ruff F/E9, docs, the 13-plugin install
  contract and whitespace checks pass; the required patch changefile is
  generated. The later watchdog #6780 matches this already-reserved repair,
  with no other writer. Hosted review/CI and containing release remain gates.

### 2026-10-15 - Optional-assignment repair merged; normal release still open

- #6781 merged as `2e1d19f58789254307fafaf42afc47c4b2de00b2` at
  `00:07:18Z`. Exact head `a4803388340d` received native Copilot approval
  with zero findings. All three same-head suites and the fresh source
  required-check verifier passed before managed merge, without CI bypass.
- Hosted Linux job `116169595572` executes the real guard selector:
  971 passed, 19 skipped, including this newly guarded case. The separate
  Windows launch job also passes. Closed #6780's stale fixture expectation;
  neither that closure nor a dev merge is release or installed-runtime proof.
- Reread the canonical Plan and Validation Plan. The campaign remains Active:
  ordinary dev CI `38658176845` and natural full `38658267992` are queued.
  Establish the latter's actual checkout before attributing a containing pass.
- #6775 remains the sole reconnect writer; its updated head now has native
  approval. Do not merge it on that writer's behalf, waive a finding or start
  another repair. Continue normal release and the original main-PR invocation
  proof; main-push coverage and all deadlines remain unchanged.

### 2026-10-15 - Optional-assignment clears; registry probe fixture is stale

- Natural full `38658267992` actually checks out
  `2e1d19f58789254307fafaf42afc47c4b2de00b2`; guards job
  `116173193107` records both the ref and actual Git HEAD. Worktrees reaches
  group 11/16 after the repaired profile group passes 964 cases with two skips.
- The next failure is the registered-repository headless-options assertion,
  with 793 cases passing and one skip in that group. Exact issue/PR searches
  find no retained writer. Reserved only this contract mismatch under #6237
  and reproduced it through the contained runner's supported file selector.
- The production registry probe intentionally delegates to the canonical
  isolated-repository Git helper. Its stale test patches the former direct
  subprocess owner's headless helper and omits explicit decoding/environment
  arguments. Correct only that test seam and expected transport contract,
  keeping the real Git helper and intercepting only its subprocess.
- The guard also checks poisoned Git repository selectors are removed,
  noninteractive prompting is enforced and unrelated environment is retained.
  No production code, deadline, isolation rule or fallback changes. Complete
  coupled-module and remaining unproven full-group coverage precede publication.

### 2026-10-15 - Retain the canonical registry repair and honest native coverage

- The local complete registry/Git-helper selection passes 142 cases. The
  normal remaining-file runner passes native group 11 with 793 cases and two
  skips, then reaches the unchanged 300-second limit in group 12 without
  reporting an assertion failure. This is not a complete native pass, and
  no deadline or resource ceiling was raised.
- A disjoint selection of still-unrun groups 13-16, not a retry of group 12,
  exposes a Windows-only windowless-parent probe failure in
  `test_status_monitor_cutover_process.py`; its GUI Python child exits 1
  without captured diagnostics. That first group otherwise passes 792 cases
  with 24 skips. Subsequent groups remain unproven; no flake is accepted.
- During that broader validation another release driver publishes #6785 for
  the same registry contract. It preserves the real shared Git helper,
  exact headless/decoding/timeout checks and stronger environment isolation.
  Retain that sole published writer, withdrawing only this session's
  unpublished duplicate fixture, testing paragraph and generated changefile.
- #6785 merges as `4f7b62facbe44df61f159dcde9b1afb11d34b3ea` at
  `01:01:18Z` after native approval. No competing PR, other worktree change
  or merge was performed here. The separate Windows probe needs bounded
  diagnosis; normal containing Linux validation and original cutover proof
  remain outstanding.

### 2026-10-15 - Native probe exposes stale uv dependency cache, not a daemon bug

- Capturing the real GUI parent's diagnostics shows an import failure before
  any daemon launch: its isolated interpreter loads an old installed
  `agent_procutil` without `spawn_daemon`, although the current vendored source
  has that API. The existing runner's explicit `--reinstall` rebuilds the venv
  but still consumes uv's stale same-version local-path wheel.
- Rebuilding only that local test dependency makes the unchanged two-cycle
  window/focus/PID contract pass. The bounded root fix forwards explicit
  reinstall to uv; ordinary cached runs and automatic fingerprint policy stay
  unchanged. The new regression fails before the fix only for explicit
  reinstall, then both explicit and ordinary cases pass.
- Broader environment-lease validation exposes a tightly coupled Python 3.11
  junction cleanup gap. Read the existing reparse tag to unlink a child junction
  without traversing its target; preserve the leased root identity and outside
  sentinel. The strengthened old regression covers absence of the newer
  `Path.is_junction` API.
- The normal runner/environment-admission selection passes 53 cases with one
  platform skip and 19 explicit exhaustive exclusions, including fresh/cached
  explicit and ordinary install paths. Six additional real-uv rebuild and
  junction-safety cases pass across all existing rebuild/alias modes.
  The actual repaired
  `--reinstall` rebuilds the first-party dependency wheels, including
  `agent_procutil`; the complete native GUI-probe module passes one case with
  its POSIX-only companion skipped. All two-cycle window/focus, cleanup and
  25/30/300-second assertions remain unchanged.
- This restores isolated test tooling, not an installed plugin/runtime or a
  production daemon. The GUI fixture keeps captured child diagnostics. The
  native group-12 aggregate still hits its unchanged 300-second boundary after
  the dependency fix, so its cause is not attributed to that cache failure or
  accepted as a flake. The 53 still-unrun files in disjoint native groups 14-16
  pass 1,320 cases with one skip under the original 25-file/300-second bounds.
  This is not a complete native pass because group 12 remains unproven.
  Normal containing Linux validation remains separate evidence.

### 2026-10-15 - Real release proves invocation cutover, not generated-tree health

- Normal full `38664339697` validates exact dev
  `4f7b62facbe44df61f159dcde9b1afb11d34b3ea` and genuinely succeeds,
  including build `116202232786`, attestation `116202731176`, promotion
  `116202875668` and artifact publication `116203558540`. No early no-op,
  skipped publication, manual retry or waiver is substituted for that result.
- #6789 merges candidate `614c453af269f2a13e80a8cd1e823f6a755eabd8`
  as `c33ccea3da5effc6f21b9e8c2ee726f8e4a85902` at `01:49:48Z`.
  Annotated tag `promote-20261015015004-c33ccea3` resolves to that exact merge;
  the merged release state names the validated dev input and prior main.
- The complete candidate-head CI query reports `total_count: 0`; the complete
  four-run head roster contains no CI invocation. Trusted host `38667722135`
  preserves the successful required `main source gate`. Its real configured
  scanner publishes `116203522264`: 763 changed files on that exact candidate,
  `DENYLIST_CONFIGURED=true`, `STALE=false`, successful scan, no infrastructure
  fallback. The published CI declaration excludes main-base PRs while keeping
  main/dev push, manual and edited-retarget triggers; the old CI gate is absent.
- Natural main-push CI `38667750280` runs on the actual merged commit, proving
  that invocation was retained. It fails, so do not claim generated-tree health
  or remove it. Read-only evidence isolates three families: unintended standalone
  vendor-test collection plus conflicting canonical/vendored dependency URLs;
  installer fixtures recopying an already embedded dependency; and a release-label
  rewrite escaping frozen authority normalization. Genuine coverage, frozen
  evidence and source identity must be preserved by any correction.
- The live main rule now reports strict freshness true, unlike the earlier
  snapshot. This session made no ruleset or credential change. The retained
  #4876/#6697 publisher was notified; its separate future explicit-dispatch merge
  contract is not retroactive evidence for this observed natural push.
- #6788 independently merges the reviewed explicit-reinstall/diagnostic repair
  as `c3e7dac9c215156c95b2533a44444103090e8213` at `02:18:24Z`.
  Exact head `41cb01c6a387` receives native approval with zero findings;
  all required same-head suites and the fresh source verifier pass. Its optional
  trusted self-hosted suite is skipped, not a missing required check or CI bypass.
- Reread the canonical Plan and Validation Plan after that merge. Close only
  the main-PR invocation-cutover item. The test-tooling repair still needs its
  containing release; generated-main validation, exact-input implementation,
  coverage/repair ownership, comparable measurements and observation remain
  open. Independent control activation stays dormant.
- The complete prior #6642 head and new #6789 head queries show CI PR
  invocations 1 -> 0, head-associated workflow runs 5 -> 4, and all check
  records 29 -> 9: 20 fewer records for this event pair. The old set includes
  20 skipped records and one optional CI failure; these are not 20 deleted
  test suites or proof of quota improvement. Both check pages return their
  full reported totals. Changed-file volume differs (322 versus 763), and
  this one-pair observation does not replace broader comparable-cycle metrics
  or include completion workflows indexed under another head.
- Closed only release-proof issues #6699 and #6740 after verifying their
  fixes are ancestors of the released dev input and actual main-push
  customizing-copilot job `116203739428` succeeds. Separate installer-copy
  failures and #6729 remain open; no local runtime adoption was performed.
- The authority normalizer hashes its own source. Fixing its release-label
  normalization therefore changes current authority identity, not merely a
  fixture checksum. Keep frozen v1 bytes unchanged while the owner selects
  the current-generation capture and historical-support policy; no authority
  compatibility change or checksum rewrite has been made.

### 2026-10-15 - Owner selects a new corpus revision; payload fixture is repaired

- The operator selects a new independently captured current authority-corpus
  generation, retaining v1 bytes as historical-producer evidence until
  owner-reviewed retirement. No runtime acceptance of historical hashes,
  protocol-generation, credential, service or publisher change is authorized
  by that choice. The authority implementation/capture remains a separate
  stretch; no old checksum has been rewritten.
- Reserved the Budget Guidance payload fixture and standalone preparation/
  Manager collection seams under #6237. The existing #6598 contributor already
  touches the Codespaces engine-adoption test; requested that worker's custody
  rather than pre-patching or superseding its still-open contribution.
- Five new cross-platform fixture guards reproduce four failures against the
  old unconditional copy, then pass with shared-copy-on-absence and explicit
  embedded manifest/package integrity checks. Embedded bytes are preserved
  even when shared bytes differ or the shared tree is absent; malformed
  embedded data cannot be silently repaired. Source trees remain unchanged.
- Full native Budget Guidance validation also exposed an oversized automatic
  pytest node ID on Windows before the response-rejection test could execute.
  Added only short explicit IDs, preserving the 65,537-byte input, 65,536-byte
  rejection boundary and redaction assertions; no response limit was lowered.
- The complete contained native suite passes 366 cases with 40 platform skips;
  the actual guard selector executes and passes all five new cases. Existing
  POSIX installer cases remain skipped on Windows, so hosted Linux and actual
  containing materialized-main evidence remain mandatory. Ruff, docs and
  whitespace checks pass. No production installer behavior or deadline changes.

### 2026-10-16 - Resume with the retained release owner and residual Budget scope

- Resumed after the newer operator-authorized release-control and standalone
  ownership decisions. The single control writer retains Main pause,
  qualification, manifest activation and verified resume; this session makes
  no unpause, rollback, workflow or runtime change. Standalone preparation and
  Manager repairs already have their separately authorized owners.
- #6801's original copy-helper correction is partially superseded by merged
  #7008. The operator explicitly selects a residual contribution rather than
  restoring the obsolete helper or closing the remaining safeguards.
  Rebase retains #7008's real materializer and both existing stamp-layout tests;
  only embedded manifest/package integrity checks are added to that helper.
- The five new guards now supply valid canonical or embedded source declarations
  to the actual materializer, preserving embedded bytes and source immutability
  without mocking composition. Their complete module passes five cases.
  Directly related bootstrap, build-attempt, fixture and response modules pass
  77 cases with six platform skips.
- Fresh broader native validation is not claimed green: ordinary cold Python
  fixture startup can exhaust the existing half-second reader budget before
  parse/exit classification, and the native failed-install fixture reaches its
  existing 30-second item bound inside a 45-second child call.
- Reader snippets require only the standard library, so their fixture now uses
  isolated no-site Python, with a real-child guard checking both flags. The real
  reader and cleanup/timeout/byte-limit assertions remain unchanged; the complete
  reader, boundary and CLI selection passes 70 cases with 23 platform skips.
  The independent native installer fixture diagnosis remains open; no deadline
  increase, accepted-flake claim or failed-gate waiver.
- Bridge #6992 has since merged under the retained source owner's authorization.
  Its current-fixture re-attestation must be reconciled with this effort's
  earlier immutable-v1/historical-producer decision; this session does not
  duplicate the implementation or declare that unresolved policy item complete.
- The native failed-install fixture now executes from a standalone payload using
  the real dependency, file-pointer and installer-engine materializers. Nothing
  is pruned or ignored. Both fresh/retained failure and retry cases pass under
  the unchanged 30-second item and 45-second child limits, preserving old-slot,
  partial-slot cleanup and controlled dependency-exit assertions.
- Broader native validation then reaches a distinct production total-deadline
  defect: completed quota values can escape the outer timeout while nested
  async waits race cancellation. The operator explicitly authorizes a narrow
  correction beyond the original test-only residual scope; #7065's independent
  foreground extension work remains untouched.
- Verified-session acquisition now uses one absolute monotonic deadline,
  checks phase completion and final delivery against it, and passes remaining
  budget into identity/quota reads. No timeout is increased and no retry or
  unverified quota fallback is introduced. Two deterministic completion-after-
  expiry regressions fail before the fix; all 91 identity/quota cases pass
  afterward. The former sub-OS-timer-resolution test now asserts remaining
  budget deterministically; separate native async cancellation cases remain.
- A later full native attempt still reaches the failed-install fixture's
  unchanged item deadline, despite its focused four-case pass. Standalone
  materialization alone is therefore not claimed to resolve that timing cause.
  Bounded fixture diagnostics continue before publication or merge; no green
  full-suite result, deadline increase or flake acceptance is inferred.
- The expanded deterministic expiry matrix covers first identity, quota and
  final identity completion exactly at and after the limit; all 95 identity/
  quota cases pass. Temporary native trace timers and helper instrumentation
  are withdrawn after bounded diagnosis establishes no safe further fixture
  fix. The captured construction/venv/seasoning evidence is shared with the
  retained #6928 owner; it is not a new watchdog implementation or a waiver.

### 2026-10-18 - Bound native fixture work without extending deadlines

- The retained installer fixture removes no-op file-pointer and engine scans:
  this payload declares neither reference. Its real dependency materializer,
  embedded integrity assertions and both existing stamp-layout cases remain.
- Copy only the payload inputs, excluding the fixture's own top-level tests
  and runtime caches. At most sixteen workers copy files, with every result
  awaited and errors propagated. New guards preserve script/package/dependency
  bytes and source immutability in both layouts, including dependency test data.
- The stdlib-only fake UV driver uses isolated no-site Python; native children
  receive explicit executable/module paths and disabled PowerShell telemetry.
  The real installer, controlled dependency failure, clean retry and old-slot
  assertions remain. No production installer or shared construction code changes.
- The affected installer/fixture modules pass 17 cases with seven platform
  skips in 42.42 seconds. The complete native source suite then passes 431 cases
  with 45 platform/opt-in skips in 69.69 seconds. Item, child and group limits
  remain thirty, forty-five and three hundred seconds respectively.
- These observations close the previously failing local source run, not a
  diagnosis of every historical timeout. Current-head hosted Linux and actual
  materialized consumer qualification, review and required checks remain gates;
  additional runner capacity is not a test-result waiver.
- The real single-plugin release preview at committed source `7540445a2bdb`
  also passes all 431 native cases with 45 skips in 66.47 seconds. Its routing
  integration uses the real sibling guidance payload, not a resolver mock.
  This is a focused materialized consumer result, not full release qualification:
  whole-tree candidate construction separately exceeded its unchanged
  120-second internal builder limit before producing a receipt. No partial tree
  was accepted as a candidate and no builder deadline was increased.

### 2026-10-18 - Generated candidate repair preserves independent control

- The release campaign's latest source repair distinguishes recorded Dev
  workflow contracts from the older preserved Main controller; 139 selected
  cells pass without modifying the actual generated controller or its limits.
  Canonical and real materialized Dispatch/Codespaces adoption modules retain
  all original cells and missing-input negatives.
- A reproduced Bridge HTTP/2 read-lock starvation defect receives an explicitly
  authorized narrow transport repair (#7186), not a timeout or test waiver.
  The full native suite passes 6,444 cases with 60 platform/opt-in skips in
  341.274 seconds, including real PowerShell coverage. Candidate-only two-worker
  admission retains the existing shared containment and eight-minute Main bound.
- Main still needs a separately reviewed minimal CI worker-environment change
  and matching source-pinned qualification inventory after this repair lands.
  No Dev controller is copied wholesale, no
  existing twelve-minute Dev limit is activated on Main, and the preservation
  manifest, pause and helper pin are unchanged by this source slice.
- Required review/checks, generated/native qualification, release and resume
  remain open. Maintainer merge/rebase stewardship remains locally inert and
  disabled; this validation repair neither configures authority nor enables it.
