# Retained terminal-reader transition

Review-first T3 refinement of [coordinated terminal retention](terminal-retention.md),
coordinated by [#13487](https://github.com/ThomasMichon/copilot-extensions/issues/13487)
under the [canonical effort](README.md). This proposes source behavior, not
permission to migrate an installed volume or stop its owner.

## Gap and existing owners

The standalone workspace host already supports explicit fresh terminal-reader
provisioning. `serve` deliberately validates retained state without adopting
extensions, and `provision` refuses a populated root. Retained baseline volumes
therefore have no supported host-level transition to the terminal reader.
Removing either refusal would turn ordinary startup into implicit migration.

Reuse `HostConfig`, the service's `agent-pull-requests-watch` `SingleInstance`
lease and the existing ReplayStore adoption transactions. This closes the
[bounded terminal-conversation intent](../../../visions/plugins/agent-pull-requests/README.md#bounded-conversation-and-terminal-tombstones)
within the [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md).
It adds no migration daemon, second state ledger or remote operation.

## Explicit transition

Add a local `adopt-terminal --config <absolute-config>` command to the
standalone service CLI. It requires the already supported explicit terminal
workspace selection and a populated, valid, service-bound conversation store.
It is not a substitute for fresh provisioning and does not create missing
replay/cache state, adopt a different namespace/service identity, change a
credential or reinterpret an unsupported layout.

The command acquires the same host-local writer lease used by `WatchService`.
A running or incompletely drained service retains that lease, so transition
refuses rather than stopping it, waiting indefinitely or treating passive
readiness as reader exclusion. Preserve the existing single-host storage
assumption: this is not a cross-host or arbitrary external-reader fence.
The deployment owner must separately exclude unsupported direct volume users.
No new source-side attestation service is required.

Before any durable adoption, validate the selected core declarations, original
service/namespace binding, supported retained envelope, existing cache and
selection compatibility. A terminal-only transition must not silently strip
prepared intents, acquired mappings, selections or any other adopted extension.
Unsupported combinations refuse before the first change. Existing terminal
mapping selection may compose only its already supported prerequisites.

Use the original command's finite operation deadline and PID/held-lease
checkpoint at every existing adoption boundary. Select the same terminal
retention, recovery and cache-retirement contracts and bounded capacity used
by fresh provisioning, with optional terminal mappings when explicitly
selected. Preserve existing conversation records, replay registrations,
request/event IDs, deadlines, delivery/ACK positions, original prepared
operations/receipts and all cache pointers/objects.

The transition enables capable readers; it does not observe a provider,
create a terminal observation, compact an operation, retire cache content,
settle a delivery or send a write. In particular, adding terminal storage
cannot manufacture authoritative terminal evidence for an existing generation.

## Interrupted adoption and skew

The existing adoption owners commit complete envelopes and recognize their
already-adopted contracts. A failure may leave a valid partially expanded
envelope; report that outcome, retain its bytes and release the lease only
after synchronous work is settled. Do not promise multi-step rollback.
An uncertain replace requires readback through that same owner before a later
step; a failed invocation is not proof of no change.

An explicit retry over the same selected volume revalidates the actual
persisted contracts and capacity and continues missing compatible adoption
steps under a newly admitted finite invocation. It does not reprovision,
reset identities, strip extensions or silently substitute another selection.
Unsupported/corrupt state remains an explicit refusal, not an invitation to
repair or delete it.

`serve` and `provision` retain their existing defaults and refusals. Older
strict readers must refuse expanded storage without rewriting it. A selected
new reader validates the adopted extensions before advertising optional
support. Removing the selection or rolling back to an incompatible reader
refuses rather than downgrading the volume. There is no effectful new-command
probe followed by a legacy provisioning fallback.

## Serial delivery and acceptance

- [ ] Review this contract before implementing the command or changing retained
  host adoption.
- [ ] Implement the local existing-owner transition without changing baseline
  provisioning, serving, remote workspace contracts or physical retirement.
- [ ] Qualify a populated baseline volume, preserving original replay/cache and
  unknown-effect identities through transition and fresh-reader restart.
- [ ] Cover actual competing service/transition lease ownership, failed drain,
  operation expiry, unsupported core/layout/binding/capacity, partial adoption,
  uncertain replacement and explicit retry. Refusal before adoption must
  preserve bytes; partial progress must remain complete and recoverable.
- [ ] Exercise the actual CLI and old/new reader directions, optional mapping
  selection and rollback refusal. Confirm no native acquisition, protected
  send, cache deletion or delivery/ACK advancement.
- [ ] Return to full T3 and both parent acceptance plans. Source controls do not
  qualify installed migration, every provider, whole-system retention or a
  production operation.

## See Also

- [Standalone terminal host](../../../services/pr/README.md#optional-terminal-read-hosting)
- [Coordinated terminal retention](terminal-retention.md)
- [Canonical effort](README.md)
