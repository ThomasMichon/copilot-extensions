# Launch Boundary Repairs

- **Slug:** `launch-boundary-repairs`
- **Repo:** copilot-extensions
- **Branch(es):** independent repair PRs against `dev`
- **Created:** 2026-10-29
- **Status:** Done
- **Vision:** existing plugin-services progress/timeout and explicit-failure contracts
- **Umbrella issue:** #9520
- **Sub-issues:** existing #5512 and #5761 retain their original scope

## Guiding Intent

Make confirmed launch failures observable and actionable without guessing their
cause, weakening repository admission, replaying prompts or changing an installed
service as a side effect of source work.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Repair coordinator | Source integration, validation and PR stewardship | Source-attributed repair PRs |
| Evidence reviewers | Read-only, disjoint source inspection | Coordinator-provided evidence packets |

## Coordination

- **Topology:** independent per-slice PRs; serialize changes sharing an owner.
- **Host (owns PRs):** Repair coordinator.
- **Delegates:** Evidence reviewers own no source edits or live resources.
- **Handoff:** Each source result includes exact validation and retained blockers.
- Existing #5512 owns exclusive monitor replacement and actual serving readiness;
  this effort does not replace that design with another restart protocol.
- Existing #5761 owns namespace discovery latency and result distinctions.
  Contribute through that owner rather than creating a second discovery engine.
- Open #9322 owns Windows Bridge PATH refresh; do not supersede its author.
  Reconcile any further Git executable requirement against that PR first.

## Context

#9520 records a bounded repair intake. Current source still admits a guest
repository through an already-installed Worktrees receiver before plugin
preparation; relay readiness makes SSH calls with a five-second timeout; failed
readiness observations have no retained diagnostic outcome; the shared
owned-delivery parser and detached-launch refusal consumer omit some refusal
details; monitor restart prints spawn failure while returning zero. The normal
typed/deferred/unconfirmed/lost pending-seed helper is not a repair target.

Installer activation already has launch-health gates. Those gates do not prove
that a guest preparation path validates a command it finds. Likewise, a
CodeSpace reaching `Available` does not prove its hosting-policy receiver exists.
Validate these actual consumer boundaries rather than adjacent installer checks.

Intentional seed-size limits, retired wrappers and optional browser capabilities
are not defects to remove. A readiness timeout is an incomplete observation,
not proof that a plugin is missing. Historical failures without sufficient
evidence remain unclassified.

## Request

The operator requested: "Fix it".

Scope summary: repair the confirmed infrastructure gaps discussed in the
preceding audit, validate them and steward their source contributions. The
implementation sequence below is agent-recommended. Source changes do not
authorize a shared runtime rollout, service restart or live venue retry.

## Plan

### Phase 1 - Review and ownership

- [x] Land this proposal after the repository's required review and CI.
- [x] Check current source, claims and open PRs for every repair; record already
  fixed, intentional and evidence-dependent items without speculative edits.

### Phase 2 - Explicit failure and diagnostic preservation

- [x] Make monitor restart report a real spawn failure through its exit status;
  preserve disabled/already-current behavior and existing installer cutover
  handling. Do not equate spawning with acquired ownership or serving readiness.
- [x] Preserve structured refusal details through the shared owned-delivery
  parser and detached-launch refusal consumer, without granting resend or
  replacement authority. Preserve the normal pending-seed helper's existing
  typed/deferred/unconfirmed/lost behavior.
- [x] Retain bounded, sanitized failed readiness observations through ordinary
  control cleanup, reusing the existing storage owner and retention machinery.
  Preserve control cancellation and successful cleanup semantics.

### Phase 3 - Preparation and deadline correctness

- [x] Repair fresh-guest preflight ordering through the canonical hosting-policy
  owner, preserving denial before launch effects and avoiding a second evaluator.
- [x] Verify and repair present-but-unhealthy guest preparation using the owning
  health/repair contract; do not merely add another command-existence check.
- [x] Deferred to `#8051`: compose relay/SSH progress and independent process
  budgets, including the actual preparation consumer's slow-healthy and failure
  qualification. Exact evidence and acceptance were posted on that objective.
  Namespace latency/result distinctions remain with `#5761`. No constants were
  inflated or healthy allowances shortened as a substitute.
- [x] Deferred to `#9322`: remaining Windows spawned-command PATH integration
  stays with that PR's author. Its inherited/explicit-environment contract was
  checked; no competing implementation or Git-installation bypass was added.
- [x] Deferred to `#5512`: monitor exclusivity, exit-awareness and serving
  ownership remain with the existing restart owner. The drain budget deliberately
  starts after admission closure; changing it is not an established defect.
  Client timeout still does not acknowledge daemon cancellation.

