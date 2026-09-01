# agent-worktrees — Session Tracking & Live State — Vision

- **Subject:** **agent-worktrees** as the fabric's **ground-layer authority for
  worktree + Copilot-session state and live tracking** — the layer that *owns the
  truth* about what worktrees exist, what each agent is doing, and whether a
  session is live, and owns the transports that produce that truth.
- **Scope:** leaf (concrete component; child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-08-26
- **Reality docs:** the agent-worktrees plugin `docs/` (worktree lifecycle +
  tracking) · the Worktree-Picker performance/IO effort (dotfiles#948) as the
  most recent reality on the state store
- **Supersedes / superseded by:** none

## Purpose & Intent

agent-worktrees is the **foundation the rest of the agent fabric builds on**, and
tracking the Copilot session **the normal way is its job** — not a favour done by
a higher layer. It owns the raw materials of truth: **local file state, worktree and
source-control mechanics, process/lock visibility, session bindings, and
lifecycle hooks.** It can consume optional observations from SSH, a multiplexer,
or higher eventing layers without owning their human-facing orchestration.
Because it owns and reduces the ground-layer records, it — and only it — is the
authority on *what worktrees exist*, *what work each is doing*, and *whether a
session is live*.

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

### The worktree remembers — binding and a bounded history in the record
The worktree is the durable unit an agent belongs to, so **the record it already
keeps is where a session declares itself and where the worktree's recent intent
lives**. Two things follow. First, a session **binds to its worktree by an explicit
self-identifying assertion** — the agent *declares* "this is my worktree" — rather
than the store inferring ownership from whatever directory a background signal
happened to observe; the binding is what brings the worktree's live tracking (the
reducer, the status surface, the picker view) to life for that session. Second, the
record carries a **bounded, append-only, session-tagged history** of the *terse
values an agent already reports about its own work* — its focus/disposition, and the
reference to a handoff it produced. This is **memory, not a log**: a small, capped
series of structured entries (each stamped with the session that wrote it), never
prose and never an event stream. The worktree's *current* disposition is simply the
**tail** of that history, and because the handoff reference lives here too, the
worktree can say what it was doing — and hand that to a successor — **from its own
record alone**.


### The aggregate is derived — single-writer slots, one reducer
The worktree's **aggregate** status and current-session (head) are **not a shared
cell** any contributor writes; they are a **pure reduction over independent,
single-writer slots**. Each signal source owns exactly **one** slot and writes only
that — git working-tree classification, per-PR lifecycle, claim/lease disposition,
multiplexer liveness, bound-process liveness (the Copilot lock), and per-session
lifecycle + succession — each carrying its **own** freshness stamp. A single
**reducer** owned by agent-worktrees folds the slots into `status` and the resolved
head, with explicit precedence and per-slot staleness. Because no contributor writes
the aggregate and the derived view is recomputed rather than being a slot two
writers race on, parallel contributors cannot clobber one another's determination or
persist a stale guess into the shared verdict. This is *derive, don't duplicate*
taken all the way down: **one writer per signal, the aggregate always derived.**

### The derivation / liveness engine — the extension-free backbone
Liveness and "what is this agent doing" are produced by agent-worktrees from its
**own transports** — on-disk session state, the multiplexer and its terminal
output, the process table, lock state, and SSH for remote worktrees. This
**derive-on-demand path is the always-on backbone**: it needs no cooperation from
the agent being observed, **no higher layer, and no in-session extension loaded**.
It is the reason the fabric stays legible even when nothing else is running — and
the reason a fragile, slow-to-initialize, or absent in-session extension can never
drag tracking below *correct*. Crucially, it reads the **least data possible**:
random-access straight to the specific resource, and **cursors / watermarks** for
any *growing* dataset (the event log, the session-state tree) so reads are
**incremental** — **never a continuous full sweep of an unbounded dataset**.

### The warm-cache accelerator — optional, on-demand, refcounted, losable
Derivation can be expensive; the ground layer **may** keep it warm in an
**on-demand resident tracker** that lives **only while at least one consumer
references it** (a picker, an SSH probe cycle, a live session) and falls away when
idle. This accelerator is **not the source of truth** — it caches and streams
*derived* state over the store of record. It is deliberately **losable**: if it
dies, the fabric loses *warmth, not data*, and every consumer still reads and
writes the store directly with it absent.

### The native session-event producer — an optional, non-load-bearing enrichment
The CLI emits a rich **native session-event stream** (session/turn/tool/idle
lifecycle) that an **in-session extension agent-worktrees owns** can observe
passively — writing a small sidecar the store reads, with **zero agent
cooperation and no injected turns**. This is the crispest, most passive source of
the graded **rest / idle** signal — an explicit *session-idle* event when the
agent and everything it spawned are quiescent; a distinct *awaiting-operator*
state when it is parked on a prompt or permission — and of a live "current intent"
line. It is an **enrichment, never the backbone**: because it rides an in-session
extension, it is **non-load-bearing** — its absence, or a failed/slow
initialization, degrades *fidelity and crispness*, **never** tracking correctness,
and **must never jeopardize a mainline flow**. When present it *sharpens*; when
missing the backbone still answers, coarsely, from the extension-free transports.

### The event sink — one owner, an extension-free backbone, optional producers
Tracking updates flow into agent-worktrees through a **single ingestion seam it
owns**. The **always-available producer is its own extension-free
polling/derivation** (the backbone above). Everything richer is an **additional,
optional, non-load-bearing producer** into that same sink — never a second owner,
never a prerequisite: agent-worktrees' own native-event extension, and (higher
still) agent-bridge's ACP eventing. The sink is authoritative on its own; the
optional producers only make it fresher and sharper.

