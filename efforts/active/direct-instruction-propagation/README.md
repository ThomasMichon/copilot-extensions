# Direct Instruction Propagation

- **Slug:** `direct-instruction-propagation`
- **Repo:** copilot-extensions
- **Branch(es):** Proposal and implementation PRs against `dev`
- **Created:** 2026-10-27
- **Status:** Active
- **Vision:** [Harness guidance](../../../visions/harness-guidance/README.md):
  `simple-local-static-delivery`, `authoritative-ownership`, `resume-stable-context`
- **Umbrella issue:** #9329

## Guiding Intent

Copy the currently enabled plugin's declared instructions at every worktree
creation and session-start boundary. Generated files are disposable copies,
not independent authorities requiring content authentication. An operator can
restart a running session to load corrected instructions after replacement.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| propagation-owner | Proposal, implementation, qualification and PR completion | #9329; worktree codename `curious-filament` |

## Coordination

- **Topology:** Sequential reviewed proposal and implementation PRs.
- **Host (owns PRs):** propagation-owner.
- **Delegates:** None.
- **Handoff:** This README and #9329 retain the source completion gate.
- **Related owner:** #9097 / `instruction-source-resolution` owns the narrower
  verifier repair. Notify that owner of this replacement contract before
  implementation; do not silently rewrite its effort or claim its work done.
- **Release and rollout:** Normal promotion owns release; installed rollout
  remains explicitly outside this source change and must not be implied by merge.

## Context

Startup already copies declared templates verbatim into
`.github/instructions/agent-worktrees-generated/<marketplace>/...`.
The source verifier instead expects marked canonical renders and excludes
some effective activation layers. Consequently a legitimate generated file
can fail a required verification step. Prepared-generation/input/output
fingerprints also make startup copy conditional on a separate evidence graph.

The [owning pattern](../../../docs/patterns/worktree-scoped-dynamic-guidance.md)
already calls for simple declaration-driven copying. This effort removes the
runtime authentication/reuse layers that contradict that intent rather than
adding another compatibility proof chain.

## Request

Operator request, verbatim:

> We need to fix instruction propagation: every worktree creation and session-start should just copy over the version of the upstream plugin; no hash-matching, fingerprint checks, etc, or else we'll get this issue. In fact, it's okay if the instruction gets overwritten while Copilot is running; the user can restart the session to pick up the corrected updates. We've got multiple layers of defense against unwanted instructions, so let's trust those layers.

The operator confirmed the slug `direct-instruction-propagation`.

## Plan

### Phase 1 - Review the replacement contract

- [x] Claim #9329, identify the #9097 overlap, and capture the operator request.
- [x] Notify #9097 of the replacement contract and preserve its separate owner.
- [x] Land this proposal and the owning vision clarification through review
  before implementation.

### Phase 2 - Implement copy-only propagation

- [ ] Copy all currently enabled, declared plugin instruction files on every
  creation/session-start boundary, including the existing paired preparation
  and independent compatibility paths.
- [ ] Remove content hashes, fingerprint matching, provenance envelopes and
  prepared-generation reuse as runtime copy or generated-source admission gates.
  Do not replace them with a different attestation, version comparison or receipt.
- [ ] Replace generated files even during a running session. Update selectors,
  catch-all instructions and source-resolution procedures so they trust the
  managed copies without asking the model to authenticate them.
- [ ] Preserve effective activation/configuration and complete-set composition.
  Keep path containment, tracked-file protection and declaration ambiguity
  checks as file/configuration boundaries, not instruction-content judgments.
  _(Agent-recommended implementation constraints.)_
- [ ] Keep checked-in authoring/sync separate from runtime copying; retain the
  offline fallback without allowing stale selectors or fallback integrity checks
  to block otherwise available generated instructions.
- [ ] Update owning docs/tests and add changefiles for every changed plugin.

### Phase 3 - Qualify and land

- [ ] Exercise focused synthetic regressions and existing refusal controls;
  self-review and satisfy CI, Copilot verdict and the target's acceptance gates.
- [ ] Merge the implementation and journal the actual source result; resolve
  every Plan/Validation Plan item or transfer it to a named tracked objective.
- [ ] Set this source effort Done and archive it. Retain the release/rollout
  distinction and do not claim installed behavior changed.

## Validation Plan

- [x] Creation and session start each copy the selected plugin version rather
  than returning a prepared-generation reuse result.
- [ ] Changed plugin content, same-version edits, missing or edited generated
  files and malformed/absent old receipts converge to current plugin bytes
  without hash/fingerprint admission.
- [ ] Generated files can be overwritten while a session is live; reporting
  distinguishes filesystem delivery from model reload/restart.
