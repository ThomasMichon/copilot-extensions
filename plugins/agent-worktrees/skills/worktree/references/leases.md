# Worktree — Resource Leases

Full detail for the payload-local `lease` operation, the harness's one
atomic primitive for exclusive, cross-machine access to any scarce shared
resource — a CodeSpace, a cross-repo worktree, a container, a bridge
session — so two agents on two machines never collide.

It is **ref shenanigans only** in an explicitly selected private state
repo: hidden `refs/agent-worktrees/leases/v1/<kind>/<key>` refs updated by
atomic compare-and-swap (`--force-with-lease`), no branches, no commits, no
service, no new credential. Each transition appends a synthetic empty-tree
metadata commit whose **OID is the fencing token**; release appends a
**tombstone** (ABA-safe); every read strictly validates linear history.

```bash
<agent-worktrees catalog argv[0]> lease acquire <kind> <key> --holder <ref> [--ttl N]  # atomic CAS
<agent-worktrees catalog argv[0]> lease renew   <kind> <key> --token <oid> [--ttl N]   # keep the grip
<agent-worktrees catalog argv[0]> lease release <kind> <key> --token <oid>             # tombstone
<agent-worktrees catalog argv[0]> lease inspect <kind> <key>                           # current record
<agent-worktrees catalog argv[0]> lease list [--kind <kind>]                           # fabric-wide view
```

- **Holder** = the qualified **ClaimRef** (`machine/project/worktree_id[#session]`),
  from `<agent-worktrees catalog argv[0]> get owner-ref` — directly resolvable for stale-takeover.