### Phase 4 - Delivery and disposition

- [x] Land each eligible source repair with mandatory changefiles, required CI
  and real current-head reviews; finalize its dedicated repair-only worktree.
  The journal-owning coordinator retires after this archive clears review and
  merges, rather than deleting the workspace holding unpublished closeout.
- [x] Record the disposition of every intake area, including exact external
  owners or missing evidence for items not safely implementable here.
- [x] Close this source effort only when every implementation and validation
  item is resolved or explicitly transferred to a named tracked objective.

## Validation Plan

- [x] Add focused red/green regressions for each changed behavior.
- [x] Exercise the real affected consumer boundary in an isolated integration
  control, including refusal propagation and unchanged success behavior.
- [x] Prove refusal details survive the owned-delivery parser and detached-launch
  consumer; keep typed/deferred/unconfirmed/lost pending-seed reporting unchanged,
  and prove refusal or uncertain acknowledgement does not authorize replay.
- [x] Exercise fresh-guest and present-but-unhealthy preparation in disposable
  fixtures where practical; never use a shared guest as an implicit test target.
- [x] Run changed-plugin tests through the canonical contained runner, focused
  Ruff checks and applicable install-contract/generated-asset checks.
- [x] Account explicitly for native Windows/POSIX and clean-room qualification;
  a mocked result is not live or installed acceptance evidence.
- [x] Record reasons for any skipped external lane. Live service mutation,
  installed rollout and venue retries remain outside this source-only scope.
- [x] Verify provider merge state for all five repair PRs and managed
  finalization of all four repair-only worktrees. Retain the coordinator until
  the reviewed archive is upstream, then release its completed effort binding
  and finalize it through the managed lifecycle.

## Proposal

The checklisted sequence is agent-recommended. Prefer existing producers,
owners, result shapes and retention policies over new services, configurable
frameworks or heuristic error-text scrubbing. No historical root-cause claim
depends on the mere existence of one of these source defects.

## Journal

### 2026-10-29 - Intake

- Claimed #9520 after searching current issues and open PRs.
- Read-only evidence identified a misleading installer/admission comparison;
  checked the actual guest hosting-policy consumer before classifying it.
- Existing PATH and monitor/discovery owners are preserved explicitly.
- No source repair, installed update, service restart or live venue retry has
  occurred as part of this proposal.

### 2026-10-29 - Plan clarification

- Applied the supplemental guidance on #9521: Context, Phase 2 and focused
  validation now name the owned-delivery parser and detached-launch refusal
  consumer, explicitly preserving the unrelated pending-seed helper.
- The guidance is not Maintainer acceptance. Implementation still awaits the
  proposal's required acceptance, current-head review and CI.

### 2026-10-29 - Accepted plan and first repair

- #9521 merged after Maintainer acceptance, current-head Copilot approval and
  required CI. Source-only scope and all live-mutation exclusions are unchanged.
- Began the narrow restart-command exit-status repair. Current source and open
  work retain #5512's exclusive-replacement owner; no replacement protocol,
  process identity, lock cleanup or serving-readiness behavior is changed here.
- A contained CLI-dispatch regression reproduced exit zero after a failed spawn.
  Success, disabled and already-current controls passed before the repair.
- After the repair, 21 focused restart/cutover controls pass through the
  canonical contained runner, including actual CLI dispatch. Focused Ruff,
  install-contract validation and whitespace checks pass.
- This narrow source slice does not change provisioning or process ownership,
  so a fresh-machine installer scenario is not applicable. Validation ran on
  native Windows; native POSIX and live installed service behavior are not
  claimed. No external service was restarted or updated for qualification.

### 2026-10-29 - Repair candidates and intake disposition

- Source candidates are open, not delivered: #9543 (restart exit status), #9545
  (fixed refusal reasons), #9547 (retained sanitized diagnostics), #9548
  (provisionless canonical guest policy) and #9549 (actual guest command health
  and existing-installer repair). Implementation checkboxes remain open until
  their respective reviewed source changes merge.
- #9545 passed 85 shared-library cases with three POSIX-dependent skips, 125
  CodeSpace cases, 63 container cases, two SSH consumer controls and six focused
  parser edge cases. Real parser output reaches provider results without raw
  errors or new replay authority; unknown acknowledgement retains custody.
- #9547 passed 57 readiness/logging/extension cases. Its actual file-handler
  control retains the sanitized failure after ordinary control deregistration;
  success remains silent and cancellation rejects late acknowledgement.
