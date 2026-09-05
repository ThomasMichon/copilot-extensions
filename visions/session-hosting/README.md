# Copilot Session Hosting — Vision

- **Subject:** The provider-neutral hosting layer that starts, presents,
  observes, reconnects, hands off, and retires user-interactive or headless
  Copilot execution.
- **Scope:** leaf (cross-cutting capability within the agent fabric)
- **Status:** Active
- **Last revised:** 2026-09-04
- **Reality docs:** [`plugins/agent-bridge/docs/architecture.md`](../../plugins/agent-bridge/docs/architecture.md) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../plugins/agent-worktrees/docs/architecture.md)

## Purpose & Intent

Copilot can be experienced through many legitimate hosts: a CLI in a terminal
multiplexer, a plain process, an ACP-based frontend, the Copilot SDK, a graphical
application, a durable session-host service, or a third-party rig. No generic
handoff or worktree component can safely own the mechanics of all of them.

The north star is a **pluggable execution-host boundary**. Each host owns the
processes, protocol connections, interaction surfaces, and host-specific
identity it can actually control. The durable agent fabric asks for outcomes
such as start, observe, reconnect, submit an initial prompt, or retire; a
capable provider realizes those outcomes according to its rig.

This separation lets worktree-lifetime agency state remain stable while users
freely choose how Copilot runs. It also lets a hosting technology integrate
without adding another special case to context-handoff, agent-worktrees, the
Picker, or every coordinating client.

## Concepts & Components

### Session-host provider

A provider represents one execution technology and states the lifecycle,
interaction, observation, and cutover capabilities it can honestly uphold.
Providers may support fully owned headless sessions, human-attached interactive
sessions, or a narrower observational surface. A provider is realized as a
physically separate component from the durable agency store — never a
configuration branch inside it.

Provider concerns compose along **two independent axes**, and providers on
different axes are not mutually exclusive alternatives:

- **Backend** — how a session's underlying Copilot process or connection is
  established and driven: a directly spawned CLI process, or a session hosted
  through the Agent Host Protocol (AHP).
- **Presentation** — how a human attaches a terminal to that running session:
  TMux/PSMux pane wrapping, a plain terminal, a GUI window, or none (headless).

Mux is a **presentation** concern: a Mux-wrapped pane can front a directly
spawned process today, and can equally front an AHP-hosted session — choosing
AHP does not forgo Mux, and choosing Mux does not forgo AHP. Treating Mux and
AHP as if they were exclusive peers understates this composability; the
contract must let a caller select a backend and a presentation independently.

For now, both the Mux presentation layer and the AHP backend are owned and
driven by the **Worktree Manager** control-plane app (see
[installer](../installer/README.md)) rather than by agent-worktrees or by a
separate per-technology plugin. This is a near-term consolidation, not a
permanent exclusivity rule: a future execution technology may still ship as
its own installable provider package under the same contract.

### Provider resolution

A caller describes the desired execution outcome and relevant agency/workspace
context. Available providers are discovered and selected by capability,
authority, user preference, and current ownership rather than by hard-coded
environment branches in the caller.

### Host-owned execution identity

Pane IDs, windows, process handles, protocol sessions, application instances,
and host connections remain opaque provider identities. The provider validates
and acts on them; the agency ledger stores only the bounded evidence needed to
relate an execution leg to durable work. The agency ledger's execution-leg
record is therefore a **provider id plus an opaque blob**, never a typed union
with one branch per provider (e.g. a mux-shaped field set beside an
AHP-shaped field set in the same record type). Adding a provider must not
require widening that union or teaching the agency layer a new provider's
field names.

### Durable transition requests

Operations that must survive caller exit—especially live handoff—are represented
as durable transition requests. Notification may accelerate pickup, but durable
state remains the authority if an endpoint, observer, UI, or provider restarts.

### Claim and fencing

Exactly one provider claims a transition. A provider that lost ownership or
holds stale identity cannot launch a duplicate successor or retire an unrelated
predecessor.

### Takeover and retirement authorization

Launching a successor is provisional. Durable agency authority moves only after
the successor proves its identity and acknowledges takeover. Retirement is a
separate authorization issued after that transition; the provider that owns the
predecessor performs the host-specific shutdown.

### Hosting clients and control planes

Pickers, applications, CLIs, handoff tools, and coordinating agents are clients
of the hosting boundary. They choose and request outcomes; they do not absorb
provider implementations.

## Features

### interchangeable-session-hosts

Copilot CLI, multiplexer-backed CLI, ACP/session-host, SDK, application, and
third-party rigs can participate as peer providers without changing durable
agency semantics.

### capability-honest-control

A provider advertises only the control it can guarantee. Observation,
notification, initial-prompt delivery, ongoing prompt admission, interruption,
reattachment, and retirement remain distinct capabilities rather than one
optimistic "supported" bit.

