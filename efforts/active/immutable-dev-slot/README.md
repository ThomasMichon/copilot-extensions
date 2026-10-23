# Immutable Dev Slot

- **Slug:** `immutable-dev-slot`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs to `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** [Immutable runtime](../../../visions/plugin-services/README.md#immutable-versioned-runtime), [bidirectional cutover](../../../visions/plugin-services/README.md#zero-downtime-cutover), [source assembly](../../../visions/plugin-services/runtime-assembly/README.md#selected-development-source-assembly)
- **Coordination:** #5472 (retained, not a replacement issue)
- **Related effort:** [Source-Managed Runtime Installs](../source-managed-runtime-installs/README.md), #7028/#7029

## Guiding Intent

Dev identifies source, not a mutable environment. Deploy only immutable venvs
whose generation identity binds the complete frozen source hash and admitted
build/dependency inputs. Build beside the serving generation or reuse its exact
healthy match, select it as current, start the incoming owner, and settle normal
health-gated handoff and drain. A running owner is superseded by full identity
inequality with current selection, not version ordering. Compatible forward and
reverse baton-passing use the same contract.

This replaces the mutable premise, not its unfinished acceptance. Preserve
delivered primitives and all thirteen adopters' construction, admission,
retention, distinct-generation repair, claim restoration and installed evidence.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Intent reconciliation coordinator | Owns this finite vision/effort supersession and publication slice, not runtime implementation | Managed source worktree and repository PR |
| Slot-admission coordinator | Retains shared lifecycle and first-adopter integration responsibility | Existing #5472 coordination |
| Adopter implementers | Own separately assigned installer/service integrations and native acceptance | Existing per-adopter claims and PRs under #5472 |
| Source deployment coordinator | Owns clean merged Dev selection, Manager integration and deployment acceptance | #7028/#7029 and canonical related effort |

## Coordination

- **Topology:** independent coherent PRs; preserve existing implementation and open PR ownership.
- **Host (owns PRs):** each named slice owner; intent reconciliation does not take over the runtime campaign.
- **Delegates:** no new runtime assignments or rollout authority from this plan.
- **Handoff:** read this contract and the assigned slice's evidence before execution; a merged prerequisite is not portfolio completion.
- Reuse the installer-engine, versioned-runtime and service lifecycle owners.
  Keep #6424 descendant settlement and #6389 listener custody intact.
  Source deployment, recurring maintenance and #7758 execution-driver work
  remain in #7028, not a second updater or implementation framework here.

## Context

The [abandoned mutable effort](../../2026/10/22%20mutable-dev-slot/README.md)
preserves delivered Phase 1/2 work and the detailed
[Phase 3 evidence](../../2026/10/22%20mutable-dev-slot/phase-3-runtime-admission.md).
Its unchecked work is transferred, not completed by renaming it. Shared
admission/fingerprinting, kernel construction and retention ownership, and
first-adopter source-generation work are reusable progress, not proof that all
adopters meet the contract. The canonical synchronizer inventory determines
the portfolio; do not infer identical defects across installers from a list.

The [development pattern](../../../docs/patterns/mutable-dev-slot.md) states
the new target and labels remaining editable behavior as legacy reality.
Source-Managed Runtime Installs remains the canonical "deploy from Dev branch"
home. Dev/Main release promotion (#3336) and test portfolio #1303 remain valid,
separate work, not superseded efforts.

## Request

Operator direction, verbatim (2026-10-22):

> So, mutable dev slot should probably be superseded by "immutable dev slot", and the "deploy from dev branch" effort. Basically, always deploy using an immutable venv, tagged with the hash of the source. Just set the new dev slot as current and handle cutover normally by giving the incoming version a start command and letting the old version shut off. We should endeavor to support baton-passing both ways; a version knows it's "old" simply because it's "not current", not via any ordering relationship between the active and owner versions.

Operator execution instruction, verbatim:

> Great. Update relevant visions and efforts to align; close out and abandon superseded efforts and off-course work.

**Accepted interpretation:** "not current" does not mean exit before the
successor is healthy and drain/work handoff have settled. State compatibility
still gates rollback. The decomposition and validation matrix below are
**agent-recommended**, preserving earlier reviewed acceptance rather than
adding operator-requested runtime scope to this documentation task.

## Plan

### Phase 0 - Reconcile intent and retire the superseded plan
- [x] Revise the existing service/runtime-assembly visions and development pattern; capture the operator's words without relaxing immutable deployment.
- [x] Abandon/archive the mutable effort with explicit transfers, preserving delivered work and journal history.
- [x] Land this reconciliation through review, then publish the final disposition to existing coordination owners (#8065 and the closing coordination comments below).

### Phase 1 - Complete shared admission and immutable development
- [ ] Reconcile existing shared/first-adopter deliveries against the archived Phase 3 evidence before choosing the next implementation slice; do not redo delivered work.
- [ ] Freeze every declared source, vendor and selected dependency input before construction; identify the immutable non-editable generation by source and assembly hashes separately from package version.
- [ ] Reuse exact healthy completed content without writers; refuse conflicting completed content even under force. A new edit builds a distinct generation, never rewrites or follows a checkout.
- [ ] Complete bounded kernel-backed first-build coalescing and revalidation, retaining the construction descriptor through publication and releasing it on success, failure, cancellation or process death.
- [ ] Preserve atomic ready/rollback retention across construction release, concurrent GC, selection, promotion/drain and rollback; no mtime pins or collection races.
- [ ] Preserve whole-operation repair/cutover authority and consistent lock order with construction and installation governance; broken published identities require a distinct health-gated repair generation.
- [ ] Retain explicit claim ownership, original restore target and deployed release/status accessibility, without retaining editable deployment as a target.
- [ ] Distinguish contention timeouts, malformed evidence, permission/storage failure and genuine health repair; reclaim only owned unpublished candidates.
- [ ] Vendor canonical helpers and enforce the executable install contract without adding a sibling-runtime or checkout dependency.

### Phase 2 - Bidirectional owner cutover
- [ ] Propagate full generation identity through selectors, manifests, discovery, running-owner evidence and recovery; package-version ordering never defines supersession.
- [ ] Current selection starts the incoming owner through the existing service lifecycle. The displaced owner serves until successor readiness, work handoff and drain settle.
- [ ] Prove forward and compatible reverse handoff, same-package different-source replacement, failed successor preservation, interrupted transition recovery and exact claim-release restoration.
- [ ] Keep release-channel ordering separate from owner obsolescence; incompatible durable state refuses reverse selection explicitly.

### Phase 3 - Complete each adopter and reconcile guidance
- [ ] Finish agent-worktrees on PowerShell and POSIX through the full lifecycle, not just shared-core completion.
- [ ] Complete the remaining canonical inventory in independently owned waves; inspect actual admission and fingerprint behavior per adopter and preserve its health/service hooks.
- [ ] For every adopter, prove ordinary reuse/refusal, immutable dev/release restoration, distinct-generation repair, retention and bidirectional cutover before checking it complete.
- [ ] Remove live editable/hot-patch recommendations as implementation lands; keep compatibility commands honest and guidance matched to available behavior.
- [ ] For each implementation wave, obtain review, merge, verify release and complete its separately authorized installed-generation acceptance with the existing rollout owner.
- [x] Deferred to `#7028/#7029 (source-managed-runtime-installs)`: clean merged Dev source selection, Manager/deployment policy, maintenance and assembled native rollout; retain that effort's full remaining Plan/Validation Plan and #7758 driver.