- #9548 passed the full 73-case policy portfolio before tier annotation. Final
  explicit packets pass 39 focused policy cases and 65 hosting/preparation
  cases; default packets pass 33/63 with six/two new actors skipped. Isolated
  Python without site packages exercises the real canonical reader, denies
  malformed/false/context-selected policy and cleans its temporary archive.
- #9549 passed all 132 detached-consumer cases, including seven T3 actors
  executing the actual constructed Bash command in disposable homes. Failure
  reaches neither trust-folder nor launch effects; default collection skips
  those actors. Its first CI run caught the source module crossing the
  thousand-line cap; the preparation constant moved to the existing startup
  owner rather than widening the cap, and all 132 cases passed again.
- Focused Ruff, all 13 plugin install-contract checks and whitespace checks
  pass for the candidates. Diagnostic contract fingerprints/fixtures were
  checked where applicable. These are native Windows source/component checks,
  including Git Bash shell controls, not native POSIX runtime qualification.
  Fresh disposable readers/synthetic tool homes do not qualify full package
  acquisition, installed-runtime reconstruction or live CodeSpace startup.
  Those external lanes require authority absent from this source-only task.
- Transferred relay/SSH progress-budget implementation and consumer validation
  to the existing two-clock objective #8051, with exact retry/timeout evidence
  in its coordination comment. #5761 retains namespace latency and result
  distinctions. #9322 retains its author's PATH integration; #5512 retains
  monitor ownership, with #5453/#6389's lifecycle acceptance unchanged.
  Intentional drain timing and the existing pending-seed helper are not fixes.
- Current-head reviews, required CI, actual merge and managed finalization
  remain delivery gates. Repeated review waits without a verdict do not
  authorize a bypass. No installed system or retained worker was changed.

### 2026-10-29 - Review correction and branch maintenance

- Addressed the #9548 login-environment finding at `cf387cd18`. The
  provisionless reader now uses the installed probe's `bash -lc`
  initialization while retaining isolated Python, stdin archive transport
  and the exact missing-receiver predicate.
- A disposable real-Bash consumer supplies an explicit context only through
  login initialization, with the installed receiver absent. Before the fix it
  reaches the preparation trap; afterward it refuses before preparation.
  The updated hosting/preparation packet passes all 66 cases. Focused Ruff,
  all 13 install-contract checks and whitespace checks pass. The Maintainer
  accepted this correction, explicitly retaining the separate Copilot and
  CI gates; it is not merged or qualified against a live guest.
- Rebased #9547 at `8d95cfff2` after a contract-registry conflict with `dev`.
  The resolution preserves the latest registry and immutable provenance,
  changing only the existing readiness source fingerprint and its
  implementation-only classification. The diagnostics implementation and
  wire fixtures are unchanged by this branch maintenance.
- The rebased readiness/logging packet passes all 56 cases; contract
  validation passes for two contracts and 814 fixtures. Full current-head
  CI passes. Fresh Maintainer confirmation was requested after the rebase;
  the earlier approval is not treated as a current-head review.
- All five repair PRs remain open. No Copilot-authored verdict has arrived
  after bounded waits and explicit requests, and the missing review was
  escalated on #9520. Maintainer conceptual approvals and green CI do not
  replace that gate. Implementation and finalization checkboxes remain
  open; no installed update, repair, restart or live venue retry occurred.

### 2026-10-30 - First repair delivered and review follow-through

- #9545 merged at `5735996e6` after current-head Copilot approval,
  Maintainer acceptance, passing required CI and no open review threads.
  The structured-refusal implementation item is complete. Managed finalization
  verified the source on `origin/dev` and settled the parent worktree's claim.
- Copilot reviewed #9543 and identified a Low-severity documentation error:
  the separate installers fail an incomplete monitor cutover rather than
  treating it as advisory. Corrected the command docstring and plan wording
  without changing either installer's behavior.
  All 18 focused restart/installer-cutover controls, Ruff and whitespace
  checks pass. This documentation-only correction adds no runtime behavior
  requiring another clean-room or external-venue qualification.
- #9547 received current-head Maintainer approval at `2ce0f3abe` after the
  second registry-only rebase and Copilot approval on that same head.
  Its five implementation, documentation, test and changefile blobs remained
  unchanged by the rebase; 56 focused cases, 13 install-contract checks,
  two Bridge contracts with 883 fixtures, Ruff and required CI passed.
  A newer base conflict still needs reconciliation before merge.
- #9548 and #9549 still await their required Copilot verdicts. The source
  effort remains Active; no installed rollout or live venue retry is authorized.

### 2026-10-30 - Two further source deliveries and preparation review

- #9543 merged at `435a4eea5` and #9547 at `5445343f4` after actual
  current-head Copilot approval, renewed Maintainer acceptance, passing required
  CI and resolved review threads. Their implementation items are complete.
  Managed finalization verified #9547 on `origin/dev` and settled its parent
  claim. The coordinator worktree remains open for the rest of this effort.
