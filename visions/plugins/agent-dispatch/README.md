# agent-dispatch — Vision

- **Subject:** The **delegation layer** of the agent fabric — the plugin that
  gives agents a durable, browsable, **claimable task queue** so many agents
  coordinate work under one shared identity, instead of racing through the
  version-control remote or minting an account per agent.
- **Scope:** leaf (a per-plugin vision under the [agent-fabric](../../agent-fabric/README.md) branch)
- **Status:** Draft
- **Last revised:** 2026-07-26
- **Reality docs:** [`docs/architecture.md`](../../../docs/architecture.md) ·
  the plugin's `plugins/agent-dispatch/` (skill `agent-dispatch`, `pick-and-claim`)

## Purpose & Intent

The fabric's coordination layer lets an agent **talk to** another agent — send a
message, drive a peer, watch it work. But a great deal of fabric work is *not* a
live conversation: it is a unit of work that should be **recorded once, picked up
later, and driven to a recorded outcome** — possibly by a different agent, on a
different machine, at a different time, with nobody watching the whole way. That
work needs a place to *live* between the agent that conceives it and the agent
that completes it.

**agent-dispatch** is that place: a **shared, single-writer task store** with an
**atomic claim** over a queue of durable task records. It is the layer an agent
reaches for when it wants to **let go** of a piece of work — stash a
continuation, delegate a spin-off, react to an event, or schedule a recurring
job — and trust that the fabric will carry it. Aware of **only** this layer, an
agent *stashes* tasks to be resumed or handed off; aware of this layer **and**
the coordination layer, an agent *delegates* tasks to spun-off workers.

The north star: an agent that has work it should not do *right now, itself,
turn-by-turn* has one honest answer — **file it as a task** — and the fabric
guarantees the rest: exactly one worker claims it, its progress and outcome are
recorded, and if it *cannot* be carried (no worker will take it, or the queue
stops draining) that failure is made **visible** rather than silently churned. A
task queue that quietly grows is a broken promise; this layer's job is to keep
the promise or surface that it can't.

## Concepts & Components

### The task — a durable unit of delegable work
A **task** is a self-describing record: a title, a prompt, and an optional
payload, carrying enough context that a *different* agent can judge whether the
work is already done, is a duplicate, or is theirs to take — without the
originating conversation. It also carries **routing** (required capabilities,
soft affinities) and **targeting** (an optional machine/worktree/repo it is
addressed to). A task is the atom the whole layer moves through its lifecycle.

### The coordinator — the single-writer store
A per-host **coordinator** owns the queue: a single-writer store that hands out
an **atomic claim** so that, of any number of agents that could do a piece of
work, **exactly one** wins it. It does *only* queue duties — enqueue-and-dedup,
atomic claim, state transition, browse/search, reconciliation, and change
notification. It does **not** run agents, decide priority policy, or embody a
worker; those belong to the layers around it. Its transport posture matches the
rest of the fabric's control plane: it is reached **over the fabric's secured
mesh**, never a raw public listener (see the parent vision's transport rule).

### Worker identity — the worktree
A worker's durable identity is the **worktree it occupies** (the same identity
the ground layer owns), not a per-agent account. A claim is stamped to a
worktree; a task addressed to a worktree sticks to it, while an unaddressed task
floats to any capable worker. This is what lets a claimed, embodied task be
joined back to the live session doing it — the delegation layer *derives* that
liveness from the ground/coordination layers rather than keeping its own copy.

### Producers — the four ways tasks are created
The coordinator only owns the queue; anything that *creates* a task is a
**producer**. Four production modes are first-class:

1. **Continuation storage** — a session about to end **stashes its handoff** as a
   task pinned to its worktree, so the next session in that worktree resumes from
   it. The queue is the durable home of a continuation, not a scratch file.
2. **Fire-and-forget delegation** — an agent mid-task spins off work it should
   **not wait on** (an optional follow-up, a tangential fix discovered in
   passing) to another worktree or machine, and moves on.
3. **Reactive production** — an **automated event** with no human in the loop
   (a posted code review, a security detection, a service outage or
   quality-of-service degradation) mints a tightly-scoped remediation task.
   Because a worker is expensive, reactive production is reserved for the
   **genuinely critical** and is given a narrow brief.
4. **Scheduled production** — a **timer** mints recurring work (a periodic
   journaler, an audit/backlog-filing agent, any calendar job), deterministically
   enough that re-firing the same occurrence does not duplicate it.

