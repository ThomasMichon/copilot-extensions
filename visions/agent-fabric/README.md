# Agent Fabric — Vision

- **Subject:** The **agent fabric** — the layered system that turns isolated
  Copilot CLI sessions into one coordinated, observable, multi-agent fabric
  spanning worktrees, machines, CodeSpaces, and containers.
- **Scope:** branch (links per-plugin child visions as they are authored)
- **Status:** Active
- **Last revised:** 2026-07-21
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

### agent-worktrees — the isolation & session ground layer
Owns **agent-per-worktree isolation**, the Copilot **session process
lifecycle**, and core **worktree + git** state. It is the foundation every other
layer builds on. On its own it yields **passive, coarse legibility**: state
discoverable through declarative hooks and on-demand reads of raw session state —
an Active / Recent / Completed view of agents, process management, and basic
remote-shell interop — with **no always-on service required**. Owning the
worktree, it also owns each worktree's **disposition** — whether its work is
genuinely *resolved and prune-able* or has *actionable follow-ups remaining* —
which the agent working there **asserts**, because git and process reality alone
cannot tell a done worktree from a finalized one that still owes follow-ups.
Alongside that, the ground layer surfaces a **live, passively-derived sense of
what each agent is currently doing**, needing no cooperation from the agent — the
same derivation instinct that already separates conversation from idle.

### agent-bridge — the coordination layer
Adds **remote agent creation, inspection, and communication** over discoverable
channels. It augments the ground layer with **granular, live state** and gives an
agent the means to **call other agents**: create agents and worktrees, peer into
another agent's status, send a message into an agent (whether one it controls or
a peer), and get a sense of what others are doing — including answering *"is an
agent already up and running to cover this worktree or repo?"*. The live state it
surfaces is rich enough to bring **granular, live status into the worktree
picker**.

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
**delegate** tasks to spun-off agents.

### agent-codespaces — a venue provider
**Provisions CodeSpaces** for related repos, injects the right plugins and
environment to **run agents headlessly** there, and then presents those CodeSpace
agents to the fabric as a **provider for the coordination layer** — so a remote
CodeSpace agent is created, inspected, and reached by the *same* contract as a
local one.

### agent-containers — a venue provider
Does the same for **local containers**: provision and set up a container-hosted
agent and present it to the fabric as a coordination-layer provider, so a
containerized agent is a first-class fabric participant.

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
The **launch** underneath (spin a session in a worktree) is a ground-layer
**primitive**; the **orchestration** of a handoff — composing the continuation,
minting the claimable delegation record, cutting a successor over, verifying it,
and retiring the predecessor — belongs to the layers **above** the primitive,
never baked into the ground layer.

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

### survivable-work
An agent's session output is **recoverable and digestible** after the fact —
including from short-lived remote venues — so a successor agent can catch up on
what a prior one did without the original conversation.

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

### passive-legibility-floor
The ground layer is legible **without any running service** — its state is
discoverable through declarative hooks and on-demand reads — so the fabric is
never wholly blind, even with no daemon up.

### uniform-venue-reach
Adding, moving, or losing a venue (a CodeSpace, a container, another machine)
does not change how its agents are addressed: a venue provider makes its agents
reachable by the fabric's one coordination contract.

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

### handoff-orchestrated-above-primitives
Session **launch** is a ground-layer **primitive** — "spin a Copilot session in
worktree `<id>`." The **handoff** built on it — compose the continuation, mint a
**claimable delegation record** (so a coordinator or the next session picks it
up), cut a successor over, **verify it came up**, and retire the predecessor — is
**orchestrated by the layers above** (the handoff extension driving the delegation
layer), never absorbed into the ground layer. The ground layer offers the
**mechanism**; a higher layer owns the **policy** — and a mux-less environment
degrades to the same claimable record, not to a silent no-op.

## Non-Goals / Boundaries

- **Not the per-host service model.** *How* each layer's runtime is deployed,
  exposed, and reached as a machine-local service — à-la-carte installability,
  collision-free discoverable endpoints, platform-native supervision — is the
  **[plugin-services](../plugin-services/README.md)** vision's territory. The
  fabric builds *on* that model; it does not restate or duplicate it.
- **Not an account-per-agent model.** The fabric deliberately coordinates many
  agents under a shared identity via leased / claimed work, not by minting an
  account per agent.
- **Not a replacement for the human's editor or terminal.** The fabric
  coordinates *agents*; it does not own the human's own interactive editing
  surface.
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
- Child visions: [agent-ssh](../plugins/agent-ssh/README.md) — the connectivity /
  transport layer the fabric's cross-machine reach rides on. Further per-plugin
  leaves live under `visions/plugins/<name>/` as authored (e.g. a future
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
