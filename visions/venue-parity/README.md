# Venue Parity — Vision

- **Subject:** The dispatch **venue** layer — how the coordination layer launches, reaches, authenticates, and monitors a Copilot agent in a remote venue (a GitHub CodeSpace or a local Docker container), via the `agent-codespaces` and `agent-containers` venue providers.
- **Scope:** leaf (cross-cutting capability across the venue providers)
- **Status:** Active
- **Last revised:** 2026-08-22
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
