# agent-bridge — Vision

- **Subject:** The **coordination layer** of the agent fabric — the plugin that
  gives agents, CLIs, and UI/fronts a durable way to create, address, observe,
  message, resume, and hand off live Copilot sessions across projects, worktrees,
  machines, and venue providers.
- **Scope:** leaf (a per-plugin vision under the [agent-fabric](../../agent-fabric/README.md) branch)
- **Status:** Draft
- **Last revised:** 2026-09-04
- **Reality docs:** [`plugins/agent-bridge/README.md`](../../../plugins/agent-bridge/README.md) ·
  [`plugins/agent-bridge/docs/architecture.md`](../../../plugins/agent-bridge/docs/architecture.md)

## Purpose & Intent

The fabric's ground layer gives an agent an isolated worktree and a local
session body. The delegation layer gives work a durable task record when an
agent should let go of it. Between those sits **agent-bridge**: the live
coordination layer that lets a participant **call another agent**, inspect what
is happening now, deliver an attributed message to an existing session, and keep
that session alive across the churn of clients, UI/fronts, transports, and
deployments.

Its central promise is **ownership clarity**. The bridge owns the lifetime and
event stream of sessions it hosts; clients attach and detach. A target-local
bridge owns the processes that must live on a target machine; the caller owns
only the relationship. Interactive sessions that the bridge does not own are
represented honestly rather than forged into a false replica. Remote links may
blink, fronts may restart, and callers may disappear without making the target
session collateral damage.

The north star is a coordination substrate that feels boring in the way good
infrastructure should: any participant can name a project, resolve an agent or
session, choose the right control pattern, send work or a side message, reconnect
by cursor, see who owns what, and recover from interruptions without inventing a
private tunnel or a private session ledger.

Delegation through that substrate should feel as direct as native sub-agent
control even though the execution model is broader: create an agent, retain its
identity, read its accumulated work, steer it with another message, wait for an
attention boundary, and cancel or retire it deliberately. Because an external
bridge cannot wake a caller whose invocation has already disappeared, attention
delivery is an explicit relationship: an attached caller or retained subscriber
is released when the target needs it, while a detached caller is told honestly
that no future wake-up is implied.

The bridge's contracts must also evolve without requiring every participant to
update in lockstep. Clients, daemons, session hosts, providers, relays, and
durable records may be on different supported generations at the same time.
Compatibility is therefore selected explicitly before work begins, retained for
the lifetime of each session instance, and retired only when no live,
recoverable, or rollback reference still depends on it.

## Concepts & Components

### bridge daemon

The **bridge daemon** is the per-machine runtime that exposes the local control
plane, hosts or represents sessions, streams session events, and participates in
the wider mesh. Each machine may run its own bridge; together they make local
and remote sessions visible through one coordination surface.

### bridge CLI

The **bridge CLI** is the agent-facing and operator-facing headless console for
the mesh. It lets a caller start, send to, wait on, inspect, interrupt, stop,
resume, and observe sessions without choosing a different tool for each venue or
transport.

### delegation control and attention boundary

The bridge presents one task-shaped control model across every venue: create,
identify, read, steer, wait, interrupt, stop, resume, and end. A caller may
retain an **attention subscription** whose completion means the target has
reached a state requiring caller action, not merely that a successful turn
finished. Completion, failure, an input or permission request, unrecoverable
loss of reachability, and a policy decision the bridge cannot safely make alone
are attention boundaries.

### authenticated local control plane

The bridge exposes authenticated local control and reconnectable event delivery
for tools, UI/fronts, and other agents. Prompt submission and event consumption
are separate concerns so a consumer can reconnect to the same session history
instead of making a long turn depend on a single live socket.

### negotiated contract envelope

Each live boundary advertises the semantic generations and optional
capabilities it can actually uphold. A new session selects one compatible
envelope before ownership, launch, relay, or message-admission effects begin and
retains that selection for recovery. Where an external released protocol governs
the boundary, that protocol's own handshake and capability model are the
negotiation; the bridge records the outcome internally rather than adding a
second handshake or generic capability bit. Implementation package versions
remain useful evidence, but never stand in for negotiated semantics.

