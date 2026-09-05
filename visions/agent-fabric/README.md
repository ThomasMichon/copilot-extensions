# Agent Fabric — Vision

- **Subject:** The **agent fabric** — the layered system that turns isolated
  Copilot CLI sessions into one coordinated, observable, multi-agent fabric
  spanning worktrees, machines, CodeSpaces, and containers.
- **Scope:** branch (links per-plugin child visions as they are authored)
- **Status:** Active
- **Last revised:** 2026-09-04
- **Reality docs:** [`docs/architecture.md`](../../docs/architecture.md) ·
  [`docs/harness-runbook.md`](../../docs/harness-runbook.md) · each plugin's
  `docs/architecture.md`

## Purpose & Intent

A single Copilot session is one agent, alone in one working tree. The **agent
fabric** is what lets many such agents — spread across parallel worktrees, other
machines, CodeSpaces, and containers — be **spun up, discovered, delegated to,
communicated with, and recovered** as one legible whole, without an account per
agent and without agents clobbering each other's resources.

The north star is a fabric built as **composable layers**. Each layer is an
independently installable plugin that stands alone with a coherent capability;
**adding a layer strictly augments what the layers below already provide**,
never breaks their standalone contract. The lower a layer sits, the more
foundational and **passive** it is (legible with no running service); the higher
it sits, the more **active** — creation, delegation, recovery. A participant
aware of only a lower layer still gets that layer's full value; awareness of a
higher layer unlocks more.

The friction this vision exists to abolish is the **coordination tax** of a
fleet: not knowing whether an agent is already on a job, spinning up a duplicate
worktree for work already in flight, losing an ephemeral agent's output when its
container is torn down, and having no shared place to hand a task from one agent
to the next. The fabric makes *who-is-doing-what* and *hand-this-onward*
first-class.

## Concepts & Components

The fabric is a **layered stack of plugins**; each layer is its own subject (a
per-plugin child vision refines it under `visions/plugins/<name>/`). Two
load-bearing properties bind the layers:

- **Graceful composition.** A lower layer is fully useful alone; a higher layer
  augments it opportunistically. No layer demands that a higher one be present.
- **Derive, don't duplicate.** Each piece of fabric state has exactly **one**
  owning layer; higher layers *coordinate over* and *derive from* that state
  rather than keeping a second copy. This is what keeps the layers'
  responsibilities separate as the stack grows.

### agent-worktrees — the worktree-lifetime agency ground layer
Owns repository and worktree identity, isolation, source-control lifecycle,
claims, obligations, asserted disposition, execution relationships, and the
durable head/succession record. A worktree is a persistent unit of agency, not a
Copilot process: its responsibility survives terminals, applications, protocol
hosts, and session generations. Execution providers contribute attributable
observations without becoming a second owner of worktree truth.
A per-plugin child vision refines this host-neutral boundary at
[`visions/plugins/agent-worktrees/`](../plugins/agent-worktrees/README.md).

### Copilot session hosting — the execution-provider layer
Owns how an agent process is launched, presented, prompted, observed, reattached,
handed off, and retired. Copilot CLI under TMux/PSMux, plain CLI, ACP/session
hosts, SDK integrations, graphical applications, and third-party rigs are peer
providers behind one capability-honest boundary. The provider owns execution
mechanics; the worktree ground layer owns durable agency meaning. The
cross-cutting [session-hosting](../session-hosting/README.md) vision refines this
seam.

### agent-bridge — the coordination layer
Adds **remote agent creation, inspection, and communication** over discoverable
channels. It augments the ground layer with **granular, live state** and gives an
agent the means to **call other agents**: create agents and worktrees, peer into
another agent's status, send a message into an agent (whether one it controls or
a peer), and get a sense of what others are doing — including answering *"is an
agent already up and running to cover this worktree or repo?"*. **A message sent
into a busy agent is not lost or refused: it is durably queued in the
coordination layer's own state and delivered — in order, exactly once — when the
target next settles**, surviving the sender's remount, a UI's death, and a
restart of the bridge or host on either side. Delivery is a property the fabric
owns, not one each caller (a browser, a host CLI) must reinvent on top. The live
state it surfaces is rich enough to bring **granular, live status into the
worktree picker**.
A per-plugin child vision refines it at
[`visions/plugins/agent-bridge/`](../plugins/agent-bridge/README.md) — durable
session hosting, live-session messaging, peer bridge ownership, and
project-addressed mesh control.

### agent-ssh — the connectivity layer
Owns the **SSH mesh** the fabric's cross-machine reach rides on. It
**provisions and maintains** the transport (OpenSSH substrate, keys, host-key
pinning), **adopts** machines into a declared mesh, stands up a **pluggable
transport module** per machine (direct, a tunnel-based provider, or real-user
interactive reach), manages a **tunnel-first firewall posture**, and keeps each
machine's advertised **reachability honest** by verifying it against the live
path. Where the coordination layer and the venue providers assume they can reach
another machine, this layer is what makes that assumption *true* — turning "SSH
is borrowed" into "SSH is provisioned, verified, and maintained." Per
*derive-don't-duplicate*, its machine registry is the **single owning store** of
mesh reachability, which the layers above route **over** rather than copy. A
per-plugin child vision refines it at
[`visions/plugins/agent-ssh/`](../plugins/agent-ssh/README.md).

