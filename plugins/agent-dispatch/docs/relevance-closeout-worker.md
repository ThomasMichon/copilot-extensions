# Original-worker relevance closeout

Back to the [relevance receiver](task-relevance.md#original-owner-http-provider)
and [task-scoped authority](task-authority.md).

The original native worker can run:

```text
<agent-dispatch catalog argv[0]> relevance-closeout TASK
```

Use the attributable command from the session catalog. The command accepts no
worker, native, machine, worktree or generation override. It derives the native
from `COPILOT_AGENT_SESSION_ID` and requires the captured workspace to match the
actual current directory. Ordinary callers use the existing local identity
resolver and disable SSH failover. A projected task caller keeps its pinned
coordinator, exclusive task credential and projected worker; it does not
discover fleet endpoints, load shared credentials or require global health.

## Ordering and custody

1. Read the worker's own closeout route and validate `task_relevance_closeout/v1`,
   version 1, the captured task/worker/native/generation and managed workspace.
   A committed acknowledgement returns the confirmed outcome without another
   prepare, finalization or terminal mutation.
2. Ask the coordinator to prepare readiness and settle the original assignment
   relationship. A retained or unconfirmed claim prevents finalization.
3. Invoke the selected Worktrees runtime's canonical `finalize-own` path,
   after its existing closeout-contract discovery. Select the **original
   allocation key from the independent readiness observation**, not the
   potentially later execution reservation in `resource_binding`. Verify the
   returned workspace and captured creation nonce.
4. Ask Dispatch to finish. Dispatch independently observes finalized state
   before its existing terminal transition; no worker-supplied finalization
   receipt is sent.

The Worktrees owner retains actual-native/head, allocation, source/landing,
PR, effort, foreign-claim and hold checks. This command neither bypasses those
checks nor yields, declines, abandons first, unregisters or terminates a native.
The existing session/conclusion owners retain their later responsibilities.
It does not delete the checkout or directly release a reservation.

### Inspect readiness without settling the driver

`GET /tasks/{task_id}/relevance-closeout/observe-provisional` advertises
`provisional_observation_versions: [1]` to the original owner when the receiver
supports a read-only observation via `POST` to the same path.
Use the existing closeout binding (`version`, `request_id`, `worker_id`, `native_id`,
`expected_generation`), with no finish action or caller-supplied evidence. Do not
substitute `/prepare` on an older receiver: that operation settles the claim.

The observation asks the selected Worktrees owner whether the active driver is
finalizable **except for its exact bound task claim**. Other unsettled or unknown
obligations still block. Each call checks the original binding before and after
the independent owner read. A driver that submitted before the source closeout
can also inspect readiness: the recorded submitter, native session and generation
must still match the captured assignment, even though its lease has ended.
Dispatch checks its held assignments, detached waiters and wake/send records
on both sides of the owner read. Each snapshot uses the
[current driver lookup](pool-placement.md#current-driver-lookup-internal);
both a match and an unknown binding block. A clear binding result does not
supersede the independent transport checks or the Worktrees observation.
Other work holding the same worker or native
on the same host blocks even without a corresponding Worktrees claim.
An unreleased pre-start placement targeting
that tree on its selected machine blocks before the task has an owner or native.
A bare legacy owner is not a hostname: a matching native without a qualified
route cannot prove the drivers are independent. Suspension
retains the assignment independently of any waiter or wake. A submitted task
retaining its submitter and native return identity also blocks until that binding
is discharged; a completed task's historical submitter is not a current assignment. A
current pending delivery (including a qualified retry) also blocks; a stale
unattempted wake or this observation's own pending closeout notification does
not. A confirmed single-attempt delivery settles an unqualified wake; a later
retry cannot establish what happened on an earlier ambiguous attempt.
For owner and waiter wakes, a qualified workspace target and a native target
are independent: a different native does not clear an unresolved wake for the
same host and tree. A terminal send label without an attributable outbox
envelope is not clearance.
For each outbox, Dispatch records the first delivery attempt with a qualified
send identity. A later `sent` or `not_sent` settles only that send. If accounting
began after attempt one, readiness still refuses. A retry of an exact known
refusal remains supported when accounting began on attempt one. On upgrade,
an existing multi-attempt row with no recorded first ordinal is not assumed
to have been qualified from the start. This evidence is for observation only:
it does not authorize a retry or change an outbox outcome.

Non-wake execution sends are also checked by destination, independently of
task lineage, reservation status or subsequent workspace changes. A qualified
sender freezes its selected route in the existing send identity before transport.
A driver route binds the execution host and the known workspace/native selectors
against the reservation in the admission transaction. Either same-host selector
blocks until the owner settles the send. The container launcher instead records
the selected venue and container directory; direct Bridge delivery records the
addressed session. Neither proves a managed driver identity. An unresolved
incomparable or legacy target therefore blocks observation, even across tasks;
it is never reconstructed from the reservation's current contents. Settled sends
do not block. No second send ledger or send permission is created.
A successful response identifies the request,
generation and assignment event; it does not persist a readiness receipt, submit,
authorize release or send a wake. Previously settled and finalized drivers cannot
use this operation to regain authority.

A still-authorized task whose goal is to correct a specified result can already
use ordinary guarded submission, rejection and resubmission in its original
workspace. That is not a way to amend an obsolete task's goal. For the latter,
an executing correction requires a distinct, owner-authorized revision objective
bound to the inspected proposal, driver and generation. Neither a readiness
observation nor evaluator feedback establishes that authority.

### Finalized review submission

For a started or suspended `pr-review` task with an independent evaluator,
the original worker retains a submission proposal **before** settling the
assignment. The receiver verifies the finalized workspace, the original
worker/native binding and the retained proposal before it submits the task
and schedules ordinary verification. Submission is not confirmation. A lost
finish reply must not cause another finalization or verification request.
The worker negotiates `submit-finalized-review/v1` on its existing closeout
route; a legacy caller cannot finish this case as abandonment on a new
receiver. Roll out the receiver before the caller. Existing acknowledged
legacy abandonments are not rewritten.

Never-claimed work without resources can instead be abandoned when the
source explicitly confirms closure. An allocated workspace or pending
launch is not resource-free merely because no worker has affirmed start.

## Compatibility and retries

The receiver expansion precedes this conditional caller. Newly accepted
closeouts use the existing durable wake to direct the original worker to its
session's attributable Dispatch command. The worker first reads that executing
CLI's `--help`; only a CLI listing `relevance-closeout` is eligible for the
invocation above. Do not select an ambient PATH command, infer support from a
package version, or try an effectful command to discover support. The command
then checks the serving coordinator through its own closeout route.

A missing catalog command, prior CLI, unsupported receiver or unavailable
authority leaves the original closeout pending and is reported in the existing
conversation. There is no generic `show`/`steer take` prerequisite: a task-only,
submitted or same-generation unassigned captured owner may not have those
permissions. Do not mint credentials or fall back to generic abandonment.
Existing accepted wakes, their request identities and sent/unknown outcomes are
not rewritten or replayed when this prompt changes. Source adapters remain
separately opt-in; baseline commands retain their semantics and no old surface
is retired. Native conclusion and installed-runtime qualification remain open.

Each mutation re-reads the same own-closeout discovery route. Missing, invalid
or unsupported protocol metadata, including provider rollback, refuses before
that mutation. An unsupported Worktrees finalization contract refuses before
finalization; preparation may already have settled the exact task claim.
There is no equivalent legacy abandon fallback.

For a review that requires submission, the mixed-version window works as follows:

| Caller | Receiver | Result |
|---|---|---|
| Old | Old | Both use the previous abandonment contract; neither can submit a finalized review. |
| Old | New | Discovery refuses the old caller. A direct prepare or finish without the submission action also fails. |
| New | Old | Discovery does not advertise a finish action. The caller stops before preparation. |
| New | New | Both check the action and original assignment. Finish submits the finalized review for evaluation. |

Roll out the receiver first and keep it available until participating callers
finish. Discovery is not a lock across services. If the receiver rolls back
between discovery and a POST, the old receiver rejects the new `finish_action`
body field before its handler runs. A rollback after preparation can therefore
leave a closed assignment; after local finalization it can leave a finalized
workspace without a submission. Restore a compatible receiver and resume the
original closeout; do not strip the field or switch to an old caller to finish.

Readiness/relationship calls reuse the existing bounded 300-second owner
settlement read timeout without changing the general client timeout. Unknown
responses are reported, not automatically replayed by a fallback. Rerunning
the command converges through the original closeout request: a lost prepare
reply reuses retained readiness/claim settlement; a lost finalization reply
uses canonical idempotent finalization; a lost finish reply is recognized from
the committed acknowledgement. Unresolved finalizing state still refuses.
Expired authority needs the existing trusted recovery owner, not self-renewal.

Contained parser/HTTP/queue controls use synthetic Worktrees IPC and cover
current and unsupported receivers, rollback, original-allocation selection,
wrong native/directory, retained claim responses, blocked finalization,
nonce mismatch and lost replies. These are source controls, not proof of
installed-native closeout or end-to-end conclusion.
