# Recover held terminal housekeeping

Correcting an underlying fault does not reopen a reservation whose cleanup
retries are exhausted. `reservations retry-cleanup` schedules housekeeping
through the existing supervisor; it does **not** rearm execution, restart work,
remove a checkout, or bypass the ground layer's disposal decision.

Use the owning host and a configured control token. First inspect the exact
reservation and task generation, correct the fault through its owning tool,
then preview:

```text
agent-dispatch reservations retry-cleanup <reservation-key> --expected-generation <generation>
```

The preview is read-only and returns a `preview_token`. Apply that exact preview
with a stable request identity and an audit reason:

```text
agent-dispatch reservations retry-cleanup <reservation-key> --expected-generation <generation> --apply --expected-preview <preview-token> --request-id <unique-request-id> --reason "underlying fault corrected"
agent-dispatch reservations cleanup-history <reservation-key>
```

Replaying the same apply request returns its original acceptance without
resetting progress again. Reusing its identity with different parameters is
an error. `scheduled: true` means accepted, not successfully cleaned or removed.
Inspect reservation conclusion progress afterward; the existing supervisor
claims a tokenized, expiring cleanup lease before executing the retry.

## Fences and retained evidence

Recovery requires a completed or abandoned task at the exact expected
generation, a retired `failed`/`settled` reservation with held cleanup, recorded
created-worktree provenance, a known driver and the matching creating host.
Submitted work is not completed work. Newer or ambiguous allocations, active
exclusive ownership and unexpired cleanup claims reject recovery. Historical
`exclusive_released: false` on a settled reservation is not an active fence,
and `conversation_retired` is not evidence of process absence.

Only housekeeping retry eligibility, counters and due times are reset.
Already-complete housekeeping components remain complete. Task lifecycle,
attempts, generation, result, timestamps, logs and session/worktree attachment
history are unchanged. The full previous failure envelope and request reason
are retained transactionally in the cleanup recovery history, including after
later success replaces the current conclusion detail. Old recorded flat retry
payloads remain supported. Structured envelopes must identify at least one
supported housekeeping component, with at least one incomplete component;
`cleanup_kind` alone is not evidence of pending cleanup. Malformed or unsupported
envelopes fail explicitly.

The task generation, exact provenance and allocation fences are rechecked when
the supervisor claims and validates its cleanup lease and records the outcome.
The retained allocation identity includes the execution project, physical
worktree path, originating reservation, logical creation key/revision, inherited
worktree and exclusive key. Drifting any of them after acceptance cannot redirect
the leased retry or change which allocation fence applies.
All ordinary terminal guards still apply: live/unknown processes, dirty or
unlanded work, open PRs, unfinished efforts, claims, pairing and ambiguous
ownership remain protected. Preview eligibility is permission to retry those
guards, never an assertion that they will pass.

For paired worktrees, an atomic ground-layer receipt retains the exact
reciprocal identity and terminal sibling document before unlinking the sibling.
A crash after that unlink can resume only when the receipt still matches both
the dispatch allocation and the unchanged sibling document (excluding only
the discharged pair fields). Live, modified, missing or retargeted siblings,
and missing/malformed receipts, remain preserved. Existing pre-receipt
one-sided corruption is not repaired by inference. An intermediate `primed`
outcome with explicit `managed_gc_eligible: false` remains retryable, not complete.
Receipt pair fields must be non-empty strings with supported complementary
roles and worktree kind before retained provenance can be reconstructed.
Managed garbage collection remains the deletion authority.

## API

`POST /spawn-reservations/{key}/conclusion/recover` accepts
`expected_generation`, `current_host`, and optional `apply`, `expected_preview`,
`request_id`, `reason`. The current host is an authenticated control-plane
attestation; the CLI supplies its resolved local supervisor identity, including
configured aliases rather than substituting the OS hostname. It is not a liveness
attestation. `GET /spawn-reservations/{key}/conclusion/recoveries` reads retained
failure history. Both require control authority; unknown routes on older peers
fail rather than silently performing another kind of recovery.
