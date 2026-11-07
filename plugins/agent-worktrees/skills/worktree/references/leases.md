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
