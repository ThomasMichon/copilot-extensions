# Opt-in service-role serving adapter

Back to [the canonical effort](README.md).

**Agent-recommended contract for review before serving implementation.**
The operator explicitly authorized this campaign to own the narrow new
service-role admission adapter and one opt-in CLI selection seam. Existing
index, credential-controller, provider, identity-verifier and admission-primitive
ownership is not transferred.

## Source scope and compatibility

Use new service-profile, policy, channel, connector and serving modules.
The shared entrypoint change is an explicit option on the existing Gateway
`serve` command; ordinary `--config` serving retains its behavior and imports.
No service impersonates `query`, `index-worker` or `credential-controller`.
Do not modify `GatewayConfig`, `Admission`, legacy `Gateway.dispatch`,
`runtime_identity`, `runtime_admission`, index/broker/controller or provider
implementations merely to make the new role fit.

The selected service profile is another capability of the same Gateway product,
not a second permanently deployed controller beside it. A deployment selects
the serving profile and its supported capabilities honestly; it does not
advertise legacy index/credential operations until their integration is proven.
No profile selection migrates their existing authoritative state.

## One coherent owner context

The explicitly selected configuration source supplies one complete bounded
candidate containing:

- The validated desired manifest, with stable fleet/Gateway and desired revision.
- Explicit issuer, audience, public signing-key trust and trust-validity inputs
  for the existing `IdentityVerifier`, not a new issuer or token verifier.
- Non-secret role-key bindings resolving the manifest's declared references to
  expected public proof-key identities. A missing or unsupported provider fails;
  the service cannot supply a substitute trusted key.
- Explicit caller grants binding authenticated subjects and proof keys to
  allowed service slots and methods. Verified identity is not global route
  permission, and a service connector grant is not a caller/admin grant.
- Bounded request, channel, stream, reconnection and withdrawal policies.

TLS listener keys and any connector private keys stay in externally provisioned
secret references. This campaign creates no real provider account, issuer,
keypair, production enrollment or credential. Tests may use synthetic issuers
and keys, labelled as fixtures rather than production identity support.

Candidate construction validates all references and projections before one
coherent publication. Mutable input aliases cannot alter an admitted context.
Configuration provenance is the selected source owner's responsibility; a JSON
document, digest, callback or parsed snapshot is not a credential.
The served context binds desired revision, trust and effective grants together.
The process-local desired store is a reusable parser/publication prerequisite,
not by itself that complete serving context.

No-op publication preserves contexts and connection identity. Changes to one
slot must not widen grants or silently bind an old connection to a different
service. An unchanged slot may retain its channel if its current identity,
trust and route/capability policy still match. Changed/removed/revoked slots
stop new admissions and apply the declared bounded stream/drain policy.
Replacing issuer trust must account for the verifier's nonce/replay epoch:
old proof epochs cannot remain accepted after replay state is replaced.

Durable accepted-revision custody across Gateway replacement remains a source/
lifecycle owner obligation. The new runtime must not claim restart-safe
publication from an in-memory counter or restore uncertain accepted effects.

## Reuse actual identity verification

Service and caller authentication consumes the existing maintained-library
JWT/PoP verifier with the actual method, configured HTTPS target and nonce.
Issuer, audience, time, token/proof key binding and replay checks remain intact.
Configured role bindings and caller grants are checked independently afterward.
The initial implementation supports the documented common profile, not a claim
of live broker, GitHub, Cloudflare or Entra interoperability.

Any new proof-construction helper must reuse the common profile semantics,
not hand-roll signature verification or change the legacy POST default.
A new method-aware helper is explicitly scoped; cryptographic primitives and
provider custody remain unchanged. Credentials never appear in URLs, logs,
manifest data, event receipts or process-wide environment variables.

## Outbound service channel and request admission

Use a maintained asynchronous HTTP/WebSocket transport behind optional server
dependencies, preserving the thin client distribution. The precise maintained
dependency and its supported version/seasoning gate are recorded with the code,
not installed implicitly by a service or marketplace hook.

An authenticated control request establishes slot, subject, desired policy and
connection correlation. If channel attachment needs a short-lived one-use
ticket, bind it to that exact context and consume it once over verified TLS;
do not put a reusable credential in a query string. Opening an unauthenticated
socket never grants a role. Bound unverified sockets, handshake bytes/time,
pending attachments and per-slot concurrency.

The connector makes the outbound channel; its local service target is selected
by deployment configuration, not client requests or service advertisements.
The Gateway chooses routes from current admitted namespaces. Connection
generations and request correlation fence late frames and stale replacements.
Requests and response streams have bounded windows, cancellation and flow
control. Do not retry uncertain non-idempotent work after disconnect.
Unsupported upgrades, generic TCP and arbitrary proxy targets refuse explicitly.

Public HTTP/UX requests require the selected authenticated caller policy.
Do not make the route unauthenticated to simplify a fixture, accept a connector
as an administrator or trust an arbitrary forwarding header. The optional
Cloudflare adapter supplies transport, not Fleet authentication. A verified
proxy path can preserve the existing caller proof without implementing provider
login. Gateway-owned application authentication with selected providers such
as Entra or Authelia is a separate retained-provider integration contract;
edge access does not implicitly satisfy it.

## Required witness and limits

The first service adapter slice proves real JWT/PoP verification against
synthetic issuer fixtures, current desired role/caller bindings, coherent reload,
revocation and value-free refusal. A copied `VerifiedIdentity` dataclass is
not authentication evidence.

The subsequent actual server/connector proof requires two service instances,
bounded HTTP/UX and response streaming, invalid manifest preservation, no-op
continuity, role/key withdrawal, forged role/namespace denial, replacement and
late-frame fencing, with stable Gateway PID/start identity across live updates.
Existing static/index/credential tests must remain green unchanged.

Production protected ingress, real provider identity, container architecture,
live deployment and stateful migration remain separately measured gates.
No successful unit fixture substitutes for them.
