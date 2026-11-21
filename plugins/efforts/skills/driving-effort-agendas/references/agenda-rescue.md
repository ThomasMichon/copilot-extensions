# Rescue an exported checkout-local agenda

Parent: [complete export](agenda-export.md).
Placement owner: [managed placement](managed-placement.md).

The optional payload `scripts/agenda_rescue.py` relocates one existing
context-qualified store-format-2 agenda into an empty owner-allocated home.
It preserves the store ID, all accepted operations and immutable records,
current documents, metadata and delivery status. It does not initialize an
empty replacement or replay operations.

This is an explicit writer cutover, not a read or a cleanup command. Establish
authority for both homes and quiesce the agenda's writers and publishers first.
It does not transfer a session head, resource claim, execution permission or
publisher authority. Manual JSON lanes, store format 1 and homes with an old
`cursor.json` or initialization-recovery file remain unsupported and protected.
Do not relabel or empty them to make this procedure accept them.

## Select and export

Use the exact selected payload's `info` result, not a package version, to
establish `agenda-managed-rescue-v1` support:

```text
python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_rescue.py info
```

Create and verify a [complete consistent export](agenda-export.md) at the
selected worktree, store ID and exact accepted revision. Keep that export in
its own owned directory. Select the existing Worktrees owner's absolute Python
interpreter, project and canonical worktree incarnation. The allocator must
declare `worktree-data-v1`; an old or unavailable provider refuses without
installation, provider fallback or guessed paths.

The source, export and allocated destination must be disjoint ordinary
directories. The source must still match the complete exported tables under
its write lock. The destination must be empty or the same attempt's fenced
reservation. Unrelated contents, another accepted store, changed identities,
stale revision or a conflicting attempt refuse without replacement.

```text
python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_rescue.py relocate \
  --source-home <old-agenda-home> --export-home <verified-export-directory> \
  --provider-python <absolute-selected-worktrees-python> --project <project> \
  --worktree <full-worktree-id> --creation-nonce <incarnation> --lane <lane> \
  --store-id <existing-32-lowercase-hex-id> --expected-revision <exported-revision> \
  --operation-id <stable-rescue-operation-id>
```

## Cutover and recovery

Every effort-kind board source must use an absolute local canonical reference.
The helper refuses checkout-relative references before allocation or fencing,
without rewriting accepted history. Retain the canonical effort independently
of any later source-checkout cleanup. If a lane uses relative references, retain
it for owner-led reconciliation; do not edit SQLite rows or the export manifest.

The owner reserves the destination with an unsupported SQLite format marker and
flushes its new directory entry before committing the source writer fence.
First creation requires a genuinely empty directory; orphan SQLite sidecars
remain untouched. Existing reservations are inspected read-only before they
can authorize source cutover, without recovering an unrelated hot journal.
The owner then commits the source fence and recovery record and flushes the
source directory before activating the destination. This persists rollback
journal deletion as well as database bytes. A failed source barrier leaves both
stores fenced for an exact retry. The record binds the exact export, placement, operation and
filesystem identities. Additive provider presentation fields are not part of
this stable identity. Ordinary readers, updates, initialization and delivery
recording refuse a fenced database, including a previously loaded writer that
waits for the write lock. The writer's existing version check runs after that
lock; no new marker file is assumed to constrain old code.

Source fencing, initial reservation and destination activation bind their
expected database identity in the common opener. It checks the named file
before and after connection creation, before SQLite can run a recovery-capable
statement, then checks again under the transaction lock and before commit.
Windows also retains and checks a separate file handle. POSIX does not open a
separate descriptor: closing one would release other SQLite connections'
process-scoped record locks. A replaced file refuses without recovering its
unrelated hot journal. These checks detect ordinary replacement races, not
arbitrary hostile filesystem changes between every check and use.

Destination activation copies the complete original tables in one transaction
and verifies their exact content hashes. Only then does the destination become
an ordinary format-2 agenda. A failed or interrupted copy rolls back to its
fenced reservation. Both source and export remain retained.

Success returns schema `efforts.agenda-managed-rescue`, version 1, with
`source_fenced: true`, the owner-qualified `placement` and the new `state_home`.
Use that exact locator for subsequent ordinary agenda commands. The source
remains fenced; readers do not silently follow a copied path as authority.
The placement descriptor remains Worktrees-owned and does not assert domain
validation or live execution authority.

After failure, interruption or a lost reply, retain all three homes and retry
only the exact command and export. A matching retry can finish an interrupted
activation. If later work already advanced the destination, retry validates its
current ledger and the complete original history prefix without resetting it.
`activated: false` distinguishes this result from a new activation.

An unrecognized partial reservation, replaced inode, changed export or
conflicting attempt requires owner-led reconciliation. Do not remove the fence,
restore stale source bytes, initialize another writer or overwrite a database.
An error after commit can mean that cutover occurred even though no success
reply was returned. Absence of a reply is not rollback.

The helper uses the complete export's native directory durability barriers and
120-second cooperative work budget. Unsupported filesystems/access refuse;
this is not hardware power-loss proof. It starts no service, publisher, archive
transport or background scan. Local rescue is not remote archival, release
qualification or permission to delete the old checkout or retained evidence.

For a supported checkout-local source that must release its checkout hold,
use Worktrees' [targeted agenda doctor](../../../../agent-worktrees/docs/worktree-data.md#targeted-agenda-doctor).
It composes this rescue with complete-history verification and explicit
retirement of the fenced original beside its export. This rescue command
alone continues to retain the original source in place.
