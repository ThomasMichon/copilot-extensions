# agent-dispatch — Vision

- **Subject:** The **delegation layer** of the agent fabric — the plugin that
  gives agents a durable, browsable, **claimable task queue** so many agents
  coordinate work under one shared identity, instead of racing through the
  version-control remote or minting an account per agent.
- **Scope:** branch (a per-plugin vision under the
  [agent-fabric](../../agent-fabric/README.md) branch)
- **Status:** Draft
- **Last revised:** 2026-09-02
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
A **coordinator** owns the queue: a single-writer store that hands out
an **atomic claim** so that, of any number of agents that could do a piece of
work, **exactly one** wins it. It is owned **per execution environment** — a
machine *and* its OS-environment — so a host that runs more than one environment
(a box with both a Windows host and a WSL guest) runs **one coordinator per
environment**, each owning its **own** queue and coexisting via **OS-assigned
dynamic ports** rather than contending for a shared one. Work that must cross
environments **federates** across those coordinators; it is never silently split
onto a shared queue. It does *only* queue duties — enqueue-and-dedup,
atomic claim, state transition, browse/search, reconciliation, and change
notification. It does **not** run agents, decide priority policy, or embody a
worker; those belong to the layers around it. Its transport posture matches the
rest of the fabric's control plane: it is reached **over the fabric's secured
mesh**, never a raw public listener (see the parent vision's transport rule).

### The supervisor — the singleton that runs registered work
Where the coordinator is the single-writer **store**, the **supervisor** is the
single-writer **runtime**: exactly **one supervisor per machine-and-environment**
owns *running* the registered work on that host. It is the one place where
registered intent becomes live processes — the **schedules**, the **emitters**,
the **evaluators**, and the **spawn** of claimable tasks — each delegated to its
own **subprocess** so a busy or failing unit never blocks its siblings or the
master. A domain plugs into a host by **registering** its work with that host's
supervisor, not by launching a rival loop; the singleton is the single point that
reconciles every registration against what is actually running, and supervises the
liveness of what it started. Like the coordinator it does **only** its own job —
scheduling, emitting, evaluating, spawning, and keeping those alive — and it is
reached over the fabric's secured mesh, never a public listener. The coordinator
answers *what work exists*; the supervisor answers *what is running it here*.
When a locally embodied headless task becomes terminal, the supervisor
terminally reaps that exact ACP session before releasing its spawn reservation;
a completed task never leaves an unowned process behind.

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

Where production authority must move between implementations without overlap,
the coordinator fences one permanent repo-lane + source domain through monotonic
generations. The control plane, not a caller-asserted producer name, selects the
next producer and grants it an opaque generation capability. Accepted producer
requests have their own durable idempotency identity, distinct from the task
subject's ordinary deduplication key, so retries remain safe across completion
and handoff without reopening retired authority. A protected label has one
coordinator-global owning repo+source scope; authority cannot transition while
nonterminal pre-fence or mismatched work still carries that label, and rejected
claim candidates remain observably audited rather than silently skipped.

### The lifecycle
A task moves through a small set of states: **proposed → queued → claimed**
(held for evaluation) → **started** (under active work) → **completed**, with an
**abandoned** terminal path for duplicates and dropped priorities. The two entry
states are two deliberate operations: to **propose** is to draft a task — its
concise **goal**, its detailed **goal payload**, the capabilities it **requires**,
the work it **rejects**, and its other filter attributes — and get back a **task
id**, *without* making it claimable; to **queue** it moves **proposed → queued**,
at which point the layer **binds it to an agent whose pool filter accepts it**.
The **claim→start** gap is then a deliberate **evaluation window**: a worker holds
a queued task exclusively while it decides whether to accept, decline (returning it
with a "not me" so it isn't re-offered the same task), or retire it as a duplicate.

### The recipe — an emitter/evaluator template
The common **shapes** of long-running agentic work ship with the layer as named,
reusable **templates** — a recipe is **not a command you run**, it is a
**template for an emitter/evaluator pair**. You **instantiate** one by
**registering** a concrete emitter/evaluator pair, made real by the specifics its
template leaves open: the target repo, the change-review technology, the issue
backend, the actual machine and account names. Three archetypes are first-class:

1. **reviewer** — a cooperative, verdict-bearing loop that carries one target
   change to merge or deliberate abandonment. Its deeper contract lives in the
   [reviewer-loop child vision](reviewer/README.md).
2. **conflict-resolution** — for a change some *other* system opened but nobody is
   driving, take the last mile: check out the branch, rebase, resolve conflicts,
   answer review/build state, suspend while updates settle, resume on the next
   state change, and declare done when it lands or is abandoned.
3. **goal-driven** — for an arbitrary purpose against one or more target repos
   (fix a bug, document a package, reconcile a plan), stay within the goal's
   bounds and drive it through one or more pull requests — handling conflicts and
   review feedback — until the goal is met or abandoned.
4. **repository-issue-loop** — a goal-driven specialization for an entire
   backlog rather than one target: triage and drive a bounded, quiet batch of
   a backlog's items to durable resolution per occurrence, over any backlog
   reachable through a provider-neutral list/reserve/claim/release surface.
   Its deeper contract lives in the
   [repository-issue-loop child vision](repository-issue-loop/README.md).

A consumer **selects and parameterizes** a recipe rather than hand-rolling a loop,
and may **extend** one where its domain needs more. An instantiated pair knows how
to **author its own tasks** — deriving the goal and payload, stamping the filters,
wiring its evaluator, defining the intermediary steps, and owning suspend/resume —
whether it fires on its own event or is **side-loaded** on demand (see
*side-load-through-an-emitter*). One reviewer template serves an automated review
service, an on-demand review of a single check-in, and a batch of target pull
requests alike — one shape, many instantiations.

### The evaluator — an emitter's lifecycle handler
An **evaluator** is the **emitter's companion handler** — it belongs to the
emitter, not to a lone task. It receives the **lifecycle events** of the tasks
*its emitter produced* and chooses the follow-through: emit a follow-up task,
update domain state, suspend/resume the worker, or confirm completion. This is
what makes an emitter's tasks **self-driving**: because the evaluator rides their
lifecycle, a standing domain automates a whole cycle without the layer needing a
bespoke module per domain. The boundary is ownership — a task produced **through an
emitter** gets that emitter's evaluator run over it automatically; a task
**proposed and queued by hand, with no emitter behind it**, has no evaluator and is
**tracked by its caller** (see *emitter-tasks-are-evaluated-mine-are-tracked*).

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

### fenced-producer-cutover
A production domain can be placed under permanent, coordinator-authenticated
generation control. Scope identity follows the task's repo lane and source; an
optional protected label binds back to that exact scope, so neither an alternate
nor omitted caller-asserted source can place work in its pool. Unlabeled work
outside the source remains ordinary queue work rather than implicitly authorized
production. Label ownership is unique across the coordinator, and handoff
refuses until every nonterminal task carrying the label has accepted provenance
for its owning scope. A handoff retires the old generation monotonically and grants the
selected successor a non-discoverable creation capability; request-level
idempotency distinguishes transport retry from ordinary work deduplication, and
replay still proves the named generation capability. Lost one-time authority is
recovered by a new generation, never by revealing or reopening an old one.

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
of that work with no coordinator-side scheduling. Capabilities and affinities are
the open end of a larger **attribute vocabulary** a task is routed by — alongside
its repo lane, an optional target **machine**, **environment**, or **worktree**,
and a **role** (a specialized body such as review or logging). *pools-are-filters-with-a-cap*
is what reads that whole vocabulary from the other side.

### recorded-outcome
A worker reports **progress toward the goal** at meaningful transitions and a
**recorded outcome** on completion — a durable, queryable result distinct from
the live transcript (the coordination layer's *summary-status-is-first-class*,
seen from the delegation side). A caller or operator surveys the fleet's progress
at a glance without reading each session.

### terminal-worktree-reclamation
When the delegation layer causes a worker worktree to be allocated, that
allocation remains an **owned obligation of the creating host and supervisor**
until the task reaches a terminal state, the supervisor-owned session has been
driven to exit, and the task carries an explicit landed or abandoned resolution.
The durable ownership key is the creating machine-and-environment; a particular
supervisor process identity is audit provenance, not a lease that expires on
restart. Only a successor supervisor for that same host scope may evaluate the
allocation for reclamation; absence from another host is never evidence that it
is gone.

The terminal task's inbound claim is released first. The supervisor then asks
the ground layer to atomically re-check liveness, claims, follow-up state, and
upstream safety at the removal boundary and reclaim that exact worktree through
its safety-checked lifecycle. Unknown liveness, a concurrently resumed
worktree, missing ground-layer capability, unmerged or uncommitted content, and
failed cleanup remain visible and retryable; they are never interpreted as
permission to delete. In particular, a supervisor never performs a destructive
abandonment unwind on a dirty workspace after the knowledgeable worker is gone.
Before that handoff, dispatch also proves that no other nonterminal task
allocation still targets the same worktree, or projects the complete inbound
claim set into the ground-layer atomic check. One terminal task can never make a
shared worktree look unclaimed.

The producing service still owns its domain state — reservations, artifacts,
external records, and the decision that the task is legitimately terminal.
The delegation layer owns only the allocation provenance and lifecycle it
created: embodiment session, durable host scope, worktree identity, whether the
spawn created that worktree or targeted a pre-existing one, and terminal
reclamation. A missing/legacy origin is unknown and therefore not reclaimable.
Prune eligibility and resource-accountability state remain owned by the ground
layer; dispatch derives and drives that authority rather than creating a second
cleanup ledger. A producer does not need to reimplement worktree liveness and
cleanup safety merely because it emitted the task.

### worktree-focus-before-collision
When substantial operator-led or task-less work begins, or changes direction,
the current worktree advertises a concise focus early enough that other agents
and operators can see it before choosing overlapping work. Before picking work
likely to collide, an agent checks the current focus advertisements. Where the
ground layer's status core is available, agent-dispatch reads and writes these
early signals through that existing worktree record; it never creates a
parallel focus store.

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

### loop-recipes
The layer ships the **shapes** of long-running agentic work — **reviewer**,
**conflict-resolution**, **goal-driven** — as reusable **emitter/evaluator
templates** (see *The recipe*). A domain **instantiates** one by registering a
concrete emitter/evaluator pair and supplying the specifics (which repo, which
review technology, which issue backend, which machine and account); the template
fixes the suspend/resume rhythm and the resolution target for its class of work.
Extension is expected where a domain needs more, but the default is **reuse**: the
same template is the engine behind a standing automated service and an on-demand
instance alike.

### concise-event-then-charter-pull
The seed handed to a freshly embodied worker is a **short, event-classified
notification**, not an inlined instructional essay. A recipe already knows the
shape of the event it just produced a task for — new work assigned, a submitter
update landed, a steer answer arrived — and states exactly that ("New bug work
assigned.", "Assigned PR has updates from the submitter.") plus **one command**
that resolves the worker's **full charter**: the task's goal, payload, policy,
and behavioral contract, fetched on demand rather than paid for on every
embodiment regardless of whether the worker ever needs all of it. This keeps the
per-embodiment token cost proportional to the event, while the charter command
itself remains the single, authoritative source the worker actually reads from.

### preloaded-dispatch-supplement
A worker's declared identity (*declarative-worker-identity*, repository-issue-loop
child vision) carries the generic "how to behave as a dispatch worker"
instruction supplement **by reference**, already loaded when the identity is
selected — claim/evaluate/complete mechanics, decline/exclusion conventions,
suspend-and-resume discipline. A worker never spends a tool call rediscovering
this supplement per task; it is preloaded exactly once, by identity, and only
the event-specific charter is fetched per embodiment (*concise-event-then-charter-pull*).
Domain-specific policy still lives in the identity's own instructions, layered
on top of the shared supplement rather than duplicating it.

### side-load-through-an-emitter
A **registered emitter can be triggered on demand**, not only by its native event
source. Handing it a specific request — "review this pull request," "unstick this
change" — makes it **author the task exactly as if it had discovered the work
itself**: it derives the concise **goal** and the detailed **goal payload**, stamps
the **filters**, associates itself so its **evaluator runs**, defines the
**intermediary steps** the worker may take, and owns **suspend/resume** off its own
and the evaluator's logged state and update subscriptions. So an operator's or an
agent's off-hand "handle this one" is **not** a bare, self-tracked task — it is a
**fully evaluated** one, just triggered manually instead of by a webhook or a
schedule. Direct hand-authoring (propose + queue with no emitter) stays available
for genuinely novel work that **no** registered emitter matches; that is the
self-tracked path (*emitter-tasks-are-evaluated-mine-are-tracked*). The point is
that the *normal* way to get one-off domain work done is to **feed it through the
domain's emitter**, reusing all of its authoring and lifecycle logic, rather than
re-deriving a task by hand.

### hibernate-the-wait
A worker that must wait on a slow external condition does not sit holding a live
process. It **hands the wait to the layer**: the layer runs the blocking step
asynchronously, **spins the worker's session down**, owns the await, and
**resumes the same worktree-affinitied worker** when the condition resolves. A
suspended worker then costs **no running process** — a reviewer parked for days
pending an update, or a goal-driven worker waiting on a merge, consumes nothing
while it waits yet wakes with its context intact. This is the mechanism beneath
*resumable-goal*'s suspend/resume: the worker posts what it is waiting for and is
torn down, rather than blocking a live process on an internal wait.

### emitters-and-evaluators
Work enters and advances through a **paired contract** layered on *the four
production modes*: an **emitter** is a producer with a body the layer supervises
(a webhook receiver that turns an event into a task; a scheduled check that emits
when a threshold trips), and an **evaluator** is its companion handler that reacts
to the lifecycle events of *that emitter's* tasks and chooses the next step (see
*The evaluator*). Together they let a domain plug its world and its judgment into
the queue without a bespoke module. An emitter can be driven two ways — by its
**native event** or by a **side-load** (*side-load-through-an-emitter*), a specific
request handed to it on demand — and either way it authors the task and its
evaluator owns the follow-through. Direct **propose + queue** with no emitter stays
available; that simply keeps the lifecycle responsibility with the caller
(*emitter-tasks-are-evaluated-mine-are-tracked*).

### bounded-concurrency-wide-charters
The pool of concurrently embodied workers is **capped** — a deliberate ceiling
that protects the compute/token budget — and throughput scales by giving each
worker a **wider charter** rather than by exceeding the cap. The supervisor's
concurrency limit is the ceiling; *buildup-is-a-health-signal*'s escalate-or-demote
is how the queue reacts **within** it. A bounded pool of broadly-chartered workers
is the default posture, not an unbounded swarm of narrow ones.

### pools-are-filters-with-a-cap
A **supervised pool** is not a hardcoded category — it is a **standing filter over
the task-attribute vocabulary, plus a ceiling on how many agents may bind to it**.
The same vocabulary describes both sides. A **task declares** its attributes — its
repo lane, the capabilities it **requires**, an optional target **machine** /
**environment** / **worktree**, a **role** (a specialized body such as review or
logging), and its **task-type** — and a **pool declares a predicate** over exactly
those attributes: it **permits** the work it accepts and **rejects** the work it
won't take, under a **max-agent cap**. A task **binds** to a pool whose filter
accepts its attributes, up to that pool's cap; two pools never fight over one task
because their filters plus the atomic claim decide ownership. A pool is **named for
its task-type** (the label dimension it gates), and its repo filter **defaults to
same-repo**, so a **repo-scoped pool is the default** and a fleet-wide one is a
deliberate exception. Specialization is nothing more than **adding filters** — a
code-review pool permits `role=review`; a container-bound task is permitted only on
machines advertising that container; a handoff **pins a worktree** so nothing else
can claim it. And because a hard per-task pin (a required capability, a target
worktree) is itself a filter, it **binds regardless of any pool** — the pinned
worker is the only eligible one. This is the generalization of
*capability-and-affinity-routing* and *bounded-concurrency-wide-charters* into one
filter language spoken by tasks and pools alike; a pool declaration
(*declarative-discovered-registrar*) is where a pool's filter and cap are written
down. An **emitter** (*emitters-and-evaluators*) is the producer side of the same
language: it **names no pool** — it stamps a produced task's attributes, and
whichever pool's filter accepts them claims it, so the producer/pool coupling is a
**filter match, not a wire**.

### registered-supervision
Supervision is **registered, not run in the foreground**. A caller hands the
host's singleton supervisor a **registration** — a lane to spawn for, a schedule,
an emitter, an evaluator — and immediately gets back a durable **registration
handle**; the call **returns** rather than becoming the loop. The registration is
the caller's durable token: it can **query the unit's status** at any later time,
or **remove** it, without holding a process open. The singleton owns the
*running*; the caller owns the *registration*. This is what turns a machine's
supervision into a **managed, inspectable, revocable set of units** rather than a
scatter of foreground loops each tied to the terminal that launched it.
Registering the same unit twice is **idempotent** (the handle identifies it), so a
re-register reconciles the unit rather than duplicating it. Each registered unit
runs in its **own subprocess**, so one unit's load or failure is isolated from the
others and from the master.

### declarative-discovered-registrar
Registered supervision has a **declarative, discovered** face, not only an
imperative call. A system, service, or repository **declares** its supervised work
as **configuration** in its **own install footprint**, and makes that footprint
known to the host supervisor through a lightweight **pointer** — the same shape as
a cache-populate registration, where a consumer points at *where its material
lives* rather than pushing it inline. The singleton **aggregates every registered
pointer**, reads the units it finds declared there, and reconciles them into what
it runs. This is what lets a domain **bring its own supervised work by convention**
— dropping a declaration into its own tree and being discoverable once — instead of
editing the supervisor or scattering a bespoke installer step per unit. Two
consequences follow. First, **supervised work travels with the code**: because a
declaration lives in the owning tree, a repository that carries its declarations
makes them **available wherever that repo is synced** — they light up on sync and
wind down when the repo or its declaration goes away, with no per-host installer
step. Second, the declaration carries **provenance** — which system owns it — so
the aggregated, machine-wide set of supervised units stays **legible** even though
many independent systems contribute to it.

A plugin can contribute the same way: its session-start hook leaves a lightweight
pointer to declarations in the plugin's own footprint. That drop-in is a
**candidate, not authority**. It contributes only while that exact plugin source
is effectively enabled **globally or by at least one registered project
repository** on the machine, and only while the pointer still resolves inside a
current, identity-matching root for that plugin. Ambiguous roots do not get an
arbitrary winner, and merely finding a stale drop-in from a former install never
keeps work alive. This is deliberately machine-wide: enabling a plugin in any
registered project makes its contribution available to the singleton, while the
declaration's ordinary filters decide which repos, machines, and environments it
may serve.

Crucially there is **one source of truth**: the declared documents themselves. The
imperative registration call
(*registered-supervision*) is a **thin writer over that source** — to register is
to **write a declaration**, to remove is to **delete it**, to query a handle is to
**read it back** — after which the singleton discovers and reconciles the change
exactly as it would a declaration authored by any other means. The declaration
*is* the registration; there is **no second, in-memory registry** standing beside
the files (*no-second-store*, applied to supervision).

## Behaviors

### focus-is-an-early-signal-not-a-heartbeat
Worktree focus is advertised and checked at the start of substantial work and
advertised again at genuine direction changes. It is not a per-turn timer, an
ongoing status heartbeat, or a replacement for the ground layer's authoritative
ongoing disposition and its update cadence.

### propose-then-queue
Putting work on the queue is **two acts, not one**. To **propose** is to author a
task — its concise goal, its detailed goal payload, its `requires`, its
`rejects`, and its other filter attributes — and receive a durable **task id**,
while the task sits **proposed** and **deliberately unclaimable**. To **queue** it
is the separate act that flips **proposed → queued** and hands it to binding. The
gap is useful: a producer (or an emitter authoring on a side-load) can **stage and
inspect** a task, revise its filters, or dedup it against existing work *before*
any agent can claim it, and a caller that wants the old one-shot behavior simply
does both in sequence. Nothing binds a proposed task; queueing is the commit.

### emitter-tasks-are-evaluated-mine-are-tracked
Whether a task is **auto-driven** or **self-tracked** is decided by **how it was
produced**. A task authored **through an emitter** — on its native event or a
side-load — carries that emitter's **evaluator**, so the layer runs the
follow-through (suspend/resume, follow-up tasks, completion confirmation)
automatically. A task **proposed and queued by hand, behind no emitter**, has no
evaluator: the layer still binds, embodies, and records it, but **the caller owns
watching it** to done. So "am I producing through an emitter?" is the single
question that decides who drives the lifecycle — the domain's evaluator, or you.

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
observable. A turn boundary is delivered by a cursor-based push subscription over
a persistent shared carrier; repeated state sampling, especially per-owner SSH
polling, is not event-driven supervision and is never an acceptable substitute.

### suspend-idle-resume-same-session
A headless worker that settles a turn without completing its task is
**suspended, not replaced**. Supervision records the idle boundary, gracefully
stops the Copilot process to release capacity, and retains the task owner,
session handle, worktree, progress, and pending steering as one cold assignment.
The next Resume or steer reattaches that exact ACP session in the same worktree;
ordinary payload, revision, or target movement never creates a replacement
session or workspace. Only confirmed context exhaustion permits an explicit
handoff to a successor session within the same task/worktree lineage.

### repo-lane-isolation
Every task belongs to the **repo lane** of the agent that produced it, and the
queue is scoped to that lane by default — an agent sees and claims **its own
repo's** tasks, never another's by accident. Cross-repo *code* work still lives
in the **producing** lane (tagged with the code target), done by a same-lane
worker; a task is never filed into a foreign lane to "send" it there. This keeps
one shared coordinator serving many repos without their work bleeding together.

### project-addressed-invocation
Every command of the delegation layer resolves its **target lane from an
explicitly named project** — `--project <name>`, or the per-project `<repo>`
binstub that supplies it — with the *same* result as being CWD-anchored inside
that repo. So a **CWD-neutral caller** (a supervisor/daemon whose working
directory is its own runtime dir, a script operating across several repos, a
caller reaching in across the SSH boundary) can create, claim, embody, and browse
a specific lane **without standing in its checkout**, and the layer is reachable
as `<repo> dispatch …` as readily as `agent-dispatch --project <repo> …` — one
consistent shape, whichever way in. The cross-lane escape hatch is
*repo-lane-isolation*'s own `--all-repos` / peer-queue browse, so naming a project
never walls off the deliberate cross-repo view. This is the delegation layer's
concretization of the parent fabric's §Features/*address-any-project* +
§Behaviors/*project-addressed-not-cwd-bound* — and it is the layer those fabric
items were **mined from** (the embody supervisor that could not resolve *which*
project to embody a queued task for from a neutral working directory).

### no-second-store
The delegation layer **coordinates over** state the layers below own; it does not
keep a private copy. Worker liveness and worktree identity come from the
ground/coordination layers; the queue adds only the **task** and its lifecycle.
(The fabric-wide *derive-don't-duplicate / single-owning-layer* rule, applied to
this layer.)

### drive-the-worktree-to-resolution
An embodied task **always** leaves its worktree in a **clean, resolved final
state**. Landing the work resolves it; so does abandonment — but abandonment means
**unwinding** the workspace to its base (a reset to the tracked upstream) so it
reads clean and is prunable, never leaving an orphan branch nobody owns.
Abandonment also **reconciles the source**: the producing domain (or effort/issue)
is notified so its own records stop believing the work landed. The queue's promise
that work never silently piles up extends to the **workspaces** that work runs in —
resolution of the goal and resolution of its worktree happen together — and a
worker that gives up part-way is expected to drive its own worktree to that clean
state before it lets go.

### allocator-reclaims-what-it-creates
The component that allocates an embodiment owns its lifecycle through
reclamation. A terminal task does not merely release queue capacity while its
worker workspace accumulates indefinitely: the supervisor retains the exact
allocation identity and creating-host provenance, retires the session it
started, releases the task's inbound claim, and delegates atomic
resolution/removal to the ground layer that owns worktrees. It never evaluates a
foreign-host allocation locally, never races a resumed workspace, and never
turns an `abandoned` task into authority to discard dirty work. Headless bodies
with no worktree require no reclamation; externally supplied or origin-unknown
worktrees remain with their external owner. A worktree shared by another live
task remains claimed. Missing or version-skewed ground-layer capabilities hold
the obligation visibly rather than degrading to unsafe local logic.

This is the delegation-layer face of the parent fabric's resource-claim
direction and *claimed-resource-not-reclaimed* guarantees: dispatch records the
allocation fact it alone knows, while the ground layer remains the single owner
of prune eligibility and safe removal. This ownership boundary keeps producer
domains focused on their own records while giving every
agent-dispatch-created workspace one generic, observable cleanup path.

### no-overlapping-live-workers
Two live workers never hold **overlapping** work. When a goal is re-carved so a new
task covers scope a previous worker held — most commonly after an abandon
reconciles an omission back onto the queue, or on a reassignment — the
**predecessor is gone before the successor starts**: its session has ended and its
worktree has been torn down. Two mechanisms make this hold without racing:
**reserved-work dedup** (*dedup-before-create*) stops the overlap from being carved
at all, and the claim's **liveness** plus the supervisor's **teardown**
(*liveness-not-lease*) keep a superseded worker from lingering beside its
replacement. Workers are still expected to tolerate a *minor* collision gracefully,
which makes this a **safety margin** rather than a timing-critical lock: correctness
does not hinge on perfect timing, yet the layer refuses to leave a zombie worker
running next to the one that supersedes it.

### a-loop-runs-with-or-without-a-service
The same template runs two ways, and neither requires the other. **On demand**: a
person or agent **side-loads** a registered emitter (or, for genuinely novel work
with no matching emitter, hand-authors a task by **propose + queue**) and the
*local* supervisor watches it to resolution — no standing schedule required.
**Service-driven**: the same registered emitter also fires on its **native event**,
and its evaluator advances the work across events. The service tier is how
*recurring* or *at-scale* work is automated; it is never a precondition for running
a single loop. The minimum viable deployment is a **coordinator plus a worker
body**, so the reviewer, conflict-resolution, and goal-driven templates are equally
available to a full automated deployment and to a bare host that has none of it.

### supervise-registers-and-returns
Registering supervised work **adds the registration and completes**, emitting the
registration info back to the caller — it never blocks as the loop. Exactly **one**
supervisor process per machine-and-environment services **all** registrations on
that host; a second registration **attaches to that same singleton** (starting it
if it is not already running) rather than spawning a rival loop. Querying a unit's
status and removing it are their own operations against the registration handle, so
a caller inspects or revokes work long after the registering call returned. A
removed registration's running work is **wound down** by the singleton; a
registration whose unit keeps failing is surfaced through the same *observable
lifecycle* as any other work, never silently dropped. This is the behavioral face
of *registered-supervision*: the terminal that registers a schedule, an emitter, an
evaluator, or a supervised lane does not become that work's host — the machine's one
supervisor does.

### discover-and-live-reconcile
The singleton does not only reconcile registrations at startup — it **watches its
registered pointers and reconciles continuously**. A declaration that **appears**
(a synced repository, a newly installed service) is picked up and its unit
**started**; one that **changes** is **applied in place** — new concurrency, a
different body, an altered lane — without tearing the rest down; one that
**disappears** is **wound down**, its in-flight work drained, never orphaned. All of
this happens **without restarting** the supervisor and without a human editing a
central list: discovery is **by convention**, so adding supervised work is a matter
of *declaring it where the supervisor already looks*, and removing it is a matter of
*deleting the declaration*. The live set of supervised units is therefore a
**continuously-reconciled reflection** of what every registered system currently
declares — the same self-healing posture the singleton already applies to a unit's
liveness, extended to the *membership* of the set itself. Discovery reconciles
**intent to reality**: what is declared by an eligible source is what runs. For a
plugin-owned source, changes to global or registered-project plugin enablement are
membership changes too: disabling the plugin everywhere winds its units down even
if an old pointer remains, while enabling it in any registered project activates
the contribution without a supervisor restart.

### overrides-take-precedence
The running set of supervised work is **declarations reconciled with operator
overrides**, and an override **wins**. A fast, local **enable/disable** toggle on a
declared unit — an **emitter** (stop it producing) or a **pool** (stop it binding) —
takes precedence over that unit's declared state *and* over the discovery layer, so
an operator can **stop a misbehaving unit immediately** without editing, or racing a
repo-sync against, its declaration. The override is applied **out of band** (a
runtime control, not a repo commit + sync cycle), is **reversible** (clear it and
the unit returns to whatever its declaration says), and is **legible** (what is
overridden-off, and why, is visible beside what is declared). This is the emergency
stop that *discover-and-live-reconcile* needs to be safe: discovery keeps *what
should run* converging on the declarations, while an override is a human's
**higher-precedence veto** for when something must stop running *right now*,
declaration notwithstanding — and, crucially, a later re-sync of the declaration
does **not** quietly undo it.

## Non-Goals / Boundaries

- **Not the live-conversation layer.** Driving or messaging a running agent
  turn-by-turn is the **coordination layer** (agent-bridge). This layer carries
  *async, recorded, claimable* work, not a live dialogue.
- **Not the embodiment owner.** *How* a claimed task becomes a running worker —
  a durable CLI session versus a headless helper — is decided by the ground and
  coordination layers (*lifetime-decides-embodiment* in the parent). This layer
  does not choose or implement the body type, but when its supervisor requests
  an allocation it owns the resulting allocation's provenance and terminal
  hand-back.
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
- Child leaf: [reviewer loops](reviewer/README.md) — the cooperative reviewer
  archetype's identity, verdict, reuse, and reliability contract.
- Child leaf: [repository-issue-loops](repository-issue-loop/README.md) — the
  backlog-batch archetype's declarative adoption, provider-neutral capability,
  and declarative worker-identity contract.
- Sibling leaf: [agent-ssh](../agent-ssh/README.md) — the connectivity layer this
  layer's cross-machine reach rides on.
- Consumer: [agent-logger](../agent-logger/README.md) — the **chronicler**, a
  scheduled-production consumer whose per-session units are ordinary claimable
  tasks on this layer's mesh.
- Reality docs: [`docs/architecture.md`](../../../docs/architecture.md) · the
  `plugins/agent-dispatch/` skills (`agent-dispatch`, `pick-and-claim`).
- Realization effort:
  [`efforts/active/declarative-dispatch-engine-generalization/`](../../../efforts/active/declarative-dispatch-engine-generalization/)
  owns *concise-event-then-charter-pull* and *preloaded-dispatch-supplement*.

## Provenance

- **2026-09-05** — Extracted the repository-issue-loop archetype's declarative
  adoption, provider-neutral capability, and declarative worker-identity intent
  into its own child vision, as a fourth named archetype alongside reviewer,
  conflict-resolution, and goal-driven. Also added *concise-event-then-charter-pull*
  and *preloaded-dispatch-supplement*: the per-embodiment seed should be a
  short event notification plus a charter-pull command, not an inlined
  instructional essay, and the generic "how to behave as a dispatch worker"
  supplement belongs on the worker identity, loaded once, not rediscovered
  per task.
- **2026-09-02** — Promoted this vision from leaf to branch and moved the
  reviewer archetype's cooperative identity, session-reuse, verdict, declarative
  adoption, and bounded-reliability intent into the reviewer child vision.
- **2026-08-31** — Added *terminal-worktree-reclamation* and
  *allocator-reclaims-what-it-creates*: terminal task settlement must not leave
  supervisor-created worktrees registered indefinitely. The producer decides
  when domain work is terminal; agent-dispatch retains and reclaims the
  embodiment allocation it created through the ground layer's safe lifecycle.
  Implementation is tracked by
  [`terminal-worktree-reclamation`](../../../efforts/active/terminal-worktree-reclamation/README.md)
  and [#1488](https://github.com/ThomasMichon/copilot-extensions/issues/1488).
- **2026-08-24** — Tightened *declarative-discovered-registrar* and
  *discover-and-live-reconcile* for plugin-owned declarations. A plugin may drop a
  pointer to declarations in its own footprint from a session-start hook, but the
  machine-level drop-in is only a **candidate**: the singleton activates it only
  while that canonical plugin source is enabled globally or by at least one
  registered project repo, and only when the target remains inside a current valid
  identity-matching root for that plugin. Ambiguous roots have no arbitrary winner.
  Stale files from disabled or uninstalled plugins are inert; enablement changes
  participate in live reconciliation. Mined from the
  agent-bridge `providers.d` and agent-codespaces `config.d` precedents, with an
  explicit stale-contribution guard added for autonomous supervised work. Concrete
  directory and manifest formats stay spec-level.
- **2026-08-11** — Added the *overrides-take-precedence* behavior: the running set
  of supervised work is declarations **reconciled with operator overrides**, and an
  override wins. A fast, local, reversible enable/disable toggle on a declared unit
  (an emitter to stop producing, a pool to stop binding) takes precedence over the
  declared state and the discovery layer, so an operator can stop a misbehaving unit
  immediately without editing or racing a repo-sync against its declaration (and a
  later re-sync does not undo it). Mined from an operator design steer (a quick
  emitter enable/disable kill-switch for when something goes wrong). The override
  mechanism/CLI stays spec-level.
- **2026-08-11** — Reframed the production model around **propose/queue**,
  **filters**, and **emitter/evaluator templates** (one operator design
  conversation). (1) The lifecycle wording was corrected to the code's real states
  (**proposed → queued**, not "drafted → ready"), and **propose** and **queue** were
  split into two first-class operations (behavior *propose-then-queue*): propose
  drafts a task (goal + goal payload + requires + rejects + filter attributes → id,
  unclaimable); queue commits it to binding. (2) **"Recipe" left the command
  surface**: a recipe is now a **template for an emitter/evaluator pair**,
  *instantiated by registering* a concrete pair with the environment specifics
  (target repo, review technology, issue backend, machine/account) — *The recipe*,
  *loop-recipes*, and *a-loop-runs-with-or-without-a-service* were reframed
  accordingly, and *recipes-run-ad-hoc* became **side-load-through-an-emitter**
  (hand a request to a registered emitter and it authors the fully-evaluated task on
  demand). (3) The **evaluator belongs to the emitter**: an emitter's tasks are
  auto-driven by its evaluator; a hand-proposed task with no emitter is self-tracked
  (behavior *emitter-tasks-are-evaluated-mine-are-tracked*). Pairs with the same
  conversation's *pools-are-filters-with-a-cap* below: pools, and the emitter/
  evaluator pairs that feed them, are all **declarations in the registrar**, coupled
  only by the shared filter vocabulary. Concrete verbs, schema, and the filter
  syntax stay spec-level (reality docs), not here.
- **2026-08-11** — Added the *pools-are-filters-with-a-cap* feature (and extended
  *capability-and-affinity-routing* to name the fuller attribute vocabulary). A
  supervised pool is reframed as a **standing filter predicate over the
  task-attribute vocabulary — permit and reject — plus a max-agent cap**, named for
  its task-type, with the repo filter defaulting to same-repo. Tasks *declare*
  attributes and pools *filter* over the same vocabulary ({repo, machine,
  environment, worktree, capabilities, role, task-type}); a task binds to the pool
  whose filter accepts it, subject to the cap, while a hard per-task pin (a target
  worktree, a required capability) binds regardless of any pool. Mined from an
  operator design steer (an "agent pool" is a repo-bound sub-group whose name is the
  task type and which caps agents-per-type; more generally, repo/machine/env/role/
  worktree are all *filters* on valid tasks, and both a dispatch and a pool are
  expressed in that one vocabulary). Concrete filter syntax stays spec-level (the
  registrar declaration's `filters:` block), not here.
- **2026-08-10** — Added the *declarative-discovered-registrar* feature and the
  *discover-and-live-reconcile* behavior: registered supervision gains a
  **declarative, discovered** face on top of the imperative call. A system,
  service, or repository **declares** its supervised work as configuration in its
  own footprint and makes it discoverable to the host supervisor through a
  lightweight **pointer** (cache-populate style); the singleton aggregates every
  pointer, reads the declared units, and **continuously reconciles** them —
  starting what appears, applying what changes, winding down what disappears —
  without a restart. Supervised work thus **travels with the code** (a repo's
  declarations light up on sync). The imperative registration is reframed as a
  **thin writer over the one source of truth** (the declared documents), so there
  is *no second registry* beside the files. Mined from an operator design steer
  (the supervisor-profile config should be a durable, YAML-first task registrar,
  self-service and hot-reloaded, rather than installer-managed per-unit env files).
  Concrete formats, directory conventions, the pointer mechanism, and the
  file-watch/reload machinery stay **spec-level** (reality docs), not here.
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
  and an AI-reviewer-style "drive this change to ready" goal — the intent
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
  is a cursor-based push subscription; repeated derived-state sampling does not satisfy
  this behavior. Mined from
  the operator's "reactive-on-turn-end" steer as the fourth full-auto piece.
  Mined from an operator design conversation refining the recovery flow (heartbeat +
  state payloads → verify-or-mitigate on a bad "done" → nudge/re-embody on a stalled
  or gone worker).
- **2026-08-03** — Added §Behaviors/*project-addressed-invocation*: every command
  of the layer resolves its target lane from an explicitly named project
  (`--project`, or the `<repo>` binstub that supplies it) identically to
  CWD-anchoring, so a CWD-neutral caller drives a specific lane without standing in
  it and the layer is reachable as `<repo> dispatch …` as readily as `agent-dispatch
  --project <repo> …`. Makes explicit at the leaf what the parent already owns
  fabric-wide (§Features/*address-any-project* + §Behaviors/
  *project-addressed-not-cwd-bound*) — and closes the loop on where those items were
  mined from: this layer's embody supervisor (`Could not resolve a project for
  'embody'`). Its realization (the CWD-neutral `--project`-named embody spawn) had
  shipped without the leaf vision claiming the intent; this binds it so any effort
  carved from the agent-dispatch delta inherits it.
- **2026-08-07** — Extended for the **loop-recipe / ad-hoc / hibernation** intent:
  added §Concepts/*The recipe — a packaged loop archetype* (reviewer /
  conflict-resolution / goal-driven) and *The evaluator — a producer's lifecycle
  handler*; the *loop-recipes*, *recipes-run-ad-hoc*, *hibernate-the-wait*,
  *emitters-and-evaluators*, and *bounded-concurrency-wide-charters* features; and
  the *drive-the-worktree-to-resolution*, *no-overlapping-live-workers*, and
  *a-loop-runs-with-or-without-a-service* behaviors. States that the layer ships the
  *shapes* of long-running work as reusable recipes runnable **ad-hoc** (coordinator
  + worker body + recipe, no standing service required), that a waiting worker is
  **hibernated** (process torn down, resumed on the awaited event) rather than left
  holding a process, that producers pair with **evaluators** to advance a loop
  across lifecycle events, that an abandoned worker **drives its worktree to a clean
  resolved state** and reconciles its source, that two live workers never hold
  overlapping work, and that concurrency is **capped** while charters stay wide.
  Mirrors the intent captured in the downstream fabric vision from the operator's
  loop-recipe design conversation; the implementing work then closes this delta.