### agent-dispatch — the delegation layer
Adds **task management and role assignment**: a **shared, transactional store**
of task definitions, plus a place for an agent to report **summary status** —
distinct from the in-conversation messages the coordination layer carries,
because dispatch asks an agent to do work *on the fabric's behalf* and record an
outcome. Aware of **only** this layer, agents **stash** tasks to be picked up
later or handed off. Aware of this layer **and** the coordination layer, agents
**delegate** tasks to spun-off agents. A per-plugin child vision refines it at
[`visions/plugins/agent-dispatch/`](../plugins/agent-dispatch/README.md) — the
four production modes (continuation / fire-and-forget / reactive / scheduled),
the *fire-and-forget-not-driven* line versus the coordination layer, and
liveness-reconciled recovery over lease timers.

### agent-codespaces — a venue provider
**Provisions CodeSpaces** for related repos, injects the right plugins and
environment to **run agents headlessly** there, and then presents those CodeSpace
agents to the fabric as a **provider for the coordination layer** — so a remote
CodeSpace agent is created, inspected, and reached by the *same* contract as a
local one. A per-plugin child vision refines it at
[`visions/plugins/agent-codespaces/`](../plugins/agent-codespaces/README.md) — the
venue lifecycle (boot-on-connect, session-survival on teardown), the single
multiplexed transport, the host-credential relay (*borrow identity, never bottle
it*), and the parent service-model invariants stated in the venue's own terms.

### agent-containers — a venue provider
Does the same for **local containers**: provision and set up a container-hosted
agent and present it to the fabric as a coordination-layer provider, so a
containerized agent is a first-class fabric participant. Its per-plugin vision
distinguishes **trusted development venues** from **restricted low-trust
sandboxes**, making the container's effective authority explicit and enforced
by construction:
[`visions/plugins/agent-containers/`](../plugins/agent-containers/README.md).

### agent-logger — the memory layer
**Recovers Copilot session data** from local and remote-dispatched agents —
especially from **ephemeral containers** whose state would otherwise vanish with
them — and provides **session compilation and segmentation**, distilling raw
session state into a form a **later agent can digest**. Work survives the agent
that did it and can be handed forward.

### agent-vault — the trust layer
Provides **credentials** to agents in the cases where an SSO / identity provider
alone is insufficient, so an agent can authenticate to the resources its work
requires.

## Features

### layered-composition
The fabric is assembled from independently installable layers. Each layer is
fully functional on its own; installing a higher layer **adds** capability to the
layers below without altering or breaking their standalone behavior.

### one-fabric-many-venues
A local worktree agent, an agent on another machine, a CodeSpace agent, and a
container agent are all reachable through **one** creation / inspection /
communication contract. Where an agent runs is a venue detail, not a different
interface.

### address-any-project
Every layer of the fabric is invocable against an **explicitly named project**,
not only the one implied by the current directory. A single per-project entry
point — the `<repo>` binstub — is a uniform dispatcher across the whole stack:
`<repo> <layer> …` reaches *any* layer scoped to that project (worktrees,
coordination, delegation, a venue provider, the vault) with the same muscle
memory. So a caller with **no project-anchored working directory** — a long-lived
service, a daemon, a script operating across several repos — can still drive any
layer against a specific project, and a human addresses the whole fleet through
one consistent `<repo> <layer> …` shape rather than a different convention per
tool.

### discover-before-duplicate
Before an agent spins up work on a target, the fabric can answer **"is someone
already on this?"** — is an agent or worktree already covering this repo/target,
running or parked — so a duplicate is a deliberate choice, not an accident.

### delegate-and-hand-off
Work can be **stashed** for later pickup, **handed off** between agents, or
**delegated** to a spun-off agent, with a shared record of the task and its
outcome — so a fleet cooperates through durable artifacts, not just live chatter.
The **launch** underneath is supplied by the selected session-host provider.
The **orchestration** of a handoff — composing the continuation, minting the
claimable delegation record, requesting a successor, verifying it, moving
durable agency authority, and authorizing predecessor retirement — belongs
above both the worktree ledger and the host. No generic handoff component
hard-codes one provider's process mechanics.

### unreachable-machine-maintenance-handoff
When every declared route to a machine is unavailable after bounded diagnosis,
the fabric treats that result as a **routing boundary**, not a reason to retry
indefinitely or bypass normal deployment. Repeatable updates and required state
are first represented in the machine's declarative convergence packages or
another explicitly declared auto-update system. Residual work that must execute
locally is recorded in an explicitly identified user repository as a
machine-scoped maintenance issue, while agent-dispatch supplies the optional
single-claim execution lifecycle. Issue prose is advisory evidence, never an
untrusted command stream: a target-local agent re-derives the action from
trusted repository state, preserves confirmation gates, verifies the
postcondition, and only then closes the item.

