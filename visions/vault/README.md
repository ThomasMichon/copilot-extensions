# Vault Service - Vision

- **Subject:** Portable credential lifecycle and authentication transaction authority
- **Scope:** leaf (cross-cutting service)
- **Status:** Active
- **Last revised:** 2026-10-24
- **Reality docs:** [agent-vault](../../plugins/agent-vault/README.md),
  [credential relay](../../libs/credential-relay/README.md)

## Purpose & Intent

Vault owns credential lifecycle **and** authentication transaction management for
authorized consumers. A headless consumer can ask for the access it needs,
continue when that access is actually usable, and retain an honest pending
outcome when user interaction is required. Its request survives the departure
of the requesting client or the presentation surface.

Worktree Manager is a stateless, replaceable operator-facing UX for this
service. It presents accumulated requests and explicit actions, not credentials
or another authentication engine. Vault remains responsible whether the user
acts through Manager, another attended client, or no currently connected UI.

## Concepts & Components

- **Vault authority:** owns credential acquisition, caching, authorized use,
  renewal, expiry and revocation, together with durable authentication
  transactions and their recovery.
- **Credential providers and custody agents:** implement provider-specific
  issuance and physical custody, including local vaults and OS/provider stores,
  under Vault's logical lifecycle authority. Provider identity policy and
  revocation remain authoritative; Vault is not merely a metadata router.
- **Authentication transaction:** the durable relationship between requested
  access, its consumer, selected provider and any necessary interaction, through
  to verified consumer readiness or an explicit unresolved outcome.
- **Attended interaction:** an explicitly requested browser or native account
  interaction on the appropriate device and signed-in user session. An
  interaction host performs only its admitted provider capability.
- **Presentation clients:** replaceable views and action surfaces over
  Vault-owned information. Manager composes its existing contributed
  presentation and progress affordances rather than becoming a transaction
  store or introducing a parallel interaction system.
- **Deployment and federation:** a central Fleet service, an independent
  machine-local service, and optional explicitly trusted relationships between
  them, each with legible authority and scope.

## Features

### credential-lifecycle-authority

Vault manages acquisition, bounded caching, scoped delivery or use, renewal,
expiry and revocation for supported credentials. Consumers obtain only the
access their identity and purpose authorize. Vault composes existing credential
providers, stores and broker coordination where suitable rather than requiring
a replacement of every backend.

Physical custody may remain in an OS-native store, a provider-owned store or
an authorized custody agent. That placement does not move lifecycle
responsibility to Manager or reduce Vault to pass-through coordination.
Provider restrictions, refresh limitations and revocation govern what Vault
can legitimately obtain, retain or serve.

### durable-client-independent-transactions

Headless clients can submit authentication-required requests without opening
an interactive session. Vault durably retains accepted requests, coalesces
equivalent needs without conflating consumers or access bindings, and owns
authentication progress, provider polling, bounded retries and reconciliation.
Service recovery and client reconnection preserve accepted responsibility
without requiring a UI process to keep it alive.

Requests can accumulate while no attended client is present. An authorized
client can later inspect and resume the required interaction without inventing
a new request or losing the original consumer relationship.

### explicit-browser-and-native-interaction

The user can explicitly **launch the browser** for the supported interactive
flow, with enough provider and target information to make an informed choice.
The service also supports the appropriate **Windows Web Account Manager
(WAM)-backed flow through Git Credential Manager (GCM), or Windows-native Entra
interaction**, where applicable. Device-code interaction remains an available
provider capability, not a universal replacement for browser or native flows.

Vault and its provider choose the correct execution device, user session and
callback destination. A central container can coordinate an interaction that
must execute in an attended desktop session without pretending that the
container itself has that session's native account access. Unsupported or
unavailable interaction capabilities remain explicit.

### stateless-manager-surface

Manager surfaces accumulated interactive-authentication-required requests,
their non-secret context, current progress and available user actions. The user
can inspect, launch a supported interaction, revisit it, dismiss its
presentation or request cancellation through the owning service.

Manager holds no credentials, durable authentication state, provider polling
or retry responsibility. Refreshing, replacing or closing it leaves Vault's
requests and ongoing work intact. Other clients can present the same truth
without taking ownership from one another.

### central-fleet-and-standalone-local

