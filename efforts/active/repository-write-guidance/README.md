# Repository Write Guidance

- **Slug:** `repository-write-guidance`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed plan, implementation and completion PRs
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [Cooperative write guards](../../../visions/plugins/agent-worktrees/README.md#write-guards-are-hygiene-nudges-named-for-their-plain-mundane-reason)
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

The [circumstantial hook contract](../../../docs/patterns/circumstantial-hook-contracts.md)
separates reusable facts from event-time decisions. The
[prepared guard map](../../../plugins/agent-worktrees/docs/guarded-anchor-map.md)
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
- [ ] Complete documentation, contract and performance-impact assessments;
  land the implementation after review and required CI.
- [ ] Observe release and any authorized installed adoption needed for the
  acceptance claim; retain uncompleted deployment gates explicitly.
- [ ] Resolve the coordination issue only after its complete acceptance gate,
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
