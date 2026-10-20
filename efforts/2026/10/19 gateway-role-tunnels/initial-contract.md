# Initial role-manifest and service-channel contract

Back to [Gateway Role Tunnels](README.md).

**Agent-recommended design for review, not an implemented API or live rollout.**

## Product and compatibility boundary

Extend the selected fleet-manager Gateway role. Do not create a second resident
Gateway, task ledger, installer, credential provider or container scheduler.
Legacy startup-configured index/credential behavior remains unchanged unless
the new profile is explicitly selected.

The initial new source scope is manifest/reconciliation, service-channel
protocol/connector modules and dedicated tests/docs. Before changing existing
CLI/server/auth consumers, agree the exact adapter and files with #6443/#5789.
Preserve #6743/#7099 primitives, #6804 provider custody and #6240/#6424 lifecycle.
An unavailable shared interface is a concrete gate, not permission to fabricate
an identity or silently replace another owner's implementation.

## Owner manifest and publication

The configuration language declares:

- Stable fleet/Gateway identity and desired revision.
- Service slots with assigned role, namespace, eligible connector identities,
  configured targets and a bounded set of supported request/stream capabilities.
- Trust/key-source references and permitted caller/control relationships.
- Resource, request, stream, reconnect and withdrawal policy limits.

The operator's example route is not a requirement to encode reusable secrets or
static signatures in configuration. Private credentials belong to the selected
provider and runtime secret mechanism. Public trust keys and non-secret
references may be declared where their owner permits.

Reject duplicate slots, ambiguous or overlapping namespace ownership, reserved
control-route collisions, unsafe path forms, unknown capabilities and unbounded
input. A request cannot choose a backend URL, authority, target socket, arbitrary
method/header forwarding policy or executable.

An explicitly selected source owner supplies candidate configuration. A mounted
configuration path is an explicit deployment/trust choice; parsing a file,
content hash or source commit is not independent proof of authorization.
Remote repository subscription, if later selected, must preserve that source's
authentication and revision authority rather than fetching arbitrary URLs.

Validate the complete candidate before one coherent publication. Readers
capture the same desired/admission generation for a decision. Changed input
with the same committed revision conflicts; an old revision cannot overwrite
newer state. No-op publication preserves identity. A failed read or invalid
candidate leaves the last valid state and a precise failure outcome.

Reuse compatible Gateway configuration/admission projections, not independently
maintained copies of their validation rules. A new manifest must not be forced
into an unrelated credential-controller role merely to pass an existing parser.
Where a genuinely new service-role contract is needed, make it explicit and
reviewed.

## Live policy and connection generations

Configuration, identity trust, current authorization and serving channels are
distinct facts that must be bound at the request/effect boundary. Existing
`AuthoritySnapshot` and `Permit` values are owner assertions/internal handles,
not transferable credentials or proof of source authenticity.

Do not mutate an `Admission` object's startup configuration in place and call
that reload. Publish a coherent owner-managed context or integrate the retained
snapshot-provider contract. Identity-verifier replacement must account for its
nonce/replay epoch; rotation cannot silently reset replay protection while
continuing to accept an old authentication epoch.

New connector admission binds identity, slot, desired revision and a current
connection generation. Reconnects and late callbacks cannot overwrite a newer
registration. Replacement does not allow two owners of one exclusive slot;
replicas require explicit slot policy rather than accidental duplicate connects.

Withdrawal stops new admission. Existing streams and accepted operations follow
the declared current policy, deadline and drain behavior. A lost or revoked
channel cannot authorize retrying an uncertain mutation. Report unavailable,
cancelled or uncertain outcomes without turning them into successful completion.

## Outbound transport and route semantics

Use a maintained transport implementation and the existing cryptographic
identity/admission boundaries; do not implement a new cipher, bespoke token
verifier, credential issuer or provider-login flow.

The initial connector owns an outbound authenticated channel and an explicitly
configured local service target. Gateway route selection comes from admitted
manifest state. Service capability advertisements describe supported behavior;
they do not grant routes or control authority.

