# Declarative Fleet Gateway - Vision

- **Subject:** A reusable, live-configured Gateway for declared services and outbound role channels
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-19

## Purpose & Intent

An owning repository declares the services, namespaces, capabilities and trust
relationships its fleet should offer. The Gateway reconciles that desired
surface while service instances independently deploy and connect outbound.
Routine service churn changes live configuration and observed availability, not
the Gateway executable or deployment.

The same Gateway core serves an ordinary container, a managed service host or a
thin platform wrapper. Optional edge and identity adapters protect reach without
making a provider, private inventory or hosting platform part of the core.

## Concepts & Components

- **Desired manifest:** owner-controlled service slots, API and UX namespaces,
  role configuration, capability restrictions and credential-source references.
- **Expected role slot:** a reserved service responsibility with explicit
  eligible identities, rather than an endpoint invented by a connecting service.
- **Outbound connector:** a service-owned authenticated channel that supplies
  only the capabilities admitted for its assigned slot.
- **Live reconciler:** validated, coherent desired-state publication and
  observable per-slot application, readiness and failure.
- **Reusable distribution and adapters:** the core, configuration language and
  base container are portable; optional ingress adapters and platform wrappers
  compose their own supported lifecycle and trust boundaries.

## Features

### repository-declared-service-surface

The manifest assigns routes and namespaces before a service is present.
Connecting instances prove the identity authorized to supply an expected role;
they cannot claim another namespace, invent public routes or acquire broader
control by announcing capabilities. Desired, connected, admitted and ready are
distinct inspectable states. An absent expected service is unavailable, not an
excuse to use an undeclared backend.

### authenticated-outbound-role-channels

Fleet services, including co-located containers, reach the Gateway through
outbound authenticated channels without publishing their own LAN-facing ports.
Routes preserve declared HTTP, streaming and UX capabilities and their
authorization semantics. Unsupported capabilities fail explicitly rather than
turning the Gateway into an arbitrary URL, socket or command proxy.

### nearly-all-configuration-is-live

Adding, replacing or removing a service, changing a valid route or role policy,
and rotating trust can reconcile without restarting or redeploying the Gateway.
Candidate validation precedes coherent publication; invalid, conflicting,
untrusted or stale revisions preserve the last valid serving configuration.
Applied revision and per-role outcomes remain observable. Changes that genuinely
require a binary update or restart are distinguished from ordinary configuration.

### portable-protected-ingress

Clients use one protected API and UX surface while the origin remains private.
An optional Cloudflare Tunnel adapter can provide outbound edge reach, with
optional Zero Trust access protection, without being required for direct, local
or other protected deployments. Tunnel transport and edge admission do not
authenticate a caller to the Fleet. The Gateway owns its application
authentication independently of the chosen tunnel, composing explicitly selected
identity providers such as Entra or Authelia without making either mandatory.
Gateway caller authentication, edge authentication, service-connector identity,
route permission and fleet administration remain separate grants. Proxy and external-origin
handling preserve authenticated target identity rather than trusting
caller-supplied forwarding headers.

### thin-hosting-wrappers

Hosting packages reuse the same core and supported container distribution.
They supply supervision, persistent storage, bootstrap configuration and health
integration rather than forking Gateway logic. Platform packaging is independent
of the live service manifest, so changing a service does not require a wrapper
release. Architecture and resource support are declared and qualified honestly.

## Behaviors

### configuration-is-authority-registration-is-observation

The desired-state owner authorizes slots, trust and publication. A connector
reports availability under that policy; neither a healthy channel nor a parsed
configuration establishes authority on its own. Keys and reusable secrets are
not embedded in committed manifests, routes, logs or images.

### reconcile-without-duplicate-writers

Readers observe a coherent configuration and connection generation. Reconnects,
replacements and delayed callbacks cannot restore superseded registrations.
Role withdrawal stops new admission and applies the declared policy to accepted
work and streams; it does not disguise uncertain effects as completed or
terminate unrelated work.

### preserve-owners-and-last-known-good

Existing task, worktree, service, credential and deployment owners remain in
place until an explicit migration transfers their responsibility. The Gateway
does not add a second installer, supervisor, issuer or container scheduler.
Failed candidate configuration or serving activation preserves the prior valid
state and gives a precise outcome.

## Non-Goals / Boundaries

No automatic production enrollment, public exposure, credential creation,
provider-account mutation or destructive migration from merely loading a plan.
No required Kubernetes installation, Cloudflare account, identity vendor or
platform-specific wrapper. A thin wrapper is not a second Gateway implementation.
Live configuration does not eliminate necessary code and security updates.

## See Also

- Parent: [machine fleet](../README.md)
- Factory interaction: [agentic factory](../../agent-fabric/agentic-factory/README.md)
- Ownership and lifecycle: [plugin services](../../plugin-services/README.md)
- Reality: [Gateway runtime](../../../fleet-manager/docs/gateway-runtime.md)
