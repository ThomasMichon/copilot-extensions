# Exact-owner steering readiness and optional atomic abandon guards

Canonical contract: [README.md](README.md).

## Qualification and outcome

This study preserves #3002 and #8780 through four whole original/comment
texts, 12,484 characters. Fresh unchanged metadata, editor/history and complete
comment rosters preceded transitive whole-reference preflight. Five distinct
whole messages provide six noncompletion relations. Both originals remain OPEN;
route readiness and atomic CLI guards are independent outcomes.

## #3002 - Preserve all three delivery/readiness alternatives

The original is an owned STARTED task with `owner_session_id=null`: steering
is stored while no owner wake is enqueued and the input remains unread.
Preserve the accepted alternatives: establish a routable binding BEFORE
readiness OR provide a supported alternate exact-owner/headless route OR
report an explicitly undeliverable/not-ready outcome with actionable recovery.
Do not infer a recipient from a newer worktree head, generic coordinator
or unrelated session.

Credit durable/idempotent acceptance, explicit `no_owner_session` receipts,
cold-headless `resume_requested`, #5701's timeout/retry behavior, #7761 wake
custody and #8013 shared drafts. Storage truthfulness and separate cold resume
do not establish the owned STARTED/null-session route.

Keep data acceptance, readiness, enqueue, transport delivery, worker consumption
and actual application distinct. Preserve accepted input through recovery and
idempotent retry; neither synchronous startup nor discarding durable input is
required. Temporarily unroutable input needs queryable actionable pending state
and correct-owner rebinding; permanently unsupported routing must not remain
success-shaped readiness.

Retain exact task/source authority, owner/session/generation and request identity
through late binding, retries, concurrent owner changes and stale delivery.
Admission/rebind belongs to the existing lifecycle owner, not the submitter.
Cover supported local/fleet/interactive variants without generalizing one branch,
and keep STARTED distinct from suspended cold-headless, detached wait and no owner.
Preserve delivery/consumption/application receipts and failures, including null/
unavailable/unsupported routes, owner races, restart/retry and eventual correct
worker consumption.

The delivery custodian remains. The task-conversation amendment consumes the
issue but does not supersede its whole residual or expand another phase's custody.

## #8780 - Expose both optional server-side guards

Add optional `--expected-generation INT` AND `--expected-owner-session-id ID`
to `abandon`, forwarding both through `cli_abandon` to the existing API/server
atomic checks. A same-status task with a newer generation or changed owner
must reject a stale attempt. A client-side check immediately before calling
does not close the race.

Preserve omission compatibility, existing expected-status handling, permission/
reason and live-owner refusal/override policy. Exposing concurrency guards is
not authority to abandon a live task or bypass its claims.

### Finite static consumer follow-through

At source `282b19ecbe4c1b32419e59c16ccaccf9b47917d5`, the complete `cli_abandon`
function forwards only `expected_status`; the complete abandon parser block
defines neither requested optional flag. The reattach module matches the
observed Dev blob. The registration module differs in unrelated start/decline/
reset commands, but its entire abandon block compares equal. This confirms
the finite static residual without claiming whole-module equality, executing
an abandon request or changing code.

## Completed-work comparisons

| Original | Whole candidate | Contract comparison |
|---|---|---|
| #3002 | Commit `5b261f2daa8cd59d8c37ceff22b00bd873c30613` | Captured native-owner wakes and release/cooling fences protect existing sends; they do not supply a null-owner-session route. |
| #3002 | Commit `15086ab1b3d757802317fd3b95eab9ad1124ecc8` | Managed-lane admission from neutral directories uses the durable host binding with identity refusal; it is not STARTED steering readiness or consumption. |
| #3002 | Commit `921b945df694404e80ca225412c87673b0abc3c1` | Fresh reservation due-time admission is not the null-binding steering outcome. |
| #8780 | Commit `cfe20b2d97cc575eb4aea481b94c5287cf7a7988` | Worker-owned decline/own-finalize closeout preserves native capacity, retry bytes and initial guidance; it does not expose abandon generation/owner flags. |
| #8780 | Commit `5b261f2daa8cd59d8c37ceff22b00bd873c30613` | Native wake custody is not optional CLI abandon-guard forwarding. |
| #8780 | Commit `9ba9af3ad85965a59203b5370a9237406b6b41cb` | Native maintenance admission/refusal, lexical pins and bounded platform fixtures are unrelated to atomic task-guard forwarding. |

No task submission, steering, wake, owner probe, abandonment, test or native/
installed operation occurred. See the [dated
journal](<journal/2026/11.04 steering-abandon-guards-contracts.md>).
