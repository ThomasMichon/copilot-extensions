# S4 — Incremental transcript catalog (#7950)

[Effort](README.md) · [Validation and ownership](validation.md)

Paths are relative to `plugins/agent-codespaces/`. This is an agent-recommended
design, not a claim that the existing mirror reparses JSON or uploads every poll.

## Causal hotspot map

`src/agent_codespaces/transcript_mirror.py`:

- `remote_script:62-92` walks every remote session directory. Each directory
  executes `printf | tr | awk` over the complete known-offset string; unknown
  sessions run `find`, eligible files `stat`. N directories and K offsets cause
  O(N x K) matching work before incremental transfer.
- `TranscriptMirror.offsets:175-183` enumerates/stats the local mirror again.
- `_read:357-384` regenerates discovery and opens a manager each due read.
- `apply` already commits only whole lines; `_limits` expands a read after a
  huge partial record, capped at `MAX_CHUNK_BYTES`.
- `__call__`, `_push_dirty`, `request_prune`, `_append_and_push` already retain
  per-venue OS locks across cancellation/thread handoff, mark debt before
  writes, retry failed pushes after restart and prune only when debt is clear.
  Preserve those properties and the live-vs-closeout label separation.

## S4a — Remove history-times-offset matching

Replace the per-directory pipeline with one validated associative lookup built
once per invocation, or one structured manifest read. Keep the current GNU Bash
consumer compatibility unless the implementation deliberately declares and
tests a replacement runtime. Validate session IDs, integers and bounds before
building any script. This slice changes O(N x K) lookup to O(N + K), but still
enumerates N directories: report it as an intermediate improvement, not full
#7950 acceptance.

## S4b — Durable local catalog and bounded discovery

Add `transcript_index.py` with versioned catalog/cursor state under the existing
mirror's owned root, keyed by source/installation, account/venue identity,
remote session ID and file incarnation. Do not use a machine-wide session scan.
Index metadata must not be uploaded as transcript content by the existing push
root: keep it outside `<venue>/session-state` and outside the pushed
`<venue>` directory, under a separate owned metadata directory.

Proposed `MirrorCursor`: remote identity (device/inode or platform equivalent,
size, high-resolution change/mtime), committed whole-line offset, read-ahead
offset and bounded pending-tail descriptor, local mirror identity/length,
discovery generation and last verification. Serialize under the existing venue
lock, using atomic replace and explicit schema version. Update cursor only
after the mirrored whole-line write succeeds; preserve mark-dirty-before-write.
On restart validate the affected local file length/identity before trusting its
cursor. A missing/corrupt catalog triggers a bounded rebuild, not deletion of
the mirror or clearing of debt.

Ordinary polls address known active/backlogged IDs directly. New session
discovery uses an already-available producer generation/catalog signal when one
exists; otherwise a bounded, resumable paged enumeration is the compatibility
path. Maintain a rotating verification cursor for quiet historical IDs so an
old transcript that appends again is eventually found. A root directory mtime
alone cannot detect appends to existing files.

Define and publish the maximum discovery/quiet-session catch-up latency before
implementation acceptance. The bound must cover a complete rotating sweep at
the supported catalog size and configured cadence; do not silently trade
unbounded history staleness for fewer calls. Recovery sweeps may enumerate the
catalog, but ordinary consumers never trigger a full history pass. Session
retirement from the active set requires no pending bytes/debt and leaves the
quiet-session verification obligation intact.

## Record and replacement correctness

- Read only appended bytes on an unchanged file incarnation. If an optional
  event-summary index is added, parse only newly completed records, never the
  full JSONL file per poll. The mirror itself remains byte-preserving.
- Handle truncation, including truncation to zero, and replacement with the
  same or a larger byte length. Size-only detection is insufficient. Remote
  generation/identity change resets the cursor and yields an explicit reset
  record even when zero bytes follow. Never append a new file incarnation
  after the old one.
- Copy-truncate/regrow or weak file identity needs a verified producer epoch or
  a bounded continuity check around the committed offset. Inability to prove
  continuity is unknown/reset-required, not silently accepted append. No
  full-file hash on every poll.
- Preserve partial-record bytes in bounded resumable read-ahead storage, so a
  large unfinished line is not downloaded from its beginning every pass.
  Only complete lines become visible in `events.jsonl`; pending data remains
  outside the uploaded mirror. Explicitly report an over-limit record and
  retain its continuation obligation rather than silently dropping it.
- Completion marker loss, malformed records, interrupted responses and stale
  offsets cannot advance the durable cursor or clear debt. Preserve valid
  already-committed lines; resume from the last confirmed offset.
- A replacement owner uses the same OS lock; an old coroutine's worker thread
  retains lock ownership until actual completion. Fence publication by catalog
  and source generation in addition to that lock.
- Push only on changed mirror content or existing debt. A disabled/partial push
  cannot clear debt; no remote contact is needed to retry local push debt.

## Fixed-workload acceptance

Use 10,000 historical synthetic directories, 10,000 known catalog entries and
one active transcript, with identical contents/event schedule before and after.
Retain initial discovery separately from subsequent steady-state polls.

For an ordinary no-discovery-due poll: one remote invocation, no per-directory
offset subprocess pipeline, at most the selected active/backlogged IDs'
metadata/byte reads, zero unrelated local-directory enumeration and zero full
JSONL reparses. A second unchanged pass transfers zero transcript payload bytes
and starts no push. Control-envelope bytes are nonzero and reported separately.

For a due compatibility discovery page of B entries, count O(B + active IDs)
metadata work; retain B, total page count, full-cycle deadline and cost rather
than hiding scans outside the sampling window. Compare the complete fixed
600-second workload, including every due discovery/reconciliation page.

Append, zero truncation, same-size/larger replacement, quiet historical append,
new session, partial line across many chunks, corrupt catalog, failed/partial
push, process crash after append-before-cursor, and owner replacement must all
produce the expected exact mirrored bytes and debt state. Record CPU, process
starts, metadata operations, wire bytes, bytes reread, index/pending-tail disk
growth and cleanup. No savings claim is valid if capture completeness regresses.

## Validation flow

Extend `tests/test_transcript_mirror.py`, especially
`test_the_script_round_trips_a_real_session_state_tree`, whole-line, reset,
large-first-line and push-debt cases. Add `tests/test_transcript_index.py`.

```text
python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_transcript_mirror.py --test-file tests/test_transcript_index.py
```

The second file is a planned addition, not present at the inspected revision.
Use `--allow-explicit-tiers` for real-shell cases assigned to the native tier.
Run real GNU Bash on owned synthetic directories, including supported Windows
Git Bash and Linux lanes; don't substitute string inspection for the shell
round-trip. The integration fixture counts actual utility invocations and file
reads, and injects a full-history lookup to prove the work-budget guard fails.
No live transcript content is needed.

S3 owns optional borrowed-transport admission; S4 owns catalog/cursor semantics
and the mirror-side integration. Sequence their shared `_read` edit explicitly.
