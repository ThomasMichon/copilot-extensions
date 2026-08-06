# agent-leases

Distributed, advisory leases for remote execution resources. Each canonical
resource has one GitHub-compatible ref under
`refs/heads/copilot-leases/v1/<kind>/<base64url-key>`. Every transition appends a
synthetic empty-tree metadata commit whose parent is the exact observed head.
The commit OID is the fencing token; release appends a tombstone instead of
deleting or resetting the ref, preventing ABA.

## Configure

Create machine-local `~/.agent-leases/config.json`:

```json
{
  "schema_version": 1,
  "origin": "https://github.com/example/coordination.git",
  "default_ttl_seconds": 3600,
  "max_ttl_seconds": 86400,
  "clock_skew_seconds": 30,
  "acquire_retries": 3
}
```

The adoption settings key is **`origin`**. It may also be supplied through
`AGENT_LEASES_ORIGIN` or `--origin`. Use a repository where every participant
can fetch and push lease refs. Do not embed credentials in the URL.

## Use

```text
agent-leases acquire codespace example-cs --holder host/worktree/session --ttl 3600
agent-leases renew   codespace example-cs --token <current-oid> --ttl 3600
agent-leases release codespace example-cs --token <current-oid>
agent-leases inspect codespace example-cs --pretty
agent-leases list --kind codespace --pretty
```

`borrow` aliases `acquire`; `status` aliases `inspect`. Successful acquisition
and renewal return `lease_id`, `token`, `expires_at`, and `safe_deadline`.
Renewal and release require the exact current token and fail if compare-and-swap
loses.

## Safety model

Git provides advisory coordination, not backend enforcement. A cooperative
holder stops using the resource if renewal fails or its local clock reaches
`safe_deadline`. Immediately before a destructive backend mutation, re-inspect
and require the same fencing token. This cannot stop an out-of-band actor or a
partitioned stale holder, so resource backends should persist and reject stale
tokens whenever they can.

Acquisition can take over only after `expires_at + clock_skew_seconds`, uses a
new lease ID, and retries only bounded CAS races with jitter. All other CAS
failures surface immediately. Reads use `git ls-remote`; writes use an ephemeral
bare repository and
`--force-with-lease=<fully-qualified-ref>:<expected-oid>` (empty expected OID
asserts absence). Caller checkouts and shared remote-tracking refs are never
used for correctness. Malformed state fails closed.
