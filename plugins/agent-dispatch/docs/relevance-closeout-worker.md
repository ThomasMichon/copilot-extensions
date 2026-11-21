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

### A review worker with an original reservation but no relationship event

The same command and submission action support `original-reservation/v1` on
the worker's **existing task-scoped credential**. This applies when the
accepted closeout recorded `original_relationship_unavailable` or retained
the original owner tuple but no binding. It does not apply when a
relationship is pending, closed, inconsistent, or awaiting reconciliation.
The authenticated task, worker, native and generation must still match the
retained pool assignment and its original spawned reservation. The receiver
joins the assignment and original reservation's pool binding in one snapshot;
it does not reapply today's pool catalog, filters, or launch admission. A
withdrawn pool can stop new work without blocking its assigned worker's
closeout. A released assignment or changed generation is not eligible. For an ordinary
Bridge worker the stored agent, Bridge handle, native and assigned workspace
must agree. A generic fleet credential is not a substitute.

Preparation reads the assigned workspace from its local Worktrees owner. It
matches the recorded Dispatch attempt and native head and pins the creation
nonce and head revision in the readiness receipt. This permits the worker to
**attempt** normal own-workspace finalization; it is not evidence that the
checkout is finalized. No assignment event or task claim is invented,
adopted or settled. The client reports `task_relationship: not-required`.

Finish independently reads the actual finalized checkout and matches it to
the preparation, then rechecks the task and reservation under the Dispatch
write lock. Acknowledgement, submission and the evaluator request commit
together. The assignment remains retained through evaluation; replay of the
committed request does not re-finalize or submit again. Uncertain finalization,
changed credentials, conflicting reservations and changed workspace evidence
retain custody. This path does not cool the native, free a slot or issue a wake.

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
