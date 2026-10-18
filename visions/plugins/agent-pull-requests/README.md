# agent-pull-requests - Vision

- **Subject:** The first-class pull-request plugin and independently operable PR service.
- **Scope:** leaf (child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-10-18
- **Reality docs:** [PR plugin](../../../plugins/agent-pull-requests/README.md)

## Purpose & Intent

Provide one coherent PR-focused client and service capability for an operator's
authorized repositories, independent of a local checkout or an interactive
agent session. Author-side operations, reviewer-side operations and durable PR
observation compose the established provider-neutral contract rather than
requiring each consumer to build another forge client or watcher.

The same service can operate natively or as a declared containerized role on
the operator's fleet. A centralized, primarily IO-bound watcher and subscription
registrar shares observation across clients without compromising independent
subscription lifetimes or delivery reliability.

## Concepts & Components

### PR plugin and thin clients

`agent-pull-requests` is the PR-domain surface. Worktree-aware callers retain
their source identity, publication obligations and repository policy while
composing this capability. Compatibility adapters share provider semantics;
they do not become competing PR implementations.

### Independently operable service

The service owns PR observation, durable registrations and delivery state.
It is installable, diagnosable and operable without Copilot, a marketplace
payload or a roaming client process. The plugin supplies commands, guidance and
connections rather than acting as its required supervisor.

### Fleet-managed placement

An adopted fleet-manager supplies declared placement, service discovery,
authorized routing and deployment lifecycle. The PR service supplies its own
supported role and stateful activation boundary. One operator owns the whole
fleet; sharing a watcher does not introduce another operator's state or
credentials into it.

## Features

### first-class-provider-neutral-pr-surface

PR operations and observation have a shared, conformance-tested domain contract
across supported providers and clients. Existing worktree PR workflows remain
usable without surrendering their policy and source-control obligations.

### centralized-watcher-and-registrar

Independent clients register durable transition criteria and deadlines while
sharing observation for an authorized target/account scope. Increasing
subscriber count does not require one polling or wait process per subscriber.
The registrar owns PR subscriptions, not a replacement task queue or fleet store.

### standalone-and-fleet-service

Native use and fleet-managed container deployment are supported realizations
of the same PR service, not independently evolving watch implementations.
Declared fleet machines can host the service without installing an interactive
Copilot harness, and clients need not host its daemon to use it.

### durable-transport-appropriate-delivery

Notifications survive disconnection, restart and supported upgrades. Consumers
can reconcile acceptance, resume pending delivery and acknowledge their own
event identity without confusing another registration. Remote delivery does
not presume that a client's local executable exists on a service machine.

## Behaviors

### one-operator-with-separated-scopes

All fleet-hosted PR state and credentials belong to the fleet's sole operator.
That operator's repository, provider-account and installation scopes remain
distinct; membership alone does not grant every PR capability. Another
operator's fleet retains its own state and credentials even if future explicit
federation permits collaboration.

### reliable-without-hidden-second-owners

Registration, replay, acknowledgement and cancellation preserve durable
identity and bounded deadlines. Ambiguous outcomes reconcile before fallback,
and the selected namespace has one active state writer. A healthy replacement
container is not evidence that writer authority transferred safely.

### efficient-and-fair-observation

Shared IO is bounded and fair. A slow forge or consumer does not stall
unrelated targets, and account isolation is not sacrificed to deduplicate
requests. Durable retry does not claim exactly-once external effects.

### explicit-backends-and-credential-authority

Clients select supported native or fleet access explicitly. An unavailable
fleet route does not silently use another account or local service. Credential
providers retain issuance, renewal and revocation authority; the PR service
does not turn its subscription store into a secret store.

## Non-Goals / Boundaries

- No multi-operator tenancy within a fleet, and no hosting another operator's
  state or credentials there. Federation between separately owned fleets is
  not authority to merge their stores, identities or credential custody.
- No competing fleet controller, release authority, credential issuer, task
  lifecycle or worktree publication authority.
- No arbitrary remote shell, callback-command or destination forwarding.
- No required fleet adoption for useful standalone PR commands and watches.
- No fixed host inventory, ports, wire schema or packaging recipe in this vision.

## See Also

- Parent: [agent-fabric](../../agent-fabric/README.md)
- PR capability contract:
  [worktree PR operations](../agent-worktrees/pull-requests/README.md)
- Consumers: [agent-dispatch](../agent-dispatch/README.md)
- Fleet: [machine-fleet](../../machine-fleet/README.md)
- Lifecycle: [plugin-services](../../plugin-services/README.md)
- Reality: [PR plugin](../../../plugins/agent-pull-requests/README.md)
