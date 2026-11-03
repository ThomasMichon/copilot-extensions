# Passive rich agenda history

Parent: [managed agenda commands](managed-agenda.md).
Planning: [driving effort agendas](../SKILL.md).

The source `agenda-history-page-v1` capability reads immutable accepted
operations from a context-qualified store. It is not a new agenda writer,
canonical effort-completion ledger or runtime host. Installed payloads can
predate this interface; discover actual support before using a new command.

## Explicit reader locator

The adopter explicitly supplies an already-owned state home, selected worktree
and expected store ID. These values may come from its allocation/registration
boundary; a published Snapshot is not permission to guess a directory or execute
publisher-supplied arguments. The reader does not crawl worktrees, initialize a
lane, import legacy JSON, discover credentials or start another service.

Resolve the script from the enabled **efforts payload**, not a sibling runtime
or new global binstub:

```text
python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_cli.py \
  reader-info --state-home <explicit-home> --worktree <selected-worktree> \
  --store-id <observed-store-id> --contract-version 1 --json

python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_cli.py \
  history-page --state-home <explicit-home> --worktree <selected-worktree> \
  --store-id <observed-store-id> --before-cursor <opaque-agenda-position> \
  --limit 64 --contract-version 1 --json
```

`reader-info` may omit `--store-id` for an explicitly allocated home whose
identity has not yet been read. It returns the actual worktree, stable store ID,
store format version, accepted revision/time, cursor and supported capabilities.
Subsequent pages require the expected store ID. An available legacy store reports
`observed-only` with no rich-history capability; a rich page request returns
`unsupported`. Missing or unsafe storage is unavailable, never an empty history
or an instruction to migrate an active lane.

The locator remains caller-owned. This interface does not create a locator
registry, move storage, archive it or change checkout cleanup. Store format
version is **not** a restore epoch. A restored database can rewind its revision
and timestamp while retaining its ID; compare independent last-known references
when available and preserve uncertainty. Missing planning state does not halt
otherwise-authorized work.

## Positions and accepted evidence

Opaque `ag1.` positions bind the exact worktree, store ID, store format version
and accepted revision. A roster sequence, raw agenda revision, session event ID
or another store's token cannot replace one. `--before-cursor` is exclusive;
omitting it requests the newest accepted range. Browsing does not consume any
delivery cursor or accept a planning outcome.

Pages contain at most 64 entries ordered oldest first within the selected page.
`next_before` is the oldest returned position when more accepted operations
remain. The page cursor is the atomic read's current head, not a cursor that
the viewer has consumed. Source replacement and an ahead-of-store position
require explicit reconciliation; no position is silently clamped.

Each entry preserves a stable source-qualified operation identity, accepted
commit time, native operation name and original rich result, including its
immutable context/frame/item/attempt/outcome records. Evidence is
`retained-event`; actor/session labels remain `caller-asserted` or `unknown`,
never authentication, live execution or resource custody. Imported provenance
and canonical acceptance still retain their independent meanings.

The scope is `accepted-operations` with partial coverage of wider work, not
lifetime progress. A before-initialization position yields an explicit `gap`
with its floor and `reader-info-and-history` reconciliation path. Broken
accepted-revision continuity, malformed stored identities or inconsistent rich
record metadata are errors, not a complete-looking partial page.

## Bounds and passive lifetime

Encoded bodies are at most 32 KiB and frames at most 35 KiB. Pagination may return
fewer than the requested entries to fit that byte limit. A large accepted result
retains an explicit omission with its canonical-content hash, byte size and
typed `accepted-operation` payload reference. That reference identifies the
store, format, revision and operation; it is not executable argv. The existing
owner `history` read can retrieve the original result, whose identities must
still match the reference before presentation.

The reader opens an existing SQLite database in `mode=ro`, using one read
transaction for header and page selection. It checks the current bounded
documents and selected accepted results without materializing the complete
immutable-record graph or scanning directories. It never writes store or
delivery state. Closing the owning process ends only that read; there is no
permanent history poller.

New commands have strict flags and bounded JSON success/error output. Existing
`read`, `next`, `check`, `history`, mutation and delivery contracts are unchanged.
Raw evidence can contain private planning details; local reading does not grant
publication, restoration, replay or completion authority.