- Copilot identified a High finding in #9548: the provisionless preflight
  fallback also reaches direct stdio's final admission helper. Restrict that
  fallback explicitly to preflight and prove missing installed receivers
  cannot reach direct stdio launch, including skipped plugin preparation.
- Copilot identified a Medium finding in #9549: invoking a legacy shim for
  its health probe can provision before refusing an unresolved explicit
  context. Refuse before that legacy invocation and cover its actual side
  effects with a contained shell consumer.
- Both preparation implementation and delivery gates remain open. No source
  merge or test result authorizes an installed update or live venue retry.

### 2026-10-30 - Preparation review corrections published

- #9548 now limits the provisionless reader to explicit preflight callers.
  Final direct stdio admission retains a strict installed-receiver requirement.
  The real CLI/SSH consumer regression first reproduced a successful launch
  without that receiver under `--no-provision`, then passed with the correction.
  All 83 hosting/preparation/stdio and 125 detached-consumer controls pass
  through the contained runner, alongside Ruff and 13 install-contract checks.
  The correction is published at `99fb382211d4122ce76e1b58264ced5c261a8f34`.
- #9549 now refuses explicit installation context before invoking any legacy
  health shim, selecting its PATH or preparing a Bridge payload. A real Bash
  consumer models first-use provisioning writes: the unhealthy shim previously
  wrote state before refusal, and the healthy shim incorrectly reached launch.
  Both now refuse without invoking the shim. All 134 detached-consumer controls
  pass, including nine explicit T3 matrix cases, alongside Ruff and all
  install-contract checks. The correction is published at
  `ad84b93e85555def60412fd9efc3a7f5b49d231c`.
- Both original blocking threads were answered with exact red/green evidence
  and resolved. PR descriptions preserve the source-only qualification limits.
  Fresh current-head reviews, required CI, mergeability and actual merge remain
  delivery gates; neither preparation implementation is counted as delivered.
- Disposable fresh-home and isolated-reader process controls are not full
  clean-room installation, native POSIX, live guest or installed-host proof.
  Those external lanes remain outside the authorized source scope. No installed
  update, service restart, live venue retry or product execution occurred.

### 2026-10-30 - Preparation source delivered

- Provider receipts confirm the Maintainer merged #9548 at `e7624bdac` at
  06:32:19 UTC and #9549 at `daca1e7c7` at 06:33:03 UTC. Both have real
  current-head Copilot approval, renewed Maintainer acceptance and resolved
  implementation threads at the exact correction heads recorded above.
  Their Phase 3 implementation items are complete.
- The provider's aggregate check history still includes earlier failed
  attribution checks. The repaired attribution checks succeeded at 06:29:13
  and 06:29:16 UTC respectively; the latest guards/lint runs also succeeded
  before merge. This journal does not erase those historical failures or
  equate the aggregate history with a fresh all-green observation.
- All five planned source repairs are now merged. The remaining source slice
  is managed finalization of the preparation worktrees, followed by reviewed
  publication and archival of this journal and retirement of its coordinator.
  The previously transferred objectives retain their own completion gates.
- This records source delivery only. Installed rollout, shared service
  mutation, native external-venue qualification and product execution remain
  outside the authorization and are not represented as complete.

### 2026-10-30 - Source scope complete and archive prepared

- Managed finalization verified #9548 and #9549 against `origin/dev`, settled
  their creating coordinator's claims and marked both worktrees finalized.
  As with #9545 and #9547, an active shell can retain the folder and branch;
  that is normal lifecycle behavior, not an instruction to remove them by hand.
- Every implementation and validation item is complete or transferred to the
  named existing objective. The source effort is Done and moves to
  `efforts/2026/10/30 launch-boundary-repairs/`. Its active index entry is
  replaced by a completed entry. The source PR coordinator explicitly moves
  from Phase 2 to Phase 4 for final journal publication and retirement.
- The coordinator's own restart source was merged in #9543. Its remaining
  publication is this documentation-only closeout, subject to the normal
  review and CI gate; it will release the completed effort binding and
  finalize only after that archive is merged. #9520 retains this final
  publication/retirement receipt until then.
- Durable behavior and qualification limits already live in the changed
  plugin/library documentation; this journal is an execution record, not a
  second runtime specification. Documentation-only archival adds no new unit,
  clean-room, native POSIX or external-venue behavior to qualify.
- #8051, #5761, #9322 and #5512 remain independently owned and are not closed
  by this source effort. Normal release publication and separately authorized
  rollout remain outside this source-only completion claim.
