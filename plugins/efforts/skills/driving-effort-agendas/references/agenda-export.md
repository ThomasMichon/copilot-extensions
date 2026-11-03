# Complete consistent agenda export

Parent: [managed agenda commands](managed-agenda.md).
Placement: [explicit new managed homes](managed-placement.md).
Passive history: [bounded presentation pages](agenda-history.md).

The optional payload `scripts/agenda_export_cli.py` exports the complete
declared context-qualified store, not a loaded history page or rendered summary.
It uses Python's standard library and an existing interpreter; no global
binstub, runtime, scheduler or archive destination is introduced.

```text
python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_export_cli.py info

python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_export_cli.py export \
  --state-home <existing-home> --destination <owned-empty-directory> \
  --worktree <full-worktree-id> --store-id <expected-store-id> \
  --expected-revision <revision> --contract-version 1

python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_export_cli.py verify \
  --destination <export-directory> --worktree <full-worktree-id> \
  --store-id <expected-store-id> --expected-revision <revision> \
  --contract-version 1
```

`info` advertises `agenda-complete-export-v1` without reading a store.
`--expected-revision` is optional; when supplied it binds the exact cut.
Source support does not establish an older installed payload's capabilities.
Both effectful export and read-only verification return bounded JSON under
`efforts.agenda-export-cli`, version 1. Errors do not return a successful
manifest.

## Snapshot and completeness

Export requires an existing store-format-2 context-qualified database and
an explicit already-owned empty destination disjoint from the state home.
Legacy, missing, unsafe, unrelated or inconsistent storage refuses; there is
no initialization, migration, directory crawl, replay, overwrite or automatic
repair.

One read-only SQLite snapshot supplies the current state, every accepted
operation's original request/result/documents, all immutable context/evidence
records, metadata and delivery state. The online backup preserves that cut
under concurrent mutation. Neither page limits nor display omissions truncate
the export.

New delivery is projected at the writer's captured accepted time, not a fresh
clock after applying the operation. Export validates that complete projection.
Older accepted observations lack an exact projection-time witness: compatibility
checks the complete owner projection at acceptance and distinct declared revisit
boundaries, retaining the existing deadline. A matching candidate is not recorded
as the actual projection time or used to rewrite accepted bytes.
Crossing a deferred `revisit_at` can change live due/next-action fields without
a database write; it does not invalidate unchanged accepted evidence or a
verified export. Inconsistent mixtures of temporal fields still refuse.

The completed export contains `agenda.sqlite3` and `manifest.json`.
The manifest uses schema `efforts.agenda-export`, version 1, and records
completeness, store version/mode, exact worktree/store/revision/commit time,
per-table row counts/content hashes and member byte counts/SHA-256.
Readback verifies the declared schema, identities, complete table content
and member integrity without executing operations or acquiring authority.
Stored requests also pass the writer's shared pure envelope, initialization
and operation-grammar validators, including caller metadata, generated bounds,
typed evidence, nested fields, enums and declared deferred-time constraints.
Recomputed transport hashes do not make malformed requests valid.
This is not operation replay or proof of historical movement, permission,
productive judgment or external outcome; original provenance remains qualified.

The database/member limit is 1 GiB, with a 120-second cooperative deadline,
256-page backup steps and 1 MiB hashing chunks. Exceeding a bound refuses
instead of publishing a complete-looking partial collection.

## Publication and custody

The success manifest follows completed durable data publication. Unsupported
filesystem/access barriers refuse rather than silently weakening durability.
Native Windows directory/write-through controls remain separately qualified;
Linux API doubles are not native acceptance or hardware power-loss proof.

Interrupted export may retain diagnostic partial files. POSIX publication may
retain `manifest.pending.json`; it is not another accepted snapshot or an
archive receipt. Preserve failed destinations for owner-led reconciliation.
The helper does not delete or adopt them; choose a fresh explicitly owned
destination rather than overwriting unknown effects.

Verification is local readback, not remote archival acknowledgment. Logger's
repository, privacy, destination and revision-bound receipt policies still
govern collection. Do not silently redact accepted planning content and call
the export lossless. Export does not collect unrelated files, credential files, sockets,
leases or other runtime authority. Sensitive content already present inside
accepted planning records remains subject to the destination's privacy gate.

No command restores a writer, head, claim, lease or live session from the
export. Historical planning evidence is not current execution permission.
Restoration, rescue, actual archive transport and retained-data reclamation
remain independent owner operations and approval gates.
