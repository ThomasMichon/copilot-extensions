# Disposable resource producer API

This provider-neutral API reserves creator responsibility in the existing
worktree resource ledger **before** an integrated producer allocates anything.
It does not allocate, start, remove, or discover resources itself. It has no
Docker dependency, sibling installer, authentication bootstrap, or second ledger.
Reusable fleet borrowing/return and legacy claims keep their existing semantics.

Retained local relationship responsibility uses the separate
[resource-relationship API](resource-relationship.md), not this disposable
allocation/absence protocol. Both share protected ledger preservation;
their version/lifetime, callbacks and transaction semantics remain distinct.

The [owned-worktree provider](worktree-allocation.md) likewise shares protected
ledger storage, but retains actual child lifecycle responsibility rather than
disposable-resource absence or relationship retirement.

Provider callback commands may include a registered argument prefix, for example
`["agent-containers", "raw-docker"]`. Status and reclaim retain their own prefix
before `claim-status` or `claim-reclaim` and the resource identifier. Installation
cells use the receipt-validated primary peer module and completed runtime only;
a different payload command is unavailable rather than silently substituted or
invoked through legacy fallback.
Windows legacy batch callbacks use literal `call` routing so quoted payload
paths with spaces survive `cmd.exe /s /c`; command paths and registered prefixes
with shell metacharacters or control characters are refused.

A provider manifest may declare `no_self_provision_env`, naming the owning
launcher's uppercase `*_NO_SELFPROVISION` variable. Legacy callbacks set it to
`1` in the child environment only, preserving unrelated environment and CWD while
stripping caller installation routing and credentials. The provider must implement
that suppression contract; declaring a variable does not itself make an arbitrary
launcher safe. A missing runtime or refused callback remains unavailable, never
proof of resource absence. Integrated disposable providers must declare this
contract; existing fleet providers retain their borrowing semantics.

## Exact CLI ABI

```bash
agent-worktrees resource-producer capabilities --json
printf '%s' '{"kind":"container","namespace":"raw-resource","recovery":{"endpoint":"opaque"}}' |
  agent-worktrees resource-producer reserve --json --owner-ref machine/project/worktree
printf '%s' '{"token":"TOKEN_FROM_RESERVE"}' |
  agent-worktrees resource-producer begin --json --owner-ref machine/project/worktree
printf '%s' '{"token":"TOKEN_FROM_RESERVE","allocation_fence":{"generation":1,"key":"KEY_FROM_BEGIN"},"identity":{"immutable_id":"opaque"}}' |
  agent-worktrees resource-producer bind --json --owner-ref machine/project/worktree
printf '%s' '{"token":"TOKEN_FROM_RESERVE"}' |
  agent-worktrees resource-producer inspect --json --owner-ref machine/project/worktree
printf '%s' '{"token":"TOKEN_FROM_RESERVE"}' |
  agent-worktrees resource-producer close --json --owner-ref machine/project/worktree
```

`capabilities` needs no owner or stdin. Every other operation consumes exactly
one JSON object on stdin; extra keys, duplicate keys, extra objects, non-JSON
values, excessive nesting, and oversized input are refused. `--json` is required.
An explicit owner resolves through the owning project's registered configuration,
execution-space checks and record path. Otherwise the current worktree or a
supported singleton anchor is resolved by the existing owner resolver. Missing,
remote or unauthorized owners fail; terminal owners refuse mutations.
Reserve never reopens a finalized
record and never creates a guessed ledger. Session-qualified owner references
are normalized to their worktree record.
Read-only inspect and replay of an already-closed claim remain available for
terminal records with valid metadata and matching owner authority.
Archived tombstones, orphaned records and finalizing owners cannot receive new
reservations or accepted producer transfers.

All results carry `"schema":"copilot-extensions.resource-producer","version":1`.
Transactional success includes `ok`, current `owner`, original `creator`, `token`,
full `ref`, `phase`, and stored `producer` metadata. The read-only `capabilities`
response instead includes `operations`, `allocation_fencing`, `lifetime`,
`ref_encoding`, `max_ref_bytes`, and `max_input_bytes`; it resolves no owner and
has no transactional `ok` or claim fields. Errors include `error` and `message`
and exit nonzero. Reserve returns an unpredictable token only after its pending
claim is durably saved. Begin durably arms allocation and returns its private
`allocation_fence` only after publication. Bind replaces the allocating callback
ref with a bound ref;
the token remains the stable operation handle. Same-identity bind is idempotent;
different identity or a ref collision is refused without changing the pending
claim. Recovery without the private fence requires a registered provider probe.

## Read-only ownership views

```bash
agent-worktrees claims ownership --json
agent-worktrees claims ownership --owner-ref machine/project/worktree --json
agent-worktrees claims ownership --kind container --namespace raw-resource \
  --identity-json '{"immutable_id":"opaque"}' --json
```

