# Explicit submission and reconciliation

Parent: [PR Review Workspaces](README.md). This closes the existing
[submission intent and recovery](architecture.md) design item; it does not
extend the campaign into a second service, writer, ledger or review-policy engine.
Contract review precedes separately tested source realization.

## Existing owners and selected storage

Extend **`agent_pull_requests.watch_replay_store.ReplayStore`**, its current
`watch-replay.json`, native operation lock, namespace/service binding and atomic
writer. Do not create `review-operations.json`, another operation store class or
a second effect owner. Registration/delivery receipts are not review receipts;
retain them unchanged while adding a review-operation namespace to this same
durable owner envelope. The generation cache remains independently rebuildable.

Keep the inner `WatchReplay` v1 codec unchanged. Select an explicit outer
`agent-pull-requests.owner-store` version 2 envelope containing namespace,
existing optional service identity, the complete replay state and review
operations. Native and service embeddings use the same owner semantics.

An explicit owner-only adoption operation converts a valid legacy native or
service store under its existing lock. Preserve all replay state and start an
empty review namespace. Missing/corrupt state is not adoption or provisioning.
Every subsequent registration transaction must preserve review operations, and
every review transaction must preserve replay state. Never treat an unsupported
outer schema as an empty store.

Old readers fail closed on the new envelope; they cannot silently discard it or
rewrite it as v1. No automatic downgrade or rollback erases review receipts,
including terminal receipts. Capability discovery reports actual codec/adoption
support, not a permission grant or evidence that a running owner was upgraded.
This slice does not install, restart or activate any service.

## Immutable selected intent

The caller supplies one stable operation ID before any effect, exact normalized
scope, generation ID, source range/attachment reference, and ordered selected
actions with stable action IDs. The fingerprint binds the entire selected
intent. Reusing an operation ID with a different scope, candidate or action
payload refuses; a retry/reconciliation never invents a replacement identity.

The initial action family is a selected new comment/thread, reply or update to
an owned comment/thread, and an explicit named verdict. Exact native thread/
comment IDs and source anchors are required where applicable. Do not infer a
verdict from text, automatically close unrelated threads, edit another
reviewer's comment, reset another reviewer or change completion settings.
Absent/null/false completion values remain observations, not write defaults.

Intent data contains no credential, role/issuer assertion or authorization
token. Generation/source hashes establish consistency only. Existing caller
rubric, consent, independent finding checks and repository policy remain outside
this component; no generic all-policies-green or merge-readiness gate is added.

## Protected owner operation seam

Source realization adds a finite library seam through the already authorized
PR-domain owner. That owner retains its actual auth, role, input, repository,
principal and resource-custody pipeline. No observer credential is promoted into
write authority and no raw secret is wrapped in fabricated permission metadata.
An object or callback claiming an owner role is not authentication.

The protected adapter supplies current-owner checks, actual current revision/
principal reads, selected action execution and effect-specific reconciliation
reads. These execute through the existing backend/provider surface. Returned
action outcomes preserve native IDs and their actual origin. The legacy
`AzureDevOpsProvider.submit_review()` string result does **not** implement this
contract: it can post a thread before voting and does not retain either action's
native IDs or distinguish lost responses. Do not pretend an empty string proves
an atomic review or wrap it as a confirmed batch.

Implementation may extend that existing provider/owner surface to preserve
structured action results, but it does not introduce another HTTP/auth client,
issuer or backend access path. An installed adapter missing this interface is
unavailable; do not improvise an operational command, role, identity or fallback.
Source-only fake backend adapters prove the library contract without claiming
installed owner adoption or live effects.

The owner must bind the identity check and selected write to the **same actual
current write credential/transport context**. An identity read under a separate
observer credential does not prove the writer principal. Credential renewal,
account switching or a changed binding requires re-verification at the mutation
boundary before sending. The ADO effective principal identity is the native
`connectionData.authorizedUser.id`, not `authenticatedUser.id` or an Entra object
ID. It is a review-scope identity, separate from the existing store namespace's
fleet/service/installation binding. That storage binding remains unchanged.
This is enforced inside the existing credential/auth pipeline; no credential
bytes, fabricated issuer label or new universal verifier is passed to the caller.

## Effect and persistence ordering

1. Validate immutable intent and the existing selected owner scope.
2. Persist the complete intent under `ReplayStore` custody before effects.
3. Before each selected action, run the owner's real permission/input checks and
   read current native principal and head/iteration/common range. A changed or
   incomplete candidate prevents remaining unattempted actions.
   Check action-relevant current discussion/comment ownership and baseline
   content/status, and refresh the decision inputs required by the caller's
   existing review policy before a verdict. Reply/edit/deletion or another
   relevant decision-input change can invalidate selection without a source
   push. Preserve the observed delta and pause unattempted actions rather than
   silently refreshing their immutable intent or resetting observation clocks.
   Previously confirmed actions in this same operation may advance the expected
   owned state through their actual receipts/readback; they do not excuse an
   unrelated concurrent change or force an unconditional all-policies-green gate.
