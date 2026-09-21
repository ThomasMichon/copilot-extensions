# Host Resource Providers — Vision

- **Subject:** How a plugin contributes a **named, locally-reachable
  capability** — a browser/profile control surface, project-specific
  configuration, a development port, or another local resource — that a
  coordinated remote session can request and use, generalizing the credential
  relay's already-proven pluggable-source shape beyond credentials.
- **Scope:** leaf (cross-cutting capability within the agent fabric)
- **Status:** Active
- **Last revised:** 2026-09-19
- **Reality docs:** [`libs/credential-relay/README.md`](../../libs/credential-relay/README.md)

## Purpose & Intent

The credential relay already proved a specific shape: agent-bridge hosts one
server: a provider plugin (`agent-codespaces`, `agent-containers`) contributes
a `CredentialSource` without agent-bridge's core ever importing that provider
package, and a remote venue reaches it over the same SSH back-channel every
venue already carries. It works because the contract is narrow, the source is
pluggable, and ownership of what the source actually does stays with the
plugin that registered it.

A remote or otherwise coordinated session has the same shape of need for
capabilities beyond credentials — a constrained browser/Playwright profile, a
bit of local configuration, a development tunnel, or a capability not yet
imagined. The north star generalizes the credential relay's *proof*, not its
literal git-credential-protocol wire format: any plugin can register a **named
local capability** behind one discoverable contract, reachable by a
coordinated session the same uniform way credentials already are, without each
new capability inventing its own bespoke tunnel, discovery mechanism, or trust
model.

This is deliberately independent of *how or where* the requesting session
runs. A session reached through
[remote-interactive-sessions](../remote-interactive-sessions/README.md)'s CLI
mode, an ordinary headless ACP-driven session, or any other coordinated
execution can request a registered capability the same way; this vision does
not gate on, or depend on, any particular session-hosting mode.

## Concepts & Components

### Named capability, pluggable provider

A provider plugin registers one or more **named capabilities** — analogous to
a `CredentialSource`, but general-purpose — under agent-bridge's existing
per-host service model. Registration is discoverable and does not require
agent-bridge's core to import the contributing plugin's package, exactly as
credential sources are today.

### One relay-shaped back-channel, many capabilities

A coordinated session requests a capability by name over the **same** SSH
back-channel every venue already establishes for the credential relay — not a
second, capability-specific transport. Adding a capability is adding a
provider behind the existing channel, not standing up new plumbing per
capability.

### Ensure/release ownership, generalized

Where a capability has a lifecycle (a running local process, a held port, a
leased profile), the provider owns that lifecycle's ensure/idempotency/release
guarantees — durable ownership journaling, safe concurrent/duplicate requests,
and confirmed cleanup — the same way the credential relay's own sources own
their behavior today. A capability request is scoped to the requesting
session; it never grants ambient, host-wide reach.

### Capability, not authority

Requesting a registered capability gives a session the *use* of that
capability's public surface (an endpoint, a value, a forwarded port) — never
the provider's own configuration, credentials, or host-wide control. What a
capability's public surface actually contains is the contributing provider's
own design decision, bounded by this contract, not expanded by it.

## Features

### pluggable-named-capabilities

Any plugin can register a named, locally-reachable capability under one
discoverable contract, without agent-bridge's core importing that plugin's
package.

### one-shared-back-channel

A capability request travels the same SSH back-channel every venue already
carries for the credential relay; no capability invents its own transport.

### provider-owned-lifecycle

A capability with a lifecycle is owned end-to-end by its contributing
provider — ensure idempotency, ownership journaling, and confirmed release —
generalizing the credential relay's own guarantees.

### session-neutral-reach

Any coordinated session can request a registered capability regardless of
which session-hosting mode launched it; this is not exclusive to, or gated by,
any one hosting mode.

## Behaviors

### scoped-not-ambient

A granted capability is scoped to the requesting session and that specific
capability; it never widens into ambient, host-wide access to the providing
plugin's other configuration or credentials.

### provider-guarantees-its-own-behavior

Ownership, idempotency, and cleanup guarantees for a capability are the
contributing provider's to uphold — the shared channel and discovery contract
do not themselves guarantee a provider's internal correctness.

### discoverable-not-hardcoded

A consumer discovers available capabilities through the shared contract; it
does not hardcode knowledge of which provider plugin happens to supply a given
capability today.

### additive-registration

Registering a new capability is adding a provider behind the existing
contract. It never requires widening the relay's core wire protocol or
teaching agent-bridge's core about a new capability's internal shape.

## Non-Goals / Boundaries

- **Not credential or token custody policy.** This generalizes the credential
  relay's pluggable-provider *shape*; it does not restate or alter who
  custodies, scopes, or brokers credentials — that remains the credential
  relay's and each credential source's own concern.
- **Not a remote GUI or browser projection to the operator.** A capability
  surfaces a bounded local resource *to the coordinated session*; it does not
  project a remote interface back to a human operator.
- **Not tied to any one session-hosting mode.** This is reachable from any
  coordinated session; it neither depends on nor is exclusive to
  [remote-interactive-sessions](../remote-interactive-sessions/README.md)'s
  CLI mode.
- **Not a specification.** This vision fixes ownership, discoverability, and
  scoping guarantees — not the concrete request schema, wire format, or
  command grammar. That detail belongs to the effort that realizes it and to
  the reality docs.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Sibling vision: [plugin-services](../plugin-services/README.md) — the
  per-host service model a host-resource provider is deployed under.
- Related vision: [remote-interactive-sessions](../remote-interactive-sessions/README.md) —
  an independent concern this vision does not gate: a CLI-mode (or any) session
  reaching locally-provided capabilities.
- Related: [`libs/credential-relay`](../../libs/credential-relay/README.md) —
  the proven pluggable-source shape this vision generalizes.
- Child visions: none (leaf).

## Provenance

- **2026-09-19** — Split out of
  [remote-interactive-sessions](../remote-interactive-sessions/README.md) at
  operator direction: driving a remote CLI-mode session and provisioning local
  resources to a coordinated session are independent concerns that should not
  gate each other. Retains that vision's original generalization of the
  credential relay's pluggable-source shape as its own standing subject.