`ownership` derives both owner-to-resources and exact-resource-to-recorded-owner
views from the existing ledger across **locally registered projects**. It does
not create a registry, load provider code, call a daemon, probe execution spaces,
bootstrap a runtime or write records. Legacy `claims owner` remains the separate
best-effort lookup for general claim references.

The version-1 `copilot-extensions.producer-ownership` result includes `complete`,
`issues`, `entries` and `resolution`. Entries retain the record owner, original
creator, generic state, phase, callback ref, handoff marker and producer metadata
(including token, recovery, allocation epoch, opaque identity and terminal
receipts when present). Pending and allocating responsibility remains visible.
Accepted transfers must form one connected, creator-rooted chain with matching
immutable metadata; closed receipts are history, not current ownership.

Filters apply **after** checking all observed records and transfer chains.
An identity filter compares the entire canonical JSON object, together with any
kind/namespace selectors; it is not a partial match or provider-specific alias
normalization. Identity keys are provider-defined; `immutable_id` above is only
an opaque example, not a required field or canonical schema.
Providers must choose an immutable physical identity independently
of reservation labels. Use all three selectors for one exact resource.

Unreadable/missing inputs, malformed raw claims or metadata, duplicate YAML keys,
duplicate tokens/references, duplicate bound identities, unresolved transfers,
remaining handoff markers and detected input changes make `complete` false.
The command exits 1 and suppresses all `current_owner` conclusions, even when
the uncertainty lies outside a requested owner filter. Invalid metadata is
represented by the existing wire sentinel, never silently dropped as an empty
ledger. Invalid command input exits 2.

**This is an observational, non-transactional local snapshot, never authority
to allocate, delete, settle or finalize.** File/directory stamp checks detect
ordinary concurrent changes but do not supply an atomic cross-record read.
`complete` means only that this local scan encountered no reported uncertainty;
each entry's execution space remains `recorded-not-probed`. It does not establish
provider availability or exclude owners in another machine, unregistered
project or execution space. No matching entries means `not-observed`, never
`unowned` or verified absence. `absence_proof` and `cleanup_authority` are always
false. Normal per-owner transactions and finalization gates remain authoritative.

## Allocation permission and completion

The integrated producer must follow **reserve, begin, create, bind, start**.
Successful `reserve` alone is not permission to create. Successful `begin`
changes phase to `allocating`; its generation/key pair authorizes exactly that
attempt. Do not allocate after a refused/unknown begin, and do not start after a
refused/unknown bind. An allocating claim cannot close, transfer or finalize.

Keep the returned fence private to the allocator. Only its key digest and
monotonically increasing generation are stored in `allocation_epoch`; inspect
does not return the key. Bind sends the returned fence along with the actual
immutable identity. A wrong key or an earlier generation cannot finish a later
attempt, even if the random-key source repeats.

After a definitively ended, unbound attempt, send:

```bash
printf '%s' '{"token":"TOKEN_FROM_RESERVE","allocation_fence":{"generation":1,"key":"KEY_FROM_BEGIN"}}' |
  agent-worktrees resource-producer complete --json --owner-ref machine/project/worktree
```

Completion returns to pending; it **does not** discharge ownership or prove
absence. Do not complete an attempt that can still allocate, has running
descendants, or has an uncertain outstanding creation request. Interrupted or
uncertain attempts stay allocating until authoritative recovery. A bound
identity cannot be erased through complete.

Each attempt's epoch remains in the callback ref after completion. Consequently,
a pending-to-allocating-to-pending cycle never reuses the subject of an old
absence probe. A retry begin also requires fresh exact-subject provider proof
that the reservation allocated nothing; completing with the key alone cannot
authorize a second untracked creation.

Without the fence, `bind` (`token` plus `identity`) and `complete` (`token` only)
are recovery operations, not allocation permission. They require the provider
quiescence contract below. A new unarmed reservation can close first, but that
revokes begin and forbids subsequent allocation.

Registered write API callers use `tracking_write.run_direct("resource_producer",
args, reason=...)`, with `args` containing `schema`, `version`, `operation`,
`input` (the same JSON object), `owner_ref`, and resolved `yaml_path`. Receiving
authority revalidates the record identity and shared owning-record resolver's
exact path under a
required sidecar `_RecordLock`. The CLI uses this registered direct RMW path to
avoid the resident monitor's shorter request deadline and does not boot a monitor.
Generic claim writes require verb version 3, so current callers never send
producer-unaware add/release/settle requests to a version-1/2 resident daemon.
Unrelated older-code full-record writers cannot understand the additive field;
upgrade all ledger-writing runtimes before enabling integrated producers.

## Metadata and callback protocol

Optional `producer` metadata on `ResourceClaim` contains:

```json
{
  "version": 1,
  "lifetime": "disposable-owned",
  "token": "opaque-unpredictable-token",
  "namespace": "raw-resource",
  "phase": "pending",
  "creator": "machine/project/worktree",
  "recovery": {}
}
```

