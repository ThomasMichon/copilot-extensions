# Remote Interactive Sessions — Vision

- **Subject:** How a human-attachable, muxed, interactive Copilot CLI session
  running in a remote venue (a CodeSpace, a trusted container, or any
  agent-ssh-reachable machine) becomes a first-class, coordinated peer of a
  locally-hosted session — durably registered in the same discovery index a
  directly-spawned Session Host session uses, through explicit reservation
  rather than ambient self-registration, and launched symmetrically by any
  venue provider.
- **Scope:** leaf (cross-cutting capability within the agent fabric)
- **Status:** Active
- **Last revised:** 2026-11-05
- **Reality docs:** [`plugins/agent-bridge/docs/architecture.md`](../../plugins/agent-bridge/docs/architecture.md) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../plugins/agent-worktrees/docs/architecture.md)

## Purpose & Intent

[session-hosting](../session-hosting/README.md) defines peer execution providers
under one durable agency model. The [Copilot Driver](../plugins/agent-remote-driver/README.md)
supplies the minimal direct-Copilot provider independently of coordination
plugins: its Session Host brokers both headless children and interactive mux
sessions whose selected, launch-injected extension connects to it.

A remote venue is another place that same **standard, muxed Copilot CLI
session** can run. The caller reserves execution before launch; the Driver
binds the resulting native session to that reservation and exposes the common
ACP-compatible broker endpoint with port, process and session information.
Venue providers prepare and reach the target rather than carry their own
Session Host or mux-launch implementation. Terminal attachment, programmatic
control and durable work remain separate identities with one execution owner.

SSH-specific forwarding and TTY launch shapes below apply to SSH-backed venues.
An optional [machine-fleet Gateway](../machine-fleet/README.md) does not gain
interactive-session capability merely by exposing daemon/task control. A provider
offering interactive Gateway reach must explicitly satisfy this same reservation,
launch, interaction, reattach, credentials, observation, and retirement contract;
unsupported interaction remains visible rather than silently falling back to SSH.

Creation/control and terminal attachment need not use the same transport.
After a Gateway-routed request creates a target-owned session, the caller can
choose SSH attachment or a supported terminal-only host channel and lightweight
renderer. Both attach to the same execution and reservation identity; neither
creates a parallel agent lifecycle or makes the Gateway the session host.

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

### Session Host brokers headed and headless execution

Headless mode has Session Host directly own Copilot and its ACP connection.
Headed mode has the Driver's mux wrap interactive Copilot while its selected
extension connects to Session Host. The broker is not another Copilot process
or a rival terminal owner: it supplies the same programmatic endpoint,
reservation, native identity and control boundary for either presentation.

The selected redistributable carries this receiving support without requiring
Bridge, Worktrees or Manager on the target. Bridge can coordinate or represent
the session as a client; its ledger does not replace the Driver's execution
record. Existing venue transport and credential owners provide reachability,
without making a particular host-side daemon or reverse forward mandatory for
every backend.

### Worktree-keyed reservation, not ambient self-registration