### legible-live-state
What every agent is doing is **observable** — from a coarse Active / Recent /
Completed floor with no service, up to granular live status surfaced into the
worktree picker when the coordination layer is present. Legibility spans two
complementary registers. A **durable disposition** the agent *asserts* —
*resolved* vs. *has actionable follow-ups* — so a glance distinguishes a
prune-able worktree from one still owed attention (a finalized worktree with an
un-pushed change, an undeployed merge, or leftover temporary state is *not*
done). And a **live activity pulse** *passively derived* from the agent's own
intent signals, needing no cooperation, giving a rapid — if coarse — sense of
current motion. The disposition is high-signal and slow; the pulse is low-signal
and fast; neither is faked from the other.

### legible-contribution-contract
Landing work is **governed by rules that differ per repo** — whether a pull
request is required, who may approve (and whether the submitter may approve
their *own* PR), who reviews and roughly how long that takes, who performs the
merge, and what a post-approval conflict does. The fabric makes those rules
**legible to the acting agent at the moment of action**: whenever an agent
drives a step that lands work, the layer performing it states — in that step's
own output — which contribution contract *this* repo uses and what the correct
next step is, so an agent never has to *remember*, *guess*, or carry over
another repo's flow. The guidance is **repo-derived**, not hard-coded to any one
project's shape, and it **keeps the agent on the sanctioned rails**: it names
only the fabric's own verbs and the reviewed flow, and never steers toward a
bypass — a raw provider call, a force/override, a merge that skips review — even
where an agent technically could. Both a successful step **and a refused one**
are reminded; a refusal says what to do *within* the rules, not how to skip
them.

### resource-claims
Every worktree carries a **legible claim ledger** — the resources and work it is
responsible for — answerable in **both directions**: *what does this worktree
hold?* and *who holds this resource?* Claims come in two complementary
directions. **Outbound** claims are the resources a worktree **produces and
owns**: pull requests it opened, worktrees it spun up in **other repos**,
CodeSpaces and containers it provisioned, SSH connections it leased, working
directories it took. **Inbound** claims are the external work a worktree **pulls
in and takes responsibility for**: a tracked bug or work item, a pull request it
adopted for review or maintenance, an effort or vision it is advancing. The
dividing line is **direction of creation** — who brought the thing into the
system — not a rigid taxonomy; a resource that blurs the line is placed by which
worktree originated it. The ledger makes a worktree's true footprint
first-class: a resource is never an anonymous orphan, and a worktree is never a
black box about what it is using elsewhere.

Claims exist only under a **resolved durable coordination identity**. A
self-hosted harness may use its own repository identity; a stateless harness
that requires an external state home must bind and resolve that state identity
before it creates or adopts claims. There is no fallback to a shared launch
repository, because two operators must never collide through coordination state
that belongs to neither of them.

Responsibility begins with the worktree that creates or adopts a resource and
stays with that creator through settlement or cleanup. A message, continuation
baton, or sender shutdown never transfers ownership by implication. When work
must move, the source may offer an exact **claim bundle** to a qualified consumer
worktree, but its claims remain authoritative and finalize-blocking until that
consumer affirmatively accepts. Acceptance is one atomic, all-or-nothing
transition across both sides of each claim: the source's forward ledger, the
consumer's forward ledger, and the resource-side owner or fenced lease holder.
Only after that transition commits may the source release responsibility, and
the consumer's own finalize gate immediately inherits it. Decline, cancellation,
an unsupported claim, or a crash leaves the source authoritative; cross-machine
acceptance succeeds only with synchronous reservation at the remote ownership
authority, never through a best-effort local fallback.

### resource-leasing
Where `resource-claims` makes a worktree's footprint **legible**, resource
**leasing** makes exclusive access to a scarce shared resource **collision-free
by construction** — one atomic primitive so two agents on two machines never
grab the same CodeSpace, cross-repo worktree, container, or bridge session at
once. The intent is deliberately minimal: the lease lives as **ref shenanigans
only** in the harness's **resolved coordination-state repository** — the
self-hosted harness repository or the required bound state repository — as a
hidden ref per resource, moved by atomic **compare-and-swap**, with **no
branches, no working-tree writes, no new service, and no new credential**.
Acquisition is atomic mutual exclusion; the winning transition mints a
**fencing token** a holder must present to renew or release, and release leaves
a **tombstone** so a stale token can never silently re-win. The holder is the
worktree's own **claim ref**, so the atomic-acquire and
liveness-for-takeover mechanisms **compose** rather than duplicate. Two
load-bearing properties bind it to the rest of the fabric:

- **Same-harness by construction, degrade-safe by default.** Because the store is
  the resolved coordination identity's repository, only agents sharing that
  identity arbitrate the same resource — coordination scope is free and
  principled, not a bolt-on check. A harness that explicitly requires an
  external state identity fails closed before new allocation when that identity
  is unbound or unresolved; it never falls back to the shared launch repo.
  Existing fenced ownership remains renewable and releasable so teardown is not
  wedged by a later resolution outage. Every layer above stays optional: where
  no external identity is required, no store, token, or network degrades the
  lease to a best-effort local advisory; only a definitive conflict or missing
  required identity is allowed to block new acquisition.
