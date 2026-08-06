---
name: leasing-remote-resources
description: >
  Install, configure, and operate distributed advisory leases for remote
  execution resources through agent-leases. Use for cross-machine borrowing of
  remote machines, CodeSpaces, remote worktrees, containers, or similar
  resources; for lease acquire, renew, release, fencing-token validation, or
  Git-ref lease setup.
  Trigger phrases include:
  - 'distributed lease'
  - 'lease a remote resource'
  - 'borrow a remote machine'
  - 'cross-machine codespace lease'
  - 'agent-leases'
  - 'fencing token'
  - 'configure lease origin'
---

# Leasing remote resources

`agent-leases` coordinates clients through append-only metadata commits on one
ref per resource. It is generic: choose a stable kind (`machine`, `codespace`,
`worktree`, `container`) and the backend's canonical resource key.

## Install or update

From this plugin's source directory:

```bash
./scripts/install.sh update
```

```powershell
.\scripts\install.ps1 update
```

## Adopt

Enable `agent-leases@copilot-extensions`, then create the machine-local file
`~/.agent-leases/config.json`. The required adoption settings key is `origin`,
pointing to a Git repository all participants may fetch and push:

```json
{"schema_version":1,"origin":"https://github.com/example/coordination.git"}
```

Do not embed credentials. The default namespace is
`refs/heads/copilot-leases/v1`, which GitHub accepts as ordinary branch refs.

## Operate

```bash
agent-leases acquire codespace <name> --holder <opaque-client-id> --ttl 3600
agent-leases renew codespace <name> --token <current-oid> --ttl 3600
agent-leases release codespace <name> --token <current-oid>
agent-leases inspect codespace <name> --pretty
agent-leases list --kind codespace --pretty
```

Persist both `lease_id` and `token`. Treat the returned commit OID as the fencing
token. Stop using the resource if renewal fails or `safe_deadline` passes.
Re-inspect and verify the same token immediately before destructive backend
changes. Git coordination is advisory: it cannot fence an out-of-band actor or
a partitioned stale holder.

Acquisition alone retries bounded CAS races. Never retry renew/release as
success, never overwrite malformed state, and never delete/reset a resource ref.