- [ ] Effective user/repository/local activation, paired-only providers and
  independently usable compatibility delivery select the intended sources.
- [ ] Generated source selection works without provenance markers, content
  equality checks, version precedence or a valid legacy selector/fallback lock.
- [ ] Traversal/link escape, tracked targets, ambiguous destinations and genuinely
  malformed/unavailable plugin configuration still report real failures.
  Partial discovery does not delete unresolved providers' instructions.
  _(Agent-recommended regression controls.)_
- [ ] Existing offline checked-in maintenance remains usable and does not become
  a prerequisite to the current managed-copy path.
- [ ] Run focused bounded tests and applicable lint/structure/docs checks; assess
  repeated-copy startup cost without restoring fingerprint-based skip gates or
  expanding required CI into a native integration matrix.

## Proposal

Reuse the existing in-process declared-source publisher and generated namespace.
Remove evidence-based skip/admission machinery instead of introducing a second
publisher or a new policy service. Both lifecycle boundaries always request
publication. The managed namespace identifies generated output; the enabled
plugin/declaration identifies what to copy, and normal plugin installation and
harness review own trust. Full reads still matter: copying a file does not prove
that a running model has loaded it.

No installed payload edits, plugin update, live service restart or machine
configuration changes are included. Release follows the repository's existing
promotion flow; rollout requires its normal authorization.

## Journal

### 2026-10-27 - Replacement contract proposed

- Created #9329 after finding #9097's narrower canonical-verification repair.
- Captured and re-read the operator's complete request without changing its
  overwrite/no-hash decisions. The retained filesystem/configuration controls
  and their refusal tests are explicitly agent-recommended.
- Implementation remains gated on the proposal's reviewed merge.

### 2026-10-27 - Proposal merged; implementation admitted

- #9331 merged after current-head Copilot approval and required green checks.
  The older fallback/provenance clause was aligned with the same direct-copy
  contract rather than leaving contradictory standing intent.
- Re-read the complete effort after merge. Phase 2 now owns runtime copying,
  source selection, model-facing guidance and their focused regression controls.
  No installed payload or running session was changed.

### 2026-10-27 - Direct-copy publisher source slice

- Removed prepared-generation reuse, instruction input/output fingerprint
  matching and output-byte comparisons from Worktrees publication. Each
  preparation replaces every selected file; SessionStart always queues its
  existing bounded backup rather than probing readiness.
- Retained pairing/composition order, serialized source resolution,
  tracked-file/ignore/path checks, partial-provider destination ownership
  by source name only, and restart notifications. There is no instruction
  content hash or provenance receipt in this publication path.
- Focused native Windows publisher, isolated-consumer and lifecycle controls
  passed: 198 tests, with 10 platform-specific skips. Synthetic 32-file
  repeated-copy controls retain timing properties without adding a CI matrix.
  Initial test-launch setup issues were corrected in the private runner, not
  worked around in production code.
- The source resolver, model-facing selectors/catch-all and independent
  compatibility renderer still need their copy-only changes. This publisher
  slice does not close #9329 or claim installed propagation is fixed.

### 2026-10-27 - Overlapping backup requests retained

- Reproduced the publisher review finding with a real worker and declaration
  snapshot: while the first copy held old bytes, a second session-start request
  returned queued but left those old bytes installed.
- Active requests now record a pending rerun under the admission gate.
  Completion consumes it under the same gate and repeats discovery/copying
  within the original shared backup budget. Gate contention explicitly reports
  an unqueued request; exhausted pending work cannot report success.
- Focused native Windows publisher/worker/lifecycle/standalone regressions passed:
  204 tests, with 10 platform-specific skips. These include real overlapping
  snapshots, coalescing, completion-gate contention, bounded exhaustion and
  retention of restart notices after an earlier pass changed files.
- The source resolver, model-facing guidance and independent compatibility
  path remain the next source slices. No rollout occurred.

### 2026-10-27 - Publisher implementation merged

- #9336 merged as `3b7a166351` after current-head Copilot approval and required
  green checks. The immutable-commit required unit packet passed 2,559 tests
  with 12 deselected; the focused publisher packet passed 204 tests with
  10 platform-specific skips.
- Re-read the entire Plan and Validation Plan after merge. The lifecycle
  copy-versus-reuse validation is resolved; Phase 2 remains open because
  source selection, model-facing guidance and independent compatibility
  copying still use the superseded verification contract.
- Continued in the same source worktree. The next slice removes generated-copy
  authentication and checked-in fallback prerequisites from runtime source
  selection, preserving offline authoring checks separately. Source merge is
  not release, installed rollout or completion of #9329.