### AHP host face

The bridge exposes bridge-owned agent, session, chat, and event state to clients
through the Agent Host Protocol (AHP). AHP is the upstream client-to-host
contract; ACP remains the downstream contract used to drive an agent runtime.
ACP-compatible agent runtimes and AHP-compatible native hosts are both
first-class participants, with convergence advancing at both boundaries in
parallel. When the bridge federates a native-host-owned session, that native
host remains authoritative and the bridge acts as a proxy or fidelity-declared
projection. The bridge maps between roles without pretending the protocols have
identical identity, lifecycle, replay semantics, or ownership.

### session host provider

The bridge's **Session Host** is one provider of the fabric's
[session-hosting](../../session-hosting/README.md) capability. It owns a hosted
Copilot child and its protocol pipes; fronts may update or disconnect while the
host keeps the child alive until deliberate drain, handoff, stop, or end. It is
not the universal implementation for interactive Copilot: CLI/mux, graphical,
SDK, and third-party hosts may own peer execution providers while the bridge
coordinates with or represents them honestly.

### session and event ledger

The bridge's session ledger records session identity, status, events, turn
boundaries, context usage, delivery cursors, target linkage, and terminal
outcomes. It is the source that CLIs, UI/fronts, other agents, and recovery
flows read when they need to know what happened.

### topology and resolver layer

The resolver layer turns a caller's target into a reachable session or agent by
combining project context, worktree context, machine topology, agent profiles,
namespace providers, and capability hints. The caller names the target; the
bridge determines which route can honestly serve it.

### peer bridges

Bridge instances can cooperate as peers. A local bridge may delegate ownership of
a remote session to the bridge on the target machine, or deliver a message
through that peer, so the environment that can keep the target alive owns the
target's process.

### live-session registry

Interactive sessions may register themselves with a bridge and receive an
attributed inbox only through an admission path that preserves one session
identity, one ordered input stream, and one model turn for each accepted prompt.
A registered interactive session becomes visible through the ordinary
session/event surface, while the bridge remains honest that it does not own that
process or the permissions mediated by its human-facing terminal. A lower-
fidelity adapter may expose presence or notifications without being trusted to
inject prompt turns.

### mesh federation

A fleet of bridges forms a discoverable mesh: sessions owned by one machine can
be seen and reached from another through peer links, a gateway, or a
reverse-tunnel-exposed satellite. Reachability may be asymmetric or gated; the
mesh represents what is reachable under the participant's policy rather than
pretending every node is equally open.

## Features

### durable-reattachable-session-hosting

A hosted session can outlive the bridge frontend, any watching client, and a
transport reconnect. The Session Host keeps the child and its pipes alive while
the bridge reconciles back to durable session state and consumers reattach by
cursor.

### session-host-provider-participation

The bridge can act as a full execution-host provider for sessions it owns and as
a coordination client or fidelity-declared projection for sessions owned by
another provider. Durable worktree identity and responsibility remain in
agent-worktrees; bridge hosting contributes execution lifecycle and observations
without taking over that ledger.

### cli-and-api-control-plane

The same coordination fabric is drivable from a CLI and from authenticated local
control surfaces: start or resume sessions, submit turns, stream events, inspect
state and context usage, interrupt the current turn without ending the session,
and intentionally stop or end the session.

### task-shaped-delegation-control

A caller uses the same compact lifecycle vocabulary whether the target is a
local process, a remote workspace, a native host, or another ACP-producing
runtime. Placement, transport, and process ownership enrich the target without
forcing the caller to learn a different delegation model for each one.

### attention-boundary-subscriptions

An attached caller or retained subscriber can wait for **anything requiring
attention**. The subscription settles with a bounded, structured reason and the
latest durable position when the target completes, fails, asks for input or
permission, remains unreachable beyond its reconnect policy, or reaches another
caller- or policy-defined attention boundary. Ordinary transport churn resumes
by cursor instead of settling the wait. Detached submission remains valid, but
never claims an asynchronous wake-up channel that the caller did not retain.

### bounded-delegated-results

A caller can retrieve a bounded account of a delegated target's accumulated
work: its current state, latest result, and incremental work since a retained
position. The raw event stream remains available for fidelity and recovery, but
ordinary delegation does not require ingesting the entire transcript or every
tool event.

