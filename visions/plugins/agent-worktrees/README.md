# agent-worktrees — Session Tracking & Live State — Vision

- **Subject:** **agent-worktrees** as the fabric's **ground-layer authority for
  worktree + Copilot-session state and live tracking** — the layer that *owns the
  truth* about what worktrees exist, what each agent is doing, and whether a
  session is live, and owns the transports that produce that truth.
- **Scope:** leaf (concrete component; child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-08-07
- **Reality docs:** the agent-worktrees plugin `docs/` (worktree lifecycle +
  tracking) · the Worktree-Picker performance/IO effort (dotfiles#948) as the
  most recent reality on the state store
- **Supersedes / superseded by:** none

## Purpose & Intent

agent-worktrees is the **foundation the rest of the agent fabric builds on**, and
tracking the Copilot session **the normal way is its job** — not a favour done by
a higher layer. It owns the raw materials of truth: **local file state, SSH reach,
the multiplexer, PowerShell/process visibility, and lifecycle hooks.** Because it
owns those transports, it — and only it — is the authority on *what worktrees
exist*, *what work each is doing*, and *whether a session is live*.

The north star is that this state behaves like a **live database with a single
owner**, not a loose pile of files each consumer races to read and rewrite. It is
**durable and consistent under concurrency**, cheap to read for a first paint,
and correct **with no higher layer and no always-on service present**. Everything
above — the picker, the coordination layer, remote fleet views — *reads from and
derives over* this one owner rather than keeping a second copy of the truth.

The friction this abolishes is **every consumer re-deriving expensive live state
from scratch**, and **multiple processes fighting over the same state files**. The
ground layer makes the answer to *"what exists and what is it doing?"* a fast,
consistent, single-sourced read — and makes *how fresh / how live* a property the
owner maintains, not one each caller reinvents.

## Concepts & Components

### The state of record — a live, single-owner store
The worktree + session state is a **single source of truth owned by
agent-worktrees**, treated as a **live database**: writes are **atomic and
consistent** (no torn or half-written state, no lost update when two things touch
the same record at once), reads are cheap, and the store is the one place the
truth lives. Consumers never keep a parallel copy; they read this owner and
**derive** over it.

### The derivation / liveness engine — polling as the backbone
Liveness and "what is this agent doing" are produced by agent-worktrees from its
**own transports** — session files/hooks, the multiplexer, the process table,
lock state, and SSH for remote worktrees. This **polling-and-derive path is the
always-on backbone**: it needs no cooperation from the agent being observed and no
higher layer. It is the reason the fabric is legible even when nothing else is
running.

### The warm-cache accelerator — optional, on-demand, refcounted, losable
Derivation can be expensive; the ground layer **may** keep it warm in an
**on-demand resident tracker** that lives **only while at least one consumer
references it** (a picker, an SSH probe cycle, a live session) and falls away when
idle. This accelerator is **not the source of truth** — it caches and streams
*derived* state over the store of record. It is deliberately **losable**: if it
dies, the fabric loses *warmth, not data*, and every consumer still reads and
writes the store directly with it absent.

### The event sink — one owner, many producers
Tracking updates flow into agent-worktrees through a **single ingestion seam it
owns** (hook-shaped, since it already owns lifecycle hooks). The **always-available
producer is its own polling/derivation**. Higher layers are **additional, optional
producers into the same sink** — never the backbone, never a second owner.

### agent-bridge — an optional ACP eventing supplement
When present, the coordination layer contributes **tool-level and message-level
events** (an agent made a tool call; a message turn occurred) into the ground
layer's event sink. These events are the natural fit for agent-bridge because they
**align with ACP, which is agent-bridge's domain** — and they let the ground layer
**sharpen liveness and activity with push instead of poll**. This is a *supplement*
that raises fidelity and cuts polling when the bridge happens to be in the session;
it is **never required** for tracking to be correct.

## Features

### single-sourced live state
There is exactly **one** owner of worktree + session truth. Any view — local
picker, remote fleet listing, a coordinating agent — is a read of, or a derivation
over, that single owner; nothing maintains a competing copy.

### concurrency-safe store
Concurrent touches of the same state (a foreground action while background
derivation writes) are **serialized and atomic** at the store, so no consumer
observes a torn record and no write is silently lost. Keeping the store honest
under concurrency is the **store's** job, not something each caller bolts on.

### instant first read, background truth
A consumer can **paint immediately** from cheap cached/last-known state, while the
authoritative (expensive) derivation completes **behind** that first read and
updates the view. Freshness is layered, never a blocking prerequisite to being
useful.

### polling backbone that stands alone
The self-derived liveness/activity path is **complete on its own** — full,
correct tracking with **no agent-bridge and no resident service**. Higher layers
and the accelerator only make it faster or higher-fidelity.

### optional warm-cache tracker
An **opt-in, on-demand** resident tracker may keep derivation hot and stream
updates for the duration that consumers need it, then release. Its presence is an
optimization; its absence changes nothing about correctness.

### event ingestion with an optional ACP producer
Tracking accepts events through one owned sink. agent-bridge's **ACP tool/message
events** are an **optional producer** into it, enriching liveness/activity when the
bridge is in the loop.

## Behaviors

### graceful degradation
Tracking is **fully correct with zero higher layers** and **zero resident
service**. Removing agent-bridge, or the accelerator, degrades *speed/fidelity*,
never *correctness*. No part of the ground layer demands that a higher layer be
present.

### derive, don't duplicate
Each piece of tracking truth has **one owner** (agent-worktrees). Higher layers
**coordinate over and derive from** it; they do not keep a second copy that can
drift. agent-bridge's contribution is *events into the owner*, not a rival store.

### durable of record, losable when warm
The **store of record survives** crashes and restarts and can be trusted as the
truth. The **warm-cache accelerator is expendable** — losing it loses only
performance, and the store can be re-derived from the underlying transports.

### IO never blocks interaction or rendering
Reading or updating tracking state **must not stall** user interaction or the
render path. Expensive or contended writes happen **off** the interaction/render
path; a keystroke is never held hostage to a file write.

### push sharpens, poll guarantees
When ACP events are available they **reduce polling and sharpen** liveness/turn
signals; when they are absent, **polling still guarantees** a correct answer. The
two compose: events are an accelerant over a self-sufficient poll.

## Non-Goals / Boundaries

- **Not dependent on agent-bridge for tracking.** agent-bridge is an *optional*
  eventing supplement; the ground layer does **not** require it, and agent-bridge
  does **not** own session liveness or the state of record.
- **The resident tracker is not the source of truth.** It is a losable accelerator
  over the durable store — never the authority, never a second owner.
- **Not a cross-agent communication layer.** Creating, addressing, messaging, and
  handing off *between* agents is agent-bridge's / ACP's domain, not the ground
  layer's. The ground layer *produces* the truth those higher layers coordinate
  over.
- **Not the presentation surface.** How this state is *displayed and acted on*
  interactively is the Worktree Picker's subject (its own vision); this vision is
  about *owning and serving* the state, not rendering it.

## See Also

- Parent vision: [agent-fabric](../../agent-fabric/README.md) — the layered
  coordination fabric (this is its **ground layer**).
- Sibling visions:
  [picker](../../picker/README.md) — the interactive front door that reads this
  state; [plugins/agent-bridge](../agent-bridge/README.md) — the coordination
  layer that supplements tracking with ACP tool/message eventing.
- Reality docs: the agent-worktrees plugin `docs/`; the Worktree-Picker
  performance/IO-contention effort (dotfiles#948) as current reality on the store.

## Provenance

- **2026-08-07** — Authored from a design conversation on *when* the file-per-
  worktree state should become an owned live database and *whether* a resident
  tracking service is warranted. Established the ownership contract: tracking is
  agent-worktrees' job via its own transports (files/SSH/mux/PowerShell/hooks)
  with polling as the always-on backbone; the state is a single-owner live DB with
  an optional, losable, refcounted warm-cache accelerator over a durable store of
  record; and agent-bridge supplements — never backbones — tracking with
  ACP-aligned tool/message eventing into one owned event sink. Mined from the
  agent-fabric branch vision's *graceful composition* + *derive, don't duplicate*
  properties, sharpened to the agent-bridge/agent-worktrees boundary.
