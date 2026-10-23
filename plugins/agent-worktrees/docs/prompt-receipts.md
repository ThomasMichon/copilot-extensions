# Recovery prompt receipts

Context-handoff's optional observer records only that a real first user message
carried a recovery locator. This does not consume a handoff, transfer a claim,
establish head ownership, or authorize process retirement.

The internal `activity-log context_handoff_prompt_received --receipt-json`
operation requires a known session, worktree and locator (`--field locator=...`).
It resolves the active project through the normal CLI configuration and uses
the existing `handoff_trace` per-project/worktree store and kernel-backed append
lock. Replay detection and durable append happen under one acquisition, across
processes. The result is a compact `received`/`replayed` JSON object.

`activity --worktree-id <id> --receipt-locator <locator>` returns the same
bounded shape for a read. Missing data is `received: false`; an unreadable store,
unknown project or invalid locator fails instead of claiming absence. No
history is returned. Records are stream-matched with a bounded per-record buffer.
An oversized individual record is unavailable rather than silently skipped:
skipping a potentially matching receipt would weaken replay detection.

Older matching rolling-log receipts remain readable and migrate into the
existing durable trace on a recording retry. The durable trace's ordinary
worktree-removal cleanup owns these records and their lock; there is no separate
receipt ledger or retention service. The observer runs this work outside the
prompt callback and retains unavailable observations for later reconciliation.

## Bounded resident operation

The separate `resident_prompt_receipt` callable is the closed control adapter's
in-process backend, not an arbitrary path/CLI entry. It accepts the creation
nonce and an existing owner-supplied record admission guard. Its immutable key
is installation scope, project, worktree, creation nonce, session and locator.
The guard precedes the trace lock and revalidates association after admission
waits and before append. No broad daemon lock is held during I/O.

Contention admission is capped at 100 ms. Total consulted history is capped at
1 MiB, 4,096 rows and 200 ms, with a 64 KiB individual-record cap. Pre-effect
work is capped at 1,000 ms and the caller's absolute deadline. These bounds do
not pretend that OS I/O or fsync can be interrupted.

Only an exact qualified query can return `absent`. Missing association,
unqualified matching legacy rows, oversized input and exhausted budgets are
unavailable. Once append may have occurred, an error or late completion is
unknown; the control owner keeps in-flight ownership through settlement.
Queries while a mutation is still in flight must remain unknown. The legacy
CLI compatibility reader is not the resident authority/admission contract.
