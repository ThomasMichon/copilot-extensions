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
agent-worktrees --project PROJECT worktree-data-read --inventory --json
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

### Project-wide retained discovery

The optional `worktree-data-project-inventory-v1` capability admits
`worktree-data-read --inventory`. Establish support through the **selected
runtime's** existing `--capabilities` operation: its additive
`optional_capabilities` list must contain that exact capability. The original
`capabilities: ["worktree-data-v1"]` remains unchanged for existing clients.
An absent optional declaration means project inventory is unsupported; it does
not disable the original exact reads or permit a consumer filesystem crawl.
No existing syntax is retired, and callers must repeat support selection after
a provider rollback rather than send the new flag to an older parser.

Inventory enumerates only the owner's retained registration directory, not the
canonical worktree list. It therefore includes descriptors whose checkout and
tracking record are gone, together with every registered store and its existing
incarnation, placement, scope and qualification fields. The response retains
the `agent-worktrees.worktree-data` envelope at version 1, names the optional
capability, and places `project`, `repository_identity`, `scope`, `worktrees`
and `live_authority` in `data`. `repository_identity` is the project owner's
normalized repository remote (for example `network:example.com/owner/repo`),
not a project alias or a caller assertion. The stored descriptor's strict
version-1 schema is unchanged.

At most 128 worktree registrations and a 1 MiB UTF-8 response are admitted.
`inventory-limit` refuses overflow without emitting a partial success.
Invalid, unsafe, partial or unreadable registrations likewise refuse the
whole inventory; an empty list means no registered descriptors were observed,
not that failed entries were omitted. A missing project-data root yields an
empty inventory with `scope: null`, still requiring a resolvable repository.
In an installation cell, the repository identity receipt must also validate
before an absent state root can be reported as empty. Missing, corrupt or
foreign receipts remain errors; this read does not enroll or create the root.
The original exact-read operation still requires its admitted cell state root.

Root, repository/installation scope, namespace membership, descriptor and
canonical-record metadata, and lane directory identities are rechecked before
return. An observed registration change refuses the read; it is not permission
to repair, initialize or retry a mutation. This is a bounded passive
observation, not a transactional export across stores or a freshness lease.
Domain owners must still export and verify each selected store at its own
revision. Reads take no record locks, ensure no runtime and never traverse
domain-store content. No consumer or scheduled collection is enabled merely
by the provider capability.

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
Returned homes use the canonical spelling of the admitted root without
resolving unsafe child aliases. Descriptor bytes, identity and placement
generation do not change merely because the root has an admitted alias.
No new global registry, listener, fleet scan or consumer-local locator cache is
introduced.

This boundary does not relocate an active lane, create a second writer, export
data, start logging, collect an archive or delete retained data. Checkout
retirement and persistent-data reclamation remain separate operations. Existing
resource and effort gates remain in force.

### Checkout-local agenda preservation

The documented standalone fallback `.effort-agenda/<lane>/` lives inside the
disposable checkout. Git cleanliness, an ignored file, a merged PR or finalized
tracking status cannot establish that its contents are disposable.

Finalize may complete the source objective while retaining a checkout and branch
that contain `.effort-agenda`. Cleanup reports `retained-data`; ordinary and forced
managed removal, the owner's Git-removal helper and directory-removal fallbacks refuse
before their destructive steps. Presence is conservative, including an empty,
partial, malformed or linked home. An inspection error is unknown, not absence.
Batch reports name the hold. A validated finalized display remains distinct from
blocked deletion eligibility; its descriptor round trip does not turn the hold
into an unsafe `FINAL` action or an unsupported-looking result.
The guard probes only this documented fallback with `lstat`; it opens no database,
follows no agenda link and performs no recursive search for arbitrary custom homes.
Boundary rechecks cover late state before fallback/retry removal, but do not claim
a filesystem transaction against an independently running external writer.

Managed data and descriptors already outside the checkout survive ordinary
checkout retirement. Their preservation does not require an archive destination
to be online, nor does checkout completion authorize persistent-data reclamation.
No archive receipt, age, force option or successful copy bypasses the legacy hold.

For an existing checkout-local lane, retain the checkout and identify its actual
state home, format and writer first. With separately applicable effect authority,
use that owner's quiescence and
[complete consistent export/readback](../../efforts/skills/driving-effort-agendas/references/agenda-export.md)
procedure to an admitted destination outside the checkout. Preserve the original
identity, accepted records and uncertainty; a new initialized store is not rescue.
The current exporter supports context-qualified store format 2, not legacy manual
JSON or older store formats. Unsupported or incomplete rescue leaves the source
and containing checkout retained.

This slice provides no automatic relocation, descriptor rebinding, writer switch,
source deletion or restore command. Only the data owner may reconcile a supported
rescue and explicitly retire its original source under the applicable permission
and retention gates. Arbitrary custom checkout-local homes remain the adopter's
explicit preservation responsibility; their absence from this bounded convention
check is not a safety guarantee.

The [reviewed lifetime and archival design](../../../efforts/active/mux-companion-manual-cutover-diagnostics/worktree-state-lifetime.md)
retains the independent efforts, Manager, logger, rescue and operational
qualification gates. Source support is not installed adoption or rollout.