The mechanism is an explicit, authorized **reservation for a worktree's next
CLI-mode session**, carried by the launching caller into the Driver before
that session starts. This is sound specifically because the fabric guarantees
*[single-current-session-per-worktree](../agent-fabric/README.md#single-current-session-per-worktree)*
— at most one current session per worktree — so a worktree-keyed reservation
is never ambiguous. The Driver binds its native session to that reservation;
coordination clients retain the relationship to their worktree rather than
making a Bridge registry mandatory for standalone execution.

One host coordination layer can see *many* venues of the same repository at
once (several CodeSpaces each running their own anchor session), so a venue
launch qualifies the identity it reserves and registers under with the venue
(`<worktree identity>@<venue>`). Within a venue the unit is still the worktree;
the qualifier only keeps sibling venues distinct at the host. The reservation
also carries the venue descriptor (which venue, which mux session), so the
claimed registration inherits trusted reattach metadata instead of asserting
its own.

### Symmetric venue launch of standard muxed sessions

Creating a muxed, interactive Copilot CLI session is not a capability
exclusive to the local worktree/mux launch path. Any venue provider —
`agent-codespaces`, `agent-containers`, or a future `agent-ssh`-reachable
machine — can offer the same launch shape: prepare the venue, allocate the
paired CLI-mode reservation, and start a standard muxed CLI process bound to
it, with a reattach descriptor that reaches the Driver's broker and terminal
over the provider's selected channel. This rides the transport and auth-relay contract
[venue-parity](../venue-parity/README.md) already establishes
for headless dispatch; it does not require or invent a second,
venue-specific transport for the interactive case.

Local CLIs, remote venue adapters and Picker share the same ensure, launch and
attach semantics as clients of the Driver. Human terminal attachment and
detached programmatic launch are explicit outcomes, not duplicated wrappers
or a requirement that a receiving Worktrees command be installed.

### Human-attachable, honestly marked

A session bound through CLI mode carries a durable marker distinguishing it as
human-attachable/mux-launched, as opposed to headless Driver-owned execution.
A headed marker does not assert that a human is currently attached. This is a
different **honesty contract**: recovery and observation guidance may tell an
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

A session's binding to its CLI-mode designation comes from its explicit
Driver reservation, qualified by the caller's worktree and venue where
applicable. Coordination can project that binding without replacing the
broker's execution record or inferring ownership from an ambient daemon.

### symmetric-muxed-venue-launch

`agent-codespaces` and `agent-containers` can create a standard, muxed
interactive Copilot CLI session bound to a pre-allocated reservation, the same
way a local client invokes the Driver, as thin transports over
the selected shared connectivity substrate. The same launch is available in a **detached** form
for a programmatic caller (an orchestrating agent that must not hand its own
terminal away): it returns a scriptable handle once the session is registered,
and the connection's durable owner keeps the session's back-channels alive for
as long as the muxed session exists — the session stays an ordinary,
attachable, messageable peer, not a new execution shape.

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

The launch-injected extension binds to its assigned Driver Session Host, not an
ambient coordination daemon. The broker correlates native identity with the
explicit execution reservation; an extension cannot choose a new owner merely
by registering itself.

### short-lived-event-based-extension

The CLI-side extension itself stays a fast, event-driven actor: it performs
quick, non-blocking discovery lookups and handoffs, then delegates all actual
connection ownership, retained state, and longer-running lifecycle work to the
Driver's Session Host. Broker communication stays asynchronous and bounded;
SDK callbacks never block on remote work or retain a caller's installer lifetime.

### no-duplicate-lifecycle-machinery

A CLI-mode-bound session uses the Driver's common Session Host broker and
execution record rather than a second Copilot conversation, copied mux
launcher or competing lifecycle owner. Coordination representation remains
a client view, not another authoritative execution registry.

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
- **Not a rival lifecycle or conversation.** The headed Driver's Session Host
  brokers its extension-connected mux session; it does not start a second
  Copilot or take terminal ownership from the mux.
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
- Execution app: [Copilot Driver](../plugins/agent-remote-driver/README.md) —
  owns the common broker and the hoistable headed/headless receiving package.
- Sibling vision: [venue-parity](../venue-parity/README.md) — the thin,
  symmetric selected transport and auth-relay contract this vision's venue
  launch rides rather than duplicates.
- Related vision: [plugins/agent-codespaces](../plugins/agent-codespaces/README.md) —
  owns the venue-launch primitive itself (`copilot <name>` /
  `copilot <name> --detach`, and the host/venue port bridging it rides);
  this vision owns the should-be contract for the coordination-layer
  integration (worktree-keyed reservation, `live_sessions` discoverability,
  honest marking) that integration already realizes end to end.
- Related vision: [host-resource-providers](../host-resource-providers/README.md) —
  the independent, related concern of a CLI-mode (or any) session reaching
  locally-provided capabilities; this vision neither depends on nor blocks it.
- Cross-cutting vision: [native-convergence](../native-convergence/README.md) —
  the same delegate-the-primitive / no-capability-regression discipline this
  vision applies to converging remote interactive access onto the existing
  hosting boundary rather than forking a parallel one.
- Child visions: none (leaf).

## Provenance

- **2026-10-07** — Corrected a stale "narrower, concrete gap" claim in the
  "CLI mode needs no Session Host" concept section and in this vision's own
  2026-10-06 Purpose & Intent revision below: both still described the
  daemon-port reverse-forward and `live_sessions` registration for a
  venue-launched CLI-mode session as **not yet done**. Checked against the
  owning effort (`efforts/active/agent-bridge-cli-mode-sessions/README.md`
  Phase 4/Validation Plan) and the actual `copilot_venue.py` code in both
  `agent-codespaces` and `agent-containers`: this was landed and
  live-clean-room-validated on **2026-09-20 through 09-22**, before the
  10-06 revision was even written — a real attached `copilot` session
  already places a worktree-keyed reservation, carries a daemon-port
  reverse-forward alongside the credential-relay one on one standing
  connection, and self-registers into `live_sessions` honestly marked
  CLI-mode-driven. The 10-06 revision preserved a stale claim instead of
  verifying it against the owning effort/code; this entry corrects both the
  Purpose & Intent framing and the Concepts section to state the
  integration as realized, scoped the remaining open work to Phase 5's docs
  sync and the separate Picker integration, and reworded the See Also
  cross-link to agent-codespaces accordingly. No new intent was added,
  only a stale current-state claim corrected a second time — this time
  verified against code and the owning effort rather than against another
  vision's own (also-stale) text.

- **2026-10-06** — Corrected a stale Purpose & Intent claim: it still said
  a remote venue "is reached only through venue-parity's headless,
  agent-bridge-driven dispatch core" and an operator "has no first-class
  path" to an attended remote session, when the venue-launch primitive
  itself (`copilot <name>` / `copilot <name> --detach`) was already added
  by the 2026-09-20/09-22 entries below and is now stated as shipped in
  [agent-codespaces](../plugins/agent-codespaces/README.md)'s own vision.
  Reworded the opening framing to name that primitive as already landed and
  scope this vision's remaining job precisely to the coordination-layer
  integration (reservation, `live_sessions` discoverability, honest
  marking) — the same narrower gap the "CLI mode needs no Session Host"
  concept section already stated correctly; no new intent was added, only
  a stale current-state claim corrected to match this vision's own later
  history. Added a See Also cross-link to agent-codespaces' vision for the
  ownership split this correction states. **Superseded by the entry
  above:** the "coordination-layer integration... remaining" framing this
  entry introduced was itself stale, not yet verified against the owning
  effort/code.

- **2026-09-22** — Added the detached, programmatic form of the venue
  `copilot` launch (operator direction: an orchestrating session dispatches
  work to remote CLI-mode sessions it can observe and steer, instead of opaque
  headless sub-dispatch). Still explicit and per-request (the operator's
  orchestrator asks for it; never a fallback for headless delegation) and still
  the same `live_sessions` registration/messaging/observation machinery. Two
  refinements it forced: the reserved/registered identity is venue-qualified
  (sibling venues of one repo are otherwise indistinguishable at the host), and
  the venue descriptor travels on the reservation rather than from the
  registering client. Keeping a detached session's back-channels (credential
  relay + the host daemon port) alive after its launcher exits is the existing
  per-venue connection owner's job, renewed from its own venue-side liveness
  check — transport stays transport; it never reads session state.

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
