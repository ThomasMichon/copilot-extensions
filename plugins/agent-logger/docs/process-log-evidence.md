# Process-log evidence

The `agent_logger.process_logs` library supplies archive-transparent, bounded
line reads for the session-intelligence accounting pipeline. `session-sync`
can now additionally publish process-log evidence alongside session-state for
the ordinary local-machine sync path (`sync.process_logs.enabled`, opt-in,
filesystem targets only). Optional scheduled ZIP snapshots preserve settled
local logs without deleting originals; reclamation and usage ingestion remain
separate implementation slices.

## Supported observations

`iter_process_log_refs(log_root)` enumerates a configured machine's log directory:

- `process-*.log` live evidence;
- `process-*.log.gz` existing gzip evidence;
- `*.zip` archives containing flat `process-*.log` members.

Each `ProcessLogRef` retains its physical path and optional ZIP member. Its
`logical_name` is unchanged when a log is compressed. Enumeration deliberately
retains storage aliases: the accounting catalog must reconcile their evidence
revisions, rather than silently prefer one path and discard a newer observation.
Source namespace and machine remain caller-owned provenance.

ZIP members must be flat. Duplicate log names and symlink members are errors.
Readers never extract files. Unrelated non-log ZIP metadata is ignored.

## Streaming and failure contract

`ref.iter_lines(max_line_bytes=...)` yields strictly decoded UTF-8 lines,
preserving line endings and a final unterminated line. The default line bound
is 1 MiB of **uncompressed bytes**, applied equally to live, gzip, and ZIP
evidence. It bounds each line allocation, not total file length. Consumers
should stream records rather than collect the iterator.

Missing roots, unreadable or corrupt evidence, unsupported ZIP members,
invalid UTF-8, invalid bounds, and overlong lines raise explicit exceptions.
A directory with no supported evidence -- including an existing empty
directory, one containing only unrelated files, or a ZIP with no supported
members -- yields no refs; this is ordinary, not an error. The opened
descriptor must refer to the regular file observed before opening; source
symlinks and identity replacement during opening are rejected.

An active file can grow during reading. This reader does not assert a settled
snapshot, checkpoint transfer completion, or authorize deletion. The ingestion
and compaction owners must record source revisions, reconcile late writes, and
enforce their separate completeness and retention guarantees.

## Root identity and symlink safety

Enumeration, direct references, and process-log sync reads reuse the ZIP
snapshot directory boundary. Each operation resolves intentionally relocated
ancestors once, then pins every canonical ancestor. POSIX reads use directory
descriptors with required no-follow/nonblocking flags; Windows holds native
ancestor handles denying directory rename while pathname reads run. Missing
safety primitives fail explicitly, never degrade to an unprotected open.

Entries and ZIP members are enumerated in filename order. Windows enumeration
collects ZIP member observations under the same pinned directory boundary as
the listing, without repinning ancestors for each archive. ZIP entry identity
is checked against the opened file, and native ZIP read handles deny writes.
Enumeration releases all native handles before yielding any reference.
Pausing or abandoning that iterator does not prevent source directory or
archive renames. Directory-fd read APIs explicitly reject Windows callers.
POSIX enumeration yields references incrementally without collecting all archive
observations before the first yield. POSIX transfers reuse the already-pinned
directory for listing, entry validation and reads; they do not reopen its pathname.
Source-root changes still report a partial transfer rather than clearing health.
Transfers check root identity before each candidate open and before its durable
publication, stopping on root churn without reopening the pinned directory.
On POSIX, a source entry replaced by a symlink between validation and opening
is skipped when the no-follow opener reports `ELOOP`; later candidates still
transfer and any prior good destination copy remains intact. Other source-open
errors and all destination-publication errors remain transfer failures.

