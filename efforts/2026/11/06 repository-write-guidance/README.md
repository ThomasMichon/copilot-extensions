# Repository Write Guidance

- **Slug:** `repository-write-guidance`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed plan, implementation and completion PRs
- **Created:** 2026-11-05
- **Status:** Done
- **Vision:** [Cooperative write guards](../../../../visions/plugins/agent-worktrees/README.md#write-guards-are-hygiene-nudges-named-for-their-plain-mundane-reason)
- **Umbrella issue:** #10778
- **Sub-issues:** none

## Guiding Intent

Deliver repository rules of engagement at the first relevant write attempt:
read-only access, an isolated worktree, required elevation, or the repository's
own agent. Reject that attempt with actionable guidance and the existing
time-bounded break-glass route. This is a cooperative workflow nudge, not
comprehensive interception of arbitrary process writes or adversarial security.
Globally installed Worktrees must remain inert for unmanaged sessions.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driver | Plan, source changes, validation, PR stewardship and completion | Externally controlled isolated source worktree |

## Coordination

- **Topology:** sequential PRs owned by one driver.
- **Host (owns PRs):** Driver.
- **Delegates:** none.
- **Handoff:** the canonical README retains the current slice and remaining gates.
- Existing managed-session, guard-map, session-projection, installer and
  performance owners remain authoritative; this effort does not replace them.

## Context

The current native command hook supplies `toolCalls[]`; `apply_patch` arguments
are a freeform patch rather than a JSON object with a path. The command-policy
entry and write-target classifiers miss these common forms. A real managed
denial control therefore failed despite positive admission and a complete
native-produced guarded-root map. An older path-shaped event still denied.

The [circumstantial hook contract](../../../../docs/patterns/circumstantial-hook-contracts.md)
separates reusable facts from event-time decisions. The
[prepared guard map](../../../../plugins/agent-worktrees/docs/guarded-anchor-map.md)
already owns caller-scoped topology acquisition outside the hot path.
The native session sidecar represents binding and control separately; its
mere existence is not necessarily current-workspace membership.

## Request

The operator requested: "Correct. Drive an effort to build this out to merge and resolution".
The complete premise and subsequent session-marker suggestion are preserved
verbatim in [inception-transcript.md](inception-transcript.md).

## Scope Approvals

The quoted request authorizes this generic source campaign and its normal
review/merge flow. It does not authorize unrelated source changes, elevation,
new access, destructive production actions, fleet deployment, or guard
disablement. Any installed rollout uses its deployment owner's independently
applicable authorization. Disposable tests retain exact ownership and cleanup.
The previous failed live control remains failed; no replay or normalized input
will be relabeled as native acceptance.

The operator separately authorized: "Authorize the canonical user-scoped suite
update on this machine, then run and clean up the live A/B test". This covers
the current-user installation, disposable local repositories and owned test
agents, not fleet changes, new access, elevation or unrelated changes. Real
repository registrations remain intact; fixture adoption uses a private registry.

## Plan

### Phase 1 - Reviewed contract
- [x] Capture the premise and marker suggestion; reconcile to the existing vision.
- [x] Land this proposal through the repository's required review and CI gates.

### Phase 2 - Common native write forms
- [x] Normalize legacy single-call and current batched command-hook inputs in
  the existing policy owner, preserving one composed deadline and all decisions.
- [x] Extract native freeform patch targets, including add/update/delete/move,
  and common structured edit targets through the existing guard family.
- [x] Consume prepared caller-scoped repository posture facts for targeted
  worktree/read-only/elevation/delegation reminders; reuse existing policy owners.
- [x] Use bounded root matching only on plausible write arguments as a fallback
  where necessary; preserve read and quoted-data behavior.
- [x] Retain first-attempt rejection, actionable workaround and existing live
  scoped break-glass expiry/revocation, without caching a tool permission.

### Phase 3 - Cheap admission and actual consumers
- [x] Reuse the native session association as the cheapest available admission
  fast path when it establishes the current session/workspace binding.
  Controller-only, unrelated, stale or incomplete markers must not activate
  management. Preserve the passive owner fallback for compatible legacy starts.
- [x] Keep admission, command policy and resident policy aligned without new
  per-event topology discovery, subprocess-per-repository work or daemons.
- [x] Prove actual native managed denial, unmanaged allowance, and scoped
  break-glass behavior against disposable repositories; qualify the exercised
  driver/hook lane rather than trusting flags or descriptors alone.

### Phase 4 - Merge and resolution
- [x] Complete documentation, contract and performance-impact assessments;
  land the implementation after review and required CI.
- [x] Observe release and any authorized installed adoption needed for the
  acceptance claim; retain uncompleted deployment gates explicitly.
- [x] Deferred to `#10778`: Resolve the coordination issue only after its complete acceptance gate,
  journal outcomes, mark Done, land the archive, and finalize owned worktrees.

## Validation Plan

These implementation choices and checks are **agent-recommended** realizations
of the captured operator intent, not additional operator grants.

- [x] Focused regressions for legacy/batched hooks, mixed read/write batches,
  all native patch target directives, common edits and boundary-safe matching.
- [x] Reads and quoted path data remain allowed; malformed/unknown input retains
  the existing actionable fail-open contract without inventing evaluation.
- [x] Admission covers no marker, controller-only marker, matching bound
  workspace, changed CWD, stale/revoked inputs and registry-only repositories.
- [x] First write rejects with the correct posture and recovery; scoped
  break-glass permits the intended retry, not another repo, and expires/revokes.
- [x] Unit/lint/install-contract gates pass against the final source; preserve
  the required unit-only CI portfolio and explicit native integration lanes.
- [x] An isolated subprocess check exercises the shipped command-hook entry,
  not just a pure classifier; a fresh fixture exercises first-touch behavior.
- [x] A live native A/B comparison proves real tool outcomes and actual
  hook/driver participation; preserve failed attempts and exact cleanup.
- [x] Measure cold/warm composed admission and policy under a representative
  root inventory against the existing two-second hook target. Report sample
  counts, wall time, added work and limitations; do not claim tail qualification
  from a small sample. Broader telemetry ownership remains #7935.
- [x] Record an explicit reason for every unavailable platform/external tier;
  do not turn missing evidence into a pass or authorize an alternative venue.

## Proposal

Approved in #10779. This campaign closes the existing cooperative-guard vision
gap, with no new sandbox, process interception, service, or security guarantee.

## Journal

### 2026-11-05 - Kickoff
- The operator confirmed guidance-first behavior and requested delivery through
  merge and resolution. The native marker fast-path suggestion was captured.
- Claimed #10778 after scoped issue/PR overlap searches. Implementation waits
  for the reviewed plan; prior failed native controls remain diagnostic input.

### 2026-11-05 - Reviewed plan landed
- #10779 merged after Copilot approval on the published head and green
  required CI. The worktree was pulled forward onto the merged proposal.
- Implementation begins with common native write forms and the existing
  policy owner; admission remains a reusable workspace fact, not permission.

### 2026-11-05 - Native forms and consumer validation
- Implemented batched/freeform target handling, supplemental prepared postures,
  live per-target grants and a scoped positive session-folder association.
  An unavailable supplemental repository no longer invalidates unrelated
  confirmed delegated roots. Managed project-mode writes refresh prepared views.
- Updated operational docs and deployment reconciliation. Install contract
  passed for all 13 runtime plugins; documentation consistency and touched-Python
  lint passed. Focused source regressions and shipped Python/PowerShell entry
  controls cover admission, native batches, grant scope, expiry and revocation.
- Retained an intermittent native PowerShell no-denial result during an
  expanded run, followed by passing focused controls. The host was at full CPU
  utilization; that is context, not proof of cause or permission to waive the
  failure. Larger combined groups also exhausted their runner budget. Native
  qualification remains open.
- Seven-process controls used 100 prepared rows. Recorded Python entry samples
  ranged from 0.73 to 2.29 seconds and PowerShell from 3.62 to 8.04 seconds in
  the later passing run. These include startup and mixed decisions, not a
  qualified latency tail; the two-second target is not established by them.
- Live startup experiments established that native `--trust-folder` rejects
  both ACP mode and an explicitly supplied session ID. Trusted prompt mode
  reached a real edit, but write-tool approval alone did not authorize SDK
  extensions: native logs explicitly reported denied extension access. That
  run is not driver-participation acceptance. A corrected disposable control
  remains required; no global trust, service repair or production registration
  change follows from these findings.
- Corrected native prompt controls subsequently confirmed an actual healthy
  Remote Driver and loaded Bridge extension. The unmanaged caller performed
  one patch successfully; after isolated project adoption, the same write was
  denied by the pre-tool hook with delegation and break-glass guidance, leaving
  the target unchanged. A repository-scoped grant then allowed the real patch.
  Cross-repository scope and revocation controls continue separately.
- The native grant exercise exposed a coupled writer/reader discrepancy:
  the existing grant writer used the process home while the guard used the
  selected registry. Routed the writer through the existing registry-path
  owner, preserving normal legacy defaults and refusing invalid explicit
  contexts before effects. The focused grant/form packet passed 11 tests;
  its real writer-to-guard control proves selected-root agreement.
- All 88 resident-policy controls passed in an isolated file run. Added two
  actor-free native-form regressions to the existing required unit cohort and
  declared its helper/import/coverage closure; all 100 cohort tests and the
  complete manifest source-binding check passed.
- All five real native controls passed: unmanaged allowance, managed delegation
  denial, allowed scoped retry, independent read-only denial while that grant
  was active, and restored delegation denial after revocation. Native event
  records confirm one actual `apply_patch` per control, pre-tool hook execution,
  matching tool success/failure and expected target bytes. Scoped native markers
  were absent for the unmanaged control and present for managed controls.
- The complete Windows-native unit packet passed 4,147 tests with coverage
  in 250.790 seconds against implementation commit `f68fa67b75`. This local
  profile is not the separately required Linux CI result.
- Fresh disposable first-touch and real native consumer lanes were exercised.
  A fresh-machine/container build and non-Windows/external-host lanes were not:
  the authorized live scope is the current host and its owned disposable
  repositories, not allocation or repair elsewhere. Installer replacement,
  release inclusion and installed adoption remain independent gates.

### 2026-11-05 - Review corrections
- Required unit CI passed, but the source guard rejected the installer at
  1,710 lines against its 1,708-line ceiling. Extracted the new registry-write
  completion into the existing projects-registry owner without changing the
  baseline or behavior. Its 22 focused tests and native fixture refresh passed.
- The first implementation review identified five actual gaps: a weaker
  target mode could hide a denial; a healthy older monitor could suppress
  native-batch fallback; a supplemental subdirectory could poison the map;
  three installation inventories omitted the shared helper; and two grant
  readers still used the legacy home.
- Corrected priority across every target/segment with one identity cache,
  evaluated batches through the current command owner before legacy transport,
  rejected mismatched supplemental roots before publication, completed all
  four installation paths, and aligned every guard's selected grant store.
- Fourteen initial review controls passed, including fresh installed-bin
  imports through all four actual publication paths. A new composed test
  initially disabled the shared master switch by mistake; correcting it to
  disable only cross-repo evaluation yielded 244 passing guard tests.
- The earlier five native controls and unit packet retain their original
  source-generation qualification. Fresh published-head CI/review and
  impact-selected native revalidation remain required before completion.

### 2026-11-05 - Mixed-version and supplemental-posture corrections
- The next review found a single-call form gap in legacy-monitor routing,
  shell false positives for nested linked worktrees and maintenance pulls,
  and tolerant project acquisition that could publish incorrect postures.
- Routed single-call native patches and multi-edits through the current local
  evaluator, reused the anchor owner's shell classifier for worktree-only
  postures, resolved literal shell destinations before anchor attribution,
  and made failed project acquisition a partial supplemental inventory.
- The focused composed-policy, guard and producer packet passed 388 tests
  with one platform-specific skip. Existing delegation/read-only scope stays
  stricter than worktree maintenance guidance.
- Published-source native grant/revocation controls both passed against
  `867c91f5ec`: the scoped retry changed the target, and revocation restored a
  real pre-tool denial with unchanged target bytes and healthy Driver evidence.
  This evidence predates the latest shell/mixed-version corrections; final
  release inclusion and installed full-flow acceptance remain independent.

### 2026-11-05 - Attached shell operands
- The final shell review found that whitespace tokenization missed attached
  redirects, `dd of=` destinations and PowerShell colon-valued path arguments.
  Extracted literal redirect operands with shell-aware quoting and normalized
  target-bearing arguments in the existing shell-parser owner.
- The anchor module's size guard required moving its unchanged Git tokenizer
  into that same already-shipped parser; no cap or deployment inventory changed.
  Fresh parser/guard controls passed 238 tests before the attached-operand fix.
- Attached/spaced redirect and argument controls, including nested linked
  destinations and unchanged stricter delegation, passed 266 tests.
- Rebased onto the current contribution branch, preserving the concurrent
  effort-index addition and all implementation commits.

### 2026-11-05 - Batch observation custody and mutating operands
- The next review identified a native-batch gap in PR-observation supervision
  and source/value shell operands incorrectly attributed as writes.
- Supervisor selection now inspects normalized calls and retains the complete
  batch under the existing owned worker and settlement deadline. Pending
  cleanup decisions are no longer discarded by the legacy command entry.
- Shell guidance shares actual mutating-operand selection: copy sources,
  `dd` input paths and PowerShell values remain reads, while output destinations
  and moved sources retain the applicable posture/delegation guidance.
- The focused guard/parser packet passed 297 tests. The real Windows legacy
  batch cleanup-error control passed in 25.88 seconds, preserving the earlier
  ask and proving retirement of the exact retained lookup child.

### 2026-11-05 - Option-before-path handling
- The latest review confirmed both previous fixes and identified option values
  such as `-Encoding utf8` being mistaken for the positional write path.
- The existing parser now consumes value-taking content/common parameters
  before choosing that path, retaining colon forms and non-target values.
  Regressions cover option-before-path writes and read-only values across
  worktree, reference and delegated postures.

### 2026-11-06 - Implementation merged
- Implementation #10800 merged as `c92c0fa78a6e045670cb32c9519829c7b10d6af6`
  after a current-head Copilot review with zero open findings and successful
  required CI. The option-order packet passed 309 tests and touched-Python
  lint; generated-hook, unit-manifest and module-cap checks also passed.
- Release promotion, the authorized current-user installed update and a fresh
  installed five-control native comparison remain open. The coordination
  issue and this effort remain active until those acceptance gates resolve.
- Release run `42142292967` failed its required Windows Resume consumer setup
  before executing tests. The editable dependency installation exits nonzero;
  the missing native evidence upload is secondary. Existing issue #10830 owns
  this blocker, now claimed for diagnosis without weakening or bypassing the
  gate. Development-checkout setup passes; candidate-layout reproduction is
  the next diagnostic check, not installed acceptance.
- A disposable materialized Manager/Bridge pair reproduced conflicting local
  URLs for their separate vendored `agent-update-driver` copies. The test
  runner now resolves each consumer graph in a separate retained installation
  transaction, preserving Bridge's test dependencies and failing preparation
  without a cache stamp if its supplemental install fails.
- Twenty-two focused preparation regressions and lint pass. The corrected
  runner prepared both real materialized payloads in a fresh Python 3.12
  environment; `uv pip check` confirms all 63 installed packages are compatible.
  The full native release gate and installed write-guidance acceptance remain
  open; local setup is not either of those results.
- Release prerequisite #10843 merged as
  `aea7ecc6e9d4ba862d94cca0c20efb45b8b1a6a7` after current-head Copilot approval
  and successful required CI. The worktree was pulled forward. Release
  observation now waits for a candidate containing this fix as well as #10800;
  neither the release blocker nor the parent coordination issue is resolved
  merely by merging the source correction.
- Promotion `42148874159` froze development input
  `aea7ecc6e9d4ba862d94cca0c20efb45b8b1a6a7`. Its required real Windows Resume
  job passed both cases in 28.20 seconds, with preparation, owned cleanup and
  native evidence upload also successful. Closed #10830 on this actual
  materialized-consumer result, not a local setup result. Full promotion and
  installed write-guidance acceptance remain separate open gates.
- The same run failed its SSH strict type check before source-unit execution:
  materialized vendored libraries and their tests were being claimed by the
  service's broad strict source prefix. Existing issue #10842 now owns this
  remaining prerequisite. The correction must preserve service-owned strict
  inputs and canonical library declarations, rather than suppress diagnostics
  or relaxing type checking globally.
- Separated the four strict services' materialized library directories into
  their own pending inventory. Service source/tests and canonical library
  declarations retain their admission. An all-project probe reports absent
  pending copies without aborting later scopes or certifying them.
- All 142 checker regressions pass under the repository's pinned MyPy
  environment; the first invocation used an interpreter without MyPy and was
  corrected rather than treated as a product failure. Six actor-free checker
  controls, strict checker analysis on both targets, lint and the complete
  required unit-manifest source bindings also pass.
- The real materialized SSH fixture reproduces failure with the former
  261-file scope. With complete first-party import inputs, all 35 service-owned
  files pass unchanged strict Python 3.10 checking for Linux and Windows.
  Exact-candidate promotion remains required before closing #10842.
- Typing prerequisite #10856 merged as
  `b53f38db3e72362fc3343837dc743c46fee32746` after current-head Copilot approval
  with no open findings and successful required CI. The worktree was pulled
  forward and the complete effort reread. The next gate is a new promotion
  containing this repair; installed acceptance and final resolution remain
  open.
- Promotion `42154035810` froze development input
  `b53f38db3e72362fc3343837dc743c46fee32746` into candidate
  `f26ae6fb9a57d68410fdb3d69f1dd3e54c9d9728`. All 4,162 candidate units passed;
  strict SSH checking passed all 35 service-owned files on Linux and Windows.
  Both real Windows Resume cases passed in 44.25 seconds, and artifact
  attestation and exact-candidate qualification succeeded.
- Generated release #10862 merged to Main. The independently authorized
  current-user canonical update is running for Worktrees only; fresh installed
  five-control native acceptance and owned cleanup remain open. No fleet,
  additional access or unrelated deployment is authorized by this result.
- The canonical targeted update completed successfully for Worktrees
  `1.24.493-dev1`, publishing the runtime selection and settling the healthy
  monitor transition. The imported runtime and shipped payload agree on that
  version; the native controls load installed payloads, not source overrides.
- Fresh installed controls passed all five actual native outcomes: unmanaged
  allowance without a managed marker, managed delegation denial, a permitted
  scoped retry, independent read-only denial while that grant was active, and
  restored delegation denial after revocation. Each recorded exactly one real
  `apply_patch`, successful pre-tool hook execution, a matching tool outcome,
  expected target bytes, a healthy Remote Driver and loaded Bridge evidence.
- The scoped grant is revoked, both targets are restored, and all five exact
  native processes completed and are absent. Framework-owned session histories
  remain intact. Disposable scratch cleanup and the reviewed archive are the
  remaining mechanical closeout; the broader two-second latency qualification
  remains explicitly unproved and separately owned by #7935.
- Owned fixture scratch is now removed. Cleanup found a retained host from an
  earlier failed launch after its child had exited and its Bridge record had
  disappeared. The exact host accepted the canonical termination message but
  remained alive; identity-pinned retirement of that owned process released
  the directory. No shared daemon or unrelated process was restarted or ended.
- Functional delivery is complete, including source review/CI, Main release,
  authorized installed adoption, five-control acceptance and owned fixture
  cleanup. The immediate archive-PR, native binding/worktree/claim and issue
  settlement is explicitly retained by the same Driver under open #10778.
  This transfer avoids claiming those future mechanical actions have already
  happened; it does not shorten the parent completion gate.
