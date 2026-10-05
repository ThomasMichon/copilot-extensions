# Venue Parity — Vision

- **Subject:** The dispatch **venue** layer — how the coordination layer launches, reaches, authenticates, and monitors a Copilot agent in a remote venue (a GitHub CodeSpace, a local Docker container, or a directly SSH-registered machine/local checkout), via the `agent-codespaces`, `agent-containers`, and static-registry SSH/local dispatch paths.
- **Scope:** leaf (cross-cutting capability across the venue providers)
- **Status:** Active
- **Last revised:** 2026-10-05
- **Reality docs:** [`docs/architecture.md`](../../docs/architecture.md)
- **Parent vision:** [agent-fabric](../agent-fabric/README.md)

## Purpose & Intent

A dispatched Copilot agent should be **the same agent** — same context, model,
skills, working directory, credentials, monitoring, and failure semantics —
**regardless of the venue it runs in**. Whether the fabric lands it in a cloud
CodeSpace or a local Docker container should be an implementation detail of
*where the compute lives*, not a difference in *what the agent is or how well it
works*.

The venues are therefore **thin, symmetric transports** over one shared dispatch
core owned by the **coordination layer (agent-bridge)**. Everything
venue-agnostic — model/effort/context propagation, in-repo skill and
`--plugin-dir` resolution, concrete working directory, session/status/
coordination, and the auth/relay bootstrap — lives **once**, in the core. Each
venue provider contributes only what is genuinely particular to its substrate:
how a venue is provisioned and its lifecycle managed, how a GitHub token is
bootstrapped, and (for CodeSpaces only) cold-boot from an idle/sleep state.

Because a **local container** is cheap, bounded only by local disk and memory,
and can be driven into arbitrary test and repro states, it becomes the fabric's
**first-class parity harness**: every dispatch flow that matters in a CodeSpace
is reproduced and *hardened in a container first*, then trusted in the more
costly, less controllable cloud venue. Parity is what makes that substitution
sound — a bug fixed against a container is a bug fixed everywhere.

**Parity applies to _trusted_ venues.** CodeSpaces are inherently trusted, and a
**trusted container fleet** is their local peer — it receives the full harness
projection (launch parity, the repo's own local-marketplace plugins, the
credential relay, and eventually container-local worktrees + multi-repo). An
**untrusted/restricted container** is a different mode: a bounded,
deny-by-construction sandbox where the provider mostly wrangles the container
runtime and offers an à-la-carte tool surface, and the host agent + scenario
decide what to use. Untrusted venues are **out of the parity scope by design**;
the trust model and both postures are owned by the
[agent-containers vision](plugins/agent-containers/README.md).

## Concepts & Components

- **The coordination layer — the venue-agnostic dispatch core.** agent-bridge
  already owns the daemon, the session lifecycle, the credential-relay server,
  and the plugin **resolution** logic. It also owns every venue-agnostic *launch*
  concern: propagating the host's model/effort/context to the dispatched agent,
  resolving a repo's own in-repo (`.ai`/`.claude`) skills and other
  `--plugin-dir`s, landing the agent in a concrete working directory, and
  monitoring/coordinating the resulting session. The core computes these once;
  venues carry them.

- **The venue transport contract.** A small, symmetric interface every venue
  provider implements. Its surface is only the genuinely venue-specific concerns:
  - **Lifecycle** — provision, start, stop, and remove a venue (`gh codespace`
    for CodeSpaces; `docker` for containers).
  - **An SSH endpoint** — *every* venue is reached over SSH. A container exposes
    SSH just as a CodeSpace does, so the transport the core drives is identical.
  - **GitHub-token bootstrap** — a CodeSpace is issued a `GITHUB_TOKEN`
    automatically; a container must have one bootstrapped. The core consumes a
    ready token; the venue supplies it.
  - **Boot semantics** — a CodeSpace may be idle/sleeping and cold-boot on first
    connect; a local container simply starts. The core tolerates the wait a
    venue declares.

- **One auth-relay back-channel, over SSH.** The credential relay is reached the
  **same way from every venue**: over the SSH reverse-forward (`-R`) from the
  venue back to the host relay. There is a single back-channel and a single
  relay-reach code path — not a per-venue transport (no venue-specific host-
  gateway TCP hop). Auth "just works" in a container exactly as it does in a
  CodeSpace because it travels the identical channel.

- **The container venue as parity/repro harness.** Local containers are the
  controllable substrate for reproducing and hardening venue flows: put them into
  broken, stale, cold, or adversarial states cheaply; validate a fix; and trust
  that the same fix holds in a CodeSpace because the code path is shared.

