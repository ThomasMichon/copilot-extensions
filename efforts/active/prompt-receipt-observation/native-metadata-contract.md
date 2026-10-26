# Native client-metadata pending slot

This is the admitted owner/API decision for review finding #8026 discussion
`4365810923`. It does not change receipt authority or qualify a native host.

## Selected existing owner

Use the SDK session's native `rpc.metadata.snapshot()`, `getClientMetadata()`
and `updateClientMetadata({set})` methods. The current host SDK 1.0.101
declarations were read directly: `session.d.ts` exposes the generated session
RPC, and `generated/rpc.d.ts` declares the persisted local-session-only private
string bag and atomic patch result. This is not model context, an event log,
telemetry, a remote export, a file-provider registration or a filesystem API.

There is **no CAS or expected revision**. Do not emulate one with get/overwrite,
`clear`, removal, timestamps, a current-head choice or another ledger.
Runtime coordination of local cross-process writers is the owner contract;
custom SessionFs providers must serialize their own writers.

Bind every operation to the actual SDK session ID and a local snapshot.
Unsupported methods/RPC, remote sessions, mismatched session IDs, malformed
responses and storage failures permit no receipt mutation and no fallback.

## Three normal records, one first-prompt slot

Owner namespace: `context-handoff/prompt-receipt/v1/`. Keys contain the phase
and SHA-256 of canonical immutable identity. Values are canonical JSON strings;
repeated writes for a key have identical bytes. Only `{set: {ownKey: value}}`
is sent. No clear, remove, foreign-key update or cleanup is performed.

1. **Capture:** actual native session ID and canonical first recovery locator.
   A null locator records a checked first prompt with no recovery seed; that
   capture is terminal and cannot produce qualified/confirmed receipt records.
   Prompt text is never stored.
2. **Qualified possibly-sent:** capture plus stable installation identity,
   runtime root and exact project/worktree/creation-nonce/session/locator.
   Install/activation and route/runtime generations remain handshake freshness,
   not immutable pending identity.
3. **Confirmed:** a monotonic marker for that exact qualified identity.
   It is observation memory, not ownership, authorization or consumption.

Conflicting captures/qualified identities or unrelated confirmed identities
remain uncertainty and refuse new mutation; never select the current binding
or overwrite the old record. A same-identity confirmed marker wins late
qualified/capture writes. Terminal records are retained because safe
non-resurrection cleanup is not proven.

There is one first-prompt slot per actual native session, not an unbounded
sequence of handoff episodes under the same session ID. A different logical
capture is refused rather than opening a new slot/history. Missing state from
pre-protocol code cannot reconstruct an already-lost earlier identity or prove
historical first-message order; no retrospective legacy recovery is claimed.

Reads validate all owned records and native bag limits without logging the bag
or foreign keys. Normal owned count is at most three; conflict/capacity fails
closed. Own values are at most 8 KiB. Native key/value/count/serialized-bag caps
remain authoritative: 256 UTF-8 bytes, 16 KiB, 128 entries and 64 KiB. Concurrent
capacity rejection is unavailable, never permission to evict another owner.

## Pipeline and lifetime

Registration restores asynchronously before observation/reconciliation.
The synchronous first-message callback only captures and schedules.
Before a record socket can be admitted, publish its possibly-sent identity and
validate the API's committed bag plus a read-back. Re-publish the identical
qualified key at each prospective record admission rather than treating a
plain read as CAS. Read-back must retain every committed owned record, so a
stale read cannot downgrade a confirmed marker. A fresh state restored from
that phase must query the original exact identity first. Existing source,
installation, runtime-root, nonce, session and locator fences remain in force.
A fresh capture-only restore lacks a qualified tuple and therefore cannot
query exactly or select a new current binding. It remains unavailable unless
the original qualified record appears or the same retained local state still
holds the complete original identity. This deliberately sacrifices recovery
liveness after a partial pre-send commit rather than redirecting uncertainty.

Use the same bounded observation deadline for restore, qualification, socket
work and confirmation. An uncancellable metadata RPC remains owned and
single-flight until actual settlement, even if its caller times out/disposes.
After each await, suppress late work when the deadline or disposal signal has
changed. Re-registration cannot queue another RPC behind an unsettled one.
Late identical writes commute and cannot erase confirmation.

The worktree trace remains the canonical durable receipt. The SDK declaration
supports persisted atomic commit, **not an asserted fsync/power-loss guarantee**.
Actual host process/refork restoration is a separate qualification gate.

## Validation and exclusions

Pure adapter and actual registration-path tests must cover fresh state,
parallel native clients without CAS, late writes/disposal, terminal dominance,
changed identity, unsupported/remote/corrupt/oversized/capacity/error paths and
zero record effects when pre-send persistence is not proven.

No handoff-request reuse/directory creation, P producer/control changes,
service/helper/installer, extra CLI/session creation or extension reload is
authorized. Actual runtime persistence waits for a separately admitted H2
native test slot and harness mechanism. Source review/CI and the explicit
draft/no-merge hold remain required.