- **Two-tier, and fenced on the resource for the cross-harness seam.** A fast
  same-machine local lock is the L1 path; the ref-CAS is the L2 authority only at
  the cross-machine boundary. Genuine **cross-harness** contention (two different
  people's harnesses on one shared resource) is out of any single repo's reach, so
  the fence moves **onto the resource itself** — a marker the resource carries and
  honors (e.g. a lockfile inside a CodeSpace), refusing a foreign holder while
  staying degrade-safe. This is the backend fence the primitive's own safety model
  recommends.

### resource-accountability
The claim ledger makes a footprint **legible** and the lease makes access
**exclusive**; accountability closes the loop by making a worktree **answer for
everything it allocated before it is allowed to disappear**. The intent: a
worktree never finalizes while it still owns **unsettled work** on a resource it
brought into being — a cross-repo worktree it opened, a CodeSpace or container it
borrowed, a bridge session it drove. Finalize is the **join point** where those
obligations are asserted clear; **release is gated on settlement** so tearing down
a claim can never silently orphan the work behind it.

The load-bearing distinction is between the **claim** and the **resource**:
*at-rest* is a property of the resource (its work is safe — merged, off-box, or
itself finalized), while *released* is a property of the claim (its lease is torn
down). They decouple — a CodeSpace can go at-rest (work safe) and have its claim
released (freeing it for the next borrower) **without being destroyed**; "closed
out" means *safe*, not *deleted*. Three properties keep this honest and cheap:

- **Settle incrementally, assert locally — never traverse at finalize.** The cost
  of proving a footprint safe is paid **continuously**, at each resource's own
  natural close-out moment, not in one expensive recursive walk when finalize runs.
  A child resource **returns its obligation upward** the instant it settles (a
  cross-repo worktree flips its parent-visible claim when *its own* finalize
  succeeds; a CodeSpace/container stamps its cleanliness on each disconnect or
  heartbeat). Finalize then reads a **cheap local balance** — "do I still hold any
  unsettled obligation?" — and trusts each child's already-recorded verdict rather
  than re-deriving the whole tree. This is reference counting on a cross-resource
  scale: the recursion **collapses into local checks**, so proving a worktree
  truly final stays fast no matter how deep its footprint.
- **Never wedge; never lose work.** The gate blocks only a *definitive* unsettled
  obligation; every ambiguity degrades to a warning, and adoption is warn-first
  (an un-annotated legacy claim never hard-blocks). A crashed holder that never
  settles cannot freeze its parent forever: **liveness + a reclaiming sweep** may
  settle or re-parent an obligation whose owner is provably gone and whose resource
  is provably safe — the safety-net GC beneath the accountable gate. And an
  explicit abandon **re-homes** the obligation to a durable owner rather than
  dropping it, so responsibility survives the worktree that abandoned it.
- **Answerable in both directions, still.** Because every obligation rides the same
  claim ledger, the fabric can always answer *what unsettled work does this worktree
  still owe?* and *which worktree is answerable for this resource?* — the
  legibility of `resource-claims` extended from "who holds what" to "who still owes
  what."

### externally-observable
Beyond the fabric's own picker legibility, each layer's lifecycle is
**externally observable** through a **backend-agnostic telemetry seam**: a layer
declares its lifecycle surface and ships a **no-op-by-default** emission hook, and
a downstream observability consumer **attaches a publisher by configuration, not
code** — without any layer depending on a specific telemetry backend or
transport. The seam carries lifecycle **state and structure only** (never
conversation content, a task prompt/payload, or any secret) and is **fail-open**:
an unconfigured or misconfigured sink leaves the layer untouched. *How* a consumer
is attached (an environment variable, a dropped config file) and the on-disk
config shape are spec-level, not fixed by this vision.

### survivable-work
An agent's session output is **recoverable and digestible** after the fact —
including from short-lived remote venues — so a successor agent can catch up on
what a prior one did without the original conversation.

### handoff-under-context-pressure
A session approaching the **limit of its own context window** is a first-class
**reason to hand off**, not merely a condition a caller happens to notice. The
fabric treats context saturation as a continuity signal: long-running work
**survives the context ceiling** by rolling in place to a fresh successor —
seeded with the predecessor's continuation — rather than degrading, stalling, or
silently losing the thread as the window fills. This makes the handoff a
*proactive* act the fabric can initiate on the work's behalf, complementing the
caller-initiated handoff of *single-current-session-per-worktree*. It is
**especially essential where there is no manual session-creation affordance** —
a phone or other minimal consumer with no "new session" / "clear" control — for
which continuing simply by **sending the next message** is the only path forward;
there, an automatic in-place roll under pressure is the difference between work
that continues and work that dead-ends at the ceiling.

### no-account-per-agent
A whole fleet of agents cooperates through the fabric **without** provisioning a
separate identity / account per agent and without agents racing each other
through a shared default branch.

## Behaviors

### compose-by-awareness
Capability scales with which layers a participant knows about. An agent aware of
only the ground layer still gets isolation + coarse legibility; adding awareness
of coordination, delegation, or a venue provider unlocks the next capability —
and never *removes* a lower one.

### derive-dont-duplicate
Each fabric state (worktree / session state, live agent status, task records,
credentials) has a **single owning layer**. Higher layers **read and coordinate
over** lower-layer state; they do not persist a competing copy. Cross-layer
answers (e.g. "who is on this target, and are they live or parked?") are
**derived** at read time from the owning layers, not stored anew.

### claims-owned-by-direction
The two halves of a worktree's claim ledger (§Features/*resource-claims*) have
**different owning layers**, per *derive-dont-duplicate*. **Outbound** claims —
the resources a worktree creates and manages — are owned by the **ground
layer**, which already owns the worktree and everything spun from it. **Inbound**
claims — the external work a worktree takes on — are owned by the **delegation
layer**, which already owns the claimable task. Neither half is copied into a
third store: the fabric **derives** a worktree's *full* ledger by reading both
owning layers at read time. Each half resolves **both ways** from its owner —
worktree→its claims, and claim→its owning worktree — so the reverse lookup
(which worktree holds this pull request, container, or task?) is always
answerable without a central registry.

### passive-legibility-floor
The ground layer is legible **without any running service** — its state is
discoverable through declarative hooks and on-demand reads — so the fabric is
never wholly blind, even with no daemon up.

### uniform-venue-reach
Adding, moving, or losing a venue (a CodeSpace, a container, another machine)
does not change how its agents are addressed: a venue provider makes its agents
reachable by the fabric's one coordination contract.

### guidance-emitted-at-point-of-action
Every fabric operation that participates in landing work emits its
rule-and-next-step guidance **inline, on both its success and its failure
path**, in whatever register the caller consumes (human prose and
machine-readable alike). The guidance is a **passive reminder** that rides along
with the action the agent already took — it never becomes a gate, and it never
depends on the agent having first asked "what are the rules here?". The floor is
that an agent acting on a repo it has never seen is told that repo's
contribution contract by the very commands it runs, and is pointed only at
sanctioned verbs — so staying on the reviewed rails is the path of least
resistance, not a discipline the agent must supply.

### project-addressed-not-cwd-bound
A layer resolves its **target project** from an explicit name (`--project`, or
the per-project binstub that supplies it) with the *same* result as being
CWD-anchored inside that project. Git-like discovery from the working directory
is a convenience for a human standing in a repo — **not** the only path. A
neutral working directory is therefore never a barrier: a service embodying work
for another repo, or a script operating across several, **names** the project
instead of having to `cd` somewhere to be understood. The seam this abolishes: a
long-lived daemon whose working directory is its own runtime dir (not any repo)
cannot resolve *which* project to act on, and dies at the exact moment it tries
to delegate real work.

### recover-not-lose
A dropped connection to a remote agent is **not** treated as a dead agent, and a
torn-down ephemeral venue does **not** silently lose its work: in-flight agents
are diagnosed and reattached where possible, and session state is recovered and
compiled for whoever comes next.

### reclaim-idle-process
A durable agent's **live process is a reclaimable resource, not a permanent
tenant**. When the fabric's connection to a hosted agent is lost, an agent that
is **idle** — its turn complete with no work still running on its behalf — has
its process **freed** rather than left pinning memory indefinitely: promptly when
the disconnect is **clean**, and within a **bounded grace** when it is **abrupt**
(so a quick reattach still wins). An agent that is **mid-work** is never reclaimed
this way — it is kept for reattach (per *recover-not-lose*). Reclaiming an idle
process **loses nothing**: the agent stays **resumable** from its recovered state,
so the fabric owns process lifetime by *connection and activity* while the
consumer need only connect and disconnect. The complement of *recover-not-lose*:
one keeps *work* from vanishing; this keeps *idle processes* from accumulating.

### claimed-resource-not-reclaimed
A resource with a **live claimant is never reclaimed** — not pruned, reaped, or
garbage-collected as idle — even when its claimant lives in a **different
worktree, repo, or machine**. A cross-repo worktree spun up as a resource, a
provisioned container or CodeSpace, a leased SSH connection: each stays held
while its claiming worktree/session is alive, and becomes reclaimable **only when
its claimant is confirmed gone** — the outbound expression of
*reclaim-idle-process* and the delegation layer's *liveness-not-lease*. A sweep
that cannot see a resource's claimant must treat it as **potentially owned**, not
assume it abandoned: absence of a *local* owner is not proof of *no* owner. This
is what keeps an actively-owned resource from being mistaken for an orphan and
torn out from under the worktree still using it.

### summary-status-is-first-class
The fabric distinguishes an agent's **in-conversation messages** (what it is
saying now) from a **recorded summary outcome** of work done on the fabric's
behalf. Delegated and handed-off work leaves a durable, queryable result, not
only a transcript.

