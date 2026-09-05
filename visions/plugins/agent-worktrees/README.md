# agent-worktrees — Worktree Lifetime & Agency State — Vision

- **Subject:** **agent-worktrees** as the durable authority for repository and
  worktree identity, worktree-lifetime agency state, relationships, claims,
  obligations, disposition, and source-control completion.
- **Scope:** leaf (concrete component; child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-09-04
- **Reality docs:** the agent-worktrees plugin `docs/`
- **Supersedes / superseded by:** none

## Purpose & Intent

agent-worktrees makes a repository worktree a durable, accountable **unit of
agency**. It answers which workspaces exist, what objective each worktree carries,
which sessions or controllers have acted for it, what resources it owns, whether
work remains, and whether its source-control lifecycle is safely complete.

Its north star is a passive, host-neutral state model. A worktree and its
responsibility survive terminals, processes, user interfaces, Copilot products,
and execution hosts. The same worktree record remains authoritative whether an
agent is driven through Copilot CLI in a multiplexer, an ordinary process, ACP,
the Copilot SDK, a graphical application, or a third-party rig.

agent-worktrees therefore owns **durable worktree-lifetime truth**, not the
interactive process that happens to animate it. Execution hosts publish bounded,
attributable observations and lifecycle assertions into the record; they do not
become competing owners of worktree identity or responsibility. Conversely,
agent-worktrees never needs to understand every way a Copilot process can be
launched, presented, prompted, reattached, or retired.

## Concepts & Components

### Repository and worktree identity

Repositories, source checkouts, worktrees, branches, remotes, contribution
contracts, and management classes form the stable spatial identity on which the
rest of the fabric coordinates. Paths vary by machine; identity and declared
relationships remain stable.

### The worktree as a unit of agency

A worktree record carries the objective-facing state that should outlive any one
session: current focus, asserted disposition, participants, session/controller
relationships, succession lineage, claims, obligations, and completion state.
The worktree is not itself a process. It is the durable vessel to which one or
more execution legs may bind over time.

### Execution legs and observations

An execution leg is an externally hosted session acting for a worktree. The
record identifies the leg, its provider, its relation to earlier and later legs,
and the lifecycle assertions or observations the provider can honestly supply.
Provider-specific process, pane, window, connection, and protocol identities
remain opaque host evidence rather than becoming worktree semantics.

### Binding and control are distinct

A session may bind as the current execution leg of one worktree while controlling
other worktrees or pull-request vessels. Control never impersonates binding.
Both relations are explicit, reciprocal where possible, and durable enough for
recovery.

### Current head and succession

The worktree carries one authoritative current head for its execution lineage.
Handoff records predecessor and successor relationships independently of the
mechanism that launched either session. Moving the head is a deliberate,
fenced state transition; a host reporting that it started a process is not by
itself proof of takeover.

### Claims, leases, and obligations

The worktree owns the ledger of resources it creates or adopts: related
worktrees, pull requests, environments, sessions, connections, and other
scarce resources. Exclusive access is fenced, ownership is answerable in both
directions, and finalization is gated on settlement or an explicit transfer.

### Source-control completion

Creation, isolation, contribution-policy enforcement, publication, finalization,
and prune safety remain worktree-lifetime concerns. They do not depend on which
interactive host ran the agent that produced the change.

### Derived status

Overall status is a reduction over independently owned facts: source-control
state, claims and obligations, asserted disposition, effort focus, relationships,
and fresh provider observations. No observer writes the aggregate verdict.
Stale or absent execution observations reduce fidelity without erasing durable
responsibility.

### Declarative presentation contribution

agent-worktrees contributes machine-readable worktree semantics and actions to
optional human or agent control planes. Presentation clients render those
semantics and may invoke a selected execution host, but do not acquire ownership
of the worktree record.

## Features

### durable-worktree-agency-record

Each managed worktree carries a durable, bounded record of its identity,
objective-facing status, execution lineage, claims, obligations, and
source-control completion.

### host-neutral-execution-binding

Execution legs from different Copilot products and hosting technologies can bind
to the same worktree model without agent-worktrees learning their launch or
interaction mechanics.

### authoritative-head-and-lineage

The current execution head and reciprocal predecessor/successor lineage are
durable, explicit, and independent of process timestamps or UI attachment.

### asserted-disposition

The acting agent deliberately states whether the worktree is resolved or still
has actionable follow-up. Git cleanliness, process exit, and session quietness
never manufacture that semantic conclusion.

### accountable-resource-ledger

Every resource a worktree creates or adopts remains attributable, fenced where
exclusive, and visible until settled or explicitly transferred.

### contribution-aware-lifecycle

Worktree publication and completion honor each repository's own contribution
contract, preserve isolated editing, and prove content safe before cleanup.

### provider-observation-ingestion

Execution hosts may publish bounded, attributable lifecycle and activity
observations. Each provider owns only its observation slot; the worktree state
owner derives the aggregate.

### provider-owned-worktrees-surface

The Worktrees presentation surface is described through machine-readable
semantics that any compatible control plane can render without importing the
engine or persisting a second copy of its state.

## Behaviors

### durable-state-outlives-execution

Closing a terminal, replacing a session host, changing Copilot products, or
losing a provider does not erase the worktree's objective, claims, lineage, or
disposition.

### explicit-relations-never-sniffed-ownership

Binding, control, succession, and claim ownership are explicit state transitions.
Incidental cwd, process ancestry, pane membership, or connection presence may be
evidence supplied by a host, but never silently creates responsibility.

### launch-is-not-takeover

A newly launched process or newly observed session does not become the worktree
head until the governing lifecycle transition acknowledges it. Failed or
duplicate launches therefore cannot steal authority.

### derive-dont-duplicate

Each durable fact has one owner. Execution providers own their runtime-specific
evidence; agent-worktrees owns worktree-lifetime state; presentation and
coordination layers derive over both rather than copying either.

### observation-loss-degrades-honestly

When a provider is unreachable, agent-worktrees reports stale or unknown live
state while preserving durable state. It does not infer that an objective is
resolved, a session is dead, or a claim is abandoned from missing telemetry.

### finalization-joins-durable-obligations

A worktree may complete only when its source-control content is safe and every
durable obligation is settled or transferred. Interactive process exit is
neither necessary nor sufficient evidence of completion.

### provider-replacement-preserves-agency

Changing the preferred session host affects future execution legs, not the
identity or meaning of the worktree. Existing legs retain their recorded
provider and semantics until they conclude or hand off.

### presentation-is-process-boundary-only

Control planes consume worktree state and actions through attributable
machine-readable boundaries. agent-worktrees never imports a TUI, terminal
manager, or session-host implementation.

## Non-Goals / Boundaries

- **Not a Copilot process manager.** agent-worktrees does not launch, wrap,
  reattach, prompt, interrupt, or terminate Copilot processes.
- **Not a terminal or multiplexer owner.** TMux, PSMux, terminal windows, panes,
  and console choreography belong to an execution-host provider.
- **Not a universal session host.** Copilot CLI, ACP, SDK, App, and third-party
  rigs retain their own hosting and interaction semantics.
- **Not a home for a provider-specific config union.** agent-worktrees does not
  carry a typed "session backend" field set with one branch per hosting
  technology (e.g. Mux fields beside AHP fields in the same record or config
  schema). Each execution leg it records is a provider id plus an opaque,
  provider-owned blob; the mechanics that establish and present a session —
  currently Mux and AHP, both driven by the Worktree Manager control-plane —
  live outside agent-worktrees entirely.
- **Not the handoff transport.** It records lineage, head transitions, and
  durable responsibility; context transfer and live cutover are orchestrated
  above it through the selected execution host.
- **Not a transcript or event warehouse.** The worktree record remains bounded
  and objective-facing. Rich conversation history belongs to the session host or
  session archive.
- **Not the presentation host.** It contributes worktree semantics but does not
  render the operator experience.
- **Not a specification.** This vision fixes ownership boundaries and durable
  guarantees, not schemas, commands, endpoints, file layouts, or provider APIs.

## See Also

- Parent vision: [agent-fabric](../../agent-fabric/README.md)
- Cross-cutting sibling:
  [session-hosting](../../session-hosting/README.md) — pluggable ownership of
  user-interactive and headless Copilot execution.
- Presentation sibling: [picker](../../picker/README.md)
- Coordination sibling:
  [plugins/agent-bridge](../agent-bridge/README.md)
- Reality docs: the agent-worktrees plugin `docs/`

## Provenance

- **2026-09-04** — Reframed agent-worktrees around durable worktree-lifetime
  agency state rather than Copilot process ownership. The revision separates
  repository/worktree identity, claims, relationships, status, and completion
  from the interchangeable technologies that host an interactive agent. It was
  mined from the requirement that the same agency model work across multiplexed
  Copilot CLI, plain CLI, ACP, SDK, graphical, and third-party hosting rigs.
