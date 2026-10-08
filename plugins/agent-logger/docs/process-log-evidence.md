# Process-log evidence

The `agent_logger.process_logs` library supplies archive-transparent, bounded
line reads for the session-intelligence accounting pipeline. It does not yet
enable process-log synchronization, scheduled compression, or usage ingestion.
Those remain separate implementation slices.

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