### agent-bridge — an optional ACP eventing supplement
When present, the coordination layer contributes **tool-level and message-level
events** (an agent made a tool call; a message turn occurred) into the ground
layer's event sink. These events are the natural fit for agent-bridge because they
**align with ACP, which is agent-bridge's domain** — and they let the ground layer
**sharpen liveness and activity with push instead of poll**. This is a *supplement*
that raises fidelity and cuts polling when the bridge happens to be in the session;
it is **never required** for tracking to be correct.

### The Worktrees presentation contribution — owned here, rendered elsewhere
The ground layer publishes a **declarative Worktrees presentation contribution**
over its machine-readable CLI: the fleet rows, semantic fields, actions, and
ordinary landing role a human control-plane can render. This is the presentation
counterpart of single-sourced state: agent-worktrees defines and serves worktree
semantics, while the optional Worktree Manager supplies only generic rendering
and interaction primitives. The contribution contains no UI stack and creates no
dependency on the Manager; without that control-plane, the CLI remains the whole
ground-layer product.

## Features

### single-sourced live state
There is exactly **one** owner of worktree + session truth. Any view — local
picker, remote fleet listing, a coordinating agent — is a read of, or a derivation
over, that single owner; nothing maintains a competing copy.

### explicit private lease store
Ref-backed resource leases use a deliberately configured private state store.
The selected owner is inspectable and stable across callers; absent that owner,
remote lease operations fail with remediation instead of publishing hidden refs
to the current source repository.

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

### rest and idle are observable
The ground layer **reports when an agent reaches rest** — and distinguishes
*done-rest* (a turn/session went quiescent with nothing in flight) from
*awaiting-operator* (parked on a prompt or permission, i.e. "this needs me"). It
sources the **crispest available** rest signal: an explicit idle event via the
native-event producer when that is present, and a **coarser at-rest inference from
the extension-free transports** otherwise. Either way, "is it at rest, and why" is
a first-class part of the truth — never dependent on any one signal source.

### event ingestion with optional producers
Tracking accepts events through one owned sink whose **always-on producer is the
extension-free backbone**. Richer producers are **optional and non-load-bearing**:
agent-worktrees' own **native-event extension** (source of the crisp rest/idle and
intent signals) and agent-bridge's **ACP tool/message events**. Each enriches when
present; none is required, and a producer failing to initialize never subtracts
from what the backbone already guarantees.

### provider-owned Worktrees surface
The Worktrees pivot and its actions are described by the ground layer and backed
by the same machine-readable operations agents use. An optional presentation
host discovers that contribution rather than carrying a second, special-purpose
worktree client. Disabling the contribution removes the interactive Worktrees
surface without changing any headless worktree, tracking, claim, lease, sync, or
source-control capability.

