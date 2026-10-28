# Direct Instruction Propagation

- **Slug:** `direct-instruction-propagation`
- **Repo:** copilot-extensions
- **Branch(es):** Proposal and implementation PRs against `dev`
- **Created:** 2026-10-27
- **Status:** Draft
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
- [ ] Land this proposal and the owning vision clarification through review
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

- [ ] Creation and session start each copy the selected plugin version rather
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
