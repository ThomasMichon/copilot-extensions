# Mechanical closeout readiness before submission

Status: **reviewed design; provider evidence landed; assignment integration and qualification pending**.
Parent: [monitor and confirmed-state effort](README.md).
Requirement: [assignment/startup outcome five](assignment-startup-amendment.md#prioritized-lifecycle-clarification).
Coordination: [#9709](https://github.com/ThomasMichon/copilot-extensions/issues/9709).
Design merged in [#10354](https://github.com/ThomasMichon/copilot-extensions/pull/10354)
after exact-head independent approval and successful required CI.

## Selected behavior

Submission checks the worker's mechanical closeout readiness. WIP,
unmerged source, open pull requests, unfinished downstream worktrees and other
unsettled obligations prevent submission with an actionable blocker. Submission
does not commit, publish, merge, abandon, transfer or clean up those obligations.
The worker fixes them through their normal owners and retries.

Passing readiness permits `SUBMITTED`, not permanent worktree finalization.
The worktree and its claim on the task remain alive throughout evaluation.
The worker process can stop normally without surrendering that durable
responsibility or making its checkout eligible for cleanup.

Rejection retains the same task/worktree/conversation binding and resumes the
agent there with the exact feedback and previous attempts. Accepted `COMPLETED`
allows that worktree's task claim to settle and permanent finalization to
proceed through normal gates. Explicit unassignment to unbound queued work is
the other claim-settlement outcome, described below. Physical cleanup requires normal
native-retirement and cleanup admission. A remote evaluator never finalizes
the worker's checkout.

Failed or unconfirmed readiness retains the proposed result, task ownership,
generation, native binding, assignment and accountable capacity. It neither
queues evaluation nor reports successful submission.

## Current source boundary

The read-only [task-obligation provider evidence](journal/2026/11.03-task-obligation-provider.md)
merged in #10365. Its atomic current-state/disposition facts do not themselves
journal ordinary assignment claims or qualify readiness, rejection recovery
or cleanup. Those existing-owner integrations remain below.
The [exact retained-task callback provider](journal/2026/11.03-task-relationship-provider.md)
adds original-queue and audited-assignment continuity to that evidence.
It merged in #10382 after exact-head approval and required CI; it does not
enable ordinary assignment callers or qualify allocator workspace binding.
The [incarnation-bound writer expansion](journal/2026/11.04-relationship-writer-incarnation.md)
adds locked expected-workspace checks and executing-writer declarations.
It merged in #10393 after exact-head approval and successful required CI.
Its source tests do not establish every participating writer's support or
enable ordinary assignment admission.
The [actual-assignment source integration](journal/2026/11.04-assignment-relationships.md)
now exercises both queue assignment owners and the real claim ledger under
validation. Ordinary construction still leaves the writer disabled; source
integration is not cohort activation or installed readiness qualification.

The CLI's completion command and client reach the coordinator's `/complete`
route. Its queue transition records `SUBMITTED` and clears the task owner.
Verification-required tasks remain provisional and schedule their configured
evaluator; existing tasks without required verification immediately reach
`COMPLETED` by self-attestation. The HTTP MCP server calls that
same queue method directly; the standalone MCP server proxies the client.
Consequently, a CLI-only check would leave other supported callers unguarded.

Worktrees already owns native-aware mechanical finalization and separate
read-only finalized-checkout observation. These preserve normal source, PR,
claim, effort, pairing, follow-up, foreign-session and native-custody gates.
Current Dispatch integration uses them for an allocator-created worker's
decline, currently finalizing before relinquishment. The new outbound task
obligation requires that ordering to change as well: check readiness while
retaining the claim, confirm unassignment and unbound requeue, then settle the
claim before permanent finalization. A submitted worker retains its claim
instead, because its task is still provisional and needs the worktree for a
possible rejection. Neither path can treat readiness as permanent finalization.

The current exact-submission rejection owner already preserves revision-bound
conversation history and feedback. Its actual routing/resume behavior must
be reconciled to the retained workspace requirement, not replaced by the
legacy "reopen completed" operation. Original-allocation providers do not
establish manual-worker or no-managed-worktree applicability.

## Integration contract

The following means are proposed implementation design, not running support.
They close the existing submission requirement without adding another task
state machine, executor, scheduler or worktree registry.

### Prepare without submitting

The existing queue/result owner durably retains a proposed result and reference
under the exact task generation, worker, native conversation and submission
request. Preparation is not `SUBMITTED`: the task stays under its existing
active or suspended owner and existing lease/hold policy.

An identical retry reconciles the original request. A changed result or binding
cannot silently replace it. A timeout does not imply failure or permit a second
effectful closeout attempt: the caller first reads its retained preparation and
the owning Worktrees outcome. Existing task incarnation and execution-admission
fences apply before retaining or advancing a request.

Expose preparation and current support through the owning task routes, including
task-only capability discovery where applicable. Do not require a worker token
to read global health or accept a caller's copied finalization receipt.

### Check readiness without concluding the worktree

The worker-facing `submit` command resolves its actual current workspace and
native process, negotiates the selected Worktrees runtime's support, and invokes
an authoritative readiness check. Retain `complete` as a compatibility spelling
where it currently denotes worker submission.

Use the existing Worktrees owner and normal preservation checks, not a local
reimplementation or a set of permission-relaxing flags. This needs a distinct
readiness result: ordinary `finalize --dry-run` is not proof that every normal
gate passed, and effectful own-finalization concludes the workspace too early.
No automatic WIP repair, PR merge, downstream claim release, paired-worktree takeover, process kill or
checkout deletion is part of submission.

An unsupported provider, changed native, shared workspace or ambiguous original
allocation returns a visible blocker. Record that blocker against the retained
preparation without releasing the task. Its own exact task obligation is
expected to remain until `COMPLETED`, not something this check force-releases.
Foreign tasks and other unsettled obligations remain blockers. Correcting an
obligation and retrying the same preparation does not reset unrelated policy.

### Observe before the authoritative transition

All supported submission surfaces converge on the existing queue transition.
The coordinator obtains its own exact readiness observation through the actual
workspace owner. It does not finalize remotely or trust success asserted by
the worker, evaluator, CLI output or submitted result.

Perform external observation outside SQLite's write transaction. Before changing
task state, recheck the relevant retained preparation, generation, owner/native,
workspace/allocation identity, hold, admission and result binding under the
queue's existing write lock. Changes refuse advancement; unrelated progress or
heartbeat updates must not manufacture a different submission request.

Worktrees retains authority over source, checkout, creation nonce, native head,
task responsibility and finalization state. The queue retains authority over
task submission, exact review and terminal acceptance.
Neither owner's receipt creates a fictitious cross-system atomic transaction.
Preserve the existing managed-write/readiness fences and test invalidation
at both boundaries rather than claiming that a snapshot freezes arbitrary
out-of-band filesystem writes.

Only the successful queue transition records `SUBMITTED` and makes the result
eligible for independent evaluation. Release the execution slot only through
existing qualified retirement; retain the durable worktree task claim and
return binding. This is not a permanent reservation of an active pool slot.
Existing supervision owns normal worker exit and native-capacity retirement.

### Reject in place; conclude only after acceptance

The exact-submission rejection transaction retains history and feedback while
returning the same logical worker to runnable work. If it needs a released
execution slot, admission still waits for the correct pool instance's capacity;
it never silently chooses another checkout or native conversation. Rejection
is not an allocator decline, a new task, or permission to replay an uncertain
resume effect.

Resume through the existing original-handle/recovery owner. Fence review and
resume by submission revision, task generation and retained workspace/native
identity. A missing or unqualified owner remains a visible recovery blocker,
not authority for reassignment. Repeated feedback and later submissions retain
all prior answers and do not release the task claim.

Assignment journals the worktree's outbound claim on the task through the
existing claim owner, not a task-owned claim on the worktree. That obligation
blocks cleanup through `SUBMITTED` and rejected attempts, independently of
whether the runner is live.

An allocator can claim before a worktree exists; that does not create a
fictional workspace obligation. When the actual worktree is assigned, its
authoritative attachment/claim owner establishes the obligation before the
assignment can be treated as fully admitted. Cover direct worker claims as
well as allocator attachment. An interrupted cross-owner write retains the
original assignment for reconciliation instead of admitting work with no
cleanup protection. Preserve separate creator/allocation custody; do not
silently reverse or release another owner's existing resource claims.

After exact independent acceptance reaches `COMPLETED`, the existing task
claim provider may settle the worktree's obligation. The other release outcome
is confirmed unassignment together with `QUEUED` and no bound agent; a status
change alone, retained affinity, stopped process or lease expiry is not that
proof. Reconcile both assignment and release through their existing owners,
including interrupted cross-owner writes; do not introduce a parallel ledger.
Normal permanent finalization then rechecks all current gates; prior submission readiness is
not a blanket later cleanup authorization. Remaining unrelated obligations
still block, and cleanup cannot remove a live or otherwise protected checkout.

For a worker decline, use readiness while the exact own-task obligation remains
active, then atomically commit the exclusion/unassignment and unbound requeue.
Only confirmation of that disposition permits claim settlement and subsequent
normal finalization. A failed or unknown disposition retains the claim and
cleanup protection. Never break the circular dependency by force-releasing the
claim or waiving unrelated obligations in effectful finalization.

`ABANDONED` and dead-letter status alone are not new automatic settlement
outcomes. Surface the retained obligation and its owner as an actionable
disposition blocker. An authorized recovery that explicitly unassigns and
returns the task to unbound `QUEUED` can satisfy the existing second outcome;
otherwise preserve the claim until the responsible owner resolves its work
through an explicit existing disposition/transfer workflow. Do not silently
discard unfinished content, reset a terminal task, or invent abandonment-driven
cleanup permission. Completion of this integration requires testing and
documenting that retained-owner path, not leaving an invisible leaked claim.

### Applicability and staged adoption

An original managed allocation is positive evidence that closeout is required.
Generalize supported manual-worktree intake through the existing authoritative
workspace/task binding, not by guessing that every slash-separated worker name
is a managed checkout. A worker cannot opt out by omitting a path at submission.

A positively established no-managed-worktree assignment has an explicit
`not_applicable` readiness outcome and keeps its non-worktree submission behavior.
Missing, unreadable, unsupported, remote-unqualified and legacy-unclassified
metadata are not positive absence. They remain visible incompatibility or
qualification gaps, never success-shaped no-worktree evidence.

Expand and advertise actual provider support before migrating callers. Define
which task/assignment generations require the new contract at admission, retain
their requirement through restart and rollback, and prevent an older completion
caller from bypassing it. Do not retroactively reopen historical completed
tasks or silently activate an unqualified mixed cohort.

## Implementation and validation sequence

1. Retain exact preparation/result/blocker custody through the existing queue
   owner. Cover conflicting retries, restart, holds, stale native/generation,
   unchanged result replay and no premature evaluation or capacity release.
2. Expand the Worktrees readiness and independent observation contracts only
   where needed. Cover actual WIP, unmerged content, open PR, downstream
   obligation, effort, pairing, foreign session and changed-checkout refusal,
   while retaining the exact own-task obligation.
3. Enforce the transition in the provider and migrate worker CLI/client/MCP
   consumers and guidance through negotiated support. Cover direct API and
   MCP bypass attempts, old/new and rollback refusals, supported manual intake
   and explicit no-worktree applicability.
4. Qualify an actual native worker: blocked submission retains its result and
   owner; after the obligation is resolved, the same request passes readiness,
   submits once and exits normally without finalizing its worktree. Reject
   the exact submission, resume the same workspace/conversation and resubmit;
   retain its task claim throughout. Only independent `COMPLETED` permits
   final claim settlement and permanent finalization, with physical cleanup
   separately admitted. Also prove that exact unassignment plus unbound queued
   work settles only that assignment's claim, while submitted, held, timed-out
   and still-bound queued tasks retain it. Exercise decline readiness before
   unassignment, lost disposition replies, and later finalization without a
   circular claim dependency. Abandoned/dead-letter tasks retain visible
   accountable disposition until an authorized settlement outcome or explicit
   owner transfer, never implicit cleanup. Source/unit and native evidence
   remain distinct.

Each source slice needs its own current-head review, required CI and actual
merge. Neither this design nor one implemented allocation type completes the
parent Plan/Validation matrix.