### disposition-is-asserted-pulse-is-derived
A worktree's **disposition** — *resolved* vs. *has actionable follow-ups* — is a
**deliberate assertion** by the agent that worked it, never inferred from git or
process state (which cannot tell *done* from *finalized-with-leftovers*). Its
**live activity pulse**, by contrast, is **passively derived** from the agent's
own activity with no cooperation required. The two never masquerade as each
other: an **absent** assertion defaults to the safe, current behavior, and the
derived pulse — being coarse and sometimes vague — **never** sets the durable
disposition. Truly finishing a worktree and asserting it *resolved* are the same
act; leaving a stopping point with work still owed is asserting *follow-ups*.

### single-current-session-per-worktree
A worktree has, at any moment, **one current session** — its head. An agent is a
**series of sessions in one worktree**, so "the agent for this worktree" resolves
to that head, and a message or a viewer addressing the worktree reaches the
**current** session even across a handoff. A session's **conclusion is asserted**,
the same way a worktree's disposition is: a session stays *current* until the
agent that owns it (or the operator) **deliberately concludes it**, never merely
because a newer one appeared or an older one went quiet. Succession is a
**durable, two-way chain** — each session knows both the one it continued and the
one that continued it — so the lineage of work in a worktree is traversable in
either direction, not reconstructed from timestamps.

