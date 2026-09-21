# Remote Interactive Sessions — Vision

- **Subject:** How a human-attended, muxed, interactive Copilot CLI session
  running in a remote venue (a CodeSpace, a trusted container, or any
  agent-ssh-reachable machine) becomes a first-class, coordinated peer of a
  locally-hosted session — durably registered in the same discovery index a
  directly-spawned Session Host session uses, through explicit reservation
  rather than ambient self-registration, and launched symmetrically by any
  venue provider.
- **Scope:** leaf (cross-cutting capability within the agent fabric)
- **Status:** Active
- **Last revised:** 2026-09-20
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

### CLI mode needs no Session Host process — mux and the extension already do its jobs

A Session Host exists to give a headless `copilot --acp` child two things it
cannot provide itself: **survival** across daemon restarts/reconnects, and an
**ACP transport surface** so the daemon can drive a child with no human
attached. A muxed, interactive CLI session already has both, from different
owners: the multiplexer (tmux/psmux) keeps the `copilot` process alive across
detach/reattach — that is a mux's entire purpose — and the coordination
layer's own CLI extension, already loaded inside that real interactive
process, already registers and participates directly with the daemon (proven
by the existing `send`-admission and inbox-polling mechanics). Spawning a
Session Host to wrap a process two other owners already keep alive and
already speak for would be a second, redundant lifecycle manager — precisely
what this vision's non-goals rule out.

Discoverability, the third thing a Session Host would otherwise provide, is
**already handled correctly** by a different, already-existing mechanism: the
extension's self-registration into the coordination layer's durable
`live_sessions` registry — the same registry every attended (non-daemon-spawned)
interactive session already uses to be observable/messageable, CLI-mode or
not. This is deliberately a *different* map from `host_index` (the daemon's
record of processes *it itself spawned*, addressed by a dialable local port
and polled by `host_pid`/`child_pid` liveness) — a CLI-mode session was never
spawned by the daemon, so it has neither a port nor a host process for that
model to describe. One process, two owners covering survival and control, one
already-correct self-registration path for discovery: no fourth mechanism
needed.

What genuinely still blocks a *remote*-venue CLI-mode session from being
discoverable this same way is narrower and more concrete: the extension
always registers against `http://127.0.0.1:<port>` (its own loopback), and
today only the credential-relay port is reverse-forwarded into a CodeSpace or
container — the daemon's own API port is not. A muxed session launched inside
a venue has no network path back to the host daemon to register at all yet.
Closing that is a transport-provisioning detail (reuse the existing
reverse-forward machinery for a second port), not a data-model change; a
related, equally concrete gap is that `live_sessions` today carries no
venue/reattach descriptor (which CodeSpace/container, what mux session name)
for an operator or the daemon to later act on.

### Worktree-keyed reservation, not ambient self-registration

