# Recover held terminal housekeeping

Correcting an underlying fault does not reopen a reservation whose cleanup
retries are exhausted. `reservations retry-cleanup` schedules housekeeping
through the existing supervisor; it does **not** rearm execution, restart work,
remove a checkout, or bypass the ground layer's disposal decision.

Thin client commands share the server-independent `control_token` HMAC helper
with the coordinator. They do not load FastAPI, Pydantic or uvicorn merely to
derive event-note sender proofs. The `coordinator_auth.scoped_control_token`
export remains compatible; token bytes, proof headers and authorization policy
are unchanged.

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
The direct Python API also requires nonblank string request/preview identities
and a string audit reason before any database access; it does not coerce IDs.
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
Only `session_end` and `worktree_cleanup` are supported state-bearing components;
an unknown component is rejected even when the known components are valid.
`cleanup_kind` alone is not evidence of pending cleanup. Malformed or unsupported
envelopes fail explicitly; kind/action values must be strings and malformed
containers return the normal typed 4xx refusal, not an internal error.
Session-only envelopes are rejected even when they
omit `cleanup_kind`: without `worktree_cleanup`, the existing consumer cannot
guarantee a lease before the session side effect. A complete worktree component
with an incomplete session component is supported and claims/validates its
cleanup lease before ending the exact session.
Legacy reconciliation leaves release-requested component envelopes to this
leased consumer, including disposable tasks whose worktree component already
completed; task labels cannot replace a pending session component with flat
success.

The task generation, exact provenance and allocation fences are rechecked when
the supervisor claims and validates its cleanup lease and records the outcome.
Listed retry state is only a due-time hint: counters, payload and component
decisions are derived again from the authoritative claimed reservation before
effects or publication, so an intervening recovery reset cannot be exhausted
again by stale counters.
Legacy disposable reconciliation uses the same authoritative claim snapshot
before deciding retry counters or exhausted state, including a listed exhausted
row recovered before claim. Its terminal cleanup policy remains unchanged.
The retained allocation identity includes the execution project, physical
worktree path, originating reservation, logical creation key/revision, inherited
worktree and exclusive key. Drifting any of them after acceptance cannot redirect
the leased retry or change which allocation fence applies.
The pre-existing release obligation is retained in that immutable fence too.
Recovery lease claim, validation and conclusion publication carry the
supervisor's resolved `claimant_machine`. Each must match the retained creating
host before granting or using a lease, including session-ending side effects.
Malformed retained recovery JSON or a non-mapping identity returns a typed
conflict without changing the reservation, task, audit events or history.
The foreground supervise construction uses the same explicit/configured local
alias resolution as registration scope; it does not omit that identity.
Direct supervisor construction resolves its default local alias before binding
the housekeeping adapter, so explicit and default identities carry the same
lease proof.
Successful cleanup claim responses must contain a nonblank string token and
their authoritative reservation snapshot before validation or side effects.
Malformed responses emit a protocol warning and retain pending cleanup.
The bound adapter accepts omitted or matching explicit claimant values and
rejects a conflicting override before any request. The optional legacy claim
body is instantiated per request rather than shared across requests.
The host binding is confined to those housekeeping RPCs. Ordinary spawning and
label-routed snapshots retain the supplied client's identity and lifetime;
the supervisor does not globally substitute a proxy client.
Unknown/empty or foreign claimants reject without changing progress or stealing
another host's lease; the coordinator never guesses a local hostname. Ordinary
conclusion rows without recovery history retain their compatible optional-host
behavior. Recovery outcomes use the host-carrying conclusion endpoint; generic
fail/settle aliases cannot publish an unbound recovery outcome.
At lease claim, unchanged task generation and provenance are checked first. A
newer reservation carrying the worktree then uses the ordinary durable
`held`/`worktree-carried-by-newer-reservation` transition, even if the newer
reservation has settled, rather than remaining pending and failing every cycle.
Equal-timestamp competing reservations are ambiguous too and use the same
inclusive fence, transitioning once to held instead of repeatedly failing pending.
No lease is granted on that path. All allocation fences still run before any
actual lease grant, and again at lease validation and outcome publication.
All ordinary terminal guards still apply: live/unknown processes, dirty or
unlanded work, open PRs, unfinished efforts, claims, pairing and ambiguous
ownership remain protected. Preview eligibility is permission to retry those
guards, never an assertion that they will pass.
Lifecycle lock recovery retains its per-path guard across proven stale PID/birth
cleanup and exclusive token creation. It does not release/reacquire that guard
between those steps or grant additional deadline time. Proof already outside the
caller deadline cannot unlink a token; a reappearing exact token is never overwritten.

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
Receipt hashing requires string keys throughout that document, including nested
mappings and lists. Unsupported YAML keys are rejected rather than coerced into
colliding JSON names; GC treats that evidence as unverified and preserves it.
For a confirmed dispatch-attempt allocation, malformed reciprocal evidence is
rejected by read-only shape validation before process/liveness or Git work,
with the same check repeated under the primary record lock. Eligible paths
still undergo all later identity, liveness, ancestry
and locked reciprocal revalidation; the preflight never grants disposal authority.
Managed garbage collection remains the deletion authority.
The sibling receipt pins the exact primary record, and a primary-side guard
keeps that record available until the sibling receipt has been collected.
The primary remains protected until both the sibling receipt and sibling YAML
are gone, including the receipt-before-YAML rollback window. Conclusion and GC
share matching-ID, complementary-role and exact reciprocal-reference checks.
GC preserves both during partial unlink, verifies durable primary unlink before
collecting the sibling, then collects the primary independently. Missing,
changed, malformed or unresolved legacy proof remains preserved; absence is
never used to infer that a paired discharge succeeded.

The owned terminal-pair receipt follows its worktree record's lifetime. The
authoritative launch-seed/record removal deletes the owned seed and exact pair
receipt first, then deletes the authorized YAML record **last**, under the same
record lock. The YAML remains available to authorize normal retry if teardown
is interrupted at either sidecar boundary. Once YAML deletion has happened,
both owned sidecars are already absent, so repeated removal is idempotent rather
than stranding an orphan receipt. Seed-only
removal and preserved/live records retain pair evidence; unrelated sidecars and
task history are untouched. Caught deletion failures restore the previously
retained raw-byte state snapshots through the existing atomic-write primitive, then
surface the error so the normal removal can retry. Missing record authority and
symbolic-file ambiguity preserve state rather than authorizing orphan pruning.
An abrupt process interruption may leave sidecar teardown partially complete;
it does not remove YAML authority before that teardown finishes. Retry addresses
only that exact record, never a newer worktree identity or foreign artifact.
This is owned-artifact teardown, not a retention sweep or record-writer repair.
The authoritative `launch_seed_remove` verb advertises and requires version 5
for this teardown behavior. A known pre-v5 monitor is refused before dispatch
or direct fallback, preserving YAML/seed/receipt state during rolling upgrade.
That refusal applies to live or unprovable owners. A positive dead-PID or
PID/birth mismatch makes the stale rendezvous absent for normal monitor startup
or authorized direct fallback; missing/malformed identity and unreadable birth
tokens never authorize that inference. No stale-owner record repair is performed.
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
These three request bodies reject unknown fields and incorrect value types with
HTTP 422 before queue access. Omitting the legacy claim body remains supported
for ordinary conclusions without recovery history.