- **Store origin** = the resolved lease store repo, from
  `<agent-worktrees catalog argv[0]> get lease-origin` (the `AGENT_WORKTREES_LEASE_ORIGIN` override,
  else the bound control-plane/knowledge repo's origin). The current project's
  source remote is never an implicit fallback. Every agent of one harness
  resolves the **same** explicitly selected origin — so coordination is
  **same-harness-scoped by construction**. Set `AGENT_WORKTREES_LEASE_ORIGIN`
  only to a private state remote when no knowledge repo is bound.
- **Two-tier consumers.** `agent-codespaces` uses this as the cross-machine **L2**
  authority behind its host-local **L1** claim (see `agent-codespaces:borrowing-codespaces`); a
  live claim on another machine raises a `ClaimConflict` naming the remote holder.
- **Degrade-safe.** Only a definitive lease conflict (exit 3) blocks consumers
  that support best-effort operation; a missing private store never falls back
  to publishing refs on the source remote.

## Acquisition without stale takeover

`lease acquire` and its `borrow` alias accept `--released-only`. This mode
acquires only an absent resource or an explicit release tombstone. A live or
expired `leased` record refuses with exit 3, retains the existing holder/token,
and does not become available merely because its TTL elapsed or its diagnostic
disposition is `at-rest`. Every bounded CAS retry rechecks the same condition.
Ordinary acquisition without the flag retains its existing stale-takeover
behavior, output schema and defaults. Neither mode proves that work is safe,
releases another owner's responsibility or authorizes provider effects.

For independently installed consumers, the existing
`--project <owner-project> coordination-readiness` response advertises
`lease-acquire-released-only-v1` in its additive `capabilities` array. Read that
response from the exact selected runtime before emitting the new flag.
The capability reports receiver support, not permission: `ready` must still
pass and existing resource, identity and lifecycle gates still apply.

An upgraded receiver accepts old callers unchanged. A caller requiring
no stale takeover must refuse that optional reuse operation when the capability
is missing, malformed or absent after provider rollback; it must not retry
ordinary acquisition as a substitute. Ordinary legacy operations remain usable.
The acquisition mode introduces no new service, coordination store or schema.

## Generation-qualified return evidence

The receiver supports optional `lease release <kind> <key> --token <oid>
--retain-return-proof`. Discover `lease-return-proof-v1` in the exact selected
runtime's existing readiness response before sending this flag. Receiver
support is not permission to return a resource or settle another owner's ledger.
Codespaces caller migration remains separate; old callers keep their syntax.

An opt-in release upgrades only that resource's payload to strict
`schema_version: 2`, within the existing envelope and resource ref. Normal
version-1 records and their output remain unchanged. Version 2 adds
`returned_generations`, an ordered array of at most 16 objects containing the
returned `lease_id`, `holder` and `released_at`. The generation ID stays stable
through renewal; the fencing OID does not. Save the original generation and
qualified owner before return if later reconciliation will need them.

Only a successful release appends evidence. Subsequent acquisition, renewal,
transfer and ordinary release preserve the bounded entries; the oldest entry
is evicted when necessary. Expiry, takeover, transfer, ledger retirement and a
successor's state cannot create a return for the original generation. Existing
`lease inspect` exposes the validated current payload; no new query, history
scan, receipt store or daemon is needed.

Native stale-history compaction carries the evidence into its equivalent
two-commit chain, independent of the discarded objects. Missing, evicted,
malformed or legacy evidence is unknown, not proof of return. A receipt proves
only that generation's shared release, not original-owner ledger settlement,
local cleanup, provider readiness or authority over a successor.

Once opted in, a resource cannot silently downgrade its payload or discard
evidence. Upgraded receivers accept both versions and ordinary old syntax.
An older version-1-only receiver refuses the opted-in resource on rollback
rather than overwriting it; unaffected version-1 resources remain usable.
Upgrade the selected receiver to resume that resource. Do not retry an
effectful new invocation with older syntax merely because its result is unknown.

## Permanent service custody receiver

The resource owner can also **read**, but cannot yet publish, the distinct
`agent-service-custody-envelope-v1` envelope in that **same** kind/key ref.
`lease inspect`, `status` and `list` report `state: service-owned`,
`custody_type: service`, its current commit `token`, and bounded service identity
and acceptance-digest fields. They do not invent a task holder, TTL, expiry or
renewal deadline. The digests identify retained evidence; parsing them is not
verification of provider authorization or proof of actual adoption.

Mixed histories retain every task predecessor. An accepted service record must
name its actual Git parent, follow a leased task record and have an acceptance
timestamp within that parent's safe interval. The receiver rejects root service
records, malformed/unknown envelopes and any successor to service custody in
this protocol generation. Retirement is not implemented.

The bounded `acceptance_margin_seconds` freezes the admission-time clock policy
for the adoption boundary and task prefixes not already frozen by an effect. Reading the
same permanent record with another client's clock-skew configuration cannot
invalidate it. This margin is historical validation metadata, not a custody
TTL or authority: a future admitted writer must record its actual selected
canonical policy, never accept a caller's margin as permission to adopt.

Task acquire/borrow, renew, transfer and release refuse permanent custody.
The CLI returns exit **3** with `code: service_custody_conflict` for task
mutations; elapsed time or using the former task's holder/token cannot override
it. Task-only Python `inspect`/`status`/`list` raise `ServiceCustodyConflict`
(a `LeaseLost` subtype); explicit mixed inventory uses
`inspect_resource`/`list_resources`. Released-task history compaction skips
service records without rewriting their predecessors.

**Receiver support is not a live adoption capability.** No service writer or
CLI adoption/retirement verb is enabled. Before any writer can be introduced,
the selected running provider/controller generations must enforce custody,
serialize outstanding effects with adoption and refuse incompatible legacy,
forced and rollback paths. An older parser's rejection alone does not protect
a provider whose unavailable-coordination path permits local borrowing.
Gateway outage, config deletion and agent finalization must never become
authority to release service custody. Those provider/effect and installed-peer
qualification gates remain open in the
[custody implementation plan](../../../../../efforts/active/linux-role-fleet/runner-service-custody.md).

## Provider-effect responsibility

The canonical owner also recognizes `agent-provider-effect-envelope-v1` in the
same resource ref. This is a durable in-flight claim, not a second store or a
native platform fencing token. Its explicit `effect-unsettled` state has no TTL.
An exact task token is consumed by CAS before the provider attempts the effect.
Concurrent task changes or adoption can win that slot instead; the losing
provider must not execute. While the claim is unsettled, task acquisition,
renewal, transfer, release and service adoption all refuse, even after the
original task expires or a caller/controller restarts.

The source-level `GitLeaseStore.begin_effect` API is for an already-authorized
provider embedding. It requires the exact live task fence, a unique attempt ID,
provider/immutable-resource and native-owner identities, a supported action and
the exact request digest. Those fields are bindings, not authentication.
The owner records its actual configured admission margin; callers cannot supply
a weaker one. Admission checks the unrounded clock before serializing a
whole-second timestamp, so rounding cannot extend the safe interval.
This generation does not authorize effects under service custody.
No effect-start or settlement CLI verb is enabled.

Each effect freezes its preceding task prefix with that effect's recorded
admission policy. Restarted inspection and settlement cannot invalidate that
prefix by choosing another clock-skew margin. Successive effects retain their
own prefix policies; task transitions after the last effect still use ordinary
current task-admission rules until a later effect or adoption freezes them.

`settle_effect` accepts only the current claim token and original attempt/owner.
The embedding must verify actual native/backend termination or non-start before
supplying its terminal outcome and retained settlement-evidence digest.
Subprocess return, a cancelled waiter, an elapsed deadline or an apparently
absent controller is not necessarily backend settlement. An uncertain effect
stays unsettled for its original owning recovery. Digests do not prove
termination, and reconstructing these Python records grants no authority.

Settlement appends evidence while preserving the original task record,
generation and expiry; it does not fabricate a task renewal or release.
The settlement commit becomes the current fencing token. `inspect_effect`
recovers that original attempt after a lost reply and after later task changes;
never repeat an effect because its reply was lost. Histories containing effects
are excluded from stale-task compaction so their responsibilities and receipts
remain recoverable.

Mixed CLI inventory reports unsettled claims as `custody_type: provider-effect`
with their actual token and bounded bindings, without task availability fields.
Task-only operations raise `ProviderEffectConflict` (a `LeaseLost` subtype);
the CLI returns exit **3**, `code: provider_effect_conflict`, and no success
output. Malformed histories and unsupported envelopes remain explicit errors,
not absence.

**Expansion only:** old task syntax and ordinary task semantics are unchanged.
This source API and receiver envelope do not establish that an independently
installed provider uses them. Older receivers reject an effect envelope; that
alone is insufficient when a provider falls back to local/forced operation.
Provider migration must select a qualified executing generation and enforce
admission internally on every managed effect, including outage and rollback
paths, before enabling a custody writer. Current/prior installed-provider
qualification and service-effect authorization remain open; no package-version
comparison or source merge substitutes for them.