### durable-cutover-request

A handoff can request live cutover without assuming who performs it. The
request survives the initiating session and remains recoverable when immediate
notification or launch fails.

### provider-owned-retirement

The provider that owns an execution leg validates and retires it using its own
safe identity and lifecycle semantics. Generic orchestration never kills a
process or pane it does not own.

### exact-initial-context-delivery

When a provider claims it can launch a successor with initial context, success
is measured from the resulting session and submitted context, not merely from a
spawn receipt.

### bring-your-own-host

Users can add a hosting integration without modifying the generic worktree,
handoff, or presentation layers.

### provider-neutral-human-control

A human control plane can present launch, resume, join, and handoff actions
across available providers while preserving the user's preferred Copilot
experience.

## Behaviors

### host-owns-mechanics-agency-layer-owns-meaning

The provider owns execution mechanics and runtime identity. The agency layer
owns the worktree, objective, claims, head, succession, and disposition. Neither
duplicates the other's authority.

### persist-before-notify

A transition is durable before any endpoint is pinged or observer is expected
to react. Notification loss delays action but does not lose the request.

### launch-receipts-are-provisional

A process, pane, window, or protocol launch receipt never proves a usable
successor. The resulting execution leg must independently prove the expected
session and opening context before takeover.

### retirement-follows-authoritative-takeover

The predecessor remains recoverable until the successor has acknowledged the
handoff and durable agency state records the new head. Only then may the owning
provider act on a fenced retirement authorization.

### one-transition-one-provider

Competing providers cannot both realize one cutover. Claiming and fencing make
selection deterministic and stale work harmless.

### preserve-interactive-ownership

A provider does not seize control from a human-facing surface it does not own.
Where only observation or notification is safe, the provider reports that
fidelity and leaves interaction with the owning application.

### degrade-to-recoverable-manual-handoff

When no compatible provider is available, the handoff remains stored and
copyable. Lack of live automation never destroys the predecessor or the
continuation.

## Non-Goals / Boundaries

- **Not one universal process manager.** Different Copilot products and rigs
  keep their native lifecycle and interaction semantics.
- **Not worktree state ownership.** Providers report execution observations but
  do not own repository identity, claims, disposition, or completion.
- **Not handoff content ownership.** The hosting layer transports the initial
  continuation and performs authorized changeover; context-handoff owns the
  baton and transition policy.
- **Not a requirement for a terminal or multiplexer.** Those are capabilities
  of particular providers, not fabric prerequisites.
- **Not a privileged built-in provider.** The first implementation does not
  define the abstraction; CLI mux, ACP, SDK, application, and third-party hosts
  remain peers.
- **Not a configuration mode of agent-worktrees.** A provider — Mux
  presentation, the AHP backend, or any future one — is realized as a
  physically separate component from the durable agency store, never an
  internal branch inside agent-worktrees' state engine. This does not imply
  Mux and AHP are mutually exclusive: they compose along independent backend
  and presentation axes (see *Session-host provider* above).
- **Not a specification.** This vision defines ownership and guarantees, not a
  registry format, endpoint protocol, request schema, or command vocabulary.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Durable agency state:
  [plugins/agent-worktrees](../plugins/agent-worktrees/README.md)
- Current host of the Mux and AHP execution mechanics:
  [installer](../installer/README.md) — the Worktree Manager control-plane app
- Hosted coordination provider:
  [plugins/agent-bridge](../plugins/agent-bridge/README.md)
- Human presentation: [picker](../picker/README.md)
- Native host convergence:
  [native-convergence](../native-convergence/README.md)

## Provenance

- **2026-09-04** — Authored from the decision to separate worktree-lifetime
  agency state from user-interactive Copilot process management. The provider
  model generalizes live handoff beyond TMux/PSMux so ACP, SDK, App, Herdr, and
  other rigs can own their mechanics without entering generic handoff or
  worktree code. Tracked by #2053.
- **2026-09-04** — Clarified that Mux (presentation: terminal/pane wrapping)
  and AHP (backend: session establishment/protocol) are **composable axes, not
  mutually exclusive peers** — a Mux-wrapped pane can front either a directly
  spawned process or an AHP-hosted session. Corrected an earlier framing that
  treated AHP as an alternative a user picks *instead of* Mux. Directed that,
  for now, both mechanics are consolidated under the **Worktree Manager**
  control-plane app rather than living inside agent-worktrees or shipping as a
  separate per-technology plugin; agent-worktrees keeps only an opaque
  provider-id-plus-blob execution-leg record regardless of which backend or
  presentation produced it. Mined from finding AHP implemented as an internal
  `session_backend.is_ahp` config branch threaded through agent-worktrees'
  `__main__.py`, `tracking.py`, `finalize.py`, and `config_dropins.py` (landed
  via #1657 / PR #1998). Tracked by #2062.