References retain their supplied path for display and reference identity.
Enumeration anchors a relative configured root to the enumeration-time working
directory and emits absolute paths without resolving their display aliases.
Directly constructed references preserve the caller's path, including a
relative path resolved against the working directory at read time.
Enumerated Windows references additionally retain the canonical reader root,
independent of that display path: retargeting an intentional ancestor alias
cannot redirect later `iter_lines()` calls. Directly constructed references
continue to resolve their own reader boundary at read time.
If an entry becomes non-regular between validation and opening, entry-opener
`ValueError` failures retain their cause and use process-log-specific wording
with the offending filename. Only `ValueError` from the entry opener is
normalized; consumer exceptions and entry-opening operating-system failures
are propagated unchanged.

The configured final root must be a real directory, not an alias. Pinning lasts
for the operation, not across separate enumeration and read calls: references
are observations, not immutable snapshots. Concurrent content writes remain
possible. Process-log payload destinations have their own pinned boundary
described below. Session-state readers/writers and transfer-health metadata
retain separate ancestor-containment limitations; the complete containment
tracker remains open.

## See Also

- [Architecture](architecture.md).
- [Session intelligence effort](../../../efforts/active/session-intelligence-and-accounting/README.md).

## Sync publication (`session-sync`)

`sync.process_logs.enabled` (default `false`) opts a machine's `session-sync`
into publishing `<sync_source>/logs/` (process-log evidence; raw, gzip, and
flat ZIP files, selected by `is_process_log_candidate`) alongside
session-state, under the target's `{machine}/logs/` subpath. It reuses the
same incremental size/mtime copy and locked-file deferral as the session
push, but is a flat, non-recursive directory copy -- process logs are never
a directory tree the way a session is.

Two scope limits, both deliberate rather than overlooked:

- **Filesystem targets only** (`local`/`onedrive`, via
  `Target.push_process_logs`). SSH and ingest targets fall back to the base
  class's unsupported response; wiring them in is a follow-up slice.
- **Unfiltered passes only.** A repo-scoped sync (`repo_allowlist`/
  `repo_denylist`/`require_repo_opt_in`) skips process-log publication
  entirely rather than attempting to filter it: a process log is not scoped
  per-repo the way session-state is (one CLI process's log can span several
  repos/worktrees across its lifetime), so admission fencing for a
  repo-scoped sync needs its own design -- a known, explicit follow-up, never
  silently approximated here.

Source reclamation after settled-log ZIP verification, SSH/ingest target support, and
admission fencing for repo-scoped syncs remain outstanding.

### Filesystem payload destination boundary

Process-log transfers pin every lexical destination ancestor, beginning at the
filesystem anchor, before creating the next child. Configured destination
symlinks and name-surrogate reparse points are rejected rather than resolved.
No pathname-based directory creation precedes that boundary.

POSIX directory creation, incremental inspection, temporary creation, metadata,
atomic replacement, byte-count inspection and temporary cleanup use the held
directory descriptors or the newly created file descriptor. If an ancestor is
renamed after opening, publication stays in that original directory; a replacement
symlink cannot redirect later operations. Windows holds native handles denying
ancestor rename through creation and payload I/O, then releases every handle at
operation exit. Its final payload move retains the existing write-through move
barrier; POSIX additionally fsyncs every held parent and the replacement directory.
Parent barriers also run for existing children, so retries after a failed flush
and concurrent directory creation cannot bypass durability acknowledgement.
Missing primitives or unsupported durability barriers fail explicitly.

Source identity/size and entry revalidation still run before publication.
Replacement no longer unlinks a previous good process-log payload first: a
rejected source or a failed atomic move leaves that copy intact. Native Windows
restrictions such as a read-only destination can therefore report a failed move
rather than delete the old file to force replacement. Ordinary destination
inspection, metadata and publication errors are visible failures, not missing
source candidates. Existing source-churn and sharing-violation deferral remains.
An error after a successful move (for example, its durability acknowledgement or
byte-count inspection) reports failure without deleting the landed evidence.

This boundary covers process-log payloads only, not `sync-meta.json`,
session-state publication, identity admission or session readers. It does not
serialize independent writers, make mutable source bytes immutable, or implement
kernel-owned ZIP snapshot claims. Those contracts and the remaining #5727 work
are separate; no source retirement or rollout is enabled.