The first integrated proof must route actual bounded HTTP API requests, UX
assets and a declared response-stream capability. Choose the exact supported
HTTP methods, body/header limits, cancellation and backpressure semantics in the
implementation contract. Unsupported upgrades, generic TCP and arbitrary proxy
targets are explicit refusals until separately implemented and qualified.
Do not describe a polling credential-controller subscription as a service tunnel.

Every forwarded request binds its configured route/slot and current
authorization to correlation and connection generation. Late responses and
changed configurations cannot be attached to a different request. Do not
automatically replay non-idempotent work after reconnect. Bound concurrent
requests, buffers, queues, response bytes or stream windows and reconnect
backoff so one slow service or client cannot exhaust the Gateway.

Preserve supported native service semantics and backend authorization. Strip or
derive trusted identity and hop-by-hop forwarding metadata at the responsible
boundary; an incoming client header is not an identity attestation.

## Protected ingress and external origin

The public core supports explicit protected ingress without requiring a
particular edge vendor. The Cloudflare adapter supplies supported connector/
edge configuration and trust references, not automatic provider-account changes.
It must not create DNS, Access applications, shared credentials or broad bypasses.

Human edge login, automation edge access, Gateway-owned application
authentication, connector identity, route permissions and fleet administration
are separate grants. The tunnel supplies transport, not Fleet authentication.
Gateway-selected providers such as Entra or Authelia remain independent of
Cloudflare Tunnel/Zero Trust or another transport. Receiving a request through
an edge tunnel is not authorization to call a service, supply a role or manage
the fleet. Validate the selected external origin and application identity
boundary; do not trust arbitrary Host or forwarded headers or disable TLS verification.

The transport-only adapter must preserve a verified private TLS hop and the
existing caller proof at the selected public origin before claiming a supported
generic proxy contract. An isolated edge fixture can prove forwarding,
cryptographic assertions and origin binding; it cannot prove cloudflared, a real
account, provider policy, public DNS, provider login or production reach.

## Container and deployment contract

Package the selected core and connector closure as ordinary unprivileged
containers with declared architectures, bounded resources and explicit state,
configuration and secret mounts. A thin platform wrapper reuses that
distribution; it does not fork the Gateway or turn every service-manifest edit
into an app update. Binary/dependency/security updates still follow their
existing lifecycle.

The first container proof uses disposable private networks and synthetic
credentials, not production mounts, a host Docker socket, exposed host ports
or live provider accounts. Reuse the existing clean-room/receipt machinery.
The Komodo proof requiring privileged Docker-in-Docker remains a separate
explicit-admission gate; the request to build this campaign does not authorize
that privilege.

The existing deployment manager owns placement and runtime supervision. Do not
invent a fleet-manager apply command or claim managed install/update readiness
from offline planning, package inspection or an image build.

An amd64 run is not ARM64 execution. Record build, emulation, native target and
live deployment evidence separately. Missing architecture tooling is a named
qualification blocker, not permission to install privileged emulation or silently
claim support.

## Required assembled witness

The witness records exact source/image/configuration identity and actual process
identity for one Gateway plus two independently isolated service connectors:

1. Expected slots are visible but unavailable before services connect.
2. Assigned identities connect outbound and only their configured API/UX routes
   work; another slot/namespace or undeclared capability is refused.
3. A higher valid manifest applies while the Gateway PID/start identity stays
   unchanged; an invalid candidate preserves the previous serving revision.
4. Replacing a connector fences its predecessor; late responses cannot cross
   generations. Disconnect and Gateway replacement remain truthful.
5. A real response stream, slow consumer, cancellation and policy withdrawal
   exercise the stated bounds without abandoning accepted work.
6. The configured external origin is enforced; forged proxy identity cannot
   obtain the selected protected capability. Adopter-owned network isolation,
   not Host validation alone, restricts direct origin reach.
7. Current static/index/credential regression cases still pass unchanged.

Only after that evidence should the adopter receive exact rollout,
capacity/storage, credential, migration and rollback prerequisites. Actual
deployment, production enrollment and provider changes remain separate approvals.
