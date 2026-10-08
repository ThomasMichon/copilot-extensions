# Process-log evidence

The `agent_logger.process_logs` library supplies archive-transparent, bounded
line reads for the session-intelligence accounting pipeline. `session-sync`
can now additionally publish process-log evidence alongside session-state for
the ordinary local-machine sync path (`sync.process_logs.enabled`, opt-in,
filesystem targets only); scheduled compression and usage ingestion remain
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

On POSIX, a ref returned by `iter_process_log_refs` reopens the configured
root with `O_NOFOLLOW` on every later `iter_lines()` call, rather than
trusting the root by path name -- so replacing the root with a symlink,
whether before enumeration or any time after a ref was handed to a caller,
is rejected rather than silently followed. Windows has no `openat`
equivalent and keeps the previous path-based behavior; this is a documented
platform gap, not an equivalent guarantee.

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

Scheduled settled-log ZIP compaction, SSH/ingest target support, and
admission fencing for repo-scoped syncs remain outstanding.
