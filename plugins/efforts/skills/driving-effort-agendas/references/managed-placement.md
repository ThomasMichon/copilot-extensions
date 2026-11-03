# Explicit new managed agenda placement

Parent: [managed agenda commands](managed-agenda.md).
Placement owner: [worktree data](../../../../agent-worktrees/docs/worktree-data.md).

The optional payload script `scripts/agenda_managed.py` composes the existing
worktree allocator with efforts-owned fresh context-qualified initialization.
It is not a global binstub, required dependency, migration tool or execution
authority. Standalone `agenda_cli.py` and explicitly located legacy stores keep
their existing behavior.

Discover this payload's independently named `agenda-managed-placement-v1`
support before using its strict `init` syntax:

```text
python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_managed.py info

python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_managed.py init \
  --provider-python <absolute-selected-worktrees-python> --project <project> \
  --worktree <full-worktree-id> --creation-nonce <incarnation> --lane <lane> \
  --store-id <32-lowercase-hex-id> --request <fresh-request-v2.json>
```

The interpreter must already belong to the selected worktree owner. The helper
uses a bounded isolated process to check its actual `worktree-data-v1`
declaration and parser, then explicitly requests the new empty placement.
Missing or old providers refuse without installation, host startup, filesystem
search or a checkout-local fallback. No independently installed package is
imported into the efforts process.

The allocator returns a home beneath its canonical admitted project root.
Supported root aliases therefore work without relaxing efforts' rejection of
symlink/reparse child paths or changing the descriptor's registered identity.

The request is an ordinary fresh context-qualified request-v2 `init`, with
expected revision zero and the exact selected worktree. Invalid initialization
data is rejected before allocation. The initial agenda accepts the same store
identity as the descriptor; the chosen identity is part of the stable retry
fence, not a way to rewrite an existing store's identity.
Preflight shares the writer's initialization preparation, including generated
document, immutable-record, result and caller-metadata bounds. A request that
fits its input limit cannot allocate a lane before deterministic output-size
failure.

Keep the store ID, operation ID, request and caller assertions unchanged for
an exact retry. Retrying preserves the original accepted result and does not
reinitialize the ledger. An allocation can succeed before agenda initialization
fails or a reply is lost. Preserve that placement and retry the exact request;
do not infer rollback, allocate another writable copy or overwrite a partial
registration. Conflicting or nonempty uninitialized homes refuse visibly.
An empty or interrupted database file is not proof of accepted initialization.
Unrelated contents require a readable accepted store with the exact selected
worktree/store identity; recognized SQLite recovery files retain the owner's
ordinary transaction checks. Overlapping exact initialization retries recheck
schema version under the write lock and preserve database bytes.

The result includes the owner-qualified placement, original accepted agenda
result and whether this invocation accepted a new operation. Actor/session
options remain caller assertions. Placement does not prove a live head or
resource authority, and initialization does not publish an observation.
Use the existing explicit publication contract separately when authorized.

This source path qualifies only new managed stores. It does not import an
active legacy lane, move data, archive it, change checkout cleanup or authorize
deletion. Filesystem durability remains conditional on the actual owner/store
barriers; source API doubles do not establish native Windows acceptance.
