# Declarative Fleet Gateway - Vision

- **Subject:** A reusable, live-configured Gateway for declared services and outbound role channels
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-25

## Purpose & Intent

An adopted harness and its paired private knowledge/configuration repository
declare the services, namespaces, capabilities and trust relationships its fleet
should offer. Reusable templates come from the harness; the paired repository
selects what should actually exist. The Gateway reconciles that desired
surface while service instances independently deploy and connect outbound.
Routine service churn changes live configuration and observed availability, not
the Gateway executable or deployment.

The same Gateway core serves an ordinary container, a managed service host or a
thin platform wrapper. Optional edge and identity adapters protect reach without
making a provider, private inventory or hosting platform part of the core.

The Gateway federates machine-owned facts through push and routes separately
authorized requests back to their target owners. Observation, control and
interactive terminal attachment are distinct capabilities, not one blanket
grant implied by a connected machine.

## Concepts & Components

- **Desired manifest:** owner-controlled service slots, API and UX namespaces,
  role configuration, capability restrictions and credential-source references,
  resolved from the adopted repository pair rather than a private Gateway fork.
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

### paired-source-refresh-and-confirmed-deployment

The Gateway retains the adopted harness/knowledge repository pairing and
refreshes it on a schedule or on an authorized fleet-manager driver's request.
An authorized driver can also deliver updates with the same reviewed source
identity; a pushed update is not a separate persistent configuration authority.
Repository webhooks can drive the same refresh contract without becoming a
separate configuration owner. Refresh exposes the selected source revisions,
desired/observed delta and necessary deployment steps through subscriptions.

Initially the Gateway and selected deployment manager prepare those steps and
wait for confirmation from an authorized user or agent through fleet-manager.
Neither a merged configuration nor a refresh notification grants activation.
Confirmation applies to the actual prepared plan; changed inputs require a new
decision. Invalid, stale or unapproved candidates preserve the serving state.
Existing credential expiry/revocation and admitted stop controls remain
effective while a deployment proposal awaits confirmation.

### confirmed-complete-outfitting

A prepared deployment covers the complete declared loadout, not just its
application containers. It accounts for required authentication relays,
tunnels, API and UX exposure, persistent storage and control-side deployments,
with explicit placement, dependency ownership and resource admission.
Subscribers can inspect that whole change before confirming its exact
prepared generation through fleet-manager.

Confirmation starts an operation with an admitted, observable outfitting
duration. Within that bound, the controller either verifies the complete
declared capability ready or reports precise blocked, failed, partial or
uncertain outcomes. Container creation, a connected tunnel or an accepted
manager request alone is not end-to-end readiness. Unsupported or unavailable
prerequisites cannot silently become success or authorize broader access.
Unresolved effects retain their original ownership and recovery correlation;
expiry does not imply that they stopped or make blind replay safe.

The Gateway coordinates the existing deployment, service, transport and
credential owners rather than replacing them. Invalid candidates and failed
activation preserve the last valid serving state where their owners support
that transition. Bootstrap and recovery of the controller and its deployment
manager remain independently available; outfitting cannot depend on calling
an unavailable copy of its own control plane.

Fleet-manager may also request temporary/preview or diagnostic adjustments
through admitted operations. These remain distinct from persistent configuration,
which changes through source pull requests and ordinary propagation.

### repository-declared-service-surface

The Gateway-backed Fleet standard integrated profile includes a central
[Vault service container](../../vault/README.md). The selected external
deployment manager owns its container lifecycle; Vault owns credential
lifecycle and authentication transactions, including coordination with
authorized local/native custody and interaction capabilities. Gateway provides
authorized routing, not another credential owner. Independent machine-local
Vault use needs no Gateway; federation requires explicit scoped trust.

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

### push-federation-and-local-authority

Origins promptly push committed observations so connected clients' remote and
All views update without per-client polling of other machines. A machine still
reads its own information from its local authority. The Gateway serves scoped
remote observations with explicit provenance, freshness and coverage, not a
claim that disconnected or delayed sources are current.

### named-target-control-and-terminal-reach

An admitted outbound channel can carry named requests to a target's existing
worktree and session-host services, including workspace creation and agent
launch. Those services perform current authorization, admission and execution
and retain their original receipts and ownership. Gateway delivery alone is
neither target acceptance nor operation completion.

Console attachment is selected separately from the control route. SSH and an
authorized terminal-only channel can present the same target-owned session;
a lightweight browser renderer need not implement a full remote shell or
become another session host. Input, observation and lifecycle control remain
separate permissions. No terminal capability is inferred from service routing.

### nearly-all-configuration-is-live

Adding, replacing or removing a service, changing a valid route or role policy,
and rotating trust can reconcile without restarting or redeploying the Gateway,
after the applicable deployment confirmation and admission gates.
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

### human-sessions-and-machine-enrollment

Ordinary human UX access composes the selected identity provider's normal
authenticated session with Gateway route and capability authorization.
Page navigation, assets and application requests do not require the browser to
hold a separately enrolled signing key unless an explicitly selected stronger
profile requires it. Trusted identity handling, protected backend reach and
request-forgery protection remain part of that boundary.

Machine onboarding and service connectors use their own explicit credential,
enrollment and current-grant contracts. A harness-selected, authenticated user
continuity source can provide discovery and delegated bootstrap authority before
the new machine has Gateway credentials or SSH. Any required new-device
confirmation is attributable to the actual requester and scope; a short code or
discovered endpoint alone grants nothing.

These are distinct admitted authentication modes, not a silent fallback between
them. A human-session adapter cannot feed an ordinary cookie or caller-supplied
identity header into a machine proof verifier. Selecting normal human SSO does
not weaken an existing machine profile, grant service identity or confer fleet
administration.

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

The parent fleet's shared current-state view is a derived observation surface,
not a second owner of source registries or service state. It preserves scoped
access, source freshness and coverage without requiring per-client source
probes. A connected role, ready route or retained observation cannot establish
the readiness of another capability, authorize an operation or prove accepted
work has stopped.

### repurpose-admitted-capacity-not-create-machines

Gateway reconciliation can repurpose existing admitted hosts and use the
selected container/deployment manager within that capacity. Whole-machine
creation and removal remain exclusively with fleet-manager's operator-side
management path and its provider owner. Gateway may surface a capacity request,
not turn it into provider administration or ask a role connector to evade this
boundary.

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
- Credential and authentication authority: [Vault](../../vault/README.md)
- Reality: [Gateway runtime](../../../fleet-manager/docs/gateway-runtime.md)