### explicit session binding
A session becomes **bound to its worktree by declaring so**, and a session that is
active in a worktree yet unbound is **detected and invited to declare** — never
silently attached from an inferred directory. The declaration is self-identifying
(the session and its terminal are knowable to the agent's own tool call), so the
binding reflects *what the agent asserted*, not what a background observer guessed.
This closes the gap where a session that did not begin life in its worktree (a
resume that starts elsewhere, a spawned successor, a headless launch) would
otherwise never register and leave the worktree looking unowned.

### the record remembers — a bounded, session-tagged history
The record retains a **small, capped, append-only history** of the terse values an
agent reports about its own work — focus/disposition and handoff references — each
entry tagged with the session that wrote it. It is **memory, not logging**: bounded
by construction, structured not prose, and the worktree's *current* disposition is
the **tail** of it rather than a separate cell. A worktree therefore always carries
an agent-digestible account of what it has recently been doing, across the sessions
that worked it.

## Behaviors

### source remotes are not coordination stores
The repository being worked on is never silently selected as the remote backend
for ref-backed claims or leases. A bound private state repository or explicit
store configuration is required; if neither exists, the operation fails closed.
Local worktree and obligation tracking remains available, and issue-tracker
claim comments remain a separate global coordination mechanism.

### graceful degradation
Tracking is **fully correct with zero higher layers**, **zero resident service**,
and **zero in-session extension**. Removing agent-bridge, the accelerator, or the
native-event extension degrades *speed/fidelity/crispness*, never *correctness*. No
part of the ground layer demands that any of them be present.

### no in-session extension is load-bearing
Tracking correctness **must not depend on any in-session extension initializing**.
The richest signals (crisp rest/idle, live intent) ride an extension, and extension
startup is inherently fallible — so the ground layer treats every extension-sourced
signal as **opportunistic enrichment over the extension-free backbone**. A missing,
slow, or failed extension may only make tracking *less crisp*; it must **never**
break tracking and must **never** jeopardize a mainline flow.

### derive, don't duplicate
Each piece of tracking truth has **one owner** (agent-worktrees). Higher layers
**coordinate over and derive from** it; they do not keep a second copy that can
drift. agent-bridge's contribution is *events into the owner*, not a rival store.

### aggregate status is derived, never written
No contributor ever writes the worktree's overall status or head directly; each
writes **only its own slot**, and the aggregate is **computed** from the accumulated
slots on read (or by the single owner). A contributor that is wrong, stale, or
racing another can affect **its** slot only — never the shared verdict — so a
mis-write is bounded to one signal and corrected by the next reduction, never a lost
update on the aggregate. Where completeness depends on more than the record — e.g. a
handoff whose conclusion must observe whether its successor ever came alive — the
reducer is a **liveness-aware reconciliation**, not merely a static fold; but it
stays a derivation, never a shared cell each caller overwrites with its own verdict.

### durable of record, losable when warm
The **store of record survives** crashes and restarts and can be trusted as the
truth. The **warm-cache accelerator is expendable** — losing it loses only
performance, and the store can be re-derived from the underlying transports.

### bounded, incremental derivation — never an unbounded sweep
Deriving state **must not continuously sweep unbounded datasets** (the whole
session-state tree, a full event log). The backbone reads the least data possible:
**random-access straight to the specific resource**, and **cursor/watermark**
reads that consume only the *delta* of a growing dataset since the last read. Cost
scales with *what changed*, not with total history — so tracking stays cheap as
the corpus grows, and a busy machine is never taxed by repeated full scans.

### IO never blocks interaction or rendering
Reading or updating tracking state **must not stall** user interaction or the
render path. Expensive or contended writes happen **off** the interaction/render
path; a keystroke is never held hostage to a file write.

### push sharpens, poll guarantees
When event producers are in the loop — the native-event extension, or agent-bridge's
ACP events — they **reduce polling and sharpen** liveness/rest/turn signals; when
they are absent or fail to start, **polling still guarantees** a correct answer. The
two compose: events are an accelerant over a self-sufficient poll, never a
dependency of it.

### binding is an explicit assertion, never a sniff
Ownership of a worktree by a session is established by an **explicit declaration**,
not inferred from an incidental directory a hook or background pass happened to see.
The store may **detect** an unbound-but-active session and **prompt** for the
declaration, but the bind itself is the agent's own asserted act — auditable and
intentional — so a stray observation can never mis-attach a session to the wrong
worktree.

### recovery is record-first
What a worktree was doing — its recent focus and the reference to any handoff it
produced — is recoverable **from the worktree's own record**, without a second
service being reachable and without a live-handoff transfer having succeeded. A
successor (or a fresh session, or an operator) reconstructs the worktree's intent
from the store alone; richer transports make a handoff *smoother* when present, but
none is a **precondition** for the worktree remembering itself. The durable record
is the floor under recovery, not an optimization layered on a fragile transfer.

### presentation is declarative and process-boundary only
agent-worktrees exports presentation semantics but never imports or hosts the
Picker. Its contribution is consumed through machine-readable process
boundaries, uses the same composition model as sibling fabric layers, and cannot
make the Manager or an interactive UI a prerequisite for ground-layer behavior.
The contribution remains bound to the exact installed agent-worktrees instance
that published it, so another marketplace's same-named runtime cannot
accidentally serve its state or actions.

## Non-Goals / Boundaries

- **Not dependent on agent-bridge for tracking.** agent-bridge is an *optional*
  eventing supplement; the ground layer does **not** require it, and agent-bridge
  does **not** own session liveness or the state of record.
- **The resident tracker is not the source of truth.** It is a losable accelerator
  over the durable store — never the authority, never a second owner.
- **No in-session extension is load-bearing for tracking.** Correctness does not
  depend on any extension initializing; extension-sourced signals are enrichment
  over the extension-free backbone, and their failure must not break tracking or a
  mainline flow.
- **Not a cross-agent communication layer.** Creating, addressing, messaging, and
  handing off *between* agents is agent-bridge's / ACP's domain, not the ground
  layer's. The ground layer *produces* the truth those higher layers coordinate
  over.
- **Source remotes do not hold private coordination refs by default.** A public
  or shared source repository is not an implicit lease store. Remote ref-backed
  coordination requires a separately selected private state owner.
- **No continuous full-scan sweeps of unbounded session data.** Deriving state by
  repeatedly reading the entire session-state tree or a whole event log is out of
  bounds; growing datasets are read incrementally by cursor/watermark, and single
  resources by random access.
- **The record's memory is not a log.** The session-tagged history holds a small,
  capped set of the terse values agents already report (focus/disposition, handoff
  references) — it is **not** a transcript, an event stream, or an audit log, and
  must stay bounded. Rich session history and replay are the session-sync / record
  domain, not this store.
- **Not the presentation host.** agent-worktrees owns and contributes Worktrees
  semantics, but does not render or host the interactive surface, carry a TUI
  framework, own human terminal/multiplexer choreography, or require the
  Worktree Manager. How contributions are composed and displayed is the Worktree
  Picker's subject.

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
- **2026-08-07** — Revised after investigating the CLI's signal surface for
  detecting an agent reaching **rest**. Confirmed the interceptor *hooks* surface
  (prompt/tool edges) carries no rest signal, while the **native session-event
  stream** does (a graded idle/turn signal, incl. an *awaiting-operator* rest and
  an explicit session-idle). Named the **native-event producer** (an in-session
  extension agent-worktrees owns) as the crispest passive rest source, and pinned
  it — with agent-bridge's ACP eventing — as an **optional, non-load-bearing**
  producer over an **extension-free backbone**: because in-session extension
  initialization is fallible, tracking correctness must never depend on any
  extension loading. Mined from the copilot-sdk event catalog and the existing
  live-pulse extension; motivated by extension-init fragility observed in mainline
  flows.
- **2026-08-07** — Folded in an upstream copilot-extensions design invariant:
  **never continuously sweep unbounded datasets** (session-state tree, event log).
  Sharpened the derivation engine to *bounded, random-access + cursor/watermark
  incremental* reads and added the matching Behavior + Non-Goal, keeping the
  extension-free backbone consistent with the org-wide efficiency invariant.
- **2026-08-19** — Sharpened *derive, don't duplicate* from "one owner of the
  store" down to "**one writer per signal, aggregate derived**": added §Concepts/*the
  aggregate is derived — single-writer slots, one reducer* and §Behaviors/*aggregate
  status is derived, never written*. The worktree's status/head is a **pure reduction
  over independent single-writer slots** (git · pr · claim · mux · copilot-lock ·
  session/handoff), each with its own freshness stamp; no contributor writes the
  aggregate, so parallel writers cannot race on one "true state" cell. Mined from an

### Private coordination state has an explicit owner
Cross-machine coordination state such as ref-backed resource leases belongs to
an **explicitly selected private state owner**, not whichever source repository
happens to be active. A bound knowledge repository may own that state; an
operator may deliberately configure another private store. The source remote is
never an implicit fallback. This keeps source distribution and personal or
machine-scoped coordination state as separate trust domains. Mined from an
  operator design conversation prompted by a failed handoff-cutover that orphaned a
  worktree's head (successor died on the CLI resume-hang before registering) — which
  also clarified that succession *completeness* is a **liveness-aware reconciliation**
  (repair layer), not a static record-local fold. Realized operationally by the
  `worktree-state-live-db` effort (cells + journal + deterministic derivation).
- **2026-08-21** — Extended for **the worktree remembering itself**, after a failed
  handoff-cutover investigation (a HOME-started / bare-resumed successor never binds
  because `sessionStart` registration keys on the start cwd, and there is no
  cwd-change hook to re-bind). Added §Concepts/*the worktree remembers — binding and
  a bounded history in the record*, §Features/*explicit session binding* + *the
  record remembers — a bounded, session-tagged history*, §Behaviors/*binding is an
  explicit assertion, never a sniff* + *recovery is record-first*, and the *record's
  memory is not a log* Non-Goal. Two new properties: a session binds to its worktree
  by an **explicit self-identifying assertion** (with detect-and-prompt for an
  unbound-but-active session, never a background sniff), and the record keeps a
  **bounded, session-tagged history** of the terse focus/handoff values agents
  already report — so the worktree's disposition is the tail and a handoff recovers
  **record-first**, independent of any transport or a successful live cutover. Mined
  from an operator design conversation carved into the aperture-labs
  `worktree-self-knowledge` effort.
- **2026-08-26** — Added the ground layer's declarative **Worktrees
  presentation contribution** and removed implied ownership of the human
  presentation/multiplexer host. Worktrees remains agent-worktrees-owned
  semantics and state, but is rendered by the optional Manager through the same
  process-boundary contribution model as sibling layers.