A Gateway-backed Fleet's **standard integrated profile installs a central
Vault service container** as part of its declared service loadout. Fleet's
selected external deployment manager retains container lifecycle ownership;
Gateway routes authorized service access, and Manager does not install or
supervise Vault. Deployment still follows the fleet's explicit adoption,
placement and activation decisions.

Vault is also independently installable and useful as a **machine-local
service without Fleet, Gateway or a central Vault dependency**. Its service,
CLI and credential consumers do not require Manager or Copilot to remain
running. Windows, Linux/WSL and macOS share portable lifecycle and transaction
guarantees while platform-native capabilities remain honestly qualified.

### explicitly-trusted-federation

A local service may optionally federate with a central or peer Vault through
an explicitly trusted, scoped relationship. The operator can understand which
Vault owns each credential lifecycle and transaction, where custody resides,
and which consumers may use it. Federation does not imply credential copying,
unbounded mirroring or permission to redistribute credentials.

Disconnected local authority can continue its own authorized work. Work owned
elsewhere remains visibly unavailable or pending; disconnection does not create
a competing durable owner or justify serving stale, expired or revoked access.

## Behaviors

### discovery-never-starts-a-ceremony

Listing requests, refreshing a view, inspecting readiness or discovering a
provider never initiates interactive authentication. Launching a browser or
native window requires the user's explicit action for the identified
transaction. Standing authorization for noninteractive renewal is distinct
from consent to start an attended ceremony.

### preserve-target-and-access-bindings

Device, user session, account, tenant, provider, resource, audience and scope
remain bound to the requested consumer throughout routing, interaction,
credential adoption and federation. Reconnection or retry cannot silently
switch accounts or broaden access. Request-supplied text is information,
not authority to execute arbitrary commands or open unvalidated destinations.

### readiness-is-not-approval

User approval, provider authentication, credential adoption and consumer
readiness are different facts. Vault reports success only when the intended
consumer can use the correctly bound access; opening a browser, receiving a
callback or obtaining approval alone does not establish it. Missing consumer
evidence remains pending or unknown rather than success.

### dismiss-is-not-cancel

Dismissing a view hides presentation, not service responsibility. Explicit
cancellation applies through Vault to the selected request and its consumers;
it does not silently revoke unrelated credentials or cancel another
consumer's shared need. Expiry, cancellation, provider failure and uncertain
completion remain distinguishable. A lost reply does not authorize blindly
repeating a ceremony or an effect.

### secrets-stay-out-of-presentation

Credentials and exposed tokens never enter Manager, presentation payloads,
logs, telemetry or session environment variables. Authorized delivery to
consumers uses the credential owner's protected custody and access boundaries.
Diagnostic context is minimal, scoped and non-secret; supportability does not
require publishing account identifiers or credential material.

### preserve-authority-through-replacement

Vault recovers durable requests and credential responsibility across service,
client and connection replacement. There is one durable owner for each
responsibility, including during federation, offline recovery and migration.
Existing independent providers and services retain their authority until an
explicit migration preserves consumers, accepted work and custody and
transfers that responsibility. Enrollment or a new declaration alone is not
such a transfer.

## Non-Goals / Boundaries

- Not a general-purpose password-manager UI or a replacement for every
  provider, OS store, issuer or account policy.
- Not a Manager-owned authentication engine, credential store, installer or
  durable polling/retry loop.
- No automatic browser/native ceremony on discovery, implicit account
  switching, arbitrary request-supplied execution or credential redistribution
  from fleet membership alone.
- No required central service, Fleet adoption or federation for local use.
  Central deployment does not confer desktop-session or native broker access.
- No competing deployment manager, custody authority or durable transaction
  owner. Logical Vault authority permits protected physical custody through
  existing providers; it does not require credentials to pass through the UX.
- No pinned API, schema, port, state enumeration, command recipe or rollout
  sequence in this intent document.

## See Also

- Parent: [plugin service model](../plugin-services/README.md)
- Presentation: [Worktree Picker / Manager](../picker/README.md)
- Deployment: [machine fleet](../machine-fleet/README.md),
  [Fleet Gateway](../machine-fleet/gateway/README.md)
- Reality: [agent-vault](../../plugins/agent-vault/README.md),
  [credential relay](../../libs/credential-relay/README.md),
  [Gateway runtime](../../fleet-manager/docs/gateway-runtime.md)