Because a second controller on one checkout is a resource clash — two agents
racing the same tree — **starting a new session where the current one is not yet
concluded is gated, not silent.** The caller must resolve the incumbent one of
three ways before a fresh session is permitted: **reuse** it (continue the
existing conversation — the default, since the worktree is already the caller's
charge), **hand it off** (have the incumbent produce a continuation, conclude it,
and open a successor seeded from it), or **sunset** it (drive the incumbent to a
finished disposition and retire it). A deliberate **break-glass** override remains
for the exceptional case, but the safe default is that the fabric *refuses to
duplicate* rather than quietly spawning a rival. This is the session-level
expression of *discover-before-duplicate* and *derive-dont-duplicate*: the
current-session pointer and the succession chain are **owned by the ground
layer** (which owns durable execution lineage), and higher layers **enforce and
derive from** them rather than keeping a rival notion of "current." The selected
session host owns the process lifetime of each leg, not the meaning of the
lineage.

### handoff-orchestrated-across-ledger-and-host
The handoff layer owns the continuation and transition policy; the worktree
ground layer owns durable head, lineage, and responsibility; the selected
session-host provider owns launch, prompt delivery, and retirement mechanics.
A cutover is represented durably before a provider is notified. A provider's
launch receipt remains provisional until the successor proves its session and
opening context; only then does durable authority move and retirement become
authorized. With no compatible provider, the same handoff remains recoverable
for manual pickup rather than becoming a silent no-op.

### context-pressure-drives-handoff
Context saturation is a legitimate **driver** of a handoff, but a driver held
under **explicit policy**, never a reflex. Automatic in-place roll on pressure is
**opt-in and off by default**: a hosted session hands itself off as its context
window nears exhaustion **only** when its owner has opted in, and preferentially
when **no interactive human is actively watching** that session (an operator at a
live console is the one who should choose reuse/handoff/sunset themselves). A
prompt submitted **into an already-saturated session** is continued by **handing
off first and delivering that prompt to the successor**, rather than spending the
last of the window on a degraded turn — so a minimal consumer that can only
*send the next message* still advances. Like every other handoff this is
**orchestrated above the primitive** (*handoff-orchestrated-above-primitives*):
the worktree ground layer never auto-rolls a session on its own, the policy and
pressure-reading live in the layers above, the selected host performs the
execution transition, and the succession chain and current-session pointer
remain **owned by the ground layer**
(*single-current-session-per-worktree*). The signal is **fail-safe**: absent an
opt-in, pressure changes nothing and the session behaves exactly as before.

## Non-Goals / Boundaries

- **Not the per-host service model.** *How* each layer's runtime is deployed,
  exposed, and reached as a machine-local service — à-la-carte installability,
  collision-free discoverable endpoints, platform-native supervision — is the
  **[plugin-services](../plugin-services/README.md)** vision's territory. The
  fabric builds *on* that model; it does not restate or duplicate it.
- **Not an account-per-agent model.** The fabric deliberately coordinates many
  agents under a shared identity via claimed work, not by minting an
  account per agent.
- **Not a replacement for the human's editor or terminal.** The fabric
  coordinates *agents*; it does not own the human's own interactive editing
  surface.
- **Not one universal Copilot process manager.** Execution is supplied by
  capability-honest session-host providers. The fabric does not force every
  Copilot product or third-party rig through one terminal, multiplexer, or
  protocol implementation.
- **No second store of another layer's state.** A higher layer must not persist
  its own copy of state a lower layer owns — it derives and coordinates. (Stated
  as a boundary precisely so realizations don't smear one capability's state
  across layers.)
- **Not a specification.** This vision fixes the *layering, roles, and
  guarantees* of the fabric, not the wiring — it does not pin transports,
  storage engines, on-disk formats, endpoints, or command grammars. Binding
  detail of that kind belongs to the reality docs or a future `specifications`
  layer.