### Additive settled-log ZIP snapshots

The existing scheduled `session-sync run` can create verified ZIP snapshots
before its unfiltered process-log transfer:

```yaml
sync:
  process_logs:
    enabled: true
    zip_snapshots:
      enabled: true
      local_source: true
      min_quiet_seconds: 600
```

ZIP `enabled` and `local_source` both default to false. `local_source: true` explicitly attests that
`<sync.source>/logs` contains this operating system's original producer logs,
not a copied, rescued, remote, or foreign-OS corpus whose PIDs cannot be checked
locally. Do not enable it on a shared Windows/WSL log root. Snapshot configuration
is machine-local; no repository config or explicit namespace push enables it.
The quiet threshold is a positive integer, not a retention/deletion policy.
Existing retention defaults and session compaction are unchanged.

Only `process-<epoch>-<pid>.log` inputs with a provably exited local PID and an
old enough mtime qualify. Active-idle producers, reused PIDs, recent files, and
unclassifiable names stay untouched. Permission/probe failures are visible
errors, not evidence of exit. Existing gzip and ZIP files are not recompressed.
The manual `session-sync compact-logs --dry-run` performs the same inspection
under the sync lock without creating archives; omit `--dry-run` for snapshots.
The manual command does not publish to any target. Disabled automation honors
`AGENT_LOGGER_SYNC_DISABLED`, including the manual command.

Each ZIP has one flat original log member. Its filename is content-addressed
from the logical name and byte hash, so retries reuse verified identical
snapshots and late changes create distinct revisions. Source fingerprints and
the producer probe are checked around bounded streaming; the ZIP member's full
hash is verified before and after no-replace publication. Truncation, rotation,
append, corrupt existing archives, and unsupported kernel-owned publication fail
explicitly. Neither a corrupt archive nor an original is overwritten/deleted.
Snapshot verification accepts stored or deflated members (new snapshots use
deflate), and rejects other codecs without attempting a decoder. Encryption
and deflate runtime failures are per-file errors: later eligible files and the
scheduled raw-log transfer still run, while the command returns failure.
An initially absent optional log root is an empty snapshot pass and is not
created. Permission errors, non-directory or redirected roots, and a root
that disappears during validated opening still fail explicitly. This does
not change transfer-health semantics for missing requested evidence.
Publication reuses the source-admission kernel-owned file boundary: Linux
uses an anonymous `O_TMPFILE` inode linked from its descriptor; Windows uses
the protected original handle and a no-replace handle rename. Cleanup never
deletes a file by a guessed temporary pathname. Abrupt Windows termination
during disposition cancellation/rename can leave an artifact; restart does not
guess its ownership or sweep unrelated files. Filesystems/platforms without
these primitives fail explicitly; macOS ZIP snapshot publication is currently
unsupported (ordinary process-log reads and sync remain unchanged).

Canonical directory traversal is pinned with Linux descriptors, or native
Windows ancestor handles denying directory rename. Intentionally relocated
ancestors resolve once before pinning; later redirects cannot redirect archive
I/O. ZIP file contents are flushed on both platforms; POSIX also flushes the
containing directory. Windows directory-entry power-loss durability is not
claimed. Process-log source reads also reuse this boundary. Process-log payload
transfers use the separate destination boundary above; the containment tracker
stays open for session-state and health-metadata paths.
Windows snapshot/verification read handles additionally deny writes and rename.
Fingerprints use birth time there because native `lstat` and `fstat` can expose
different `ctime` meanings; POSIX retains change time. Published ZIP verification
stays on the kernel-owned original handle and checks the published file identity.

Raw sources remain present and continue syncing alongside ZIP revisions.
Readers deliberately retain those aliases; accounting must reconcile them by
evidence identity, not count each path as new usage. This slice adds scheduled
ZIP preservation, **not storage reclamation or completed compaction**. Source
retirement, full normalized-storage parity, and native platform acceptance
remain separate completion gates.

### Retirement boundary prototype

