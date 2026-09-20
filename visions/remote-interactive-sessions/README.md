# Remote Interactive Sessions — Vision

- **Subject:** How a human-attended, muxed, interactive Copilot CLI session
  running in a remote venue (a CodeSpace, a trusted container, or any
  agent-ssh-reachable machine) becomes a first-class, coordinated peer of a
  locally-hosted session — bound to its Session Host through explicit
  discovery rather than ambient self-registration, and launched symmetrically
  by any venue provider.
- **Scope:** leaf (cross-cutting capability within the agent fabric)
- **Status:** Active
- **Last revised:** 2026-09-19
- **Reality docs:** [`plugins/agent-bridge/docs/architecture.md`](../../plugins/agent-bridge/docs/architecture.md) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../plugins/agent-worktrees/docs/architecture.md)

## Purpose & Intent

[session-hosting](../session-hosting/README.md) already establishes that many
execution rigs — CLI/mux, ACP, SDK, application, third-party — can host a
Copilot session as peer providers under one durable agency model. What it does
not yet resolve is **where** that hosting happens: today a muxed, interactive
CLI session is a *local* affordance (agent-worktrees/Worktree Manager), while a
*remote* venue (a CodeSpace or trusted container) is reached only through
[venue-parity](../venue-parity/README.md)'s headless, agent-bridge-driven
dispatch core. An operator who wants the same muxed, interactive, attended
experience they get locally — but running in a remote venue, with the same
reattach, observation, and coordination guarantees — has no first-class path to
it, and the coordination layer's own CLI-side extension currently resolves
which daemon to register with **ambiently** rather than through an explicit,
discoverable assignment.

The north star: a remote venue is simply another place a **standard, muxed
Copilot CLI session** can run, coordinated by the existing hosting boundary
exactly as a local one is — never a second, parallel execution protocol
invented to carry interactive access over a network boundary. The coordination
layer **pre-allocates** the execution home a launching CLI session will bind
to; the CLI's own extension **discovers and binds** to that specific
assignment rather than defaulting to an ambient daemon; and venue providers
symmetrically gain the ability to **launch** such a session, the same way the
local worktree/mux launch path already does, riding the single uniform SSH
substrate venue-parity establishes. Once bound, the session is ordinary,
coordinated, reattachable, multi-observer work — no new vocabulary, no
duplicated retirement or replay machinery.

This is deliberately an **explicit, per-request mode**, not a default execution
shape most agents should reach for. Ordinary delegated/headless work continues
through the coordination layer's existing ACP-driven path; CLI mode exists for
the operator who specifically wants an attended, muxed, interactive session in
a remote venue.

Reaching **local capabilities** (a browser profile, local configuration, and
similar) from such a session is a related but independent concern, owned by
[host-resource-providers](../host-resource-providers/README.md); this vision
does not depend on it.

## Concepts & Components

### Session Host CLI mode

A **mode** of the existing Session Host (see
[session-hosting](../session-hosting/README.md) § *Session-host provider*),
not a second host type. In its ordinary mode, a Session Host spawns and owns a
`copilot --acp` child directly. In CLI mode, the coordination layer allocates
and prepares a Session Host **first**, then leaves it waiting to be claimed by
an independently launched, muxed, interactive CLI process rather than spawning
that process itself. The two modes share every downstream mechanic —
reattachable transport, connect-nonce identity, process survival, retirement —
so a CLI-mode-bound session and a directly-spawned one are indistinguishable to
every consumer above the host boundary.

### CWD-keyed discovery, not ambient self-registration