## See Also

- Parent vision: [visions index](../README.md)
- Sibling vision: [plugin-services](../plugin-services/README.md) — the per-host
  service model the fabric's layers deploy as (it defers cross-host agent reach
  to this fabric).
- Cross-cutting vision: [native-convergence](../native-convergence/README.md) —
  how the fabric's constructs (worktree isolation, session identity, roots,
  projects, live-session steering) converge onto Copilot CLI's *own* native
  constructs: delegate the primitive, align vocabulary + layout, keep the durable
  value the CLI lacks, without regressing a capability or hard-depending on an
  unreleased construct.
- Cross-cutting vision: [session-hosting](../session-hosting/README.md) —
  provider-neutral ownership of Copilot execution and live cutover mechanics.
- Child visions: [agent-ssh](../plugins/agent-ssh/README.md) — the connectivity /
  transport layer the fabric's cross-machine reach rides on;
  [agent-dispatch](../plugins/agent-dispatch/README.md) — the delegation layer's
  task queue (production modes, fire-and-forget vs. driven, liveness-reconciled
  recovery); [picker](../picker/README.md) — the fabric's **front-door
  presentation surface**, which renders this vision's legibility model (disposition
  vs. pulse) and lets each layer contribute a pivot;
  [venue-parity](../venue-parity/README.md) — the cross-cutting principle that the
  fabric's venue providers (agent-codespaces / agent-containers) are thin,
  symmetric SSH transports over one agent-bridge dispatch core, so a dispatched
  agent is the same in a CodeSpace or a local container. Further per-plugin leaves
  live under `visions/plugins/<name>/` as authored (e.g. a future
  `visions/plugins/agent-bridge/`).
- Reality docs: [`docs/architecture.md`](../../docs/architecture.md) ·
  [`docs/harness-runbook.md`](../../docs/harness-runbook.md) · each plugin's
  `docs/`.

## Provenance

- **2026-07-14** — Initial authoring. Intent mined from the operator's
  description of the layered agent-* stack (ground isolation → coordination →
  delegation → venue providers → memory → trust) and the composition property
  that each layer stands alone and augments the ones below. The
  *derive-don't-duplicate / single-owning-layer* rule crystallized from
  reconciling where overlapping cross-layer responsibilities (dedup, liveness,
  identity) should live — generalized here as the standing boundary that keeps
  the layers separate.
- **2026-07-15** — Extended §Features/`legible-live-state` and added
  §Behaviors/`disposition-is-asserted-pulse-is-derived`: the two-register model
  (agent-*asserted* **disposition** vs. passively-*derived* **live activity
  pulse**), both owned by the ground layer. Mined from operator friction — a
  worktree picker full of `FINAL` entries that hid which ones still offered
  follow-ups, and a conversation state that couldn't separate consequential from
  throwaway. Placed on the ground layer by the *derive-don't-duplicate /
  single-owning-layer* rule (the delegation layer coordinates over, not copies,
  it).
- **2026-07-21** — Added §Features/`address-any-project` and
  §Behaviors/`project-addressed-not-cwd-bound`: a project is a first-class,
  **CWD-independent** address across *every* layer, and the per-project `<repo>`
  binstub is a uniform `<repo> <layer> …` dispatcher over the whole agent-*
  stack. Mined from a concrete seam — the agent-dispatch **embody supervisor**,
  running as a service whose working directory is its own runtime dir, could not
  resolve *which* project to embody a queued task for (`Could not resolve a
  project for 'embody'`) because embody discovered its project only from CWD.
  The fix (name the project via `--project`) generalized into the standing intent
  that no fabric layer should be reachable *only* by standing inside a repo.
- **2026-07-26** — Added §Features/`externally-observable`: beyond the picker's
  internal legibility, each layer surfaces its lifecycle through a
  backend-agnostic, no-op-by-default **telemetry seam** that a downstream
  observability consumer attaches **by configuration, not code** (an environment
  variable or a dropped config file), carrying lifecycle state and structure only.
  Mined from extending the delegation/coordination layers' pluggable emission hook
  with a config-file attachment path so a host wires telemetry without any
  environment variable; the attachment mechanism and on-disk config shape stay
  spec-level. Paired with the leaf `visions/plugins/agent-dispatch`
  *observable-lifecycle* feature.
- **2026-07-31** — Added §Features/`resource-claims` and §Behaviors/
  `claims-owned-by-direction` + `claimed-resource-not-reclaimed`: a worktree
  carries a **claim ledger** of the resources and work it is responsible for,
  split by **direction** — *outbound* (resources it produces/owns: pull requests,
  cross-repo worktrees, CodeSpaces, containers, SSH connections, working
  directories) owned by the ground layer, *inbound* (external work it takes on:
  bugs, review PRs, efforts/visions) owned by the delegation layer — each
  resolvable both ways, the union derived not duplicated, and a resource never
  reclaimed while its claimant lives (even cross-repo/cross-machine). Mined from
  a concrete seam: an agent editing a **cross-repo** worktree as a resource left
  no ownership trace, so a machine-local, point-in-time prune sweep could not
  tell an actively-owned resource worktree from an abandoned one. Generalizes the
  existing same-repo bridge caller-link into a directional, cross-repo claim
  model, and states the safety rule (absence of a *local* owner is not proof of
  no owner) that a reaper must honor.