### 2026-10-27 - Raw-copy reader and compatibility implementation

- Runtime lookup now selects enabled declarations' managed copies before
  reading reviewed selectors, fallbacks or locks. Raw bodies require no
  instruction digest, version comparison, envelope or receipt. Model-facing
  selectors, catch-all guidance and the owning pattern use that same contract.
- Independent compatibility publication now copies templates byte-for-byte into
  the managed namespace on every call, including identical/edited outputs and
  legacy `skipLocalCache` sources. It shares the startup publisher's native
  workspace lease and resolves effective user/repository/local settings inside
  that lease. Runtime discovery no longer collects Git/version provenance.
- Explicit invalid marketplace configuration cannot silently select an installed
  fallback. Path/index/ignore and ambiguity refusals remain visible. Incomplete
  discovery retains old slots; historical bodies outside the reserved namespace
  are reported for explicit migration rather than content-authenticated.
- The focused reader/copy/standalone packet passed 256 tests with six
  platform-specific skips. The broader customization/scanner/standalone packet
  passed 570 tests with six skips. Independent review then identified stale
  cleanup after a failed destination migration; read, write, ignore and tracked
  refusal regressions now all retain the earlier copy and passed separately.
- Python lint and the mechanical customization review passed their blocking
  gates. Required immutable-commit validation and reviewed merge remain open.
  No installed payload, running session or machine hold was changed.

### 2026-10-27 - Reader review compatibility corrections

- #9350's first Copilot verdict identified five concrete compatibility defects.
  Added focused regressions before changing production behavior: independently
  healthy refresh after a sibling failure, case-only destination migration,
  native built-in settings objects, supported remote/local source kinds and
  all startup-supported marketplace-manifest layouts.
- Separated declaration completeness from refresh failures: the former protects
  unresolved slot ownership, while either prevents stale cleanup. A failed
  replacement no longer blocks another independently resolved existing copy.
- Stale cleanup now recognizes filesystem aliases of desired copies without
  retaining genuinely different files on case-sensitive filesystems. A coupled
  tracked-alias regression also reproduced an overwrite; index protection now
  includes portable filename aliases in both initial and final probes.
- Runtime discovery filters native built-ins before validating marketplace
  values and matches startup's source kinds and manifest roots. It does not
  route runtime settings through the separate committed-authoring validator.
- These corrections remain on the same implementation PR, with review and
  current-head validation still required before merge. No rollout occurred.

### 2026-10-27 - Declaration and preparation parity corrections

- The next #9350 review confirmed all five earlier findings resolved, then
  identified catalog identity and four additional runtime parity gaps.
- Focused controls reproduced acceptance of invalid/missing plugin manifests,
  rejection of top-level destinations, publication during pending pairing and
  loss of staged-deleted/renamed copies. The initial focused run failed 15
  cases while accepting the explicitly mapped catalog control.
- Runtime discovery now requires the existing explicit local catalog mapping
  when registration and catalog identity differ. Payload manifests validate
  plugin name only, without restoring instruction/version attestation.
- Runtime destination mapping accepts startup's top-level and suffix forms.
  Compatibility checks the existing pending-pair marker under its shared lease
  before discovery, including linked-worktree metadata. Tracking probes include
  staged deletions with rename detection disabled before copy and cleanup.
- The focused compatibility packet passed all 31 cases, and the complete
  customization/scanner/standalone packet passed 605 tests with six
  platform-specific skips. Lint and whitespace checks passed. Immutable
  current-head validation and another passing review remain open; no rollout
  occurred.

### 2026-10-27 - Activation and complete-declaration parity corrections

- #9350's next review reported no new inline issue, but its full body contained
  one High and four Medium previously missed findings. Those remain blocking
  regardless of the overview's zero-open-issues wording.
- Added ten failing controls for malformed qualified activation keys, startup's
  portable case/underscore identities, BOM-prefixed JSON, complete-set
  ancestor/descendant collisions and declared-count reporting.
- Runtime discovery now rejects malformed activation identities before an empty
  result can authorize cleanup. Runtime declarations use portable identities
  rather than the stricter authoring identifier validator, and accept optional
  JSON BOMs without changing copied instruction bytes.
- Complete-set declaration validation excludes every conflicting output before
  copy or selection, including across providers, while retaining independent
  safe paths. The compatibility result again reports its declared-source count.
  The focused compatibility packet passed all 41 cases; the complete
  customization/scanner/standalone packet passed 615 tests with six
  platform-specific skips. Lint and whitespace checks passed.
- Full current-head validation and a passing review remain required. No rollout
  or machine hold was performed.
