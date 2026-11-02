# Instruction source resolution

- **Slug:** `instruction-source-resolution`
- **Repo:** copilot-extensions
- **Branch(es):** Plan and focused implementation PRs against `dev`
- **Created:** 2026-10-26
- **Status:** Draft
- **Vision:** [Harness guidance](../../../visions/harness-guidance/README.md):
  `authoritative-ownership`, `portable-operator-policy`, `simple-local-static-delivery`
- **Umbrella issue:** #9097

## Guiding Intent

Repair the compatibility gap between startup's declared, verbatim instruction
publication and read-only source verification without weakening provenance,
folder trust, path safety, ownership, or reviewed-fallback requirements.
Keep repository-owned checked-in maintenance separate from effective
user/repository/local source discovery.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| repair-owner | Plan, implementation, tests, and source PR ownership | #9097; worktree codename `sluggish-cog` |

## Coordination

- **Topology:** Sequential plan and implementation PRs.
- **Host (owns PRs):** repair-owner.
- **Delegates:** None.
- **Handoff:** The canonical README and #9097 carry source progress.
  Release and installation qualification require separate explicit ownership;
  a source merge is never a claim that an installation changed.

## Context

The [startup publication contract](../../../docs/patterns/worktree-scoped-dynamic-guidance.md)
copies declared static templates into
`.github/instructions/agent-worktrees-generated/<marketplace>/...`.
The publisher intentionally does not rewrite these files with projection
envelopes. The catch-all still requires `resolve-source --from-settings`
before dependent action.

The verifier's source discovery excludes user/local settings even for
read-only resolution, and its local candidates cover the conventional
marked sibling and legacy namespace, not startup's verbatim output.
Thus a legitimate user-enabled declaration without a checked-in selector
cannot satisfy the required verification path.

This is a source-owned compatibility repair, not a new publisher, guidance
admission policy, installation mechanism, or permission exception.

## Request

Approved operator choices, quoted verbatim as form values:
`repair_setup`, `clone`, and `instruction-source-resolution`.
The operator also confirmed creation of the source checkout.
Private identifiers and unrelated downstream task context are omitted;
they are not prerequisites or inputs to the generic repair.

The technical proposal and validation obligations below are
**agent-recommended**, not additional operator-requested features.

## Plan

### Phase 1 - Review the plan

- [x] Reproduce the failure and claim #9097 with a generic diagnosis.
- [x] Establish an isolated source worktree and reconcile the proposal with
  the owning vision and current publication/verification contracts.
- [ ] Land this plan through the repository's required review and acceptance
  gates before implementing it.

### Phase 2 - Repair and qualify source resolution

- [ ] Add synthetic failing regressions for effective enabled-source
  discovery and startup-generated unpaired instruction resolution.
- [ ] Extend read-only discovery to honor effective user/repository/local
  enablement and overrides while retaining persisted folder-trust checks.
  Preserve the repository-only scope of committed sync and its worker.
- [ ] Recognize only the enabled declaration's exact generated destination.
  Authenticate the ordinary local file against its canonical template bytes,
  marketplace/plugin/source identity, and applicability. Never accept a
  generated path, receipt, or self-supplied hash as provenance by itself.
- [ ] Preserve reviewed-lock/artifact integrity refusals and deterministic
  version/hash precedence for paired sources. Invalid or unavailable local
  proof retains valid reviewed authority, never a success-shaped bypass.
- [ ] Preserve marked sibling and legacy compatibility. Raw generated bodies
  have no full-body receipt; selection must not claim model admission or
  permit skipping the required complete read.
- [ ] Update the owning procedure/pattern documentation and add the required
  plugin changefile without hand-editing plugin versions.

### Phase 3 - Land and account for remaining delivery

- [ ] Run focused validation, self-review the final diff, and satisfy required
  CI, Copilot verdict, and separate Maintainer acceptance before merging.
- [ ] Journal the merged source result and resolve every Plan/Validation Plan
  item, transferring release/installation qualification explicitly to #9097.
- [ ] Archive the completed source effort and release its worktree binding.
  Do not edit installed copies or imply an unapproved runtime rollout.

## Validation Plan

- [ ] Prove user-only and local-only enabled sources resolve, and effective
  disabled/overridden sources do not regain authority.
- [ ] Prove the real CLI path can select an exact verbatim generated file
  without a selector/lock, using synthetic settings and payloads.
- [ ] Prove wrong marketplace/path, ambiguous declarations, edited bytes,
  unsafe indirection, unavailable/disabled source, and untrusted repository
  settings cannot authenticate generated content.
- [ ] Prove valid reviewed fallback, version/hash precedence, invalid reviewed
  artifacts, and conventional/legacy local behavior remain intact.
- [ ] Prove checked-in sync discovery and its worker still exclude
  user-only/local-only plugin activation.
- [ ] Confirm resolution is read-only and raw bodies remain receipt-incapable
  with model admission unknown.
- [ ] Run the focused customization tests and applicable lint/structure/docs
  checks; keep process fixtures bounded and synthetic.

## Proposal

Use the existing source resolver, canonical declaration parser, safe-file
reader, and precedence helpers. Select the effective settings scope only for
read-only resolution; do not globally widen maintenance discovery.
Add the publisher's exact deterministic output path as a canonically verified
candidate, not an arbitrary directory scan. Keep legacy artifacts and strict
unknown-argument handling compatible. No daemon, new service, permission
change, or publisher dependency is needed.

## Journal

### 2026-10-26 - Kickoff

- Claimed #9097 and reproduced the settings-scope/generated-namespace mismatch.
- Read the current publisher and verifier contracts; preserved the distinction
  between publication, source verification, and model body coverage.
- Prepared this plan for review before source implementation.
