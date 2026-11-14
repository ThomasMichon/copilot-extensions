# Managed command-emitter creation

An opted-in JSON command emitter can supply an existing managed-producer grant
at its task-authoring boundary. The domain's discovery command stays
credential-free; grant issuance and storage remain with the existing
[producer-scope owner](../README.md#producer-creation-fences).
No scope is created or handed off by this adapter.

```yaml
id: review-source
command: [source-reader, discover, --occurrence-ids]
task_output: json
interval_seconds: 60
evaluator_ref: source-evaluator
managed_producer:
  grant_command: [credential-owner, read-review-producer-grant]
  timeout_seconds: 30
```

`grant_command` is a trusted configuration argument list, not a command supplied
by task output. Its stdout must be the original stored successful
`producer-fence handoff` response: `scope`, `active_producer`,
`current_generation` and the one-time `producer_capability`. It must **read**
that existing grant, never run another handoff each tick. The emitter does not
recover a lost capability from status. Resolver stderr/stdout and argv are not
included in failure diagnostics. The timeout defaults to 30 seconds and is
bounded by 300. Only nonempty JSON task output resolves the grant.
Supplier-local executable and script operands are qualified through the existing
registered-resource owner before persistence, so moving a registered supplier
relocates the grant reader along with discovery. Other command arguments retain
their literal meaning; external credential-store paths remain their owner's
configuration.

The grant's producer id must equal the emitter's `id`, and its scope source must
equal the configured `source` (default `emitter`). Each task's canonical repo
must match the grant scope. The current coordinator scope must still select
that producer and generation. Grant fields in the source output are rejected.
The existing managed-create transaction independently verifies the capability,
generation, required label, request ledger and dedup conflict before insertion.
Results and creation receipts never include the capability.

Each emitted row includes the existing `producer_request_id` field as well as a
nonempty `dedup_key`. The former identifies a domain occurrence, not merely a
forever-reused object: retries of an uncertain create retain it, and a genuinely
new episode after terminal release uses a new id. The emitter does not invent
an occurrence from wall-clock time or a dedup key. Keep the original creation
bytes stable while an uncertain request is being reconciled.

Before creating, the adapter uses the existing exact repo/dedup lookup. A live
occupant must retain matching producer scope, id, generation, source, origin
and evaluator. It is returned as an observation of that original task, without
reposting or changing its accepted request even if discovery has a newer
candidate. A foreign or ambiguous occupant is refused, not adopted, reset or
replaced. This observation is not fresh capability authentication or a new
verified creation receipt. Periodic enrollment still independently verifies the
original coordinator request ledger. Candidate steering remains with the
domain's existing original-owner path.

Lookup and insertion are not one transaction. If a competing create wins
between them, the existing managed-create transaction returns its normal exact
request replay or dedup refusal. The adapter does not blindly replay a mutation
after an unknown outcome. A later source invocation can observe an accepted live
occupant, while a changed request after terminal release must still respect the
original request ledger.

## Compatibility and adoption

Absence of `managed_producer` preserves previous command-emitter behavior and
wire shape. The same optional authoring path serves periodic ticks and
registered side-loads. An old receiver missing producer status or exact dedup
lookup refuses before this adapter posts. A receiver supporting both continues
to enforce its existing managed-create schema; no new RPC is introduced.

Publish this adapter before enabling its configuration and source occurrence
output. Old emitter readers can ignore unknown spec properties; that does not
prove this carrier ran. The coordinator's already-managed scope must remain
the enforcement boundary and reject unfenced creates. Do not remove that fence
to make a rolled-back emitter work. Disable the caller rather than minting a
new grant, dropping its fields or retrying as an unmanaged source.

Configuration and grants are not deployed or migrated by this source. Enabling
[periodic relevance](periodic-relevance.md), production enrollment and original
agent/workspace closeout remain separate gates.