### standards-compatible-host-control

An AHP client can discover agents, create or subscribe to sessions and chats,
drive turns, reconnect to ordered state, and mediate supported tool or input
requests without binding to agent-bridge's private REST vocabulary. Existing
CLI, REST, and ACP faces remain usable while clients converge on the standard
host boundary.

### agent-and-host-protocol-convergence

The bridge can drive an agent runtime through ACP while exposing or federating
host-owned sessions through AHP, and can adopt additional compatible runtimes
without changing its delegation semantics. Protocol roles remain explicit even
when one conversation crosses both boundaries.

### cursor-stable-event-replay

Session events form an ordered, reconnect-safe stream with stable identities and
delivery cursors. A consumer that disconnects can resume without losing a turn,
duplicating delivery, or waiting forever past a rebuilt authoritative log.

### topology-aware-agent-resolution

Named agents and sessions resolve through project context, worktree state,
machine topology, namespace providers, and capability probes. A target may be
local, remote, elevated, dynamic, or venue-provided, while the caller sees one
catalog and one resolution contract.

### bridge-as-agent-delegation

The bridge can present itself upstream as an agent that routes work to a named
downstream agent. Host-to-sub-agent delegation reuses the same session manager,
event ledger, and resolver layer rather than inventing a second protocol.

### peer-bridge-session-ownership

Remote sessions are owned by the bridge closest to the environment where they
run. The host bridge becomes a peer client; target processes and state survive
host-link churn because the target-local bridge owns their lifetime.

### live-session-messaging

The bridge can deliver attributed messages to sessions that already exist:
bridge-owned sessions, peer-owned sessions, and registered interactive sessions.
Prompts, notifications, requests, replies, and broadcasts preserve sender
identity so agent-to-agent traffic never masquerades as operator input.

### single-stream-message-admission

Every accepted prompt is admitted exactly once to the authoritative controller
for its session lineage and enters one serialized conversation stream. A busy
authoritative target durably queues the prompt in order until it can admit the
next turn. Message delivery may be retried, routed through an adapter, or follow
a deliberate session handoff, but those transitions cannot fork persistent
model streams or append competing responses to the same conversation. This is
the coordination layer's concretization of the parent fabric's ordered,
exactly-once message-delivery promise.

### three-control-patterns

The bridge supports three honest ways to reach a target over one substrate:
**full headless control** of a bridge-hosted agent, **side-exchange or broadcast**
with an already-running session, and **takeover** by stopping a headed session
and resuming its context headlessly. A caller chooses by desired ownership, not
by wiring a bespoke transport.

### interactive-session-representation

An interactive session can be represented through the bridge's ordinary session
surface even when the bridge does not own its process. That representation is
best-effort and fidelity-honest: it exposes what can be known and mediated
safely, and leaves human-terminal permission decisions with the surface that
actually owns them.

### observable-mesh-status

The mesh is inspectable: sessions, subscribers, context use, turn status,
delivery progress, topology, capability resolution, peer reachability, drain
state, current heads, and stranded hosts are visible from logs, CLI output, and
event streams before a caller needs to guess.

### graceful-deployment-and-version-survival

Bridge updates cooperate with live sessions. Frontends reattach where compatible;
in-flight work is drained, cancelled-and-resumed, or handed off deliberately; and
older hosts remain bounded but alive long enough for their children to reach a
safe stop.

### version-skew-safe-contract-evolution

The bridge can add and adopt new protocol generations, optional capabilities,
provider behavior, and durable metadata while current and previous supported
participants coexist. New behavior is negotiated and canaried for new sessions;
existing sessions keep the adapter and authority model they selected; retirement
waits for evidence that no live session, recoverable record, or rollback path
still requires the older contract.

### reach-active-worktrees-and-configured-repos

A caller can address agents in active worktrees and in configured projects the
fabric knows how to resolve, including projects whose working body lives on
another machine or venue provider. The reachable set is a catalog, not a
collection of one-off connection recipes.

### satellite-exposure-and-federation