4. Persist that action's **unknown/in-flight** state durably before entering the
   backend operation. A pre-replace commit failure performs no backend call;
   uncertain local persistence requires readback and does not authorize a send.
5. Invoke only the selected action through the current protected owner. Once
   entered, a lost reply, cancellation, exception or missing native result is
   conservatively unknown unless the owner has explicit no-send evidence.
6. Persist an actual confirmed or explicitly unattempted action result, keeping
   every previously known native ID and outcome. An uncertain receipt commit or
   owner loss after an effect is not rollback or permission to resend.
7. Read back the actual candidate/result as supported. Report post-effect drift
   without erasing a confirmed effect or pretending it applied atomically to the
   old head. Stop further unattempted actions when appropriate.

Use the existing single writer across each operation's durable transitions.
Cancellation cannot release custody while an admitted worker may still write.
Whole-file commits preserve both namespaces; the cache never participates in
effect transaction identity, authorization or recovery.

Actual finite operation/byte/action/storage limits are explicit and owned by the
library/store contract. They are not delegate permission, read-count or phase
ceilings. A full store refuses new admission without dropping old or unknown
receipts. No automatic receipt eviction is added.

Capacity admission reserves **bounded worst-case receipt/state growth for every
selected action before any send**, including partial and unknown outcomes and
their native IDs. Receipt persistence and reconciliation consume that reservation,
not a new admission quota after the effect. Unexpected oversized backend data
cannot become an unbounded receipt; retain bounded known IDs and an honest
unknown/error outcome within the reserved shape.

Replay and review namespaces have explicit disjoint capacity reservations.
Review admission cannot consume headroom required by existing pending watch
registrations/events and their durable outcomes. Adoption validates the existing
replay envelope plus its possible pending-event growth against that reservation
before converting it. Full review capacity must not prevent replay observation,
delivery/acknowledgement or durable review reconciliation; reject new admission
instead. Do not narrow old replay retention/capacity silently or evict receipts
to make the new namespace fit.

## Reconciliation and race limitations

Result states are action-specific **confirmed**, **unattempted** and **unknown**,
with original operation/action identity, native platform IDs, candidate revision,
stage and measured operation timing. Overall partial completion is not success.
Completed action receipts are immutable; a repeated operation returns/rechecks
their receipt rather than recreating the action.

Unknown actions are reconciled read-only through the same owner and exact
identity. Confirmation requires effect-specific evidence linking the actual
platform event/object to this operation/action, scope and selected payload.
Identical comment content, identical current vote, a matching reviewer ID,
absence from a partial listing or an observation timestamp alone is insufficient.
If the provider cannot establish attribution, retain unknown and do not resend.

A native response to the original performed request may confirm that individual
action. The ADO reviewer ID identifies the vote target, not a unique vote event;
after a lost vote response, current value alone cannot reconstruct that event.
Do not fabricate platform IDs or upgrade client fingerprints into server dedup.

The first contract promises per-action revision checks, **not** atomic multi-
action submission or atomic head compare-and-swap. A provider-supported stronger
condition requires its actual capability and proof; otherwise the check/write
race remains explicit. No force vote, compensation, new comment or duplicate
batch is an automatic response to drift or an ambiguous write.

## Acceptance and remaining gates

- [ ] Explicit legacy adoption and new-code restart preserve replay and review
  namespaces; old/unsupported/corrupt readers fail without fallback or data loss.
- [ ] Same-ID intent replay, mismatched intent, exact source/principal drift,
  selected action/ownership validation and unchanged-head follow-up are explicit.
- [ ] Credential/account changes between identity checks and writes refuse before
  send; identity under an observer credential cannot qualify a different writer.
- [ ] Discussion-only/decision-input drift pauses stale selection, while verified
  prior self-effects advance only the expected owned baseline.
- [ ] Real atomic-file consumers and synthetic protected actions prove
  pre-send persistence, no-send failures, post-effect uncertainty, cancellation/
  owner loss, lock contention, partial outcomes and native-ID retention.
- [ ] Lost replies do not repeat confirmed actions; unattributable current vote/
  content remains unknown. Full/partial readback and drift never fabricate proof.
- [ ] Cache rebuild cannot erase either namespace or revive an effect.
- [ ] Reserved receipt growth remains writable at capacity, and review admission
  cannot starve pending watch events, replay acknowledgements or reconciliation.
- [ ] Existing registration/store/provider conformance remains intact; focused
  tests, contained validation, source gates and actual PR review/CI/merge pass.

Native/service serving adoption, actual ADO review qualification, source fetching
and separately authorized rollout stay with their current owners. This source
slice is not permission to exercise them and cannot itself complete the parent
end-to-end validation gate.
