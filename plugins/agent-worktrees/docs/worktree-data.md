# Owner-managed worktree data

Worktree data belongs to the existing installation-aware project-data owner,
outside disposable checkouts. A bounded descriptor gives later sessions,
Manager and archival readers one discovery boundary instead of separate path
maps. It is not a claims ledger, session registry or permission to resume work.

The source boundary is `worktree-data-v1`, independently versioned from package
versions and the existing agenda projection. Use the selected installation's
attributed command. Consumers must establish its actual capability/parser
support before emitting new syntax; an older provider is unsupported, not an
invitation to provision a runtime or search the filesystem.

```text
agent-worktrees --project PROJECT worktree-data-read --capabilities --json
agent-worktrees --project PROJECT worktree-data-read \
  --worktree FULL_ID --store-id STORE_ID --contract-version 1 --json
agent-worktrees --project PROJECT worktree-data-allocate \
  --worktree FULL_ID --creation-nonce INCARNATION \
  --domain agendas --lane main --store-id STORE_ID --contract-version 1 --json
```

## Discovery and identity

`worktree-data-read` performs no allocation, host ensure or registration. Its
payload dispatchers refuse a missing runtime before publication or provisioning.
Reads select the exact project and full worktree ID. A store ID selects its
registered placement without requiring the caller to remember a lane or path;
omitting it requests that worktree's bounded inventory.

Optional creation-nonce and placement-generation expectations reject a different
incarnation or placement. Repository, project and installation scope remain
owner-qualified: a canonical record or retained descriptor cannot substitute
another project's repository for the selected project. A descriptor can be
retained after checkout retirement without a session or live checkout. An
existing canonical record must still agree with its incarnation; missing
canonical identity does not create live authority.

The JSON response distinguishes a registered placement from genuinely absent
registration. Malformed, unreadable, partial or unsupported state is an error,
never an empty successful inventory. The point-response bound is 128 KiB;
overflow refuses rather than clipping a complete-looking descriptor set.

## Explicit allocation

`worktree-data-allocate` is a separate mutation for a new empty home. It requires
the canonical full worktree identity and creation nonce. This source slice
admits only the `agendas` domain and a 32-lowercase-hex store identity; arbitrary
runtime material and credentials are not declared managed collections.

Allocation rechecks the canonical record under its existing required sidecar
lock. An exact retry is idempotent. Conflicting store/incarnation identity,
unsafe child paths and partial or foreign existing homes refuse without
overwriting or adopting their contents. At most 32 placements and a 64 KiB
descriptor are admitted through this boundary.

Success follows flushed descriptor contents, atomic publication and directory
barriers for the placement and its ancestry. Windows uses native write-through
replacement and directory flushing; exact retries reflush the retained
registration before acknowledgment. A filesystem or access policy that cannot
provide those barriers returns `durability-unavailable`, not successful
allocation with a weaker guarantee. Passive discovery does not require writes.

The focused `tests/test_worktree_data_store.py` packet includes publication-order
fault injection and a native Windows allocation/retry control. Linux API-double
coverage does not qualify native Windows or hardware power-loss behavior.

An allocated home is not an initialized or validated agenda store. Efforts owns
agenda initialization, transactions and complete consistent export. A later
consumer must match the actual store identity through its owning reader; a
descriptor alone cannot validate store contents or authenticate current work.

## Lifetime and remaining integrations

Discovery reuses the admitted project-data root, including its supported root
aliases. Child traversal and aliasing cannot redirect a registered placement.
No new global registry, listener, fleet scan or consumer-local locator cache is
introduced.

This boundary does not relocate an active lane, create a second writer, export
data, start logging, collect an archive or delete retained data. Checkout
retirement and persistent-data reclamation remain separate operations. Existing
cleanup, resource and effort gates are unchanged.

The [reviewed lifetime and archival design](../../../efforts/active/mux-companion-manual-cutover-diagnostics/worktree-state-lifetime.md)
retains the independent efforts, Manager, logger, rescue and operational
qualification gates. Source support is not installed adoption or rollout.