A field or otherwise one-way-reachable machine can expose its local bridge to
the mesh through a reverse tunnel or gateway while it is online. It participates
as a provider on the terms its reachability and policy allow, rather than being
limited to a tunnel-only client.

### context-aware-in-place-handoff

A hosted session can roll itself to a successor in the same worktree when asked
or when context pressure requires it under policy. The retiring session's
continuation seeds the successor, the head moves deliberately, and watchers are
told that the work continues under the successor identity. The bridge performs
this changeover as the owning session-host provider; generic handoff policy and
durable worktree succession remain outside its process mechanics.

## Behaviors

### own-the-lifetime

If the bridge starts or adopts a hosted session, the bridge owns that session's
lifetime. Clients, UI/fronts, and other agents attach as consumers or callers;
watching a session does not make the watcher its process owner.

### reattach-never-kill

A bridge frontend restart, client disconnect, or UI/front remount reconciles to
live Session Hosts and durable state. It does not kill an active child merely
because the observing process changed versions or lost its connection.

### drain-before-letting-go

Stopping, updating, cutting over, or retiring a session first seeks a safe
state: finish the turn, cancel gracefully, mark for resume, carry context
forward, and only then let go. A hard kill is an explicit last resort, never the
normal maintenance path.

### one-owner-many-callers

A hosted session has one controller for turn submission, while many consumers may
read or request work through that controller. The bridge serializes competing
turns into one transcript so callers converge on shared state instead of staging
a custody fight. One accepted prompt produces at most one model turn across the
session's succession chain; no adapter may create a second hidden controller
behind the same session identity.

### attention-requires-a-live-relationship

The bridge releases an attached caller or retained subscriber at each attention
boundary selected by the caller or governing policy. A deliberate session
handoff carries the subscription to the successor unless handoff itself requires
a caller decision. If a caller deliberately detaches without retaining such a
relationship, the bridge preserves the target and its durable state but does not
pretend it can later wake that caller's model loop.

### any-agent-delivery

The bridge delivers by the path that matches ownership and reachability: local
hosted session, peer bridge, bridge-as-agent route, namespace provider, or
registered interactive inbox. The caller asks for the agent or session; the
fabric chooses the route it can guarantee.

### project-addressed-invocation

The bridge is addressable against an explicitly named project — `--project`, or
the per-project `<repo>` binstub that supplies it — with the same result as
being CWD-anchored inside that project. Starting, sending to, waiting on, or
inspecting an agent/session for a project therefore works as `<repo> bridge …` as
readily as `agent-bridge --project <repo> …`, so a CWD-neutral front drives the
mesh for a specific project without standing in its checkout. This is the
coordination layer's concretization of the parent fabric's
§Features/address-any-project and §Behaviors/project-addressed-not-cwd-bound.

### capability-probe-then-fallback

A richer route is chosen only after the specific machine, namespace, worktree,
agent, and session combination proves serviceable. If a peer path, live
injection, or namespace route is unavailable, the bridge falls back to a safe
simpler path or refuses clearly.

### negotiate-before-side-effects

A new session or ownership transition selects a mutually supported semantic
contract before it claims a venue, launches a child, adopts a relay, publishes a
route, or admits a prompt. An unsupported capability fails or degrades with an
explicit reason before partially authoritative resources are created.

### session-contract-survives-default-changes

Changing the preferred contract for new sessions does not reinterpret an
existing session. Its selected adapter, source authority, identity, and
ownership semantics survive frontend replacement, provider updates, and
rollback until that session ends or a separately proven handoff changes them.

### readers-expand-before-writers

New behavior is never preferred before every supported reader and recovery path
can interpret it safely. No supported older writer may erase ownership,
authorization, or identity evidence merely because it does not understand newer
metadata.

### prompt-injection-requires-single-stream-proof

A route may inject a prompt into an existing interactive session only when it
can prove authoritative session identity, serialized admission, and exactly-one
turn creation. Queueing through that authoritative controller is the normal
answer when the target is busy. If the route cannot establish those guarantees,
it may expose presence, status, or attributed notification delivery, but must
not present itself as a reliable prompt inbox.

### transparent-acp-passthrough