## Validation Plan

- [ ] **Frozen inputs:** changing/removing the worktree after construction cannot change deployed imports; source-only and external vendor changes produce distinct identities.
- [ ] **Concurrency:** real competing installers yield one exact-input build and bounded healthy reuse; conflicting content refuses after the winner publishes. Exercise cancellation, process death and storage/permission errors separately.
- [ ] **Immutability:** both native entrypoints leave completed numbered and development generations byte-for-byte unchanged on reuse and forced/content-conflicting attempts; legitimate out-of-slot reconciliation still runs.
- [ ] **Retention/repair:** concurrent GC cannot collect ready/rollback-pinned generations; repair retains whole-operation authority through promotion, drain or rollback without lock inversion or changing the old slot.
- [ ] **Baton both ways:** replace numerically higher with lower, same-package different source, and return to the prior generation; prove readiness before retirement, exactly-once work ownership and explicit incompatible-state refusal.
- [ ] **Claims:** competing owners refuse; iterative builds preserve the original restore target; deployed release works without the source checkout and failed restore retains attributable ownership.
- [ ] **Portfolio:** record every canonical adopter's actual two-entrypoint, native process and installed acceptance evidence. Shared primitives or one pilot never close #5472.
- [ ] **Platforms:** qualify Windows, Linux and macOS separately, with named tracked gaps where a lane is unavailable; use isolated roots and owned processes, never live user state as a fixture.
- [x] **Guidance:** audit new intent, archive transfers, links and literal request capture; retain explicit implementation/rollout gaps instead of claiming documentation deploys behavior.

## Proposal

Extend the existing immutable installer and service lifecycle; no replacement
framework, new mutable slot, parallel updater or new coordination umbrella.
This reconciliation changes intent and effort ownership only. Implementation,
native installation and rollout remain separately assigned and gated.

## Journal

### 2026-10-22 - Reviewed intent landed; finite reconciliation complete

#8065 merged as `738b370a14cda49aefdf82ac785535fc11e87718` after approval and
required CI. Effort/vision structure, documentation consistency, changed local
links, all 18 explicit archival transfers and literal Request capture passed.
No executable behavior changed, so runtime unit/E2E, clean-room provisioning
and live rollout were not exercised by this documentation slice.

Published the final disposition to
[#5472](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6356383975)
and [#7028](https://github.com/ThomasMichon/copilot-extensions/issues/7028#issuecomment-6356384651).
The intent reconciliation coordinator's finite slice is complete. This effort
stays Active: the Slot-admission coordinator and separately assigned adopter
owners retain Phases 1-3 and their unchecked validation; Source-Managed Runtime
Installs retains #7028/#7029's deployment acceptance. Select the next existing
implementation slice against delivered evidence, not another mutable framework.

### 2026-10-22 - Immutable development direction

Captured the operator's direction and compared the Request back to the literal
input. Retained #5472 and #7028/#7029 as the existing coordination homes.
Abandoned the old mutable premise and transferred every unresolved checkbox,
without reverting delivered primitives or declaring the adopter portfolio done.
The bounded open-work inventory identified no additional mutable-only open PR
or issue to close; #3376 is already merged history, not an open closure target.
This finite documentation slice does not change a runtime or terminate a worker.
