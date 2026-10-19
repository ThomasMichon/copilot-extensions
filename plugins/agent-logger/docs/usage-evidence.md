# Session-local usage evidence

Back to [architecture](architecture.md).

Scheduled `session-sync run` preserves allowlisted accounting into
`session-state/<id>/usage-evidence.json` before incremental signatures or cold
compaction. The artifact follows the session through existing transports and
archive codecs. Explicit `push` and rescue publication do not modify their
inputs to create accounting. Dry runs create no accounting files.

Machine-local configuration `sync.usage_capture` is a boolean, default `true`.
Set it to `false` to retain the previous raw-only behavior. Repository config
does not gain authority to change this setting or select an accounting store.
The producer reads the sibling `session-store.db` in a read-only SQLite
transaction, including committed WAL records, and modern transcript
`session.usage_record` events. It neither copies DB/WAL independently nor uses
SQLite immutable mode. Repo admission applies before selecting store rows.

## Artifact contract

The JSON schema is `agent-logger.usage-evidence`, version `1`. Each artifact
contains its session identity, current source coverage and normalized records
with a source-qualified key, scalar values and observed multiplicity. Store
rows and transcript observations remain separate: consumers must not sum
competing sources or interpret an observation key as a cross-source billable
call identity.

Values allow only model, original timestamp, call/session/agent lineage,
integer token buckets, duration, request multiplier and provider nano-AIU
meters. Optional missing values remain null or absent, not zero. An original,
nonempty timestamp is required for each published observation; a store row with
a null or empty timestamp is a capture failure, not an invented timestamp or
an omitted row. Input tokens already
include cache reads/writes; those buckets must not be added to input tokens.
Legacy request multipliers are not currency. No prompts, response/reasoning
text, raw tool arguments, token-details blobs, account identities or consumer
rate cards enter this artifact. Checkpoint and shutdown cumulative totals are
not inserted as per-call usage.

Native usage/event identities suppress replay. Without a native identity,
source timestamp/model/call-lineage fields identify observations conservatively:
identical weak rows retain their maximum observed multiplicity across snapshots.
Incompatible facts under one source-qualified identity are explicit conflicts,
not silent replacements. Rebuilds and shrinking stores retain previously
captured observations; null token fields may be enriched without replacing
known values. This is evidence preservation, not resolved exact accounting.

## Failure and resource bounds

Publication is serialized per session, uses pinned lexical directory ancestors
and regular-file reads, and atomically replaces a flushed artifact. Producer
lock files are siblings of session directories, outside the signed and archived
session tree. Unchanged bytes preserve mtime, avoiding perpetual resync.
Corrupt prior artifacts are never reset to empty. Failed reads, conflicts, unsafe paths, concurrent writers
or exceeded limits retain the last-good artifact.

One pass admits at most 10,000 directory entries and 20,000 selected store
rows. A ledger has at most 20,000 distinct records and 16 MiB. Transcript
scans stop before exceeding 64 MiB, 200,000 events or a 4 MiB JSON line.
An unfinished tail or concurrent transcript change is incomplete evidence,
not a truncated successful capture. Accounting strings are bounded to 2,048
characters, with an additional 8,192-byte SQLite projection limit so embedded
NULs cannot bypass the pre-materialization bound. Numeric duration and multiplier
values must fit a finite floating-point range. Limits refuse work rather than
emit success-shaped partial data.

Missing legacy sources are reported as coverage gaps, never fabricated zeros.
Corruption, unsupported schemas and other capture failures are reported and
make the pass fail even if raw session transfer succeeds. Raw transfer may
continue; any gap or accounting failure prevents local and hub cold compaction
for that pass, without blocking transfer of already-existing archives.
A missing legacy store alone is nonfatal to raw transfer, but the run explicitly
reports incomplete accounting.

SQLite's standard-library connection reopens by filename rather than binding
to the admitted no-follow file handle. Pinned ancestors and pre/post identity
checks reject observed redirects or replacements; they cannot exclude a hostile
file swap restored between checks. This reader assumes the producing machine's
accounting directory is not controlled by a concurrent hostile writer. SQLite
VM work and operations between calls obey a deadline; a blocking operating-system
filesystem call cannot be preempted by that callback.

The separate [transcript estimation pipeline](usage-estimation.md) can pair
these preserved measurements with count-only features. Preservation itself does
not estimate tokens, resolve cross-source billing identity, infer cache splits,
assign payers or produce dollar invoices.
