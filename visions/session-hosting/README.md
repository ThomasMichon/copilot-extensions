# Copilot Session Hosting — Vision

- **Subject:** The provider-neutral hosting layer that starts, presents,
  observes, reconnects, hands off, and retires user-interactive or headless
  Copilot execution.
- **Scope:** leaf (cross-cutting capability within the agent fabric)
- **Status:** Active
- **Last revised:** 2026-09-24
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

### Durable liveness snapshot

A provider's own process is not exempt from the same loss it is meant to detect
for others — its host process can itself die, restart, or run on a machine that
reboots entirely, discarding whatever it held only in memory. A provider that
wants to honestly answer *"who was I hosting"* after **any** restart of its own —
not only a graceful one — persists that answer to durable storage independent of
its own process lifetime, updating it at meaningful lifecycle transitions (a leg
starting, retiring, or handing off) in addition to any periodic cadence, so the
window in which the record can be stale stays small. The snapshot is still only
ever as fresh as its last write, and reconciliation at startup must treat it
that way: a leg the snapshot names that current reality no longer shows alive is
**candidate** host-loss evidence, not an automatic verdict — the same leg may
simply have retired cleanly between the last write and the restart, and a leg
that started after the last write is not itself proof of anything either way. A
provider reconciling a possibly-stale snapshot marks an unconfirmed leg as
exactly that (mirroring the fabric's own
`§Behaviors/uncertainty-is-marked-not-multiplied` on the agent-worktrees
vision) rather than asserting loss it cannot actually back, and corroborates
with an independent liveness check before treating a named leg as genuinely
gone. This is what makes
a provider's host-loss observation (`§Concepts/Host-owned execution identity`)
honest across the harder case a full machine restart represents, not only the
case where some part of the provider survives to notice its own child died.

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

### host-native-title-projection

Where the hosting technology has its own native window/tab title (a
multiplexer pane title, a terminal emulator tab), the provider projects the
worktree's durable title into it — composed with the wrapped process's own
title hint rather than replacing it, so an operator scanning terminal tabs
recognizes a worktree by name without switching into it first. This is a
per-provider mechanic (opaque host evidence, per *Host-owned execution
identity*), not a new durable field the agency ledger stores.

### provider-neutral-human-control

A human control plane can present launch, resume, join, and handoff actions
across available providers while preserving the user's preferred Copilot
experience.

### recoverable-across-full-restart

Whatever a provider was hosting remains discoverable even after the machine it
ran on restarts entirely — not only after the provider's own process dies while
the machine keeps running. Because the provider's durable liveness snapshot
(`§Concepts/Durable liveness snapshot`) survives independently of any one
process's memory, the provider's next startup can compare that snapshot against
current reality and publish the same attributable host-loss observation it
would have published had it merely crashed and restarted in place. A client
built against that observation — the Picker's bulk-resume, for one — never
needs a special case for "the whole machine went away" versus "just this host
process did."

## Behaviors

### host-owns-mechanics-agency-layer-owns-meaning

The provider owns execution mechanics and runtime identity. The agency layer
owns the worktree, objective, claims, head, succession, and disposition. Neither
duplicates the other's authority.

### persist-before-notify

A transition is durable before any endpoint is pinged or observer is expected
to react. Notification loss delays action but does not lose the request.

### snapshot-outlives-the-process

A provider's record of what it is currently hosting is written to storage that
survives the provider's own process ending, not held only in memory. A provider
that never persists this cannot honestly distinguish "nothing was lost" from
"I simply don't remember" after it restarts — the guarantee exists specifically
so that distinction stays truthful across the provider's own worst case, a full
machine restart, not only its process exiting cleanly. Because the persisted
record is only ever as current as its last write, a stale entry is reconciled as
an **unconfirmed** candidate, never asserted as loss outright — the same
honesty-under-staleness the fabric already requires elsewhere
(`§Behaviors/uncertainty-is-marked-not-multiplied` on the agent-worktrees
vision), applied here to the provider's own bookkeeping instead of a
worktree's.

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
- Remote venue extension:
  [remote-interactive-sessions](../remote-interactive-sessions/README.md) — how
  a Session-host-provider's mechanics extend into a remote venue via a CLI mode
  bound through explicit worktree-keyed reservation, rather than a parallel execution
  protocol.

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
- **2026-09-18** — Added §Concepts/*Durable liveness snapshot*,
  §Features/*recoverable-across-full-restart*, and
  §Behaviors/*snapshot-outlives-the-process*. Prompted by a direct follow-up to
  the Picker's newly-added `fleet-recovery-relaunch` bulk-resume feature
  (`copilot-extensions visions/picker`): that feature depends on a provider
  honestly naming which execution legs it lost, but a provider whose own
  process restarted has no memory to name them from unless it had already
  persisted that record somewhere durable. Generalizes the guarantee from
  "survives this host process dying" to "survives the machine it runs on
  restarting entirely" — the harder, and more common in practice, failure mode
  an operator's laptop or any facility machine periodically produces.
