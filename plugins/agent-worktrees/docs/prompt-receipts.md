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
Matching queries synchronize the same open history file before acknowledging
receipt, including the compatibility activity reader. A synchronization failure
propagates as unavailable instead of bypassing an earlier uncertain append.

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
Before recording, the backend reserves row and byte capacity for the receipt
and any separator newline. It refuses an append that would put the new receipt
beyond the bounded query window, rather than creating an unreconcilable commit.
Both writers also refuse a separator that would grow an unterminated terminal
record beyond 64 KiB. An already-matching maximum-sized record can still replay
without adding a separator or changing history.

Only an exact qualified query can return `absent`. Missing association,
unqualified matching legacy rows, oversized input and exhausted budgets are
unavailable. Once append may have occurred, an error or late completion is
unknown; the control owner keeps in-flight ownership through settlement.
Queries while a mutation is still in flight must remain unknown. The legacy
CLI compatibility reader is not the resident authority/admission contract.

A complete readable row alone is not proof of durability after a failed
append/fsync. Exact resident queries and recording replays synchronize the
matching existing trace under the same admission and trace locks before
reporting `received`. A failed or late synchronization remains `unknown`;
reconciliation does not append another receipt.
The resident reader keeps the matching descriptor open through owner
revalidation and synchronization. A rolling-log replacement cannot redirect
that synchronization to a different pathname target. The bounded history
descriptors close before the trace and record guards are released.

## Focused source integration

The opt-in native `tests/native/test_resident_prompt_receipt_integration.py`
composes the actual registration caller/subject writer, ControlServer, resident
backend and Node observer using isolated selected-installation, session and
tracking files. Registration-only lifecycle services and checkout trust are
fixture-owned seams, not live services. The fixture allows only the harness's
exact Node invocation; producer, backend and observation helper launches fail.
It checks a registered non-head subject, an exact pending candidate, and an
actual legacy rewrite that removes trusted subject fields.
The registered first-message callback also runs through the real socket path:
it must return before socket I/O, settle within the unchanged whole-observation
budget and launch no helpers. Additional controls exercise four retained
in-flight operations with no fifth admission/queue, the 8 KiB request boundary,
oversized responses after commit, and the exact uncertain-identity fence.

Run it through `tools/run-plugin-tests.py agent-worktrees --allow-explicit-tiers
--test-file tests/native/test_resident_prompt_receipt_integration.py`, with
`RECEIPT_CLIENT_ROOT` naming the explicit test client payload. A separately
owned provider can be supplied through `RECEIPT_PROVIDER_SOURCE` as an
immutable, byte-verified source fixture; it is never a production fallback.
These contained source tests do not establish installed/native-host readiness
or authorize enabling the backend's ready capability.

The native fixture alone accepts `RECEIPT_TEST_BUDGET_FACTOR=1|2|3` (default
`1`, exact integer spelling). It verifies that explicitly selected experimental
client/provider copies have matching observation/reserve/caller time constants
before constructing a resident. Only its in-process lock/scan/pre-effect
budgets and wall-clock test guards scale; production files/defaults, polling
cadences and all byte/row/count/identity/custody/cleanup assertions stay intact.
The deliberately slow scan remains above the active scan budget. Raw elapsed
and callback measurements are never normalized; nominal and experiment budgets
and outcomes are reported separately. A scaled pass is not a nominal pass, and
factor `3` requires separate run authorization rather than automatic retry.

Pure `tests/test_receipt_budget_fixture.py`, Node syntax checks, and the existing
driver's `--fixture-contract-smoke` mode validate factor/argument representation
without importing the observer, opening a socket, creating a native session or
invoking the backend. Fixture preparation and smoke validation are not permission
to execute the native selector.

One bounded full33-case factor2 source-fixture run was accepted at preparation
head `8f0b0f30ec570f4abf2fc1ab65e377f13607fd83`: all cases and their cleanup
assertions passed, with zero code-path helpers and at most four in-flight
operations. All51 captured observations were below4,000ms; two were not below
the nominal2,000ms target (maximum3,851.874ms). The SDK shell in that lane was
explicitly a double. This is not actual SDK-host/refork, whole hook-dispatcher,
installed or nominal-SLA acceptance, and does not enable the ready capability.