- **2026-08-02** — Added §Features/`handoff-under-context-pressure` and
  §Behaviors/`context-pressure-drives-handoff`: a session nearing the limit of
  its **own context window** is a first-class *reason to hand off*, so
  long-running work **survives the context ceiling** by rolling in place to a
  seeded successor rather than degrading — a *proactive* handoff the fabric can
  initiate, complementing the caller-initiated handoff of
  *single-current-session-per-worktree*. Held under **explicit opt-in policy**
  (off by default; preferring sessions no interactive human is watching), with a
  prompt submitted into an already-saturated session continued by **handing off
  first, then delivering the prompt to the successor** — the only path forward
  for a minimal consumer (a phone) that can *only* send the next message and has
  no manual session-creation affordance. Orchestrated above the primitive like
  every other handoff; the signal is fail-safe (absent an opt-in, pressure
  changes nothing). Mined from the concrete seam that the coordination layer
  already *tracks* context usage and even warns "consider handoff" but could
  never *act* on it, so a bridge-hosted headless session at the ceiling simply
  dead-ended — the exact "silent no-op" that
  *handoff-orchestrated-above-primitives* names as the failure to avoid.
- **2026-08-06** — Added §Features/`legible-contribution-contract` and
  §Behaviors/`guidance-emitted-at-point-of-action`: the fabric makes each repo's
  PR/landing rules — PR-required?, who approves (and may the submitter approve
  their own?), who reviews and how long it takes, who merges, what a conflict
  does — **legible to the acting agent at the moment of action**, emitted inline
  on **both** a command's success and its refusal, and pointing **only** at
  sanctioned fabric verbs (never a raw provider call, force/override, or
  review-skipping merge). Mined from enabling PR flow on a *second* repo with a
  different contract than the first: agents could no longer assume one repo's
  shape, and the ground layer already *knew* each repo's flow profile
  (`classify_pr_flow`) but surfaced it from only one verb — so an agent driving
  a PR "forgot" the rules mid-flow and mis-sequenced approve/merge/rebase, or
  fell back to a bypass (`gh pr merge --admin`) the tooling should have steered
  it away from. Realized as a per-repo policy matrix + a state-aware reminder
  the pr-* / push-changes verbs emit; tracked in the copilot-extensions PR-flow
  legibility effort.
- **2026-08-07** — Added §Concepts/`resource-leasing` and `resource-accountability`.
  `resource-leasing` names the atomic, cross-machine, same-harness exclusion
  primitive — **ref-shenanigans-only** compare-and-swap in the harness's own repo
  (fencing token + tombstone), two-tier local+ref-CAS, degrade-safe, with the
  cross-harness seam fenced **on the resource itself** — shipped by the
  git-ref-resource-leases effort (folding David Michon's CAS engine into
  agent-worktrees core). `resource-accountability` then closes the loop: a worktree
  **answers for everything it allocated before it may finalize** — release is
  **gated on settlement**, *at-rest (resource safe)* is decoupled from *released
  (claim torn down)*, and the "is it truly final?" proof is kept **cheap** by
  **settling incrementally + asserting locally** (a child returns its obligation
  upward the moment it settles; finalize reads a local balance and never traverses
  the tree — reference counting on a cross-resource scale), with a liveness/reclaim
  sweep beneath so a dead holder never wedges a parent and an abandon **re-homes**
  the obligation rather than dropping it. Mined from the follow-on question the
  lease store raised — now that a worktree's whole outbound footprint (CodeSpaces,
  containers, cross-repo worktrees, bridge sessions) is representable as leases, the
  ground layer's `finalize` should refuse to let a worktree vanish while it still
  owes unsettled work on those resources. Tracked in the
  `resource-obligation-settlement` effort.
- **2026-08-31** — Extended §Features/`resource-claims` and
  `resource-leasing` with the durable coordination-identity prerequisite. A
  self-hosted harness coordinates through its own repository; a stateless
  harness that requires external state must bind and resolve that state
  identity before new claims or leases are created, with no fallback to the
  shared launch repository. Existing fenced ownership remains releasable during
  a later binding outage so fail-closed acquisition cannot wedge teardown.
- **2026-09-04** — Split durable worktree-lifetime agency state from Copilot
  execution hosting. agent-worktrees remains the host-neutral owner of worktree
  identity, responsibility, claims, disposition, head, and succession, while
  pluggable session hosts own launch, interaction, observation, reconnection,
  prompt delivery, and retirement for CLI/mux, ACP, SDK, App, and third-party
  rigs. Handoff now spans those authorities through durable requests and
  verified takeover rather than treating one ground-layer launcher as universal.
