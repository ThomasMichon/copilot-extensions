# Retained relationship claim API

`resource-relationship` is the Worktrees authority primitive for durable
responsibility for a provider-owned local relationship. It never allocates a
workspace, starts a peer process, deletes a remote resource or erases its history.
The separate [disposable producer API](resource-producer.md) retains its
version-1 allocation, fencing and verified-absence semantics.

This primitive alone does **not** make Bridge remote sessions worktree-owned.
The Bridge acquisition/retirement provider integration and acknowledged
relationship handoff are separate implementation slices under #7237.
Capabilities currently advertise `handoff: false`; retained claims cannot use
the disposable/legacy handoff path.

## Command boundary

```text
agent-worktrees resource-relationship capabilities --json
agent-worktrees resource-relationship reserve --owner-ref machine/project/worktree --json
agent-worktrees resource-relationship inspect --owner-ref machine/project/worktree --json
agent-worktrees resource-relationship bind --owner-ref machine/project/worktree --json
agent-worktrees resource-relationship close --owner-ref machine/project/worktree --json
```

Except for capabilities, supply exactly one JSON object on stdin:

| Operation | JSON input |
|-----------|------------|
| `reserve` | `{"kind":"bridge","namespace":"example","request_id":"relationship-one","recovery":{"authority":"opaque","binding":"opaque"}}` |
| `inspect` | `{"request_id":"relationship-one"}` or `{"token":"RETURNED_TOKEN"}` |
| `bind` | `{"token":"RETURNED_TOKEN","identity":{"native_id":"opaque"}}` |
| `close` | `{"token":"RETURNED_TOKEN"}` |

The owner is explicit, qualified and locally authoritative. A supported
session-qualified owner normalizes to its worktree. Wrong/unresolved/remote
owners and frozen records refuse mutations rather than reopening or guessing
a ledger. New responsibility requires compatible owner coordination readiness.
Inspection and authorized close do not reacquire a missing state-root binding.
Already-closed replay remains read-only even after finalization.

Inputs are strict UTF-8 JSON, bounded to 16,384 bytes and 32 levels; duplicate
keys, unknown fields, NaN and oversized callback refs are refused. A request ID
is 1-128 ASCII letters, digits, underscores or hyphens. Recovery and identity
are opaque JSON objects, not credential storage.

Responses use schema `copilot-extensions.resource-relationship`, version 1.
Transactions return `ok`, current record `owner`, original `creator`,
`request_id`, `token`, full `ref`, `phase` and `producer` metadata. Errors have
`error`/`message` and exit nonzero. Capabilities resolve no owner or stdin.

Capabilities advertise `inspect_absence: true` for an optional read-only query:
`{"request_id":"relationship-one","allow_missing":true}`. An existing reservation
returns its normal complete receipt. If the exact, locally authoritative ledger
has no matching reservation, inspection returns only schema/version, `ok: true`,
the canonical `owner`, exact `request_id` and `absent: true`. This query remains
available on frozen/finalized owners and never reserves or changes a claim.
Opaque claim collections/items, malformed protected metadata, ambiguous matches
and unresolved/foreign owners refuse instead of proving absence. New reservation
also refuses opaque storage rather than appending into an unreadable ledger.
Ordinary inspection without `allow_missing`
still refuses a missing handle; unsupported authorities reject the extra field.
Neither a timeout nor a generic refusal is absence evidence.

## Replay and obligation

Reserve persists pending responsibility before returning. Exact request-ID
replay returns the same claim, including its later bound/closed phase;
different original kind, namespace or recovery under that key conflicts.
Bind preserves the request/token and makes identity immutable. Neither success
authorizes creation at a remote peer; the relationship-owning integration must
confirm responsibility before its own consequential action and reconcile
unknown responses using the same request ID.

Metadata uses the existing protected `ResourceClaim.producer` slot with exact
`version: 2` / `lifetime: retained-relationship`. Its callback ref is a canonical
`namespace:l1.<base64url>` envelope containing the original creator, request ID,
token, recovery, pending/bound subject and optional identity. Terminal closeout
retains the exact subject verified; the complete ref is at most 4,096 ASCII
bytes. `resource_relationship.decode_ref` validates the envelope.

Pending/bound claims block both initial and final locked finalization checks,
including kind `session`, disabled/warning gate modes and abandon flows.
Generic release/settle/remove, at-rest reconciliation, stale full-record writes
and finalize cascades cannot erase protected metadata. An active owner cannot
freeze/finalize with an unsettled retained relationship. Malformed/unknown
metadata stays raw and blocking, with an explicit invalid-metadata wire view.

The new registered `resource_relationship` transaction is a distinct capability,
not an enriched request sent to an older same-named writer. It uses the existing
logged direct RMW path and required sidecar locks, with provider I/O outside the
lock and exact revalidation afterward. Existing generic claim verb version 3
already preserves/refuses all explicitly present producer metadata; its wire
shape is unchanged. This is not qualification of arbitrary old full-record
writers: upgrade/qualify every participating writer before activating retained
relationships. An old/incompatible command is a refusal, not permission to use
legacy add or write a ledger directly.

## Verified local retirement

The provider registered for the namespace must answer its existing
`claim-status` callback with the required boolean `exists` plus:

```json
{
  "exists": true,
  "relationship_verification": {
    "version": 1,
    "subject": "EXACT_FULL_NAMESPACE_QUALIFIED_REF",
    "retired": true,
    "quiescent": true
  }
}
```

The provider establishes durable local retirement, closed admission and no
remaining local operations. A retained history record may still exist, and
the remote peer may remain live: `exists: false` is neither required nor
sufficient. Worktrees gets the proof from the registered, installation-qualified
callback, never from a close caller. Missing/unavailable provider, wrong
subject, malformed proof or changed phase/identity leaves the claim active.

The provider must durably retire its relationship before close. Lost close
responses reconcile through inspect or exact close replay; stop, disconnect,
daemon exit and remote unreachability alone are not retirement. A provider may
retire locally without contacting the peer, but must preserve and disclose an
uncertain remote outcome rather than claiming it was cancelled or never ran.