- **Symmetric, thin venue providers.** `agent-codespaces` and `agent-containers`
  shrink toward the same shape: lifecycle + SSH endpoint + token bootstrap +
  boot semantics, and nothing else. Shared launch/session/auth logic is not
  duplicated between them.

- **Static-registry SSH/local dispatch is a venue too, not a side door.**
  `agent-bridge`'s static-registry resolution (a bare or `machine/`-qualified
  agent name, as opposed to a `codespace:`/`container:`-namespaced one) builds
  its `SpawnTarget` directly, outside the `codespace:`/`container:` namespace-
  resolver contract described above. It has its own two sub-shapes:
  - **Local loopback** — the resolved machine is the dispatching machine
    itself; the target is reclassified `type="local"` and reached without a
    network hop at all.
  - **Genuine remote SSH** — a different machine, reached over the same SSH
    transport this vision already mandates, but with the remote `copilot
    --acp` invocation assembled and exec'd by **that target project's own
    `agent-worktrees`-generated binstub on the remote host**, not composed
    by the coordination layer the way a CodeSpace/container launch command
    is.

  Both sub-shapes are owed the same venue-agnostic-launch guarantee as
  `codespace:`/`container:` targets — a dispatched agent should carry the
  same resolved plugins regardless of *how* it was addressed — but today
  neither fully receives it (see the `plugin-dir-parity-for-static-targets`
  feature below).

## Features

### venue-agnostic-launch
The dispatched agent inherits the host's **model, reasoning effort, and context
tier**, its resolved **in-repo skills / `--plugin-dir`s**, and a **concrete
working directory** — computed by the core and applied identically in every
venue. Plugins that explicitly target *operating within a venue* (an in-context
venue-agent plugin) remain venue-scoped and are layered on top.

### single-ssh-transport
Every venue is reached over **one SSH transport**. A container provides an SSH
endpoint just as a CodeSpace does, so dispatch, interactive reach, and staging
run over the same channel with no venue-specific transport code.

### unified-auth-relay-back-channel
Credentials are relayed over a **single back-channel** — the SSH reverse-forward
to the host relay — for all venues. One relay-reach path serves ADO, Azure, and
GitHub auth in any venue.

### token-bootstrap-abstraction
GitHub-token acquisition is a venue responsibility behind a uniform seam: a
CodeSpace surfaces its issued token; a container has one bootstrapped for it. The
core never branches on venue to obtain a token.

### container-parity-harness
The local container venue is a supported, first-class way to **reproduce and
harden** any venue dispatch flow, using arbitrary local test/repro states, ahead
of exercising the same flow in a CodeSpace.

### symmetric-thin-venues
`agent-codespaces` and `agent-containers` expose the same contract and share all
non-venue-specific logic; neither carries a private copy of launch, session,
auth, or coordination code.

### plugin-dir-parity-for-static-targets
A dispatched agent's resolved `--plugin-dir` set — its target repo's own
enabled `.ai`/`.claude` plugins, **and** any control-repo-declared related
plugin (`related_plugins_for_repo`) — is the same regardless of whether the
target was addressed as `codespace:<name>`, `container:<name>`, a bare
static-registry agent name resolving to local loopback, or one resolving to
genuine remote SSH. Concretely:
- **Local loopback** gains `related_plugins_for_repo` resolution alongside
  the repo-own resolution it already has (`_own_plugin_args`) — a same-
  machine filesystem read, no staging required.
- **Remote SSH** gains both: resolving what to stage (repo-own + control-
  repo-declared) and staging any control-repo-owned payload onto the remote
  host (reusing the existing SSH channel for an egress-free tar+base64 copy,
  the same technique `agent-codespaces` already uses) before the remote
  launch command is built, so the target project's own binstub receives a
  complete `--plugin-dir` set via `copilot_args` rather than none at all.
- **An elevated/privileged relay lane** (a dispatch that hands off to a
  separate privileged sub-daemon rather than running directly in the
  unprivileged SSH session used to reach it) is a **distinct** staging
  variant: its stage-root and copy/exec primitive may need sourcing through
  whatever channel already reaches the elevated process, not assumed to
  share the plain SSH session's filesystem view — but the observable effect
  (a working `--plugin-dir` on the elevated launch) is the same guarantee.

## Behaviors

### quality-parity-across-venues
A task dispatched into a container and the same task dispatched into a CodeSpace
produce **equivalent-quality** work — same model, skills, cwd, and tools — modulo
the venue's own compute.

