# Disposable resource producer API

This provider-neutral API reserves creator responsibility in the existing
worktree resource ledger **before** an integrated producer allocates anything.
It does not allocate, start, remove, or discover resources itself. It has no
Docker dependency, sibling installer, authentication bootstrap, or second ledger.
Reusable fleet borrowing/return and legacy claims keep their existing semantics.

## Exact CLI ABI

```bash
agent-worktrees resource-producer capabilities --json
printf '%s' '{"kind":"container","namespace":"raw-resource","recovery":{"endpoint":"opaque"}}' |
  agent-worktrees resource-producer reserve --json --owner-ref machine/project/worktree
printf '%s' '{"token":"TOKEN_FROM_RESERVE","identity":{"immutable_id":"opaque"}}' |
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
remote, unauthorized or terminal owners fail; reserve never reopens a finalized
record and never creates a guessed ledger. Session-qualified owner references
are normalized to their worktree record.
Read-only inspect remains available for terminal records with valid metadata.

All results carry `"schema":"copilot-extensions.resource-producer","version":1`.
Success includes `ok`, current `owner`, original `creator`, `token`, full `ref`,
`phase`, and stored `producer` metadata. Errors include `error` and `message`
and exit nonzero. Reserve returns an unpredictable token only after its pending
claim is durably saved. Bind replaces the pending callback ref with a bound ref;
the token remains the stable operation handle. Same-identity bind is idempotent;
different identity or a ref collision is refused without changing the pending
claim or contacting a provider.

Registered write API callers use `tracking_write.run_direct("resource_producer",
args, reason=...)`, with `args` containing `schema`, `version`, `operation`,
`input` (the same JSON object), `owner_ref`, and resolved `yaml_path`. Receiving
authority revalidates the record identity and owning-project path under a
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

Binding adds an immutable `identity` JSON object and changes phase to `bound`.
Recovery and identity are opaque to agent-worktrees; do not include credentials.
Opaque JSON is bounded to 32 nesting levels and 16,384 encoded input bytes.
The complete callback ref is limited to 4,096 ASCII bytes, so recovery and identity
must be small enough that their combined encoded envelope fits.

The full ref is `namespace:r1.<base64url-without-padding(JSON)>`. Its canonical,
sorted, compact ASCII JSON envelope has exactly `version`, `token`, `creator`,
`phase` and `recovery`, with `identity` added when bound. The namespace registry
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
evidence. Pending close requires proof that the reservation allocated nothing.
The status probe runs outside the record lock; the final locked transaction
rechecks authority, handoff reservation, phase and exact subject before storing
the callback receipt as `closeout`, setting phase `closed` and generic state
`released`. Repeating an already-confirmed close is a no-op. A closed claim
retains the exact pending/bound callback subject that was verified.

## Finalization, repairs and transfer

Pending/bound producer claims block initial validation and final locked cleanup
even if their generic state is corrupt, gate mode is off/warn, their kind is
`session`, or `--abandon --handoff-to` is requested. Malformed/unknown producer
metadata, including an explicitly present null, is preserved and blocking.
Only trusted producer/acceptance transactions can change producer metadata.
The full-record save chokepoint preserves producer claims across stale saves,
generic disconnect/handoff repairs, metadata removal and fabricated closeout.
Generic add-overwrite, release/remove, settle, gone/safe sweep, at-rest
reconciliation and release-all/finalize cascades cannot discharge them.

Pending claims cannot be offered or transferred. Valid bound claims may use
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