`sync.log_retirement_file` supplies an internal exact-file boundary for native
Windows safety validation. It is not called by sync or the manual snapshot
command and enables no retirement policy. A caller-held archive directory pins
ancestry; the source opens with read/delete access and no sharing. Existing and
new writable opens and source renames are excluded while that original handle
is held. Full revision fingerprint and content hash must still match before
an explicit disposition commit; abandoning the context without commit retains
the source. An optional caller-owned commit guard enters after the final raw
hash and metadata checks, not before that potentially long read. Its lifetime
spans disposition. The `committed` observation stays true if a subsequent guard
or close acknowledgement fails; such failures cannot truthfully claim rollback.
Read-only sources and hardlinked/reparse entries are rejected
without attribute changes or pathname-delete fallbacks.

POSIX retirement is explicitly unsupported by this primitive. Directory
descriptors and advisory file locks alone do not prove exact-entry deletion
and writer exclusion. The all-target readback, required-target set, explicit
retention age, scheduler integration and native acceptance gates remain in the
[reclamation contract](../../../efforts/active/session-intelligence-and-accounting/process-log-reclamation.md).
No local or target ZIP is removed by this primitive.

### Readback and policy preparation

`Target.process_log_readback(machine)` resolves an internal, pass-local
destination. Filesystem targets (`local` and `onedrive`) support full
single-member ZIP hash/length verification against a `LogArchiveRevision`.
SSH, SSH-tunnel and ingest explicitly return unsupported, never successful
verification. An all-target result requires a nonempty set and every result
to be supported and verified. It never uses transfer success, file count,
size/mtime equality or a prior verification receipt as evidence.

Readback pins the lexical destination ancestry without creating directories
or issuing durability writes. Before accepting a POSIX readback, it rechecks
every lexical parent/child edge against the held directory identities; a moved
original directory cannot stand in for a replaced configured destination.
Ordinary publication still stays in its pinned original after a rename.
A missing/offline root, redirected ancestor,
changed ZIP entry, unsupported codec or corrupt member is a failed result.
The result names the content-addressed archive and failure class without
exposing configured paths. OneDrive readback proves local stored bytes only,
not cloud upload acknowledgement. These observations alone do not authorize
retirement or promise continued retention at the destination.

The reserved machine-local `sync.process_logs.reclamation` block is strictly
parsed: boolean `enabled`, positive integer `min_age_seconds` with no default,
and explicit `required_targets` transport names including the active target.
Enabling also requires process-log collection, ZIP snapshots and the local
producer attestation. Inactive target option defaults are not required targets.
The internal target-set resolver reuses existing options, resolves them once,
and rejects unknown/unsupported, duplicate and source-overlapping destinations.
It does not turn ordinary session-state sync into a multi-target transfer.

**Enabled runtime reclamation is still explicitly refused at configuration
construction**, before any sync writes. Post-publication orchestration, scope
gates, revision revalidation and the retirement policy are not wired yet.
Disabled defaults and existing additive snapshot/manual behavior are unchanged.
Do not enable this reserved setting as an operational feature.

### Exact-ZIP publication preparation

`sync.log_archive_publication` supplies an internal publisher for one
`LogArchiveRevision` to the already-resolved filesystem destinations. It copies
only that revision's ZIP, never neighboring raw/gzip logs, other archives or
session-state. Bounded streaming verifies both the complete compressed-byte copy
and its single member. Source identity is rechecked around copying and
publication; a protected source handle and pinned lexical ancestry remain held
through the operation.

Publication uses a kernel-owned no-replace claim. An existing ZIP is fully
verified, not overwritten. The claim closes before independent fresh readback,
so native Windows sharing restrictions cannot turn the publisher's own handle
into a readback failure. An additional read-only directory guard must identify
the original publication root and hold its lexical ancestry through readback;
even a replacement directory containing a valid ZIP cannot stand in for it.
Missing, offline, corrupt or unsupported destinations
produce separate failure results without skipping later targets. POSIX source
or target ancestor replacement cannot produce a successful verification for
the replacement path. A failure after publication may leave additive ZIP
evidence; the helper never deletes that copy to simulate rollback.