### reproduces-in-a-container
Any venue dispatch flow (auth relay, launch, session/monitoring, coordination)
**reproduces in a local container**, except the CodeSpace-only cold-boot/idle
path. A container repro is accepted as evidence for a CodeSpace fix.

### harden-once-benefits-all
A fix to a shared dispatch code path takes effect in **every** venue at once;
there is no second venue where the same class of bug must be re-fixed.

### fail-loud-not-silent-degrade
When a venue-agnostic guarantee cannot be met — the host model didn't propagate,
a token couldn't be bootstrapped, the relay back-channel didn't establish — the
dispatch **surfaces it** rather than silently falling back to a degraded default.

### auth-just-works-everywhere
From inside any venue, credential requests to ADO/Azure/GitHub succeed over the
shared back-channel with no venue-specific setup visible to the agent.

## Non-Goals / Boundaries

- **Not erasing genuine venue differences.** Lifecycle (`gh codespace` vs
  `docker`), token bootstrap, and cold-boot/idle are real and stay venue-specific
  — parity is about everything *else* being shared.
- **Not credential custody or token-minting policy.** *How* tokens are custodied,
  scoped, and brokered is the credential-relay trust model's concern; venue-parity
  only requires that the relay is reached uniformly and a token is bootstrappable
  per venue.
- **Not a general container orchestrator.** The container venue is a personal,
  bounded fleet for dispatch/repro, not a production scheduler.
- **Not the dispatched-agent *content* fixes themselves.** *What* good context/
  model/skill parity means is realized by the launch-parity work; this vision
  requires those guarantees live in the shared core so both venues inherit them.
- **Not parity for untrusted/restricted containers.** Full launch/plugin/worktree
  projection targets **trusted** venues (CodeSpaces + trusted fleets). A
  restricted sandbox deliberately receives none of it by default; provisioning it
  and offering à-la-carte tools is the
  [agent-containers vision](plugins/agent-containers/README.md)'s concern, not a
  parity gap.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Related visions: [plugins/agent-bridge](../plugins/agent-bridge/README.md) (the coordination layer that owns the dispatch core) · [plugins/agent-codespaces](../plugins/agent-codespaces/README.md) (the CodeSpace venue provider) · [plugins/agent-containers](../plugins/agent-containers/README.md) (the container venue provider + the trusted/restricted trust model this vision scopes parity by)
- Child visions: none (leaf)
- Reality docs: [`docs/architecture.md`](../../docs/architecture.md)

## Provenance

- **2026-08-22** — Conceived from operator direction: except for CodeSpace
  idle/boot, every venue issue should reproduce in a container via
  `agent-containers`; auth-relay, daemon management, etc. should match 1:1. Share
  all logic between `agent-containers` and `agent-codespaces` minus container
  lifecycle plus the GitHub-token bootstrap; container management is bounded only
  by local disk/memory and can be driven into arbitrary test/repro states — so
  align the two systems and use containers to repro and harden remaining flows.
  Refined same day: the venue-agnostic launch concerns (model/effort/context,
  in-repo `.ai` staging, `--plugin-dir`, concrete cwd) belong in **agent-bridge**,
  not the venue providers; **both** venues ultimately provide an **SSH transport**;
  and the auth-relay **back-channel** should be unified (over SSH `-R`) so it works
  seamlessly and identically in every venue.
- **2026-08-23** — Scoped parity to **trusted** venues (operator direction):
  segment container fleets into **trusted** (project full harness capabilities —
  launch parity, relay, eventually container-local worktrees + multi-repo — to be
  a seamless agent-bridge node) vs **untrusted** (provider wrangles the container
  runtime + à-la-carte tools; host agent/scenario decide). Untrusted containers
  are out of parity scope; the trust model is owned by the agent-containers vision.
- **2026-10-05** — Extended to the static-registry SSH/local dispatch path
  (#5286): investigation while designing a related-repo plugin-distribution
  mechanism found that `codespace:`/`container:` namespace-resolved targets
  are the *only* dispatch shape with working `--plugin-dir` resolution today
  — a bare static-registry agent resolving to local loopback gets only its
  own repo's plugins (no control-repo-declared ones), and one resolving to
  genuine remote SSH gets **no** plugin resolution at all, because that
  launch is assembled by the remote project's own binstub rather than
  composed by the coordination layer. Added the
  `plugin-dir-parity-for-static-targets` feature and named the
  elevated/privileged-relay lane as a distinct, explicitly-handled staging
  variant rather than an assumed extension of the plain SSH case.