The coordination layer's CLI-side extension currently reaches back to bind
itself to a daemon by assumption rather than by explicit assignment. The
target state is a durable, host-local mapping from **a working directory to
its currently assigned Session Host address**, populated by the coordination
layer at allocation time and read by the extension at startup. This is sound
specifically because the fabric already guarantees
*[single-current-session-per-worktree](../agent-fabric/README.md#single-current-session-per-worktree)*
— at most one current session per working directory — so a cwd-keyed
assignment is never ambiguous. An extension that finds no assignment for its
cwd is not silently adopted by an arbitrary daemon; it either declines or falls
back to today's ambient behavior only as an explicitly inferior, honestly
labeled compatibility path.

### Symmetric venue launch of standard muxed sessions

Creating a muxed, interactive Copilot CLI session is not a capability
exclusive to the local worktree/mux launch path. Any venue provider —
`agent-codespaces`, `agent-containers`, or a future `agent-ssh`-reachable
machine — can offer the same launch shape: prepare the venue, allocate the
paired CLI-mode Session Host, and start a standard muxed CLI process bound to
it. This rides the single SSH transport and auth-relay back-channel
[venue-parity](../venue-parity/README.md) already establishes for headless
dispatch; it does not require or invent a second, venue-specific transport for
the interactive case.

### Human-attended, honestly marked

A session bound through CLI mode carries a durable marker distinguishing it as
human-attended/mux-launched, as opposed to a headless, fully agent-bridge-owned
execution. This is not a lesser session — it is a different **honesty
contract**: recovery and observation guidance for a marked session may tell an
operator to reconnect and look directly (over the same reachable transport)
rather than promising an automated recovery equivalent to a headless session's.

## Features

### remote-cli-sessions-as-first-class-peers

A Copilot CLI session started interactively inside a remote venue is
coordinated exactly like a locally-hosted one: the same reattach, the same
multi-observer model, the same durable identity. No separate execution
protocol, terminal-replay format, or resource-ownership vocabulary is invented
to carry this over a network boundary.

### cwd-keyed-host-discovery

An extension's binding to its Session Host is resolved from an explicit,
durable cwd-to-host assignment made by the coordination layer at allocation
time, not inferred from an ambient default.

### symmetric-muxed-venue-launch

`agent-codespaces` and `agent-containers` can create a standard, muxed
interactive Copilot CLI session bound to a pre-allocated Session Host, the same
way the local worktree/mux launch path already does, as thin transports over
one shared SSH substrate.

### capability-honest-attended-marking

A CLI-mode-bound session is durably distinguishable from a headless one, so
recovery and observation guidance can honestly nudge an operator toward direct
reconnection rather than claiming automated equivalence it cannot honestly
provide.

### explicit-per-request-mode

CLI mode is requested deliberately by an operator for a specific session, not a
default execution shape the coordination layer or a delegating agent reaches
for on its own. Ordinary headless/delegated work is unaffected and continues
through the existing ACP-driven path.

## Behaviors

### one-host-per-cwd-lane

Exactly one Session Host may be assigned to a given working directory at a
time, consistent with *single-current-session-per-worktree*; a discovery
lookup for a cwd never resolves ambiguously.

### allocate-before-launch

The coordination layer allocates and prepares a CLI-mode Session Host before
the paired muxed CLI process starts, so a discovery assignment is guaranteed to
exist by the time the extension looks for one.

### bind-dont-self-register

An extension resolves and binds to its explicitly assigned host through
discovery. It does not default to registering with an arbitrary ambient
daemon merely because one is reachable.

### short-lived-event-based-extension

The CLI-side extension itself stays a fast, event-driven actor: it performs
quick, non-blocking discovery lookups and handoffs, then delegates all actual
connection ownership, state, and longer-running work to agent-bridge's daemon
or the Session Host process. It never holds open a long-running connection,
watch, or blocking operation in the extension-host process — doing so risks
the extension-host locking its own plugin directory.

### no-duplicate-lifecycle-machinery

A CLI-mode-bound session reuses the exact reattach, observation, and
retirement machinery any other Session Host-hosted session uses. Reaching it
from a remote venue is a matter of transport and discovery, never a forked
protocol.

### opt-in-not-ambient-default

CLI mode is only ever entered on an explicit request naming the venue/cwd, and
is never selected automatically as a substitute for headless delegation, a
fallback when ACP admission is inconvenient, or a default a coordinating agent
picks for itself.

### degrade-to-direct-reconnect-honestly

When binding or coordination hiccups on a human-attended session, guidance
nudges the operator to reconnect over the same reachable transport and look
directly, rather than asserting an automated recovery guarantee that a
headless session's mechanics do not actually extend to an attended one.

## Non-Goals / Boundaries

- **Not a new execution or terminal protocol.** Interactive access to a remote
  session rides the existing ACP/Session Host boundary and its established
  reattach/observation/multi-client mechanics. This vision does not add a
  parallel raw-terminal replay format, a separate writer/observer protocol, or
  a competing execution-identity model alongside the one session-hosting
  already defines.
- **Not a second Session Host implementation.** CLI mode is a mode of the one
  Session Host, sharing its every downstream mechanic — not a new host type
  with its own lifecycle.
- **Not reaching local capabilities from the remote session.** Whether and how
  a CLI-mode session reaches locally-provided resources (a browser profile,
  local configuration, etc.) is
  [host-resource-providers](../host-resource-providers/README.md)'s independent
  concern; this vision does not depend on it, and CLI mode is fully meaningful
  without it.
- **Not an ambient or default execution mode.** CLI mode exists for an
  operator's explicit, per-request choice of an attended remote session. It is
  never a fallback path a coordinating agent selects on its own in place of
  ordinary headless delegation.
- **Not an exemption from single-current-session-per-worktree.** A CLI-mode
  launch is gated by the same reuse/hand-off/sunset resolution as any other
  session start; it does not open a side door around that invariant.
- **Not a specification.** This vision fixes ownership, discovery, and
  guarantees — not the concrete discovery-file format, port/socket wiring, or
  command grammar. That detail belongs to the effort that realizes it and to
  the reality docs.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Sibling vision: [session-hosting](../session-hosting/README.md) — the
  provider-neutral hosting boundary this vision extends into remote venues,
  and the origin of the Session-host-provider and
  single-current-session-per-worktree concepts this vision builds on.
- Sibling vision: [venue-parity](../venue-parity/README.md) — the thin,
  symmetric SSH transport and auth-relay back-channel this vision's venue
  launch rides rather than duplicates.
- Related vision: [host-resource-providers](../host-resource-providers/README.md) —
  the independent, related concern of a CLI-mode (or any) session reaching
  locally-provided capabilities; this vision neither depends on nor blocks it.
- Cross-cutting vision: [native-convergence](../native-convergence/README.md) —
  the same delegate-the-primitive / no-capability-regression discipline this
  vision applies to converging remote interactive access onto the existing
  hosting boundary rather than forking a parallel one.
- Child visions: none (leaf).

## Provenance

- **2026-09-19** — Authored from operator direction reconciling a proposal to
  add a parallel bridge-owned "native execution"/PTY protocol for
  human-attended remote sessions (raw-terminal projection, writer/observer
  takeover, and a bespoke host-resource ensure/release RPC) with the fact that
  the coordination layer's existing ACP/Session Host boundary already supplies
  durable reattach, multi-observer coordination, and structured observation —
  and that its own CLI extension already (awkwardly) self-registers with a
  local daemon. The generalization mined here: give the coordination layer a
  CLI mode of its existing Session Host, resolved by explicit cwd-keyed
  discovery instead of ambient self-registration; and let venue providers
  launch such sessions symmetrically over the transport venue-parity already
  establishes.
- **2026-09-19** — Split the pluggable local-capability generalization
  (credential-relay-shaped host-resource providers) out to its own
  [host-resource-providers](../host-resource-providers/README.md) vision, and
  marked CLI mode as an explicit, per-request operator choice rather than an
  ambient default — driving remote CLI-mode sessions and provisioning local
  resources to them are independent concerns that should not gate each other.
