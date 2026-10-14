# Main-Owned Workflow Control

- **Slug:** `main-owned-workflow-control`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees; ordinary changes target `dev`
- **Created:** 2026-10-12
- **Status:** Active; first wave live, baseline correction dev-landed, composed validation open
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
- [ ] Consolidate main-PR admission into its existing trusted reminder
  invocation: release and prove admission and generated-content identifier
  scanning first, then remove legacy CI allocation while retaining full
  main-push validation.
- [x] Coalesce or prevent redundant event-triggered runs, not merely mark all
  jobs skipped; preserve timely contributor feedback and review delivery.
  #6308/#6314 reduce trusted invocations; released #6359 prevents irrelevant
  PR ancestry runner allocation. Natural events verified both remaining jobs.
- [ ] Measure before/after run and check counts and review latency over comparable
  events; retain distinct heavy/platform/installation coverage.
- [ ] Deploy only the minimal critical control repair required to unblock the
  next safe consolidation slice, through the narrow owner-reviewed main path.

### Phase 2 - Inert preservation support where needed

- [x] Extend the existing composer with strict, exact-path preservation from
  pinned main; absent manifest retains existing behavior (#6521).
- [x] Reject unsafe/missing files, collisions and conflicting ownership; record
  control provenance and test intentional deletion (#6521).
- [x] Land implementation, tests and related documentation via dev (#6521).
- [ ] Verify a covering promotion releases inert support while no preservation
  manifest is active; release is separate from source merge.

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

- [x] Land the dev implementation of reviewed, widen-only module-size baseline
  maintenance and remove its obsolete main exception from source (#6438).
- [ ] Verify a covering promotion activates the dev-owned baseline producer on
  default main; record authorized runtime adoption separately.
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
