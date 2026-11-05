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

### Workspace incarnation and executing-writer support

Capabilities advertise `expected_creation_nonce: true`. Any transactional input
may include `expected_creation_nonce`, a nonempty string from the authoritative
workspace creation receipt or record. The locked owner transaction requires an
exact match before inspecting claims, proving absence, reserving, binding or
probing retirement. Missing legacy nonces are not lazily assigned by this API.
Close rechecks the nonce after provider I/O, including already-closed replay.
The option does not change opaque claim metadata or callback encoding.

An opted-in success adds `creation_nonce` and `writer_contract`. The latter is
the version-1 `copilot-extensions.worktree-record-writer` declaration, with
`retained_relationships: 1` and `creation_nonce: 1`. It describes protected
relationship preservation and nonce preservation by that executing serializer.
Generic current-code saves retain a nonce backfill and refuse a different
nonempty incarnation rather than overwriting a successor at the same path.
Existing receiving-allocation saves still restore their authoritative binding
before this check rather than accepting a stale caller's replacement nonce.
Inputs without the option retain their existing response shape and semantics.

The CLI capability response labels its declaration
`writer_scope: executing-process`; it does **not** establish support in a
resident daemon, another installation or every writer sharing the ledger.
Its additive `writer_identity` contains the selected `installation_root` and
the executing process's cached installed-build `runtime_generation`. The latter
is the immutable build UUID, not a package version, a fresh observation ID or
a mutable payload stamp. Source and legacy builds without that evidence return
null; consumers must keep their support unqualified rather than invent a
generation. A changed root or build invalidates the corresponding observation.
Neither identity nor the serializer declaration alone is assignment admission.
The resident publishes its own `tracking_write_record_contract` through the
existing rendezvous alongside `resource_relationship` verb version 2. IPC
callers requiring incarnation checking must select that verb at `min_version=2`;
the operation's response independently confirms the checked incarnation and
actual executing writer. Direct CLI execution uses the same registered handler.
No monitor bootstrap or additional preflight process is required.

The executing CLI additionally advertises `resident_inspect: 1`. Only after
negotiating that declaration, callers may use `inspect --resident-only` with
an explicit `expected_creation_nonce`. This selector uses the selected
installation's existing authenticated resident transport and version-2 handler.
It never boots a monitor or substitutes the calling CLI's direct writer.
The live response must confirm the owner, handle, checked nonce and executing
writer contract. Success adds `resident`, containing the observed transport
`generation` and `runtime_generation`; discovery is re-read after the response,
and a changed endpoint, token, generation or selected installation refuses.
Missing, older, unavailable or incompatible residents remain unknown support.
The read leaves record bytes unchanged, including when inspecting an absent
request with `allow_missing`. It does not reserve a claim or grant assignment
readiness. The observation applies only to that selection; an assembling owner
must invalidate it when participants change and retain effect-side nonce checks.
Baseline syntax still uses the registered direct handler unchanged. A CLI
without this optional declaration must not receive the new selector; rollback
leaves baseline operations available but cannot qualify the resident.

This is provider-first expansion, not ordinary assignment activation or
whole-writer-cohort qualification. Older handlers reject the extra input field;
missing declarations or responses without checked-incarnation support are
unknown support, never permission to drop the requirement or use legacy claim
writes. Current serializers cannot prevent an unrelated, genuinely older
process from overwriting YAML with fields it does not understand. Qualify or
replace all participating ledger writers before enabling governed assignments;
one new CLI declaration or direct fallback is insufficient. Supported old
callers keep the unchanged baseline, while a provider rollback cannot satisfy
an incarnation-required assignment. These facts constrain assignment admission,
not authorized installer replacement or unrelated legacy operations.

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

The registered `resource_relationship` transaction uses the existing logged
direct RMW path and required sidecar locks, with provider I/O outside the lock
and exact revalidation afterward. Its version-1 baseline remains supported;
version 2 additionally checks the optional expected incarnation. Existing generic claim verb version 3
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
