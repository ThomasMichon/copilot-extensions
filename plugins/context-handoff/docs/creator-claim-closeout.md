# Exact creator-claim closeout

A task-backed handoff has three independent outcomes: Dispatch task retirement,
pending-handoff ledger cancellation, and settlement of the originating worktree's
task claim. A successful abort does not imply that all three succeeded.
This closes the finite creator-obligation case in #4619; it does not make the
two stores' abort/arm sequence atomic or replace that separately owned work.

## Recorded selector, existing authorities

After successful `claims add task`, Worktrees returns `claim_binding` with
contract `context-handoff-creator-claim/v1`: `machine`, `project`, `worktree_id`,
`creation_nonce`, `claim_nonce`, `kind` and `ref`. The binding comes from the
actual locked journal transaction, not the caller's current directory or an
optional author observation. Re-journaling the same active task preserves its
nonce; replacing its kind or reactivating a released claim does not.
Ordinary full-record saves retain a newer task-claim incarnation and its
disposition, including when a stale snapshot omits it. Explicit native removal
and reserved claim handoff retain their existing locked transactions.
Terminal nonce-bearing entries are retained until explicit native removal;
previously read snapshots cannot recreate a removed entry. Fresh kind changes
or reactivation retain a new incarnation even when the replacement is no
longer a task. Changing the kind never clears its existing generation evidence.
An older bound task snapshot also cannot overwrite a recreated different-kind
claim that has no nonce. Claim-bundle storage retains the source nonce during
reload and validation; the recipient journals its own fresh claim incarnation.

Context-handoff records that binding once in the existing Dispatch task row's
`handoff_claim` metadata, including when completion raced ahead of journaling.
Original payload/author metadata is not rewritten. Session-request metadata can
also retain the selector; it is not the settlement authority.

The Dispatch owner re-reads the task before reconciliation. Ordinary completed
consumption can release its recorded obligation. An abandonment is eligible only
with zero attempts, no claimant/session and no claimed/started timestamp.
Unknown, live, execution-bearing abandoned and dead-letter state retain
protection.

Worktrees first exposes the selected native owner's current binding through
`claims binding <task-id> --owner-ref <machine/project/worktree> --json`.
Settlement then uses `claims settle --released` with that qualified owner and
all three identity pins: `--expected-record-nonce`, `--expected-claim-nonce`
and `--expected-kind task`. Both nonces and the kind are checked inside the
record lock at the actual write. A pending successor candidate, claim handoff
reservation, producer metadata, unreadable owner or ordinary mutation refusal
still blocks. Finalization does not ignore task claims.

Qualified `cancel-handoff --owner-ref ... --expected-record-nonce ...` targets
the same pending ledger without a leaf-ID search. Its existing dry-run check,
expected-status Dispatch abandonment and later ledger cancellation retain their
ordering. A later candidate still retains the creator claim at settlement.

## Truthful partial outcomes and retry

`handoff-cli abort` reports `taskCancelled`, `ledgerCancelled`, `claimSettled`
and explanatory notes separately. `ok: true` acknowledges task cancellation,
not resource settlement. A lost abandon reply leaves `taskCancelled: null`.
Retrying that exact abort re-reads the owner; an already abandoned,
never-consumed task is reconciled without a second abandonment.

The native Dispatch command
`handoff-claim <task-id>` retries only the recorded creator bookkeeping. Its
response includes `taskStatus`, `taskTerminal`, `claimSettled`, `claimBinding`
and, when pending, `claimNote`. It never re-abandons a task.

An unbound legacy task is not automatically released, even if a unique leaf
worktree ID happens to be discoverable. After independently establishing its
creator, use the explicit owner-qualified retry:

```text
<agent-dispatch catalog argv[0]> handoff-claim <task-id> --owner-ref <machine/project/worktree>
```

For that exact legacy owner, Worktrees' `claims binding --apply` can qualify
missing record/claim nonces under its existing mutation fences. It neither
creates/reactivates a resource nor releases it. Dispatch checks the task and
target, persists the binding, and then attempts the guarded settlement.
A conflicting existing binding is not overwritten. Unknown prior effects or a
replacement incarnation need owner investigation, not force-release.

The reconciliation endpoint requires the coordinator's configured control
authority independently of the selector. The terminal hook remains
fire-and-forget on its existing daemon thread; subprocess latency does not
block the request that completed/abandoned the task. Native failures leave a
pending result rather than causing an unqualified fallback.
The targeted HTTP retry allows at least 60 seconds for its bounded native
reads and settlement, preserving longer or unlimited caller read timeouts.

## Independently updated peers

The running Dispatch owner's task detail advertises
`handoff_claim_contract: "context-handoff-creator-claim/v1"`. Health advertises
it only when control authority is configured. New task-backed saves check
health before task publication; missing or unsupported capability
falls back to the existing file store. Metadata binding and targeted retry use
the same declared contract. Old callers can still create/read tasks, but a new
owner conservatively retains their unbound legacy obligations.

Worktrees' additive journal result supplies the selector. The read-only binding
operation establishes support from the selected executable before emitting new
settlement syntax. Missing/older/rolled-back native support leaves bookkeeping
pending; it never retries an older unpinned mutation. The resident writer's
`claim_add`, `claim_release` and `claim_settle` schemas are version 5, so an
older writer cannot silently ignore identity pins or discard nonce-bearing
metadata during an unrelated release. Normal versioned dispatch selects the
capable owner or current local implementation.

Expand Worktrees and Dispatch providers before enabling new context-handoff
callers. This is not a lockstep-install requirement or rollout permission.
The supported service rollback window is to another v1-capable provider, not a
pre-v1 Dispatch binary: an older daemon cannot enforce the meaning of retained
binding metadata. A pre-v1 receiver is not a supported owner for those tasks.
New caller/old receiver file fallback is a pre-publication compatibility path,
not evidence that old code protects previously stored v1 tasks. This is a
contract requirement, not an exact-build pin or installer replacement gate.
Ordinary unpinned native claim operations retain their existing contract; no
legacy CLI form is retired by this change.