### The lifecycle
A task moves through a small set of states from *drafted* → *ready* → *claimed*
(held for evaluation) → *started* (under active work) → *completed*, with a
*discarded* terminal path for duplicates and dropped priorities. The
**claim→start** gap is a deliberate **evaluation window**: a worker holds a task
exclusively while it decides whether to accept, decline (returning it with a
"not me" so it isn't re-offered the same task), or retire it as a duplicate.

## Features

### durable-claimable-work
Work an agent conceives but should not do itself, right now, is captured as a
**durable, browsable, single-claim** record. The atomic claim guarantees a single
winner among many eligible workers, so parallel agents cooperate without a leader
election and without colliding through the version-control remote.

### the-four-production-modes
Continuation storage, fire-and-forget delegation, reactive production, and
scheduled production are all **first-class** ways to put work on the queue (see
*Producers*). One queue, one lifecycle, one claim primitive serves all four; a
deployment adds producers without changing the store.

### dedup-before-create
Before a task is created, the layer supports **finding existing equivalent work**
— reading the corpus of live tasks (and, where available, a semantic index over
it) and keying a subject so a second attempt at the same subject **collides**
rather than duplicates. Correctness rests on descriptive, self-contained task
text; a semantic index is a performance layer over it, never a precondition.

### capability-and-affinity-routing
A task can **require** capabilities (hard: only a worker advertising them may
claim) and **prefer** affinities (soft: order candidates without excluding). Two
workers advertising the same capability give **cooperative, redundant** coverage
of that work with no coordinator-side scheduling.

### recorded-outcome
A worker reports **progress toward the goal** at meaningful transitions and a
**recorded outcome** on completion — a durable, queryable result distinct from
the live transcript (the coordination layer's *summary-status-is-first-class*,
seen from the delegation side). A caller or operator surveys the fleet's progress
at a glance without reading each session.

### observable-lifecycle
The layer's task lifecycle is **externally observable** through a
**backend-agnostic telemetry seam**: the coordinator declares its lifecycle
surface and ships a **no-op-by-default** emission hook, and a downstream
observability consumer **attaches a publisher by configuration, not code** —
without the layer depending on any specific telemetry backend or transport. The
seam carries lifecycle **state and structure only** (never a task's prompt,
payload, or any secret) and is **fail-open**: an unconfigured or misconfigured
sink leaves the coordinator untouched. *How* the consumer is attached — an
environment variable, a dropped config file — and the on-disk shape of that
configuration are spec-level, not fixed here.

So that a consumer need not inject its own code into the coordinator's process
to observe it, the layer also ships a **built-in, dependency-free emitter** the
declaration can select — a plain **append-only spool** of the generic lifecycle
records. The consumer then **drains that transport out of process**, on its own
schedule and in its own environment, and shapes the records into whatever
telemetry system it runs. This keeps the coordinator process **self-contained**
(only the layer's own code runs in it) while still making its lifecycle fully
consumable: the integration is **process-to-process over a declared transport**,
not a shared runtime.

## Behaviors

### fire-and-forget-not-driven
This is the line between the delegation layer and the coordination layer. A task
is work an agent **lets go of**: the producer may later *inspect* its status or
*send it a steering message*, but it does **not** own the worker turn-by-turn the
way a coordination-layer caller drives a peer. "Drive or converse with a live
agent" is the coordination layer; "file work you won't wait on and let a worker
claim it" is this layer. Both may cross machines — the distinguishing axis is
**ownership, not location**.

### liveness-not-lease
A claimed or started task has **no wall-clock expiration** while the worker that
owns it is **alive**. Recovery is **not** a lease timer that assumes a slow
worker is a dead one — that conflates *took a long time* with *is gone* and
silently re-runs work. Instead the layer **reconciles outstanding work against
worker liveness**: it periodically checks each non-terminal, owned task against
the **live state of its owning worktree** (derived from the ground/coordination
layers, per *no second store*), and only a task whose owner is **confirmed gone**
returns to the queue. Long-running work is safe as long as its worker is live;
recovery is triggered by a *vanished worker*, never by elapsed time.

### buildup-is-a-health-signal
In a healthy system tasks are **short-lived** — created, claimed, and completed
promptly. A growing pile of unclaimed or unfinished tasks is therefore not
normal churn to be silently requeued; it is a **system-health signal that
warrants attention**. When outstanding work has **no live worker to take it**,
the layer does not let it rot: it either **escalates** (bring up a worker to
drain it) or **demotes the work back to its source** — the effort or tracked
issue it came from — so the intent is preserved somewhere durable and the queue
returns to reflecting only live, in-flight work. Persistent, undraining buildup
is surfaced as the failure it represents.

### complete-means-done
A task reaches **completed** when its *work* is done, not when a baton merely
changed hands. A worker that takes over a delegated or embodied task **completes
it explicitly** once it judges the goal reached (deferred completion). The one
exception is a **continuation baton**: a handoff task is spent the moment it is
picked up, because the continuing *work* is tracked by its own effort or issue,
not by the handoff record.

### repo-lane-isolation
Every task belongs to the **repo lane** of the agent that produced it, and the
queue is scoped to that lane by default — an agent sees and claims **its own
repo's** tasks, never another's by accident. Cross-repo *code* work still lives
in the **producing** lane (tagged with the code target), done by a same-lane
worker; a task is never filed into a foreign lane to "send" it there. This keeps
one shared coordinator serving many repos without their work bleeding together.

### no-second-store
The delegation layer **coordinates over** state the layers below own; it does not
keep a private copy. Worker liveness and worktree identity come from the
ground/coordination layers; the queue adds only the **task** and its lifecycle.
(The fabric-wide *derive-don't-duplicate / single-owning-layer* rule, applied to
this layer.)

## Non-Goals / Boundaries

- **Not the live-conversation layer.** Driving or messaging a running agent
  turn-by-turn is the **coordination layer** (agent-bridge). This layer carries
  *async, recorded, claimable* work, not a live dialogue.
- **Not the embodiment owner.** *How* a claimed task becomes a running worker —
  a durable CLI session versus a headless helper — is decided by the ground and
  coordination layers (*lifetime-decides-embodiment* in the parent). This layer
  owns the **queue and its scheduling**, not the body a worker runs in.
- **Not a priority engine.** The coordinator does not own scheduling policy,
  weighting, or fair-share arbitration; it hands out atomic claims over a queue.
  Prioritization is a producer/consumer concern layered on top.
- **Not an account-per-agent model.** Many agents coordinate under one shared
  identity via **claimed** work keyed to worktrees — not by minting an account
  per agent.
- **Not a specification.** This vision fixes the *role, guarantees, and
  behaviors* of the delegation layer, not the wiring — it does not pin the state
  names, transport, storage engine, on-disk format, endpoints, or command
  grammar. Binding detail of that kind belongs to the reality docs or a future
  `specifications` layer.

## See Also

- Parent vision: [agent-fabric](../../agent-fabric/README.md) — §Concepts/
  *agent-dispatch — the delegation layer*.
- Sibling leaf: [agent-ssh](../agent-ssh/README.md) — the connectivity layer this
  layer's cross-machine reach rides on.
- Reality docs: [`docs/architecture.md`](../../../docs/architecture.md) · the
  `plugins/agent-dispatch/` skills (`agent-dispatch`, `pick-and-claim`).

## Provenance

- **2026-07-24** — Initial authoring as a per-plugin leaf under agent-fabric.
  Intent mined from the operator's four canonical use cases for the delegation
  layer (continuation storage, fire-and-forget delegation, reactive
  event-driven production, scheduled production) and the decision to replace
  **lease/TTL expiry** with **garbage collection reconciled against live
  workers** — recovery triggered by a vanished worker, not elapsed time, with
  undraining buildup surfaced as a health signal (escalate or demote to source)
  rather than silently requeued. The *fire-and-forget-not-driven* axis was
  crystallized from recurring agent confusion between this layer and the
  coordination layer.
- **2026-07-26** — Added the *observable-lifecycle* feature: the layer's task
  lifecycle is externally observable through a backend-agnostic, no-op-by-default
  telemetry seam that a downstream consumer attaches **by configuration, not
  code** (an environment variable or a dropped config file), carrying lifecycle
  state and structure only. States the intent behind the pluggable emission seam;
  the attachment mechanism and on-disk config shape stay spec-level.
- **2026-07-26** — Extended *observable-lifecycle* with the **built-in emitter**
  intent: the layer ships a dependency-free, declaration-selectable emitter (a
  plain append-only spool of the generic records) that a consumer drains **out of
  process**, so observing the coordinator needs no consumer code injected into its
  runtime. Crystallizes the *process-to-process over a declared transport, not a
  shared runtime* property behind the seam. Mined from an operator steer to treat
  the plugin's own runtime as sealed and integrate telemetry process-to-process.
