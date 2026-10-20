# Retained relationship authority proposal

Back to the [canonical effort](README.md). This is an **agent-recommended,
source-only design proposal**, not documentation of an available command.
Review and merge it separately before implementing the shared claim changes.
It selects a bounded compatibility extension; it grants no release, runtime,
credential or live-request authority.

## Reconciliation and ownership

This closes the existing bridge vision's
[externally-hosted-acp-agents](../../../visions/plugins/agent-bridge/README.md#externally-hosted-acp-agents)
requirement and the worktree vision's
[claims, leases and obligations](../../../visions/plugins/agent-worktrees/README.md#claims-leases-and-obligations)
requirement. It does not change their intent.

Honor the patterns for installation-qualified invocation, durable coordination
before side effects, standalone composition and graceful daemon cutover.
Reuse the existing worktree ledger, registered tracking transactions, required
record locks, claim-provider registry and claim-handoff registry. Reuse the
bridge session database and lifecycle serialization. Add no daemon, ownership
index, workspace allocator or peer-service lifecycle.

Worktrees owns the qualified owner, claim and accepted transfer. Bridge owns
the retained conversation relationship and its local retirement. Neither writes
the other's private store. Caller-session identity is provenance, never the
owner or a worktree head-succession epoch.

## Compatibility boundary

Keep the existing `resource-producer` version-1 command and
[`disposable-owned` contract](../../../plugins/agent-worktrees/docs/resource-producer.md)
unchanged: reserve/begin/create/bind/start, allocation fencing, quiescence,
verified absence and existing transfer restrictions all remain mandatory.
A retained relationship must never call its close operation with fabricated
peer absence.

Introduce the separate JSON-only `resource-relationship` command. Store its
additive metadata in the existing guarded `ResourceClaim.producer` slot, with
metadata `version: 2` and `lifetime: retained-relationship`. This reuses one
protected-obligation mechanism instead of adding a parallel generic-release
escape path. Dispatch validation by the exact version/lifetime pair; version 1
still means only `disposable-owned`. Unknown or malformed pairs remain raw,
visible and unsettled, never coerced into legacy claims.

Its capabilities response advertises schema
`copilot-extensions.resource-relationship`, version 1, the supported lifetime,
operations and input/ref limits. Preserve strict bounded UTF-8/JSON ingress,
duplicate-key rejection and namespace-safe callback arguments. No permissive
fallback to `claims add`, disposable producer verbs or private-store writes.
Installation-qualified command discovery selects the owning Worktrees
authority, not ambient PATH.

Before activation, every participating ledger writer must support the new
metadata-preservation rules. Bump the affected registered write-verb generations
where older daemons could ignore them; capability-check the actual receiving
authority, not just a client package version. Missing/incompatible claim or
bridge capabilities refuse a managed request before remote creation.
Supporting the new command is not permission to activate it in an old-writer
environment. Existing records are not silently converted or assigned owners.

## Transaction surface

Except for `capabilities`, operations require one JSON object on stdin and a
qualified `--owner-ref`; normalize any supported session-qualified reference to
its worktree. Resolve the owning project's record and local execution-space
authority through the existing resolver. New responsibility additionally
requires the owner's compatible coordination readiness.

| Operation | Input and authoritative result |
|-----------|--------------------------------|
| `reserve` | `kind`, `namespace`, stable `request_id` and opaque `recovery` object. Durably append a pending active claim before returning its token and ref. An exact replay returns the same claim; a changed request under the same key is a conflict, including after closure or transfer. |
| `inspect` | Exact `request_id` or returned `token`, never a scan. Return the current owner-record identity, full claim, phase and transfer evidence. No mutation or external probe. |
| `bind` | `token` and immutable opaque `identity` object. Change pending to bound; identical replay is a no-op, different identity is refused. It grants no remote allocation permission. |
| `close` | `token`. Query the registered relationship provider for exact local retirement, then revalidate the claim subject and owner under the required record lock before recording closed/released. Already-closed replay is read-only. |

All transactional results carry schema/version, explicit success or nonzero
error, owner, original creator, request ID, token, full ref, phase and retained
metadata. Tokens identify claims; they are not a new credential system.
Pending/bound/closed/transferred are the ledger phases. There is deliberately
no allocating phase or disposable allocation fence in this lifetime.

The callback ref uses a distinct versioned `namespace:l1.<base64url>` envelope,
not the disposable `r1` envelope. It canonically binds the original creator,
request ID, token, phase, recovery data and bound identity when present.
Provider verification must match that exact subject; a phase/identity change
invalidates an earlier probe. Keep the existing producer byte/depth bounds.
Recovery data and identity contain no credentials.

Mutations revalidate the exact owner, record path, namespace and metadata under
the record lock. Reject unresolved, remote, archived, orphaned, finalizing or
finalized owners; never reopen a terminal record. Inspection and replay of an
already-completed close remain available with matching authority on terminal
records. Read-only inspection and authorized teardown must not require
reacquiring a now-missing state-root binding; teardown still requires a
mutable, identity-valid owner and valid claim/provider evidence.

## Bridge acquisition and recovery

1. Resolve the configured target's canonical binding and installation identity,
   and the supplied host owner. Explicitly absent managed ownership means
   standalone/unowned; invalid supplied ownership is an error, not a fallback.
2. Persist a bridge relationship ID and a pending acquisition in the existing
   database before calling `reserve`. Use that ID as the stable request ID and
   include the provider authority and binding identity in recovery metadata.
   The bridge record distinguishes intended owner, confirmed claim and native
   session ID; it is not a second worktree claim ledger.
3. Confirm the durable Worktrees reservation before any remote conversation
   creation. A timeout/lost response is unknown: inspect by the persisted request
   ID, then retry the same transaction if necessary, never a fresh reservation.
   A refused or unreconciled reservation forbids remote creation.
4. Persist that the consequential remote attempt is being entered before
   issuing it. A crash at that boundary is conservatively uncertain. On a known
   accepted native ID, save it before binding the claim's immutable identity;
   reconcile a lost bind response before reporting owned success.
5. An unidentifiable remote creation outcome remains an active, disclosed
   uncertain relationship. Do not retry `session/new`: ACP offers no generic
   idempotency key. Recover only through an authoritative known ID or explicit
   local retirement that preserves the uncertainty disclosure.

Target identity includes the approved canonical binding, not a display alias or
bare endpoint. A native session ID is meaningful only within that binding.
Bridge serializes native-session attachment/reuse against its existing session
registry and persistence: aliases do not fork relationships, and different
owners cannot attach to an already-owned conversation as a new claim.
Duplicate requests, concurrent create/load/reuse and discovered native-ID
collisions reconcile the existing relationship or remain conflicting/uncertain;
they never release an obligation merely because the new attachment failed.
An existing unowned record is not implicitly adopted by a managed caller.

After reservation, all paths retain the claim until explicit confirmed
settlement. Worktree freeze/finalization must reject unsettled retained
claims under the same authoritative locking boundary, including the final
cleanup check. A reserve/freeze race therefore either refuses admission or
leaves a blocking obligation; it cannot finalize while creation proceeds
untracked. A same-worktree successor reuses the durable owner, not the original
caller session. Reconnect, stop and daemon cutover preserve the relationship.

## Local retirement, not peer deletion

Explicit end closes local admission under the session lifecycle lock, drains
or cancels owned local operations, and durably retires the bridge relationship
before attempting claim close. A lost response leaves settlement pending and
retryable. Neither end nor compensation deletes remote history or terminates
the external server, workspace or service.

The registered bridge provider returns a versioned `relationship_verification`
for the exact claim subject, asserting durable local retirement and no further
local admission/in-flight operation for that relationship. Worktrees obtains
this through the registered callback, never accepts caller-supplied evidence,
and stores the result as closeout. `exists: false` alone is insufficient.
Missing/unavailable provider, a live local operation, wrong subject or unknown
retirement leaves the claim unsettled.

An idle disconnected relationship can retire without contacting the remote
peer. An uncertain remote outcome remains in its terminal audit record and
is reported to the caller; retirement says only that the local relationship
cannot act again, not that remote work did not occur. Do not manufacture a
claim of remote cancellation or successful completion.

Generic release/remove/settle, at-rest/safe/gone sweep, release-all, stale
full-record saves, metadata removal and finalize/abandon cascades cannot
discharge either protected lifetime. Unknown metadata stays blocking even if
its generic claim state says released. The shared save chokepoint and both
finalization checks enforce this, not only the new CLI.

## Accepted ownership transfer

Keep the existing explicit offer/consumer-accept flow and recipient-first
persistence. Initially support only separate retained-relationship bundles
between mutable owners in the same local execution space. Refuse mixed,
pending, closed, cross-machine or conflicting bundles explicitly.

An offer reserves the bound claim. Bridge consequential operations must validate
current claim authority, including the offer marker, and stop admission during
an offered/accepting transfer. A transfer callback closes old-owner admission
and drains existing local operations under lifecycle serialization before
changing the bridge owner. The source is never silently released first.

Acceptance persists the recipient's blocking obligation, then coordinates the
same bundle with Bridge through an installation-qualified provider operation.
Bridge records the intended recipient and bundle durably as transfer-pending,
with further consequential operations blocked. Only after that acknowledgement
may Worktrees retain a transferred source tombstone and mark the bundle
accepted. Bridge confirms the accepted authoritative bundle before reopening
admission for the recipient. A lost response or crash leaves at least one
blocking obligation and an explicit pending state; retry repairs the same
transfer without creating a new conversation.

Use the existing ordered record/registry locks for local transactions, but do
not hold them across provider I/O. Revalidate the exact reserved claim/bundle
after callbacks. Cancellation may resume the source only after the bridge
confirms that no recipient transition committed; accepting/uncertain transfers
cannot be cancelled as if nothing happened. Keep creator, binding, native ID
and claim identity unchanged. Inspection shows pending transfers on both sides,
not a falsely completed handoff.

## Implementation and acceptance slices

The effort's Phase 2 includes the following ordered, **agent-recommended**
source slices. Merge this proposal before starting them.

- [x] Implement Worktrees metadata/transaction capability and preservation
  guards, exact request replay and non-bypassable finalization. Keep both
  lifetime validators and disposable regressions explicit.
- [ ] Integrate Bridge owner forwarding and durable acquisition/reconciliation,
  inspection, capability refusal and local-retirement callback.
- [ ] Integrate the acknowledged transfer path and recover interrupted
  acceptance/cancellation before advertising transfer support.
- [ ] Exercise the real ordinary CLI/client/API and registered command boundary
  with temporary authorities, a fresh database and a synthetic TLS ACP peer.
  Assert claim visibility, finalize blocking, same-owner succession and zero
  workspace/process allocation or external-service termination.

For each slice add unit tests plus focused actual-command tests. Cover lost
reserve/bind/close/transfer responses, exact replay conflicts, stale full-record
saves, malformed/unknown metadata, owner freeze races, concurrent lifecycle
operations, restart at each persisted boundary, alias/native-ID collisions,
old daemons and standalone unowned operation. Existing disposable allocation,
absence, recovery and accepted-transfer tests must still pass unchanged.

The integrated synthetic lane supplies fresh-state and lightweight end-to-end
proof without deployment. Record any unavailable clean-room or live-venue tier
and its reason in the implementation PR. Source review or tests do not waive
runtime-writer qualification or authorize promotion, rollout, grants or live
external requests. Once implemented, promote the actual public contract into
plugin documentation; this proposal remains the planning record.
