# Bounded Azure DevOps PR remediation

These explicit, one-shot operations resolve the target repository's own PR
binding and account, independently of the caller's active project. Other
providers report unsupported. Existing watch/default behavior is unchanged.
Neither operation votes, approves, asserts human presence, bypasses policies,
creates builds, or provides a general HTTP/REST command.

```
pr-watch requeue Project/repository 123 --policy "Validation" --reason expired --expected-head FULL_SHA
pr-watch requeue Project/repository 123 --policy 42 --reason flaky --expected-head FULL_SHA --evidence-file evidence.txt --json
pr-watch reply Project/repository 123 --thread 7 --text-file reply.txt --expected-thread sha256:FINGERPRINT --idempotency-key fix-round-2 --status fixed --json
```

Input files are UTF-8 and at most 64 KiB. Flaky recovery requires at least 40
characters of substantive, operator-provided evidence explaining why this
particular rejection warrants a retry; the tool does not diagnose flakiness.

## Existing evaluation only

Requeue selects exactly one latest native evaluation by configuration ID or
case-insensitive display name. Chronology uses the shared UTC-normalized,
fail-closed policy reader (fractional seconds and timezone offsets included).
The policy must be enabled, blocking, and the native Build type. A nonempty,
unambiguous source commit must match the full expected current source head.
An existing evaluation UUID is mandatory.

Expired recovery must still be expired immediately before PATCH. Flaky recovery
must still be rejected, not running, broken or expired. The tool revalidates
lifecycle, head, configuration and evaluation before the sole evaluation
mutation: `PATCH .../policy/evaluations/<id>?api-version=7.1-preview.1` with
`{"status":"queued"}`. Queued readback is mandatory.

Flaky recovery first posts a closed evidence thread with both the legacy
`<!-- watch_pr:requeue POLICY@HEAD_FIRST_12 -->` marker and a central marker
containing a canonical full-target digest, policy ID and full head. Complete
thread reread elects the oldest integer thread ID when competing markers are
visible. Existing legacy markers remain protective after migration.
Routine expired recovery deliberately posts no note, preserving non-spammy
legacy behavior. Neither server markers nor local locking constitute a
distributed atomic claim: concurrent machines can race, especially expired
recovery. **Distributed exactly-once is not claimed.**

## Discussion revision, not current source head

`providers.azure_remediation.thread_fingerprint(native_thread)` produces the
`sha256:...` expected-thread value for the `pr-watch threads` projection.
It covers the full native thread, including stable thread/comment/parent IDs,
authors, timestamps, status, body and native comparison iteration context.
Complete native thread reads and existing iteration membership are required.
A source push does **not** invalidate an unchanged older-round discussion.
Fetch fresh threads after another reply, edit or resolution; never reuse a
fingerprint from an older thread revision.

The exact UTF-8 reply text is preserved and an invisible central idempotency
marker is appended. The parent is the existing native root comment ID, not a
newly invented or stringified parent value. Only that selected thread can receive
an optional native `fixed`, `wontFix`, `byDesign`, `closed` or `pending` PATCH,
after a verified comment and unchanged discussion readback. No bulk resolution.

The idempotency key binds target, thread, text and optional status; the initial
expected fingerprint is retained as a hash-only precondition. Same key/same
request reconciles marker-bearing readback without another
POST; a different request for that key, or conflicting server marker, refuses.
An uncertain POST with no matching readback is never automatically retried.
If a reply exists but requested status is unverified, reconciliation returns
`partial` and performs no further write: inspect the native thread, then use a
new explicitly reviewed action rather than blindly replaying partial mutation.

## Consumed state and recovery

The registry's `pr-remediation/<canonical-target-digest>.json` contains only
request hashes, native IDs and state, never bodies or tokens. A repo-standard
OS-held per-target file lock serializes local processes. Atomic replacement
records `in_flight` **before any write**. Success becomes `verified`; errors,
interruption and transport-uncertain outcomes remain consumed (`unknown`, or
`in_flight` after a hard crash). Corrupt/unreadable ledgers fail closed.

Requeue consumption is once per full canonical target, full source head and
policy ID, regardless of reason or new evaluation. No automatic retry, skip,
ledger clearing or retry-reset switch exists. Inspect native read-only reports
to reconcile unknown outcomes; retain the consumed ledger. This is a
machine-local safety boundary, not a cross-machine consensus service.
