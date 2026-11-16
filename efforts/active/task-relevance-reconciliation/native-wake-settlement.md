# Original native-wake settlement

Part of [Task Relevance Reconciliation](README.md), continuing
[#9532](https://github.com/ThomasMichon/copilot-extensions/issues/9532).
**Status: proposed; review before implementation.** This is a bounded recovery
prerequisite, not a new task lifecycle, activation policy or permission grant.

## Established gap

Dispatch retains attempted original-owner wakes after an uncertain transport
result. Its existing reconciliation operation asks Bridge for the original
ordinary keyed native-send receipt. A positive receipt settles acceptance.
`native_receipt_missing`, an unavailable observer, a stopped session or a single
recorded turn cannot establish that the original send had no effect.

Bridge currently records accepted keyed sends in its existing turn journal.
Receipt observation does not close an unadmitted original key against a delayed
caller. Consequently, observation alone cannot safely turn a receipt miss into
Dispatch's `not_sent` outcome.

The [Bridge vision](../../../visions/plugins/agent-bridge/README.md#lossless-message-recovery)
already requires original-owner recovery without blindly replaying uncertain
effects. This slice closes that intent; it does not weaken the
[unknown-observation rule](../../../visions/plugins/agent-bridge/README.md#unknown-observation-is-not-a-verdict).

## Owner boundary

Extend the original Bridge native-send owner and Dispatch's existing
[wake recovery](../../../plugins/agent-dispatch/docs/task-recovery.md#wake-reconciliation-is-observation-not-retry).
Do not introduce a second queue, daemon, session, task, authoritative flow
database, arbitrary outcome setter or caller-authored negative receipt.

Keep passive observation passive. A missing receipt remains unknown. An
explicitly selected settlement operation is a separate, authenticated mutation:
it can close that original key, but cannot send, resume, allocate, retire a
conversation or erase its outstanding history.

The provider must distinguish:

- **Accepted:** return the matching existing admission receipt, without
  changing the admitted turn or sending it again.
- **Proved unadmitted and now fenced:** persist a request-bound settlement in
  Bridge's existing authoritative database, atomically excluding any later
  admission of that original key, then return an owner-issued negative result.
- **Unknown, conflicting or unsupported:** preserve the outstanding operation
  and explain the missing evidence. A caller cannot select its preferred result.

An unadmitted result needs both a trustworthy original admission history and a
durable exclusion against late admission. Neither missing rows nor the new
exclusion alone proves the past. Qualify the original native/session birth,
request identity and admission-before-effect contract, and establish that the
relevant authoritative history is complete. Refuse deleted, recreated,
incomplete, contradictory or unqualifiable legacy history. Do not silently
upgrade a historical send to a contract it did not use.

Use the existing session/native and keyed-request facts, not timestamps or a
new universal provenance service. Preserve the original prompt privately for
request comparison; diagnostic/public results must not export it.

## Implementation sequence

1. **Qualify the original journal boundary.** Map accepted turn persistence,
   native execution, history retention and session recreation. Establish the
   precise original contracts whose history can prove non-admission. If that
   proof is unavailable for an operation, leave it unknown rather than
   manufacturing a repair.
2. **Expand the Bridge owner.** Add idempotent original-key settlement using
   its existing database transaction and session coordination. Settlement and
   admission must linearize across processes, delayed requests, daemon
   generations and supported older writers, not merely a new in-memory lock.
   An accepted turn wins as accepted; a valid unadmitted settlement prevents
   every later same-key admission before native effects. Retain both results
   across restart and lost responses without fabricating a native turn.
3. **Expose the supported operation.** Reuse the native-send command/API
   boundary with explicit settlement semantics and actual-runtime capability
   negotiation. Publish receiver support before enabling consumers. Preserve
   existing receipt-only, ordinary send and positive-reconciliation behavior.
   New callers refuse an unsupported provider before effects; older writers
   must respect the persisted exclusion, including during supported rollback.
   If a rollback cannot uphold it, refuse that rollback explicitly rather than
   silently reopening the original key.
4. **Consume through Dispatch recovery.** Extend the existing revision-guarded,
   control-authorized reconciliation owner. Resolve the original outbox,
   reservation, native target, prompt and key internally. Only a matching
   provider-issued outcome can settle that same captured wake; recheck after
   observation/settlement. A negative outcome remains attached to the original
   row and is not permission to replay it, reset counters, change generations
   or invent another task. Any later natural wake follows the existing recovery
   policy, deadlines, ownership and authorization.
5. **Qualify and document before adoption.** Update the owners' recovery,
   admission and compatibility docs. Exercise the real supported CLI/HTTP and
   database boundaries with isolated synthetic state. Source acceptance,
   release, authorized runtime adoption and an actual original-flow outcome
   remain distinct completion facts.

## Validation plan

- [ ] An already accepted original send returns its original receipt and no
      extra turn, including after a lost acceptance response.
- [ ] A qualified unadmitted original key becomes durably fenced without a
      resume, native prompt, replacement session or fabricated turn.
- [ ] Missing, truncated, recreated, mismatched and legacy-unqualified history
      remains unknown; stopped state, age and absent log entries do not qualify it.
- [ ] Independent-process admission/settlement races and a delayed older
      writer cannot produce both an authoritative negative result and a native
      admission. Exercise the actual persistent mutation boundary.
- [ ] Restart and a lost settlement response recover the same original outcome;
      changed native identity, prompt, caller or key refuses rather than retargeting.
- [ ] Concurrent Dispatch task/outbox changes refuse settlement of another
      episode; successful reconciliation preserves the original row, attempt
      counters, task generation, retained workspace and policy deadlines.
- [ ] Current/prior callers, providers and supported rollback either preserve
      the exclusion and existing operations or refuse the unsupported effect
      before admission. Receipt-only reads never acquire settlement effects.
- [ ] A fresh isolated original-owner flow recovers through the public
      front doors; unresolved legacy operations remain visibly held.

These are implementation obligations, not claimed passes. In particular,
shipping the new path does not retroactively qualify an original operation
whose history cannot satisfy its proof requirements.