Dry run performs source and existing-target verification without creating
directories, claims or archives, or issuing durability writes. Missing evidence
remains unverified. This helper is not called by scheduled sync or manual
snapshotting yet and does not enable reclamation. The orchestration and
retirement gates in the [reclamation contract](../../../efforts/active/session-intelligence-and-accounting/process-log-reclamation.md)
remain open.

### Internal post-publication retirement sequence

`sync.log_reclamation.reclaim_process_logs` composes the prepared primitives
for isolated validation. It has no scheduled, tenant, namespace or manual CLI
caller. Enabled runtime reclamation is still refused: correct machine-local
configuration provenance and scheduling integration remain unimplemented.

A caller must own the sync lock and supply the complete resolved target set,
explicit preservation/retention settings and a current-policy commit guard.
The internal sequence requires native Windows for any retirement. It selects
only quiet, old raw producer files with an exited PID, holds the original
exclusively, and requires an existing local ZIP for the current raw hash.
A late raw revision lacking its own ZIP is retained for another snapshot pass.
It never substitutes an older archive or creates a new local snapshot itself.

All required target roots are pinned before exact-ZIP publication. After
publication and fresh readback, the complete local and target ZIPs are verified
again through protected handles held across the retirement commit. Final raw
hashing precedes the caller's policy guard; source-root identity, retention,
quietness and producer exit are checked inside that guard immediately before
original-handle disposition. A changed policy or any missing, corrupt,
unsupported, redirected or unreadable replacement retains the raw source.

Results separately count inspected, eligible, retained and retired files,
confirmed reclaimed raw bytes and filename-only failures. A disposition that
committed but lost a later acknowledgement is separately
`committed_unconfirmed`, never counted as retained, confirmed retired bytes or
a successful rollback. Every local/target ZIP and unrelated raw/gzip/session
file remains untouched by retirement. Dry run only inspects raw and existing
ZIP evidence, with no publication, mkdir, durability write or retirement;
eligible files remain retained and missing verification stays visible.

### Combined transfer health

Filesystem `sync-meta.json` retains independently recoverable `sync_legs`
entries for `session-state` and (once attempted) `process-logs`. Each records
its last actual attempt/check, status, consecutive partial attempts, exact deferred
count, and bounded path samples. The existing aggregate `status` is partial
while either recorded leg is partial; its streak is the maximum current
per-leg streak, and its deferred count is the sum. A successful session transfer
cannot reset a failing log streak, so existing sustained-partial health
thresholds also detect repeated log deferrals.
Non-enumerable failures record a bounded leg `reason` and zero identified
deferred files; diagnostic strings never inflate the file count.

A clean process-log retry, including an incremental pass that copies no files,
clears only that leg. Session diagnostics and detritus measurements survive.
Missing requested log roots are recorded as partial rather than recovering
previous failures; an existing empty log root is a successful empty observation.
No-change session heartbeats preserve both legs and do not count as attempts.
They refresh only the session-state check time; health uses the oldest recorded
leg check so a log retry cannot disguise stale session-state (or vice versa).
Legacy partial metadata has no failed-leg identity and conservatively requires
a real session-state retry before becoming healthy. Unreadable or malformed
metadata is preserved and its update failure logged.
The first legacy heartbeat migrates the original timestamp as an attempt time
before refreshing only its check time. A present null leg object is corruption,
not a legacy record to repair silently.
Classification validates complete leg schemas and their aggregate rather than
trusting a contradictory top-level `ok`. Deferred process-log samples are bare
filenames, not configured source paths. Roots that vanish or change identity
during enumeration/copy remain partial, even if no file needed transfer.

This composes persisted transfer results, not a new containment guarantee.
Unsupported/scoped log passes remain outside this filesystem health contract,
and outright session-state failures still use the existing failing command
result rather than a new persisted leg update. Previously attempted log health
is retained when log publication is disabled; disabling is not recovery proof.