The coordination layer's CLI-side extension currently reaches back to bind
itself to a daemon by assumption rather than by explicit assignment. The
mechanism is an explicit, operator-created **reservation for a worktree's
next CLI-mode session**, made by the coordination layer before that session
starts. This is sound specifically because the fabric already guarantees
*[single-current-session-per-worktree](../agent-fabric/README.md#single-current-session-per-worktree)*
— at most one current session per worktree — so a worktree-keyed reservation
is never ambiguous. Correlating a registering session against a pending
reservation, and marking the matched `live_sessions` row accordingly, is the
daemon's own responsibility at registration time; an
extension that already resolves and sends its worktree identity needs no
separate lookup step to participate. Only once a *remote* venue's own local
daemon differs from the host machine's does an explicit, client-read
discovery step become necessary — the mechanism generalizes without changing
this concept's identity unit.

### Symmetric venue launch of standard muxed sessions

Creating a muxed, interactive Copilot CLI session is not a capability
exclusive to the local worktree/mux launch path. Any venue provider —
`agent-codespaces`, `agent-containers`, or a future `agent-ssh`-reachable
machine — can offer the same launch shape: prepare the venue, allocate the
paired CLI-mode reservation, and start a standard muxed CLI process bound to
it, self-registering into `live_sessions` with a reattach descriptor that
knows how to reach that venue over a daemon-port reverse forward set up as
part of the same launch. This rides the single SSH transport and auth-relay
back-channel [venue-parity](../venue-parity/README.md) already establishes
for headless dispatch; it does not require or invent a second,
venue-specific transport for the interactive case.

**One verb name, everywhere: `copilot`.** "Deliver a TTY Copilot session to
the user in the current terminal" is a single, unambiguous action —
`agent-worktrees copilot` performs it locally (ensure a durable mux'd
session exists, then hand this process's own terminal to it via exec);
`agent-codespaces copilot <name>` / `agent-containers copilot <name>`
perform the *identical* action for a remote venue by preparing it (the
venue-specific "setup" step: reverse forwards, credentials, the reservation)
and then running that same local `copilot` command remotely over an
interactive SSH channel. "Interactive," reused elsewhere in this vocabulary
for at least three different meanings (has a TTY, has a picker UI, is a
muxed human-attended session), is deliberately retired in favor of this one
named action. This is also the primitive the Worktree Picker's own
CodeSpaces/containers navigation invokes on "Open" (or "create a new
session" for an idle venue) — the CLI ergonomics of the verb itself matter
less than it being one cleanly scriptable action any caller (a human typing
it directly, or Picker driving it) can invoke the same way.

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

### worktree-keyed-host-discovery

A registering session's binding to its CLI-mode designation is resolved from
an explicit reservation keyed by worktree identity, correlated at
registration time in the same durable `live_sessions` registry every
attended session is found through — not inferred from an ambient default,
and not tracked in a second, parallel discovery mechanism.

### symmetric-muxed-venue-launch

`agent-codespaces` and `agent-containers` can create a standard, muxed
interactive Copilot CLI session bound to a pre-allocated reservation, the same
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

Exactly one Session Host reservation may be active for a given worktree at a
time, consistent with *single-current-session-per-worktree*; creating a
second reservation while one is still unexpired never resolves ambiguously.

### allocate-before-launch

The coordination layer allocates and prepares a CLI-mode reservation before
the paired muxed CLI process starts, so a registering session always has
something to correlate against by the time it registers.

### bind-dont-self-register

An extension registering with its ambient local daemon does not itself decide
whether it is CLI-mode; the daemon correlates the registration against any
pending worktree reservation server-side. Nothing about a session's own
behavior changes based on a guess about which mode it's in.

### short-lived-event-based-extension

The CLI-side extension itself stays a fast, event-driven actor: it performs
quick, non-blocking discovery lookups and handoffs, then delegates all actual
connection ownership, state, and longer-running work to agent-bridge's daemon
or the Session Host process. It never holds open a long-running connection,
watch, or blocking operation in the extension-host process — doing so risks
the extension-host locking its own plugin directory.

### no-duplicate-lifecycle-machinery

A CLI-mode-bound session reuses the exact `live_sessions` registration,
messaging, and observation machinery any other attended session uses —
never a second registry or a forked protocol, and never wrapped in a
Session Host it does not need.

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
- **Not a second Session Host process or discovery mechanism.** CLI mode
  spawns no wrapping host process — the multiplexer and the already-loaded
  extension already provide a Session Host's survival and daemon-facing
  duties — and it discovers through `live_sessions`, the same registry any
  attended session already uses, never a parallel index or lifecycle.
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

- **2026-09-20** — Named the canonical local/remote launch verb `copilot`
  ("deliver a TTY Copilot session to the user in the current terminal"),
  replacing the ambiguous "interactive" vocabulary used elsewhere for at
  least three different meanings. `agent-worktrees copilot` is the
  foundational local case (ensure-then-attach, landed); `agent-codespaces`/
  `agent-containers copilot <name>` reuse it verbatim over an interactive
  SSH channel rather than reimplementing attach logic per venue. Also
  clarified that this verb's primary caller is expected to be the Worktree
  Picker's own CodeSpaces/containers navigation (already exists as an
  account-scoped pivot in `worktree-manager`'s Picker TUI) invoking it on
  "Open"/"create a new session," not a human typing it directly most of the
  time -- the verb's scriptability (clean JSON/exit-code contract from the
  `embody` logic it wraps) matters more than CLI ergonomics polish.
- **2026-09-20** — Same-day correction to the entry directly below: the
  `host_index` framing was itself wrong, found while starting the
  implementation follow-up it called for. `host_index` is specifically the
  daemon's map of processes **it spawned** (a dialable local port,
  `host_pid`/`child_pid` liveness) — a CLI-mode session was never spawned by
  the daemon, so it has neither a port nor a host process for that model to
  describe. The self-registration a CLI-mode session already performs writes
  into `live_sessions` — a different, already-existing, already-correct
  discovery registry every attended (non-daemon-spawned) session uses, CLI
  mode included. Phase 2 got the *mechanism* choice right the first time;
  there was no discovery mechanism to unify. What genuinely still blocks a
  *remote*-venue CLI-mode session is narrower: the extension always resolves
  its daemon at `127.0.0.1`, and only the credential-relay port is currently
  reverse-forwarded into a venue -- the daemon's own API port is not, so a
  remote CLI-mode session has no network path back to register at all yet.
  A related, separate gap: `live_sessions` carries no venue/reattach
  descriptor today. Both are transport/schema additions tracked in the
  realizing effort, not a `host_index` unification.
- **2026-09-20** — Course-corrected "Session Host CLI mode" after operator
  challenge to a hypothetical "always spawn a real Session Host, even
  locally" reading of Phase 2/3's implementation gap. Neither extreme is
  right: Session Host's two other jobs (survival across
  reconnect/restart, and an ACP transport surface so the daemon can drive a
  headless child) are already fully provided, for a CLI-mode session, by its
  two real owners — the multiplexer (survival) and the already-loaded CLI
  extension (direct daemon participation, proven since Phase 1). Spawning a
  Session Host to wrap a process two other owners already keep alive and
  already speak for would itself be the redundant "second host type" this
  vision's non-goals rule out. What Phase 2/3 actually left missing was
  Session Host's *third* job — durable discoverability through `host_index` —
  which CLI mode instead reimplemented as a bespoke, parallel
  `cli_mode_reservations`-only correlation. Corrected the concept: CLI mode
  spawns no host process, but a claimed reservation is promoted into a real
  `host_index` registration (a second, honest record shape — mux-reattach
  instead of a dialable port — in the *same* index), so discovery is unified
  across ACP and CLI mode, and across every venue, without a second index or
  a redundant process. Implementation follow-up (extending `HostRecord`/
  `host_index.py` with a CLI-mode record shape and mux-liveness check, and
  promoting the Phase 2 reservation-claim into a real registration) is
  tracked in the realizing effort, reopening parts of already-merged Phase 2/3
  work rather than treating them as closed.
  **(Superseded same-day by the entry above — the `host_index` target was
  wrong; kept for the record of what was actually tried.)**
