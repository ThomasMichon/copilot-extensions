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
