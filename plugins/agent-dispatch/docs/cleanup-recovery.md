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

Recovery requires an already-recorded `release_requested` obligation, in addition
to a completed or abandoned task at the exact expected
generation, a retired `failed`/`settled` reservation with held cleanup, recorded
created-worktree provenance, a known driver and the matching creating host.
It never creates release authority from a held result or task labels. A missing
or unknown obligation is rejected without changing the row, events or recovery
history; the existing lifecycle owner must establish any release obligation.
The owning supervisor must match the task's established pool.
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
payloads without components remain supported. Structured envelopes must identify
the normal producer's `worktree_cleanup` component, with at least one incomplete
housekeeping component;
`cleanup_kind` alone is not evidence of pending cleanup. Malformed or unsupported
envelopes fail explicitly. Session-only envelopes are rejected even when they
omit `cleanup_kind`: without `worktree_cleanup`, the existing consumer cannot
guarantee a lease before the session side effect. A complete worktree component
with an incomplete session component is supported and claims/validates its
cleanup lease before ending the exact session.

The task generation, exact provenance and allocation fences are rechecked when
the supervisor claims and validates its cleanup lease and records the outcome.
The retained allocation identity includes the execution project, physical
worktree path, originating reservation, logical creation key/revision, inherited
worktree and exclusive key. Drifting any of them after acceptance cannot redirect
the leased retry or change which allocation fence applies.
The pre-existing release obligation is retained in that immutable fence too.
Recovery lease claim, validation and conclusion publication carry the
supervisor's resolved `claimant_machine`. Each must match the retained creating
host before granting or using a lease, including session-ending side effects.
Unknown/empty or foreign claimants reject without changing progress or stealing
another host's lease; the coordinator never guesses a local hostname. Ordinary
conclusion rows without recovery history retain their compatible optional-host
behavior. Recovery outcomes use the host-carrying conclusion endpoint; generic
fail/settle aliases cannot publish an unbound recovery outcome.
At lease claim, unchanged task generation and provenance are checked first. A
newer reservation carrying the worktree then uses the ordinary durable
`held`/`worktree-carried-by-newer-reservation` transition, even if the newer
reservation has settled, rather than remaining pending and failing every cycle.
No lease is granted on that path. All allocation fences still run before any
actual lease grant, and again at lease validation and outcome publication.
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
The raw sibling YAML must contain none of the four pair keys before receipt
reconstruction, regardless of how record loading normalizes false, empty, null,
numeric or unsupported values. This absence check and the non-pair digest use
the same parsed document; malformed or reintroduced pair keys preserve both
records and the receipt with the ordinary pair-preservation reason.
Managed garbage collection remains the deletion authority.

The owned terminal-pair receipt follows its worktree record's lifetime. The
authoritative launch-seed/record removal deletes the owned seed and exact pair
receipt first, then deletes the authorized YAML record **last**, under the same
record lock. The YAML remains available to authorize normal retry if teardown
is interrupted at either sidecar boundary. Once YAML deletion has happened,
both owned sidecars are already absent, so repeated removal is idempotent rather
than stranding an orphan receipt. Seed-only
removal and preserved/live records retain pair evidence; unrelated sidecars and
task history are untouched. Caught deletion failures restore the previously
retained UTF-8 state snapshots through the existing atomic-write primitive, then
surface the error so the normal removal can retry. Missing record authority and
symbolic-file ambiguity preserve state rather than authorizing orphan pruning.
An abrupt process interruption may leave sidecar teardown partially complete;
it does not remove YAML authority before that teardown finishes. Retry addresses
only that exact record, never a newer worktree identity or foreign artifact.
This is owned-artifact teardown, not a retention sweep or record-writer repair.
The authoritative `launch_seed_remove` verb advertises and requires version 4
for this teardown behavior. A known pre-v4 monitor is refused before dispatch
or direct fallback, preserving YAML/seed/receipt state during rolling upgrade.
The caller does not substitute manual unlink or restart/take over that monitor.

## API

`POST /spawn-reservations/{key}/conclusion/recover` accepts
`expected_generation`, `current_host`, and optional `apply`, `expected_preview`,
`request_id`, `reason`. The current host is an authenticated control-plane
attestation; the CLI supplies its resolved local supervisor identity, including
configured aliases rather than substituting the OS hostname. It is not a liveness
attestation. `GET /spawn-reservations/{key}/conclusion/recoveries` reads retained
failure history. Both require control authority; unknown routes on older peers
fail rather than silently performing another kind of recovery.
The existing conclusion claim/validate/record endpoints accept optional
`claimant_machine` for legacy compatibility, but require a known retained-host
match for every recovery allocation.
