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
