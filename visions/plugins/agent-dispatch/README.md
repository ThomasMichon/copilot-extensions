# agent-dispatch — Vision

- **Subject:** The **delegation layer** of the agent fabric — the plugin that
  gives agents a durable, browsable, **claimable task queue** so many agents
  coordinate work under one shared identity, instead of racing through the
  version-control remote or minting an account per agent.
- **Scope:** leaf (a per-plugin vision under the [agent-fabric](../../agent-fabric/README.md) branch)
- **Status:** Draft
- **Last revised:** 2026-07-31
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

For work that is **pursued** rather than performed in one shot, the record is
richer than a fire-once prompt: it can carry a **durable goal** — an objective
plus the criteria for *done* — and an **accumulated progress log** that grows as
the work advances. The task is then a *resumable statement of an outcome to
reach*, not just an instruction. That durability is what lets a task genuinely
outlive the specific agent pursuing it: a worker that vanishes mid-goal is
replaced by one that **resumes from the recorded progress**, not one that starts
the goal over.

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

Because the claim is stamped to the worktree, a task resolves **both ways** — a
worktree enumerates the tasks it holds, and any task resolves back to the
worktree that claimed it. This is the **inbound half** of a worktree's fabric
**claim ledger** (parent vision §Features/*resource-claims*): the external work a
worktree has taken responsibility for — bugs and work items, pull requests
adopted for review or maintenance, efforts or visions it is advancing — the
delegation-layer counterpart of the ground layer's *outbound* resource claims.
Both halves are derived from their owning layer, never merged into a third
store, and each answers the reverse lookup (which worktree holds this?) on its
own.

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

### resumable-goal
A task may be a **durable goal an agent works toward across turns — and across
embodiments** — not only a one-shot instruction. The record can carry the
objective, the criteria for *done*, and an **append-only progress log** (distinct
from the latest-only status beat) that accumulates as the work advances. This is
what turns a standing charge — *pick one thing and improve it*, *drive this
change to a ready state* — into a first-class unit the layer can carry: a worker
**loops toward the goal**, records what it accomplished at each pass, and
completes only once it judges the done-criteria met. Because the goal and its
accumulated progress are durable, an interruption costs the fabric only the
*remainder* of the work: the next worker continues from the recorded progress
rather than from nothing. The layer does not drive that loop turn-by-turn
(*fire-and-forget-not-driven*); it makes the goal **durable and resumable** so the
worker — or its replacement — can.

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

### verify-the-completion-claim
A worker's completion is a **claim to verify**, not a fact to trust on faith. For
a **goal-bearing** task the layer corroborates the claim against what was actually
recorded — a result reference, and progress consistent with the stated
done-criteria — before treating the goal as met. A completion asserted with **no
recorded result and no progress** toward a real goal is **held for attention**
rather than silently accepted, so a worker that declares done without doing the
work cannot quietly close a goal. This is *complete-means-done* made defensive:
the worker still self-judges completion, but a goal's closure is corroborated, not
assumed. (A plain one-shot task with no goal keeps the simple deferred-completion
contract.)

### resume-the-goal-not-restart-it
When a worker owning a goal-bearing task is confirmed gone (*liveness-not-lease*),
recovery does not discard the work already done. The task's durable goal and its
**accumulated progress** are what return to the queue, so the next worker to claim
it **resumes** — continuing toward the same done-criteria from the recorded
progress — rather than restarting the goal from nothing. A vanished worker costs
the fabric the *remainder* of a goal, never the whole of it. This is the recovery
counterpart of *resumable-goal*: the depth of a resume is only ever as rich as the
progress the work took care to record, and a one-shot task with no accumulated
progress simply re-claims.

### nudge-before-recover
Recovery is **graduated, and liveness gates every step**. A worker *confirmed
gone* has its goal-bearing work **re-embodied**, so a replacement resumes from the
accumulated progress (*resume-the-goal-not-restart-it*). But a worker that is
**alive yet quiet** — still holding its claim, merely not emitting progress — is
**not** a recovery candidate: it is first **nudged** (an attributed steering
message to its live session) to re-engage, never killed on elapsed time. Only a
worker *confirmed gone*, or one still unresponsive after a nudge, escalates to
re-embodiment. Elapsed time may trigger a **liveness check**; it never by itself
declares death. This is the graduated complement of *liveness-not-lease*: a
slow-but-working worker is left alone, a quiet-but-live worker is prodded, and only
a truly absent worker is replaced.

### react-to-turn-end
Supervision advances on the **worker's turn boundary, not only on a timer**. When
the coordination layer exposes a worker's turn signal, the layer reacts to an
embodied worker **settling a turn** (going idle) by running a
reconcile/recover/spawn pass **promptly** — so a completed goal is settled and the
next task embodied without waiting out a poll interval, and a worker that just fell
quiet is checked for a nudge sooner. This is a **latency optimization layered on**
the liveness reconcile of *liveness-not-lease*, never a replacement for it: the
periodic reconcile remains the **floor** that catches missed signals, workers whose
turn stream is unavailable, and workers with no live session at all. Correctness
never depends on receiving a turn signal — only *promptness* improves — and the
loop **degrades cleanly** to exactly the periodic reconcile where no turn signal is
observable. *Which* signal (a raw event subscription, a derived turn-state sample)
carries the turn boundary is spec-level, not fixed here.

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
- Consumer: [agent-logger](../agent-logger/README.md) — the **chronicler**, a
  scheduled-production consumer whose per-session units are ordinary claimable
  tasks on this layer's mesh.
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
- **2026-07-31** — Extended §Concepts/*Worker identity — the worktree* with the
  **inbound-claims** framing: a claim stamped to a worktree makes claimed work
  resolvable **both ways** (worktree→its tasks, task→its claimant), and is the
  **inbound half** of a worktree's fabric claim ledger (parent §Features/
  *resource-claims*) — the delegation-layer counterpart of the ground layer's
  outbound resource claims. Mined alongside the parent revision that introduced
  the directional claim model; states the reverse-lookup intent already implied
  by claim-stamped-to-worktree, and names this layer as the owner of the inbound
  half (both halves derived, never merged).
- **2026-07-31** — Extended for the **goal-loop / full-auto** intent: §Concepts/
  *The task* now carries an optional **durable goal** (objective + done-criteria)
  and an **accumulated progress log**; added the *resumable-goal* feature (a task
  as a goal a worker loops toward across turns and embodiments, completing on
  self-judged done-criteria) and the *resume-the-goal-not-restart-it* behavior
  (recovery returns the goal + accumulated progress so the next worker resumes,
  not restarts). Consistent with *fire-and-forget-not-driven* (the layer makes the
  goal durable/resumable; it does not drive the worker's loop) and
  *complete-means-done* (deferred, self-judged completion). Mined from an operator
  design conversation on standing "pick one thing and improve it" board charters
  and an Intelligence-Dampener-style "drive this change to ready" goal — the intent
  the implementing work then closes.
- **2026-07-31** — Extended for **supervised auto-recovery** (the full-auto slice):
  added the *verify-the-completion-claim* behavior (a goal-bearing `complete` is
  corroborated against a recorded result + progress before the goal is treated as
  met; an empty "done" is held for attention) and the *nudge-before-recover*
  behavior (graduated, liveness-gated recovery — a *confirmed-gone* worker's goal
  is re-embodied to resume from progress; an *alive-but-quiet* worker is nudged, not
  killed; elapsed time triggers a liveness check, never a death verdict). Makes
  *resume-the-goal-not-restart-it* operational and defends *complete-means-done*.
- **2026-07-31** — Extended for **event-driven supervision**: added the
  *react-to-turn-end* behavior — supervision reacts to an embodied worker settling
  a turn (going idle) by running a reconcile/recover/spawn pass promptly, instead of
  only sampling on the poll cadence. Framed strictly as a latency optimization
  *layered on* the *liveness-not-lease* periodic reconcile (which stays the correctness
  floor and the degrade-clean fallback), so a completed goal is settled and the next
  task embodied without waiting out an interval. The signal carrying the turn boundary
  (raw event subscription vs derived turn-state sample) is left spec-level. Mined from
  the operator's "reactive-on-turn-end" steer as the fourth full-auto piece.
  Mined from an operator design conversation refining the recovery flow (heartbeat +
  state payloads → verify-or-mitigate on a bad "done" → nudge/re-embody on a stalled
  or gone worker).
