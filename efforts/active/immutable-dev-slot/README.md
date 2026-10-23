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
healthy match, select it as current, and start the incoming owner. Prefer
health-gated handoff and drain, but permit bounded cold takeover when contracts
are incompatible or handoff fails. A running owner is superseded by full identity
inequality with current selection, not version ordering. Compatible forward and
reverse baton-passing use the same best-effort contract. Interrupted clients
and owned sub-daemons recover through reconnect/retry and durable state.

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

Operator follow-on, verbatim (2026-10-22):

> One rule we need to add about handoffs: handoffs are *best effort*. If the contracts are incompatible, outgoing daemons are welcome to just shut down everything they own and quick-terminate, leaving the newcomer service to recover from cold. Both sides need a degree of fault-tolerance: the outer consumer of the service needs to be able to handle a momentary outage or cut-off of stream (it's like a network failure), and the sub-daemons need to be able to handle an abrupt shutdown and support a retriable flow (it's like a power outage). If we produce a new version of a service in the dev slot, with breaking versions of all contracts, and start it up, we expect it to basically abruptly take over. One way to ensure the "drop on the floor" effect is for the newcomer daemon to force-terminate the old daemon if it can't facilitate a successful handoff. Rather than fighting or refusing handoff or shutdown, outgoing daemons just need to maintain (reasonably) durable state, such that a cold-start form the new version can roughly continue without major chaoes ensuing.

**Accepted interpretation:** graceful handoff is preferred, never an
incompatibility or indefinite-drain veto. A confirmed selected replacement may
retire the old daemon and its owned scope before incoming readiness, including
identity-checked force termination, then recover cold. Clients tolerate network-
like interruption; children tolerate power-loss-like shutdown and retriable
flows. Durable-state compatibility still gates safe recovery/rollback; breaking
handoff contracts alone must not block takeover. This is a documentation rule,
not authorization to terminate live services in this task.
The decomposition and validation matrix below are
**agent-recommended**, preserving earlier reviewed acceptance rather than
adding operator-requested runtime scope to this documentation task.

## Plan

### Phase 0 - Reconcile intent and retire the superseded plan
- [x] Revise the existing service/runtime-assembly visions and development pattern; capture the operator's words without relaxing immutable deployment.
- [x] Abandon/archive the mutable effort with explicit transfers, preserving delivered work and journal history.
- [x] Land this reconciliation through review, then publish the final disposition to existing coordination owners (#8065 and the closing coordination comments below).

### Phase 0a - Best-effort handoff amendment (documentation only)
- [x] Land the revised service vision, binding patterns and effort acceptance, retaining implementation ownership and all unproven native/runtime gates (#8090).
- [x] Validate the literal follow-on, document links and canonical schemas; publish the merged rule to the existing #5472/#7028 coordination owners.

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
- [ ] Current selection starts the incoming owner through the existing service lifecycle. Prefer compatible readiness/handoff/drain, but incompatible or non-settling contracts permit bounded owned-scope shutdown and cold takeover before readiness.
- [ ] **Agent-recommended decomposition of the follow-on:** route cooperative and forced predecessor retirement through existing serialized lifecycle authority, revalidate installation/selection/process identity, confirm old-owner exit or write exclusion, and never terminate unrelated/current incoming owners.
- [ ] Prove forward/reverse and same-package/different-source graceful replacement, breaking-contract cold takeover, interrupted transition recovery and exact claim-release restoration. Failed construction still preserves the working generation; cold-start failure reports an actual outage/recovery outcome.
- [ ] Qualify retriable consumers after lost replies/cut-off streams and crash-consistent child recovery after abrupt owned-scope shutdown; reconcile duplicate/unknown effects rather than promising exactly-once execution.
- [ ] Keep release-channel ordering separate from owner obsolescence; incompatible durable state requires explicit safe recovery or refusal, not an implicit wipe. Handoff incompatibility alone is not state incompatibility.

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
- [ ] **Baton both ways:** replace numerically higher with lower, same-package different source, and return to the prior generation; prove preferred graceful ordering and bounded cold takeover when all handoff contracts break, without version-ordering or old-owner consent vetoes.
- [ ] **Cold recovery:** incompatible/refused/hung handoff permits owned-scope quick exit or identity-checked termination before incoming health, followed by exclusive recoverable execution, durable/checkpointed progress and explicit readiness or failure.
- [ ] **Fault tolerance:** simulate abrupt child death, truncated streams, lost side-effect replies, replay, replacement-caller death and PID/owner mismatch. Prove reconnect/retry and deduplication/outcome reconciliation, not universal exactly-once or zero-outage behavior.
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

### 2026-10-22 - Best-effort rule landed; finite amendment complete

#8090 merged as `4b78ba2d5dd2f49d695fe312af5f8d34d3c0385d` after current-head
approval and successful required CI. The final diff is twelve Markdown files;
the library and all consuming plugin payloads remain unchanged. The amendment's
schema, documentation consistency, link/anchor, changefile-scope and literal
Request checks passed. Runtime unit/E2E, clean-room and live activation were not
exercised by this documentation task.

Published the actual merged disposition on
[#5472](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6357855668)
and [#7028](https://github.com/ThomasMichon/copilot-extensions/issues/7028#issuecomment-6357856879).
Phase 0a is complete, not Phases 1-3. Existing implementation owners retain the
new unchecked forced-takeover, crash/retry, native and installed-adoption gates.
The next implementation slice reconciles delivered evidence against those
gates; it must not restore unconditional handoff compatibility as a veto.

### 2026-10-22 - Best-effort handoff and cold takeover amendment

Captured the follow-on verbatim and re-read it against the literal operator
input, including prompt owned-scope shutdown, newcomer force termination,
network-like client interruption and power-loss-like child recovery. Replaced
the unconditional successor-readiness-before-retirement premise with a
preferred graceful path plus bounded cold takeover. Existing lifecycle owners
retain identity, serialization and state-recovery duties; no process is
terminated or runtime changed by this finite documentation amendment.

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