- **2026-09-19** — Refined "cwd-keyed discovery" to **worktree-id-keyed
  reservation + server-side correlation** after implementing Phase 2: the
  fabric's own `single-current-session-per-worktree` identity unit is the
  worktree, not a raw filesystem path, and for the local (single-machine)
  case a registering extension already sends its worktree identity — so no
  client-side discovery-file mechanism is needed at all; the daemon
  correlates a registration against a pending reservation entirely
  server-side. A client-read discovery step remains the right mechanism once
  a remote venue's own daemon differs from the host's (Phase 4).
- **2026-09-19** — Authored from operator direction reconciling a proposal to
  add a parallel bridge-owned "native execution"/PTY protocol for
  human-attended remote sessions (raw-terminal projection, writer/observer
  takeover, and a bespoke host-resource ensure/release RPC) with the fact that
  the coordination layer's existing ACP/Session Host boundary already supplies
  durable reattach, multi-observer coordination, and structured observation —
  and that its own CLI extension already (awkwardly) self-registers with a
  local daemon. The generalization mined here: give the coordination layer a
  CLI mode of its existing Session Host, resolved by explicit, worktree-keyed
  correlation instead of ambient self-registration; and let venue providers
  launch such sessions symmetrically over the transport venue-parity already
  establishes.
- **2026-09-19** — Split the pluggable local-capability generalization
  (credential-relay-shaped host-resource providers) out to its own
  [host-resource-providers](../host-resource-providers/README.md) vision, and
  marked CLI mode as an explicit, per-request operator choice rather than an
  ambient default — driving remote CLI-mode sessions and provisioning local
  resources to them are independent concerns that should not gate each other.