The bridge routes and multiplexes agent protocol traffic without inventing a
competing dialogue. Protocol interpretation belongs at the protocol edge; the
bridge preserves the downstream agent's semantics while adding routing,
ownership, and recovery.

### host-protocol-above-agent-protocol

AHP and ACP occupy different layers. For bridge-owned resources, the AHP face
owns shared client-visible host state, subscriptions, ordering, replay, and
named capabilities; the ACP face drives the downstream agent. A bridge never
labels an ACP transport, session-host envelope, or REST event stream as AHP
merely because the same conversation passes through it.

### native-hosts-are-feature-detected-peers

When the underlying CLI supplies a released, stable AHP host of its own, the
bridge and its clients interoperate through that public contract. The bridge may
delegate a local primitive to the native host or coexist as the richer
multi-venue host by owning distinct resources. It never creates parallel
lifecycle or replay authority for a native-owned session, never hard-depends on
unreleased internals, and never drops its durable routing, recovery, or remote
capabilities in the name of convergence.

### represent-at-honest-fidelity

When representing a session it does not own, the bridge reports the fidelity it
can actually guarantee. A lower-fidelity truthful view is preferred over a
high-fidelity illusion, and control that cannot be mediated safely is left with
the surface that genuinely owns it.

### cursor-stable-replay

Event IDs and delivery cursors remain stable across frontend cycles and
reattach. If recovery must rebuild a stream, consumers converge on the rebuilt
authoritative log rather than silently diverging.

### eventual-terminal-reconciliation

A session never remains indefinitely "running" after its turn has actually
ended. Clean finish, child death, interrupted stream, and frontend loss all
eventually reconcile to a persisted terminal turn state. Read surfaces also
derive an **at-rest** verdict from the durable event tail, so a stale ACP
"live/running" flag cannot hide a completed response from schedulers waiting on
the turn boundary.

### local-first-peer-mesh

Every participating bridge can host local sessions and initiate outbound reach.
The mesh does not require a single central bridge to become the only neck the
whole fabric depends on.

### attributed-prompt-injection

Every injected message is attributed to its sender and kind. Agent-to-agent and
system-to-agent traffic is distinguishable from operator input, so live-session
messaging remains collaboration rather than impersonation.

### authenticated-local-control

Control surfaces are local, tunnel-bound, or otherwise explicitly secured and
authenticated. The bridge is a coordination control plane, not a public prompt
socket.

### connection-loss-never-destroys-the-target

Losing the relationship between a caller and a target never destroys the target.
The target's owning bridge keeps the session alive until the connection can be
re-established, the session reaches a safe stop, or an explicit policy action
retires it.

### deliberate-creation-prefer-owned

Creating a new worktree or session is deliberate. A caller prefers to reuse a
worktree's current head or a worktree it already created for delegated use, and
is steered to reuse, hand off, or sunset an incumbent before starting another
session where a head already exists.

### handoff-carries-context-and-announces-the-changeover

When the bridge rolls a hosted session to a successor, it carries a continuation
brief into the successor's opening context and announces the changeover on the
session event surface. Watchers follow the baton instead of mistaking a
deliberate handoff for a death.

### federate-over-mesh-or-gateway

Bridges default to federation. A bridge can discover peers and present the union
of reachable bridges and sessions from any seat, over a peer mesh, gateway, or
other secured rendezvous, without requiring hand-wired connections to every
participant.

### reachability-may-be-one-way-and-gated

Reachability may be asymmetric. A satellite or field machine may join by
initiating outbound registration and exposing its agents while online, and a
machine may deliberately gate outbound reach until policy allows it.

## Non-Goals / Boundaries

- **Not the task queue.** Durable, claimable, fire-and-forget work belongs to
  agent-dispatch. The bridge may embody or message workers, but queue state and
  scheduling are sibling-layer concerns.
- **Not the git worktree or agency-state manager.** agent-worktrees owns
  worktree creation, finalization, relationships, claims, disposition, and
  durable execution lineage. The bridge hosts or coordinates execution against
  that state.
- **Not the universal interactive Copilot host.** The bridge is a full provider
  for sessions it owns and an honest coordinator for other hosts. It does not
  require every CLI, SDK, App, multiplexer, or third-party rig to surrender its
  native process and interaction ownership.
