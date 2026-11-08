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
