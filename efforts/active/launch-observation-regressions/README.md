# Launch Observation Regressions

## Goal

"We need to undo whatever change is causing us to show \"MUX(?)\" in
worktree-manager. It really feels like the `has-session` bug may be
self-inflicted. We need to sweep how we interface with PSMux to ensure this
isn't a \"self-own\", and restore the correct mux status reporting"

Also investigate the reported `embody` regression and New's required-application
observation failure without bypassing admission or affecting existing sessions.

- **Slug:** `launch-observation-regressions`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed source slices targeting `dev`
- **Created:** 2026-11-09
- **Status:** Active
- **Vision:** [Picker](../../../visions/picker/README.md);
  [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #11722
- **Related owners:** #11564 finite mux observation; #11718 legacy PID-sentinel compatibility

## Guiding Intent

Audit this suite's own behavior before attributing failures to PSMux. Restore
actual observations, not optimistic badges. Keep New's required readiness,
selected-runtime compatibility and finite admission budget intact.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source maintainer | Audit, bounded correction, validation and PR stewardship | This effort's source worktree and reviewed PRs |

## Coordination

- **Topology:** One owner, sequential reviewed source slices.
- **Host (owns PRs):** Source maintainer.
- **Delegates:** None.
- **Handoff:** This canonical effort and its remaining acceptance items.
- Do not duplicate #11718's independently owned legacy registration correction.

## Context

- #9155 changed architecture documentation, not executable `embody` behavior.
- An installed Worktrees generation explicitly returns no Windows listing;
  its display adapter therefore reports unknown. #11564 already replaces that
  producer with finite native observation. A merged fix is not installed proof.
- The selected Machines runtime declares required read-only observation support,
  and profile selection succeeds. Discovery alone exceeded a bounded 50-second
  diagnostic; a separate bounded stack capture located full-payload hashing in
  `installation_repair_admission.payload_identity`. Application checks had not run.
- The New caller currently conflates timeouts, selection failures and other
  observation errors into an unavailable-or-unsupported message.

## Request

The Goal preserves the mux request verbatim. The other requests were to
investigate `embody` and this New failure:

> New authoring requires application readiness before allocation. Required
> application observation is unavailable or unsupported by the selected runtime.

Private invocation paths and an unrelated quoted task seed are omitted. The
seed is diagnostic input, not authorization for the action it describes.

## Scope Approvals

Source diagnosis, bounded isolated regression tests and ordinary reviewed
publication are in scope. Existing operational holds remain: no shared-host
PSMux probes, live embodiment/launch, update/install, restart, teardown,
registration reconstruction, service changes, access changes or release of
retained resources. Source completion does not claim installed recovery.

## Plan

### Phase 1 - Review the correction
- [x] Land this proposal through the normal review and CI gate (#11725).

### Phase 2 - Audit and repair
- [x] Audit mux callers' targets, arguments, environment/CWD, observation provenance, budgets and cleanup; identify installed-versus-source differences and preserve independent owners (#11740).
- [x] Remove redundant readiness-discovery filesystem work without weakening full-plan payload identity, linked-path refusal, selected-owner qualification or the existing admission deadline (readiness-only implementation).
- [x] Make readiness failures identify their stage and timeout versus unsupported selection without exposing raw output or changing refusal semantics (#11740).

### Phase 3 - Read-only New observation
- [x] Review a readiness-only observation scope before implementing it: bind enabled-owner declarations and the actual readiness invocation without constructing an installer plan (#11740).
- [x] Keep full payload identity and executable/installer qualification for ordinary repair planning/apply; a readiness-only digest must never authorize apply (including an empty selection).
- [x] Prove the New observer finishes within the original aggregate budget against a representative fixture roster; reject changed declarations, unsupported selected runtimes and incomplete readiness.

### Phase 4 - Deliver and close
- [ ] Land the bounded correction after passing review and required CI.
- [ ] Record source results and any separately owned operational acceptance; archive this effort only after every item is resolved or transferred to a named tracker.

### Phase 5 - Reopen without replaying an uncertain prompt
- [x] Let ordinary Open preserve an unconfirmed prompt handoff without submitting it again; retain explicit-delivery refusal and final intent revalidation.
- [x] Validate both planning surfaces and the real admission-only preparation boundary, then land through review and required CI (#11809).

## Validation Plan

- [x] Prove the full-plan payload digest remains content-sensitive and deterministic, and linked/reparse/nonregular/oversized inputs still refuse.
- [x] Prove the readiness-only caller performs no payload walk or irrelevant application-owner qualification; use real native fixture observations within the original budget.
- [x] Exercise the actual readiness caller boundary with synthetic, isolated owners: supported success, timeout and unsupported selection all preserve admission.
- [x] Verify mux consumer truthfulness and caller contracts with focused existing tests, without shared-host probes or pretending a source test proves rollout.
- [ ] Record unit, simple integration, clean-room and available external/native tiers, with explicit skipped-tier reasons; preserve unrelated sessions and resources.

## Proposal

Retain the original per-leaf ancestor checks and full payload hashing for
installer plans, including refusal of directory replacement during enumeration.
Do not solve latency by increasing the admission timeout, disabling required
readiness, adding a resident cache or trusting a different runtime.

New's explicitly read-only observation binds owner/roster declarations and the
exact readiness action and executable. It must not construct an installer plan
or recursively hash unrelated files. The scopes must be non-interchangeable,
including empty selections; a readiness-only digest cannot authorize apply.
Known non-application capabilities need no application-owner qualification, but
declared runtime claims, selected prerequisites, malformed declarations and
unknown enabled payloads retain their admission boundaries.

This closes existing responsiveness and truthful-observation intent rather
than extending a vision. The service-contract and performance-measurement
patterns govern the implementation. Readiness observation retains the existing
required-observation contract. Promptless reopening uses the opt-in,
versioned retained-seed contract documented by the Engine/Picker boundary.
Runtime replacement and rollout remain with their existing owners.

## Journal

### 2026-11-10 - Promptless Open landed; readiness delivery continues
- #11809 merged as `8108fff27a` after approval at the final source and green
  required CI. Exact-source Windows qualification passed 22 Worktrees cases
  and 13 Manager cases with zero skips or failures, including stopped-to-live
  reattachment, early restore refusal, identity preservation across delegation,
  both native interpreter patches and real older-parser pre-effect refusal.
- Phase 5 is complete in source. Neither that merge nor isolated qualification
  proves installed recovery. Readiness delivery in #11782 remains the next
  stretch, followed by release and separately authorized operational acceptance.
- Rebased that source onto the actual changed contribution base and preserved
  both source slices' journal entries. Fresh exact-source qualification, review
  and required CI remain; no check-recognition workaround or bypass is inferred.

### 2026-11-09 - Ordinary Open and retained prompt custody
- A subsequent report shows ordinary Open refusing because a prior creation
  prompt handoff is unconfirmed. Metadata confirms retained typed intent;
  no prompt content, liveness probe, live launch or replay is needed to diagnose
  the planner's unconditional refusal.
- Reuse the existing admission-only path with the retained seed identity.
  Opening must not claim, finish, restore, discard or resubmit that prompt.
  Explicit delivery remains refused and a changed identity before execution
  remains blocking. This is a targeted routing correction, not new launch syntax,
  wire generation, receipt authority or replay permission.
- The readiness-only correction remains separately reviewed in #11782; its
  required-check recognition blocker does not authorize bypass or rollout.
- Both planning/preview files completed 140 assertions with one platform skip.
  The local Windows ARM64 wrapper then failed deleting a copied x64 fixture
  interpreter (WinError 5), matching the failure class tracked in #7643. This
  is not a green whole-run result or an identification of the current lock
  owner. Preserve the runner's retained artifacts and obtain disposable native
  Windows qualification; do not alter host services to clear the mapping.
- Ruff F/E9 and the install contract pass. No installation or first-provision
  behavior changed; fresh native caller fixtures cover the existing admission
  boundary rather than a new installer clean-room scenario.
- Disposable Windows exercised the published planner source: 140 passed and
  the existing POSIX-only case skipped, with clean native cleanup. The strict
  qualification packet therefore remains incomplete, not qualified.
- Local shell delegation re-resolves a plan, so preserving intent only inside
  its first command was insufficient: a replacement could become a new delivery.
  Carry the retained identity through both resolves and final preparation.
  Qualify preserved plans through an optional caller hint so older Managers keep
  the safe refusal, and refuse unsupported explicit selectors before effects.
- Both launchers must skip legacy prompt delivery for a preserved plan and
  refresh recognized admission-only module commands after runtime selection,
  just as they already refresh prompt-delivery commands.
- The corrected Worktrees packet passed 17 focused cases with clean containment
  and cleanup. The Manager packet passed 179 cases but exposed five failures
  where adding an environment hint changed selected-engine resolution.
  Pinning the already-selected command fixes that class; all 116 engine-client
  cases then passed with clean cleanup, including an explicit wrong-engine
  refusal control. Final native qualification of the complete corrected source
  and versioned preservation declaration remains pending.
- Preserve the identity across a stopped-to-live transition without applying
  delivery-only cold-start restrictions to promptless reattachment. A
  preservation selector combined with `--restore` must refuse before any
  bound-process recovery, not after that effect.

### 2026-11-09 - Caller corrections landed; readiness slice
- #11740 merged after a current-head owner-policy passing Comment, resolved
  findings and green required CI. Only a minor Proposal/Journal editorial item
  remained; its invariant is now stated in Proposal and its history stays here.
- Real Windows warm-worker qualification passed six cases; Linux passed four
  plus two Windows-only skips. The stale active-pane residual controls were
  repaired and qualified separately, without calling the original enclosing
  failed workflow green.
- Full-plan traversal was restored unchanged and seven identity controls pass.
  Continue the reviewed readiness-only scope, retaining complete inventory,
  qualified selected owners and the original aggregate deadline.
- The new scope also exposes two portable adapter defects: generic command
  plugins were incorrectly restricted to an `agent-` prefix, and an unselected
  optional owner could add a required readiness protocol. Both have focused
  canonical-generator/closure controls; unknown enabled payloads still refuse.
- The final focused caller/repair packet passed 24 cases, including core and
  generic actual callers, default repair and nested timeout cleanup. The separate
  twelve-application/128-capability native case passed in 41.68 seconds overall
  under the original 55+5 caller budget; this is one functional sample, not a
  percentile or installed-health claim. Grammar-only library controls passed
  45 with one platform skip and three unchanged wrapper controls deselected.
- Strict library checking passed all ten physical files for both Linux and
  Windows targets. Fresh external qualification and reviewed delivery remain.
- Exact-source disposable Windows qualification passed all 25 selected cases,
  including both actual callers and the original-budget roster case, at
  `17e38f0c`. Review then identified original-path information lost by resolution.
  The shared path resolver now rejects linked declaration/action components
  before resolution, without restoring a payload walk.
- Six direct readiness-only symlink/reparse controls and the budget case passed
  together; the grammar packet still passed 45 with its documented skip and
  deselections. Both strict platform targets passed again. Refresh exact-source
  qualification after this guard correction before delivery.

### 2026-11-09 - Native scope review corrections
- Review identified creation-namespace drift and a scrubbed teammate flag that
  still participated in the worker hash. Native reads now receive the exact
  creation namespace without changing shared environment; both context writers
  treat the flag as transport state.
- The private Windows lifetime fixture now uses a deliberately different
  ambient observer namespace and a parent teammate flag. It must confirm the
  actual private native incarnation before publishing readiness.
- The original 89-test hosted Windows result covers the first source slice;
  changed namespace and worker-scope behavior needs fresh qualification.
- The revised private native lifetime packet passed all six Windows cases
  and four Linux cases (two Windows-only cases skipped on Linux). The enclosing
  workflow then failed in an old active-pane control that still expected a
  PSMux CLI probe. That control is being corrected to assert the native caller
  contract while retaining the TMux syntax check; the workflow is not called green.
- A controlled directory-replacement finding invalidated the attempted
  ancestor-walk optimization. It did not meet the launch budget anyway, so
  restore the original full-plan traversal instead of adding handle/attestation
  machinery. Add deterministic symlink/reparse replacement controls. Phase 3's
  readiness-only scope remains the proposed latency correction.

### 2026-11-09 - First source slice and remaining budget failure
- Initially removed repeated per-file ancestor walks; payload bytes and digest remain
  unchanged. Existing repair-boundary selection passed 120 tests, with 17
  platform/tier skips.
- New diagnostics and the legacy updater controls passed 87 tests, with six
  platform/tier skips. Warm Picker and badge assertions passed 89 tests, but
  local teardown failed on a synthetic launcher executable; a one-case control
  repeated that teardown failure. This is not a clean local suite pass.
- The real discovery-only candidate still exceeded its 55-second execution
  allowance. A 30-second per-owner control found the first payload hash still
  opening files; no application readiness script was invoked. Phase 3 is the
  remaining correction, not a reason to enlarge the deadline.
- Compared #11710's sole production change with its parent: the added
  post-lock expiry check handles four send-only mutation notices, not the
  pre-allocation application observer. It does not explain the captured New
  failure; no conclusion is claimed about a separately unobserved launch error.

### 2026-11-09 - Proposal accepted
- #11725 merged after an approving Copilot review and green required CI.
- Continue Phase 2 in the same source worktree; no operational hold changed.
- The caller sweep also found legacy status-updater fallback and warm Picker
  observation paths that still execute PSMux CLI reads. Confirm their actual
  consumers and add focused source controls rather than treating nominal reads
  as harmless or duplicating #11718's inventory work.

### 2026-11-09 - Diagnosis and proposal
- Separated intentional installed mux-listing suppression from a PSMux failure.
- Isolated profile selection and selected runtime support successfully.
- Discovery-only timeout and stack capture identify the next correction stage;
  no application checks, live mux probes or installation effects were performed.
