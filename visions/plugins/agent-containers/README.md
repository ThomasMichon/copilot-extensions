# agent-containers — Vision

- **Subject:** The **local-container venue provider** of the agent fabric — the
  plugin that provisions container-hosted agents, presents them through the
  fabric's coordination contract, and supports both trusted development and
  restricted low-trust work without confusing the two security postures.
- **Scope:** leaf (a per-plugin vision under the
  [agent-fabric](../../agent-fabric/README.md) branch)
- **Status:** Active
- **Last revised:** 2026-08-22
- **Reality docs:** [`docs/architecture.md`](../../../docs/architecture.md) and
  the plugin's [`README`](../../../plugins/agent-containers/README.md)

## Purpose & Intent

A container agent should be a first-class participant in the agent fabric
without every container inheriting the authority of the host that launched it.
agent-containers provides local, disposable, repo-shaped venues and makes their
security posture an explicit part of the venue rather than an assumption hidden
in launch defaults.

The provider serves two legitimate modes. A **trusted development venue** may
borrow host capabilities needed for ordinary engineering work. A **restricted
venue** is a security boundary for lower-trust or injection-prone reasoning:
its blast radius is the container and its one repository workspace, host
credentials do not cross the boundary, network reach and tools are explicitly
granted, and the failure mode is a disposable bad diff rather than host impact.
Both remain the same kind of fabric participant; trust changes authority, not
the coordination interface.

## Concepts & Components

### Trust-profiled venue
Every fleet has a legible trust posture. The trusted-development profile
preserves the productive host-integrated venue; the restricted profile removes
ambient authority and admits capabilities explicitly. Callers can determine
the effective posture before dispatch.

### Container as the boundary
For restricted work, containment rests on the container runtime and its
resource, filesystem, privilege, and network boundaries — never on instructions
asking the agent to behave. The harness running inside is replaceable; the
containment contract is not.

### Repository-shaped workspace
The agent receives a full repository clone inside its container and can use
ordinary local version control there. It does not receive a shared host
worktree or visibility into unrelated host paths. The host owns venue and
workspace lifecycle.

### Explicit capability envelope
Credentials, network reach, environment values, tools, and resource budgets are
capabilities of the fleet profile. Restricted venues begin without ambient host
capability and gain only named grants appropriate to their assignment.

### Coordination-layer face
Trusted and restricted containers are presented through the same
`container:` venue-provider contract. The coordination layer addresses the
participant uniformly while the provider enforces the venue's effective
posture.

## Features

### trust-separated-fleet-profiles
Fleets declare whether they are trusted development venues or restricted
sandboxes. Existing trusted-development use remains available, while restricted
work has a first-class posture rather than a fragile collection of caller
conventions.

### restricted-credential-boundary
A restricted venue receives no host credential, credential relay, or ambient
identity. If an assignment later needs an identity, it is a distinct,
least-privilege identity granted explicitly for that venue — never an accidental
inheritance of the host's authority.

### worktree-confined-repository
A restricted venue exposes exactly its container-local repository workspace for
agent work. Other host worktrees and host filesystem state remain outside the
venue.

### explicit-network-envelope
Network access for a restricted fleet is intentional and bounded to what its
assignment requires. A venue that needs one model endpoint does not thereby gain
ambient reach to every host or network service.

### harness-and-tool-latitude
The provider can launch a full agent CLI, a smaller harness, or a purpose-built
runner with an explicitly selected tool surface. The venue does not force
trusted-development tool authority onto restricted work.

### observable-security-posture
Machine-readable fleet state reports the effective trust posture and capability
envelope so dispatchers can verify the boundary they are about to use instead
of inferring it from configuration.

### bounded-disposable-execution
Restricted fleets can bound compute and process resources so a runaway agent is
contained, and their venues can be discarded without losing anything outside
the repository proposal they produced.

## Behaviors

### deny-by-construction
In a restricted venue, absent capability is structurally unavailable. A prompt,
tool call, or harness mistake cannot recover a host credential, mount an
unrelated host path, add privilege, or widen network reach after launch.

### secure-restricted-defaults
Selecting the restricted profile produces a coherent safe baseline without
requiring every caller to remember a list of hardening flags. Additional grants
are explicit, narrow, and legible.

### trusted-compatibility
Existing trusted-development fleets retain their established behavior unless
their configuration deliberately selects a different posture.

### same-fabric-contract
Trust posture does not fork the coordination API. A restricted container is
created, discovered, leased, and addressed through the same venue-provider
contract as a trusted one.

### no-shared-worktree-boundary
A restricted venue never relies on a shared host git worktree. Its repository
state is container-local, so branch ownership and worktree metadata cannot
escape or dangle across the boundary.

### policy-legible-before-dispatch
The provider exposes enough effective posture for a caller to reject an
incorrect venue before starting an agent. Security is not a promise discoverable
only after inspecting a running process.

## Non-Goals / Boundaries

- **Not a merge or deployment authority.** The provider supplies a venue and
  local repository workspace; contribution review, merge, deployment, and
  worktree lifecycle policy belong to higher orchestration layers.
- **Not prompt-based safety.** Instructions may guide an agent, but they are not
  the containment boundary.
- **Not one mandatory harness.** Containment must survive replacing the agent
  harness.
- **Not ambient host identity for restricted work.** A restricted venue never
  impersonates the host by default.
- **Not a shared host worktree.** Host workspace mounts are outside the
  restricted model.

## See Also

- Parent vision: [`../../agent-fabric/`](../../agent-fabric/README.md)
- Sibling venue vision:
  [`../agent-codespaces/`](../agent-codespaces/README.md)
- Reality docs: [`../../../plugins/agent-containers/README.md`](../../../plugins/agent-containers/README.md)
  · [`../../../docs/architecture.md`](../../../docs/architecture.md)
- Tracking: [#951](https://github.com/ThomasMichon/copilot-extensions/issues/951)

## Provenance

- **2026-08-22** — Split the container provider's trust model into explicit
  trusted-development and restricted postures after identifying that host
  credential forwarding and broad tool authority are useful defaults for the
  former but unsafe foundations for the latter.
