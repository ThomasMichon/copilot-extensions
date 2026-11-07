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

This is the receiver-expansion phase. Existing callers send no new arguments.
An upgraded receiver accepts old callers unchanged. A future caller requiring
no stale takeover must refuse that optional reuse operation when the capability
is missing, malformed or absent after provider rollback; it must not retry
ordinary acquisition as a substitute. Ordinary legacy operations remain usable.
No new service, coordination store or on-disk schema is introduced.
