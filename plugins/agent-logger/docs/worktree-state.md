# Typed worktree-state collection

Parent: [Logger architecture](architecture.md#session-sync).
Owner export: [complete consistent agenda export](../../efforts/skills/driving-effort-agendas/references/agenda-export.md).
Standing intent: [durable worktree data](../../../visions/plugins/agent-worktrees/worktree-data/README.md).

## Current source boundary

`sync.worktree_state_archive` supplies additive collection and fresh local
readback for an explicitly selected, owner-verified agenda export. It uses the
existing `Target` abstraction; only `LocalTarget.worktree_state_root()` opts in.
Other targets explicitly lack that capability, including OneDrive replicas.
This first source slice is a library boundary, not a new scheduled collection,
CLI, source crawler or installed integration. Ordinary session sync is unchanged.

The orchestration caller must first qualify the worktree owner's repository,
full worktree/incarnation and selected store, obtain the complete domain-owned
export, and negotiate the selected owner's complete-export verifier. The library
takes that exact typed identity and verifier; it never imports another plugin's
runtime or guesses a state home. Provider discovery/export orchestration remains
an independent unfinished integration.

## Admission before effects

Collection requires an actual nonpassive, authorized **enforce-mode** aggregate
plan for the executing machine's complete name/platform/role identity, using the
same normalization as plan selection. A valid diagnostic/observe plan is not
permission. Exactly one collection claim must cover the canonical source
repository and bind the actual active target's filesystem root. The supplied
target must also match the current configured target. Native path equality
preserves case; the compiler's conservative case-folded collision identity alone
cannot grant access to a distinct case-sensitive path.

The selected sync configuration remains authoritative. Secret redaction or
essential-session-only publication blocks this lossless database collection;
Logger does not silently redact accepted history and call it complete. Legacy
session allow/deny/opt-in filters do not yet define typed-state admission and
therefore refuse this boundary. Claims with content profiles, landing,
retention or mutation policies also refuse until those semantics are actually
implemented here.

The initial adapter requires POSIX directory-relative publication and directory
barriers. Native Windows collection is explicitly unsupported, not inferred
from the existing owner's separate export support. The isolated Linux packet
does not qualify Windows or hardware power-loss behavior.

Collection roots must also be outside the reserved `session-state`,
`provenance`, `archived` and `.session-sync-*` payload/replacement components.
Such custom placements are unsupported even when a filesystem claim otherwise
matches: ordinary session retention is not persistent-state reclamation.

## Identity, publication and readback

One collection key binds canonical repository, **full** worktree identity,
worktree incarnation, agenda store and accepted revision. A hashed namespace
avoids path-shaped identifiers. The revision is immutable: a different export
at the same key conflicts instead of replacing previously retained evidence.
This also exposes a changed delivery-state cut at an unchanged accepted
revision rather than silently discarding either snapshot.

The top-level `worktree-state/` namespace has its own schema-v1 format marker.
It is claimed only when empty or already owned; an existing ordinary machine
with that name is neither adopted nor hidden. Fleet health excludes only a
positively identified collection namespace, so retained revisions do not consume
the machine-directory scan budget. Ordinary session pushes cannot enter that
owned namespace.
The session-shaped archive-source enumerator also excludes that positively
identified namespace instead of reporting it as an empty unknown machine.
Namespace claiming and potentially overlapping ordinary publication share a
domain guard held through session, process-log and archive publication as well
as heartbeat and mutating retention/compaction operations. Read-only maintenance
does not allocate a missing destination or archive directory. Native alias checks
reject case-insensitive equivalents without hiding genuinely distinct
case-sensitive machine directories. Non-directory entries are not probed as
collection namespaces during fleet discovery.
Admission checks every reserved destination prefix, including prefixes above a
configured target root, and takes the corresponding namespace parent's guard.
Collection claims cannot nest inside an owned or partial collection. Invalid
markers block mutation and are reported separately without dropping healthy
fleet peers. Prefix-count overflow refuses before destination creation; it does
not truncate the protected path.

Only the verified `agenda.sqlite3` and original `manifest.json` are collected.
Every new database, metadata and reference file requests mode `0600` in the
initial POSIX creation syscall, not a later permission change.
The owner's optional prepared manifest is not needed in a transported export.
Copying checks the exact declared byte count and SHA-256, with bounded chunks
and a complete-copy deadline. The selected owner then verifies the destination's
complete schema, ledger and immutable records. A receipt follows that readback,
never a partial copy or transport no-op.

Contributing sessions reference the same collection through additive,
content-checked metadata; the agenda is not copied beneath each session.
An exact retry verifies actual retained bytes afresh before adding references.
Conflicting or interrupted publications remain visible for owner-led
reconciliation; the library neither overwrites nor deletes them.

`readback_worktree_state` independently repeats admission and owner verification.
It does not trust a cached transfer result or receipt in place of stored bytes,
and a missing collection remains missing without creating directories.

The schema-v1 receipt binds the destination, exact source identity, manifest
digest and member size/hash. It reports **local readback verified** and
**remote archive acknowledgment false**. Neither a receipt nor a successful local
copy grants reclamation, retention changes, restoration, head ownership, claims,
leases or live execution authority.

## Ancestor disposal and retained legacy evidence

Ordinary session archiving, replacement and recursive cleanup must not consume
or erase a marked collection hidden below a session or replacement candidate.
Marker presence, including partial or unsupported metadata, blocks those
operations before recursive deletion or permission changes. A verified ordinary
session archive does not authorize removal of a nested worktree collection.
Session-copy walkers refuse a marked subtree instead of publishing its database
under a session identity.
Raw and redacted copies also refuse entering an existing collection before
creating destination directories, including missing parents below its export;
the generic session archiver participates in destination namespace admission.
Recursive removal does not change permissions through external symbolic links.

Preservation checks read directory metadata only, never ledger contents. Their
failure budgets are 100,000 entries, 20,000 directories, depth 64 and a
30-second cooperative deadline. An unreadable or over-budget candidate remains
retained; the check does not truncate evidence or authorize an unverified wipe.
The disposal owner still owns quiescence and the separate deletion permission.
This is not a universal recursive lease or authorization to rescue/migrate data.

## Qualification

The opt-in `agent-logger` `tests/test_worktree_state.py` and
`tests/test_worktree_state_coexistence.py` packet covers real
efforts export/readback in separate Python processes, complete-byte retention,
multiple contributing sessions, exact retry, privacy/claim refusal, corruption,
partial publication, creation-time permissions, namespace/fleet coexistence
and redirected paths. The coexistence packet covers every mutating publication
leg and deterministic session/log/archive claim interleavings. It uses synthetic private fixtures,
not an operator's actual agenda or archive destination. Required unit-only CI
admission and operational/native acceptance are separate gates; see
[testing](../../../TESTING.md).
The separate `tests/test_worktree_state_preservation.py` packet qualifies unsafe
placement refusal and existing/restored nested-marker retention across prune,
verified-archive reconciliation, compaction, replacement and generic cleanup.
