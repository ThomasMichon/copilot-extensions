---
visions:
  - visions/plugins/agent-dispatch
---

# Task Relevance Reconciliation

## Goal

Reconcile source-backed tasks that are no longer relevant. Retire a
never-claimed, resource-free task atomically; otherwise request closeout from
its existing agent through durable steering and wake delivery. Allow both
emitter-driven reports and periodic evaluation of eligible unbound tasks.
Keep emitter, pool, task, session and worktree correlation inspectable.

- **Slug:** `task-relevance-reconciliation`
- **Repo:** copilot-extensions
- **Branch(es):** per reviewed slice
- **Created:** 2026-11-12
- **Status:** Active
- **Vision:** [Dispatch task relevance](../../../visions/plugins/agent-dispatch/README.md#task-relevance-reconciliation)
- **Umbrella issue:** [#12274](https://github.com/ThomasMichon/copilot-extensions/issues/12274)

## Guiding Intent

Source disappearance must reach the task even when no agent ever starts it.
For work that did acquire an agent or resources, obsolescence is a closeout
instruction, not permission to erase its ownership.

## Participants

| Participant | Role | Reached via |
|---|---|---|
| Implementation driver | Design, implementation and source delivery | #12274 and its source PRs |
| Independent reviewer | Architecture and implementation review | Normal repository PR review |

## Coordination

Use sequential reviewed slices under #12274. The driver owns publication and
merge follow-through. Existing coordinator, registrar, supervisor, Bridge and
Worktrees owners retain their respective state and lifecycle boundaries.

## Context

An event-driven evaluator can miss work that stays unclaimed: the source object
may close before the task produces an execution event. Emitters already receive
[durable creation receipts](../../../visions/plugins/agent-dispatch/README.md#emitter-command-receipts),
and Dispatch already owns task transitions, attachment history and wake delivery.
Those are the integration points, not a new polling daemon or cleanup database.

The [service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md)
governs independently deployed callers and receivers. Existing
[terminal housekeeping](../../../plugins/agent-dispatch/docs/cleanup-recovery.md)
remains separate from deciding whether a task's goal is relevant.

## Request

Public-safe scope:

- When a source-backed task has never been picked up and its backing object
  closes or merges, its emitter should request conditional abandonment.
- When an agent did claim the task, request closeout from that agent using
  live steering or a queued wake and steer; include worktree finalization.
- An evaluator may periodically reconsider registrar-owned unbound tasks.
  An unbound timeout can trigger that evaluation.
- Headless task, worktree and session records must expose their emitter and
  pool relationship so their resource cohort can be identified.

The following protocol shape is an implementation proposal, not a claim that
these operations already exist.

## Scope Approvals

This effort covers reviewed source changes and bounded isolated validation.
It grants no deployment, production task cancellation, native termination,
workspace deletion or migration of unrelated owners. A passing source test or
merged plan does not establish running adoption.

## Plan

### Phase 1 - Reconcile the architecture before implementation

- [x] Review this plan and the vision extension through the normal PR flow.
- [x] Map the actual emitter receipt, evaluator registration, task attachment,
      allocation, steering/wake and terminal-workspace seams. Reuse an existing
      capable operation where possible; document any genuinely new boundary.
      The [receiver map](../../../plugins/agent-dispatch/docs/task-relevance.md)
      identifies the queue decision as the new boundary; other owners are reused.

### Phase 2 - One relevance decision, two callers

- [x] Add a coordinator-owned relevance report with durable request identity,
      source/registration scope and expected task revision or generation.
      Emitters and evaluators use the same transaction.
      Direct and declared command-emitter and companion evaluator admission,
      effective-source revisions and runtime support merged in #12293/#12300.
      Automatic source caller migration remains in Phase 3.
- [x] Inside that transaction, abandon only a task with no prior claim/start,
      current assignment, native attachment, pending startup or retained
      resource obligation. Claim and preparation races must linearize with it.
      Internal receiver and independent-process controls merged in #12281;
      this does not qualify an emitter caller or live runtime.
- [x] Otherwise record closeout intent and steering for the existing owner.
      Queue a wake when needed, retaining the original conversation, task
      generation and acknowledgement history. Do not mark cleanup complete
      merely because a notification was admitted.
      The source receiver links one original-owner wake atomically; native
      transport, pending/unknown outcomes and suspended steering use existing owners.
      Source delivery merged in #12285; this does not prove actual closeout.
- [ ] Preserve closeout intent through restart, owner recovery and requeue.
      Recovering an obsolete task must not restart its original work.
      Unknown or unavailable owner state remains visible for recovery.
      Accepted original-owner wake and steering now survive same-generation
      unassignment and requeue in source controls. Generation-advancing recovery,
      uncaptured owners and actual lifecycle closeout remain open.

### Phase 3 - Source and periodic evaluation integration

- [x] Let an emitter reconcile its retained task receipts against authoritative
      backing-object state, including closed objects missing from an open-only
      listing. An unavailable or incomplete listing is not closure evidence.
      The opt-in [receipt caller](../../../plugins/agent-dispatch/docs/emitter-relevance.md)
      supplies the source-check contract and registered transaction; domain
      adapter migration and installed-runtime qualification remain separate.
      Source caller merged in #12311.
- [x] Extend the existing registrar/evaluator execution path with optional,
      bounded reconsideration of its own eligible unbound tasks. Persist due
      work and retry/backoff through the existing owner, without a second daemon.
      Source merged in #12325; verified managed creation provenance is required,
      and production registration/domain-adapter adoption remains separate.
- [x] Treat elapsed time as evaluation eligibility only. Define keep,
      no-longer-needed and unknown outcomes; a failed check never implies abandon.
      The same receiver handles explicit needed/obsolete/unknown judgments;
      final-attempt report recovery remains distinct from source-check budgets.
- [ ] Fence stale source observations and source reopen against newer task
      episodes. Manual tasks and other emitters remain outside the scope.

### Phase 4 - Cohort attribution and closeout

- [ ] Preserve immutable original task/emitter and allocation/pool references
      through the owning Worktrees creation and Bridge session-state paths.
      Distinguish original allocation from later assignment or reuse.
      The [original assignment audit](../../../plugins/agent-dispatch/docs/task-obligations.md#original-assignment-cohort)
      retains verified creation and exact pool-assignment facts. Conditional
      Worktrees creation propagation merged in #12360; Bridge session-state
      propagation and running qualification remain open.
      Worktrees provider expansion retains optional original cohort metadata in
      first-save records and replay. Dispatch discovers the selected provider
      before sealing its immutable wire choice.
      The reservation owner retains allocation-cohort evidence before body
      preparation, separately from later assignment audit evidence.
      The [ACP session-origin provider](../../../plugins/agent-bridge/docs/dispatch-session-origin.md)
      merged in #12373; CLI-native first-save registration and selected-daemon
      acquisition negotiation merged in #12387. The ordinary keyed-creation
      CLI/client carrier and executing-CLI declarations merged in #12396.
      The reservation owner now captures session-origin candidates separately
      from reusable allocation evidence and seals optional wire selection in its
      existing reservation/placement transaction; source merged in #12398.
      Ordinary keyed retries retain their original session request; acquisition
      deliveries retain their own attempt. Conditional CLI acquisition migration
      merged in #12405; managed ordinary keyed migration merged in #12408.
      Running qualification remains open.
- [x] Expose forward and reverse correlation through existing owner APIs.
      Pool identity must identify the actual registration/assignment, not just
      a display name; opaque legacy records remain explicitly unattributed.
      The [correlation guide](../../../plugins/agent-dispatch/docs/cohort-correlation.md)
      maps existing attachment, reservation, workspace and session views.
      Contained source controls cover original pool references after reassignment
      and stopped/expired session discovery; installed qualification stays open.
- [ ] Connect owner closeout acknowledgement to the existing terminal task,
      session and managed-workspace lifecycle. Preserve actual unlanded work,
      open PRs and foreign obligations rather than force-clearing them.
- [ ] Publish receiver support before enabling new callers; document baseline,
      upgrade and rollback behavior against the executing runtime.

## Validation Plan

- [x] Source closure before claim abandons without launching an agent.
      Isolated receipt-caller/current-HTTP controls exercise explicit source
      closure with empty open-only production, not installed-runtime activation.
- [ ] Concurrent claim, reservation and workspace/native preparation cannot
      create a body after a successful unbound-abandon decision.
- [x] A previously claimed but now unassigned task does not become
      never-claimed merely because its current owner field is empty.
      The source receiver checks durable assignment/resource history, including
      reset-cleared mutable fields; source controls merged in #12281.
- [ ] Busy, idle, suspended and temporarily unreachable owners receive
      durable closeout in the original conversation without duplicate work.
- [ ] Restart, duplicate reports and lost responses preserve a single accepted
      request, steer and closeout outcome; pending effects remain inspectable.
- [ ] A stale report, reopened source, changed task episode, foreign emitter
      or incomplete source read cannot abandon unrelated/current work.
- [x] Periodic selection respects registration scope, eligibility, batch bounds,
      retry policy and disabled registrations; age alone never retires work.
      Scoped isolated controls merged in #12325 include query-cost bounds,
      original-request recovery and verified producer enrollment. This does not
      qualify production registration adoption or native owner closeout.
- [ ] Task, pool, emitter, native and workspace joins survive allocation,
      reassignment, session exit and a later task episode without guessed stamps.
- [ ] Actual session conclusion and workspace disposition are verified
      separately from task abandonment. Live use and genuine obligations hold.
- [ ] Exercise current/prior receiver compatibility and declared rollback;
      unsupported peers refuse new effects without breaking existing operations.

## Proposal

Keep the domain judgment with the emitter/evaluator and the atomic ownership
decision with Dispatch. An automatic source-relevance path is distinct from an
operator's explicit bulk reset; it must not inherit that reset's broad discard
authority. Add no universal attestation layer or duplicate lifecycle engine.

## Journal

- [2026-11-12 - Receiver delivery](journal/2026/11.12-receiver-delivery.md):
  architecture, internal ownership, original-owner delivery and effective-source
  receiver merges; callers and running adoption remain open.