Claim and status JSON projections preserve valid metadata and emit
`"producer":{"error":"invalid-producer-metadata"}` for invalid metadata.
This includes preserved YAML values such as timestamps, binary data, sets,
non-string keys, non-finite numbers and recursive aliases that cannot travel
as protocol JSON. The exact raw YAML remains in the ledger and remains an
unsettled obligation; the display sentinel is not authority to settle or
repair it.

Binding adds an immutable `identity` JSON object and changes phase to `bound`.
Recovery and identity are opaque to agent-worktrees; do not include credentials.
Opaque JSON is bounded to 32 nesting levels and 16,384 encoded input bytes.
CLI ingress reads at most that byte limit plus one overflow byte before strict
UTF-8 decoding; multibyte text cannot expand the read budget.
The complete callback ref is limited to 4,096 ASCII bytes, so recovery and identity
must be small enough that their combined encoded envelope fits.

The full ref is `namespace:r1.<base64url-without-padding(JSON)>`. Its canonical,
sorted, compact ASCII JSON envelope has exactly `version`, `token`, `creator`,
`phase` and `recovery`, with `allocation_epoch` added after begin and `identity`
added when bound. The epoch contains `generation` and `key_hash`, never the
private key. The namespace registry
receives only the bare `r1.<...>` positional argument through its existing
provenance-qualified, payload-local `claim-status` callback. No shell-unsafe
characters or command flags are introduced. `resource_producer.decode_ref`
validates the canonical version, types and size for provider authors.

Close never accepts caller-supplied absence evidence. The registered status
callback must return:

```json
{
  "exists": false,
  "producer_verification": {
    "version": 1,
    "subject": "EXACT_FULL_NAMESPACE_QUALIFIED_CLAIM_REF",
    "absent": true
  }
}
```

The registry must report the callback as available. A missing/unknown provider,
failed invocation, malformed output, wrong subject, unverified absence or
`exists:true` leaves ownership active and fails explicitly. The provider is
responsible for verifying authoritative endpoint, immutable identity and creator
evidence. Pending close requires proof that the reservation allocated nothing
across all its attempts, not merely the current generation.
The status probe runs outside the record lock; the final locked transaction
rechecks authority, handoff reservation, phase and exact epoch-bearing subject before storing
the callback receipt as `closeout`, setting phase `closed` and generic state
`released`. Repeating an already-confirmed close is a read-only no-op even after
its owner freezes or finalizes: no provider probe, save or event write occurs.
Frozen owners still cannot close a live claim. A closed claim
retains the exact pending/bound callback subject that was verified.

### Lost-fence recovery

Ordinary absence alone cannot seal an allocating attempt. Recovery complete
requires exact verified absence **and** `producer_verification.allocation_finished`
equal to the boolean `true`. Recovery bind requires `exists:true`, that same
quiescence assertion, and `producer_verification.identity` equal to the actual
immutable identity supplied to bind. Both proofs use version 1 and the exact
current allocating/pending subject. The final RMW rejects a changed subject.

The provider must authoritatively establish that the attempt cannot allocate
again; it must not infer quiescence from absence, caller assertions, a timeout
or a worker exit alone. Missing evidence retains the active obligation. Recovery
complete retains its callback receipt as `allocation_completion`, then still
requires the ordinary verified-absence close. Recovery bind remains bound and
owned. Repeated recovery cannot resurrect a closed or transferred claim.

## Finalization, repairs and transfer

Pending/allocating/bound producer claims block initial validation and final locked cleanup
even if their generic state is corrupt, gate mode is off/warn, their kind is
`session`, or `--abandon --handoff-to` is requested. Malformed/unknown producer
metadata, including an explicitly present null, is preserved and blocking.
Only trusted producer/acceptance transactions can change producer metadata.
The full-record save chokepoint preserves producer claims across stale saves,
generic disconnect/handoff repairs, metadata removal and fabricated closeout.
Generic add-overwrite, release/remove, settle, gone/safe sweep, at-rest
reconciliation and release-all/finalize cascades cannot discharge them.

Pending and allocating claims cannot be offered or transferred. Valid bound claims may use
`claims handoff offer` and the consumer's explicit `claims handoff accept`.
For this ABI, producer bundles are separate from legacy claims and source and
consumer must be in the same local execution space. Cross-machine and mixed
bundles are explicitly refused, not implicitly released. Recipient persistence
comes first; interrupted acceptance retains either the source obligation or the
active recipient obligation (often both until retry completes). The source
keeps a `transferred` tombstone with accepted bundle/source/consumer evidence.
The recipient remains bound and active, preserves the original creator and
opaque ref/identity, and becomes the current owner by its record. Immutable
born-at-creation provider evidence can therefore survive ownership transfer.
Retrying acceptance repairs the same bundle, never allocates a replacement.
Incoming tokens must be unique, and a recipient cannot already hold another ref
with the same token. Rejection preserves both records and the bundle registry.
Once a bundle is accepted, marker repair validates only the recipient; a reaped
source is no longer a dependency. Closed/transferred recipient metadata and
onward handoff reservations are preserved rather than reactivated.