- **Not the connectivity provisioner.** SSH keys, host adoption, tunnel setup,
  and reachability verification belong to the connectivity layer; the bridge
  routes over declared reachability.
- **Not a web UX.** A rich UI/front may consume the bridge, but the bridge is the
  runtime and headless control plane underneath it.
- **Not a scheduler inside the caller.** The bridge can hold an attached
  invocation or subscription until attention is required, but cannot promise to
  wake a caller that detached without retaining an attention subscription.
- **Not package-version lockstep.** Correctness does not depend on every client,
  daemon, host, provider, or venue plugin converging on the same build before a
  supported feature can be used safely.
- **Not a private reimplementation of a native local host.** The bridge exposes
  a standards-compatible host boundary and retains its differentiated
  multi-venue coordination value. Released native hosts are feature-detected
  peers or providers, not private internals to copy or require.
- **Not a credential broker.** The bridge launches sessions in environments that
  may carry credentials; credential storage, ceremonies, and policy live in the
  trust layer or host environment.
- **Not an account-per-agent model.** The mesh coordinates sessions and
  worktrees under existing identities rather than minting a separate account for
  every agent.
- **Not a specification.** This vision fixes role, guarantees, and behaviors. It
  does not pin endpoints, ports, database schemas, command grammar, token
  formats, process managers, or on-disk formats.

## See Also

- Parent vision: [agent-fabric](../../agent-fabric/README.md) — §Concepts/
  *agent-bridge — the coordination layer*.
- Cross-cutting hosting vision:
  [session-hosting](../../session-hosting/README.md) — the provider boundary
  agent-bridge implements for sessions it owns.
- Sibling leaf: [agent-dispatch](../agent-dispatch/README.md) — the delegation
  layer that records claimable work, may embody workers through this runtime,
  and can hibernate a genuinely asynchronous wait until work needs attention.
- Sibling leaf: [agent-ssh](../agent-ssh/README.md) — the connectivity layer the
  bridge's cross-machine reach rides on.
- Venue provider: [agent-codespaces](../agent-codespaces/README.md) — a remote
  venue presented through the bridge's coordination contract.
- Reality docs: [`plugins/agent-bridge/README.md`](../../../plugins/agent-bridge/README.md) ·
  [`plugins/agent-bridge/docs/architecture.md`](../../../plugins/agent-bridge/docs/architecture.md).

## Provenance

- **2026-09-04** — Clarified agent-bridge as one execution-host provider and
  coordination surface within a plural hosting ecosystem. Bridge-owned ACP and
  headless sessions retain durable hosting and replay, while CLI/mux, SDK, App,
  and third-party interactive hosts may remain peer providers. Durable worktree
  agency state stays with agent-worktrees.
- **2026-08-31** — Added explicit version-skew-safe contract evolution as the
  shared foundation beneath AHP host convergence and native-sub-agent-like
  delegation control. The foundation owns negotiation, session pinning,
  reader-before-writer rollout, and evidence-gated retirement; each convergence
  retains ownership of its public semantics. Tracked by #1460.
- **2026-08-30** — Extended the delegation model with native-sub-agent-like
  control semantics over the bridge's broader execution substrate: explicit
  attention subscriptions, honest detached operation, and a single-stream
  prompt-admission invariant. Clarified that ACP agent-runtime convergence and
  AHP native-host convergence advance in parallel without collapsing their
  ownership roles. Tracked by #1433.
- **2026-08-27** — Extended the vision to distinguish the upstream AHP
  client-to-host contract from downstream ACP agent control. Added the
  standards-compatible host surface and the released-surface convergence
  boundary for native local hosts, while retaining bridge-specific durable and
  multi-venue value.
- **2026-08-03** — Authored to give agent-bridge its own canonical plugin vision
  (the coordination-layer sibling of the agent-dispatch leaf), distilled and
  portabilized from a downstream facility's agent-bridge vision, surfaced while
  binding the fabric's address-any-project guidance at the agent-* leaves. Closes
  the structural gap that agent-bridge had no canonical per-plugin vision leaf
  alongside its siblings.
