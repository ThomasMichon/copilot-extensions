# New Gateway/index vertical slice

Back to the [canonical fleet effort](README.md). Coordination: #6443; index
distribution/client consumption remains linked through #6383 and
[the standalone index effort](../agent-index-standalone-service/README.md).

This is reviewed-intent preparation, not a resident Gateway, enrolled controller
or production migration. The operator assigned end-to-end responsibility for the
new Gateway/index integration and excluded replacement of older adopter Gateways.
All implementation details below are **agent-recommended**.

## Outcome and boundaries

Deliver two distinct authenticated paths:

```text
index CLI -> new fleet-manager Gateway -> registered index container -> query result
index worker -> Gateway -> subscribed credential controller -> scoped credential reply
```

A **subscribed credential controller** is the authenticated provider-side role
registered to answer approved source/account/audience credential requests. It is
not the Gateway coordinator or an index worker. Later references to a controller
in the credential flow mean this role.

The Gateway remains a fleet-manager role. It does not become a second plugin,
installer, objective/task ledger, credential issuer, model host or deployment
manager. Reuse the product's release/lifecycle and canonical operation/receipt
contracts. Existing index persistence, provider custody and substrate/deployment
authorities stay with their owners.

The initial slice does not implement V1 authority migration, retire an old
Gateway, restore a native database backup into a concurrent writer, or duplicate
the workspace/allocation/effect-owner campaigns. Publication is not enrollment,
offload or completion of the broader V2 effort.

"New Gateway" names the product role/interface, not a second permanent
coordination deployment. An adopter selecting its containerized Gateway
integrates this slice through that service's owned lifecycle rather than
starting an index Gateway beside a status Gateway. The
[central backing-store plane](central-state-backing-store.md) uses the same
selected Gateway role with independently scoped capabilities. Unselected
adopter deployments stay untouched; a separate credential coordinator/controller
is not a duplicate Gateway to retire.

## Current reusable contracts and missing runtime

- `fleet-manager` provides role/release declarations, offline planning and local
  operation/receipt/allocation primitives. They are not resident authenticated
  Gateway or node services.
- `fleet_contracts` supplies bounded registrations, offers, route requests,
  native response shapes and generation/expiry correlation checks. Parsing or
  `check_offer()` alone does not establish caller identity or authority.
- `fleet-index-adapter` is a fixed native health/search consumer prototype.
  It does not authenticate callers or enforce corpus/source grants.
- `fleet-vault-adapter` is a fixed cached-vault pull prototype. It does not
  dispatch requests to a subscribed credential controller.
- Legacy credential-relay authorization and unverified token `exp` parsing are
  not substitutes for JWT/JWKS signature verification, issuer/audience/role/source
  grant validation or connector proof-of-possession validation.

Inventory existing unpublished auth/connector prototypes and coordinate their
owners before adding replacement code. Use supported authentication and
cryptographic libraries, not handwritten token verification. The index image
slice can use its existing loopback service contract independently; full Gateway
integration cannot be credited from that image proof alone.

## Ownership and implementation order

The Gateway/index integrator owns the vertical integration and its PRs under
#6443. Existing #6240 release/lifecycle, #6241 ledger/receipt, #5789 routing/auth,
#6030 deployment and provider owners retain their artifacts and active PRs.
Client-only owned installation #6424 remains its own slice; query/controller
integration consumes that lifecycle without taking over its implementation.

1. Review this amendment and name the interfaces and exclusive source scopes.
   Reconcile local prototypes with the merged contracts before implementing.
2. Add explicit Gateway role configuration, runtime entrypoints, endpoint
   discovery and scoped request admission. Keep thin-client installation free
   of server, model and container-manager dependencies.
3. Wire a trusted fixed index backend with stable deployment/corpus identity,
   current offer generation and backend-enforced query grants.
4. Add authenticated controller subscription and credential request/delivery
   consumption, then worker-local refresh and explicit CLI Gateway routing.
5. Prove the actual separated processes and authorized Linux container path;
   only afterward propose native corpus transfer and deployment cutover.

Do not add another lifecycle implementation or resolve an implementation conflict
by silently taking another owner's work. If a dependency cannot meet a required
contract, record the missing owned capability and review its amendment.

## Gateway role and query contract

Gateway configuration is explicit and validates role, installation/fleet
identity, trusted issuer/key references, approved service offers, source/corpus
policies and bounded request/subscription limits. Defaults remain local-first
with discovered endpoints; cross-machine access requires an explicitly
configured encrypted transport and trusted peer identity. No anonymous listener,
arbitrary HTTP proxy or public index/model port is introduced.

Verify signatures, approved algorithms/keys, issuer, audience, expiry/not-before,
principal role/fleet/service/source grants and live enrollment/generation fences.
Connector identity requires the reviewed proof-of-possession boundary; a caller
must not self-assert its role or registration by supplying parsed contract data.
Key rotation, revocation, stale discovery and unknown authority fail explicitly.

Reuse closed logical operations and bounded request/results. Resolve the actual
backend from trusted node/service configuration, never a caller URL, method,
header map or path. Authenticate native index deployment/corpus identity
independently of its publicly returned lifecycle `instanceToken` or empty legacy
installation field. Opted-in Gateway hosting must supply a stable validated
identity; native legacy configuration remains usable without Gateway enrollment.

The index CLI selects Gateway transport explicitly before local/SSH routing in
search, similar, clusters and status. Preserve arguments and result serializers.
An explicitly selected Gateway must not silently fall back to SSH, another
account or a local corpus after discovery, authorization or transport failure.
Scoped operations not yet supported by a backend are rejected truthfully rather
than advertised or served unscoped. All supported reads remain closure gates.

## Corpus/source authorization at execution

Gateway admission and backend execution both bind the principal to permitted
corpus/source grants independently of fleet membership, service enrollment or
operation permission. Resolve configured source aliases to declared canonical
stored families, including commit-source descendants. Do not treat an arbitrary
string prefix or an alias guess as a grant.

- Omitted filters mean the authorized scope, not the entire store. Explicit
  requests outside the grant are rejected.
- Vector/hybrid retrieval must apply the authorized scope before searching and
  hydrating results. An empty allowed-ID/source set must never mean unrestricted
  retrieval.
- Similarity authorizes the reference chunk before lookup and constrains
  neighbour retrieval. Cluster membership and hydration obey the same grant;
  source/bucket flags alone are not authorization.
- Status histograms, table/chunk counts and task/source metadata are scoped at
  execution or rejected when a safe view cannot be produced. Global diagnostics
  require a distinct management grant; admission health discloses no inventory.
- A query credential cannot authorize reindex, drain, deploy or another lifecycle
  mutation. Existing native control ownership checks remain separate.

Preserve legacy selector behavior for non-Gateway users. The new execution
boundary must not rely on stripping forbidden hits after unrestricted retrieval.

## Subscribed credential controller contract

Credential controllers subscribe through authenticated, scoped and revocable
identities. Bind subscription ownership to approved provider/account/audience/
source capabilities and current generation; Gateway membership alone grants no
issuance or response authority. The credential provider remains the issuer and
custodian, outside coordinator lifetime.

An index worker's request binds its enrolled service, installation, job,
configured source/account, audience/scopes, recipient and correlation identity.
The Gateway selects an eligible subscription from policy; clients cannot nominate
an arbitrary issuer or credential action. Use closed credential actions and
bounded payloads rather than unrestricted Git/CLI/REST forwarding.

Replies bind the original content/correlation, recipient and live generations
to an authorized controller and bounded validity. Reject mismatched, duplicate,
stale, revoked, out-of-order, expired or unsolicited replies. Use protected
delivery over authenticated encrypted transport; prefer an authenticated
recipient-bound envelope so the Gateway does not need plaintext secret custody.
Any envelope implementation uses maintained cryptographic primitives and tests
its recipient/correlation/expiry binding, not a custom cipher.

Keep token bytes out of durable command/audit/task records, source manifests,
argv, images, diagnostic output and telemetry. Reuse canonical durable
correlation/receipt metadata where needed, not a second task ledger. A Gateway
restart, delivery-ack loss or controller disconnect cannot become a success or
permission to reuse an uncertain response. Define truthful pending/unavailable/
UNKNOWN outcomes and bounded retry/redelivery before claiming restart safety.

Authenticated request scope does not magically downscope an external token.
An approved provider must honor the declared credential audience/permission
policy or report unsupported/denied. Never advertise a broad account token as
source-limited solely because its surrounding request is source-scoped.

## Index worker and provider consumption

Add an explicit non-secret provider selection/trust reference to standalone host
configuration. Detached workers resolve their own provider from that contract;
they must not depend on ambient settings that hosted composition removes.

Acquire and refresh at HTTP request/Git fetch boundaries rather than capturing
one credential forever during connector construction. Validate correlation,
recipient, audience, scope and expiry before use. Retain existing HTTP pacing and
pagination. Git delivery uses a reviewed process-local credential mechanism,
never a token-bearing command line or global environment mutation.

For explicitly selected Gateway credentials, exhaustion or disconnection is a
truthful bounded wait/failure under the existing queue contract, not anonymous
ingestion, stale-fetch success or another account fallback. Query credentials
and source-ingestion credentials remain distinct. Native source behavior is
unchanged when this mode is not selected.

## Validation and adoption gates

- Unit/contract: genuine signature/issuer/audience/key/role/fleet/generation
  rejection; approved and revoked connector/controller identities; closed
  operations, bounded payloads and content-bound correlation.
- Simple E2E: separate real Gateway, controller and index/worker processes.
  Credential refresh, expiry, disconnect, duplicate/stale reply and Gateway
  restart produce explicit outcomes without secret logging or wrong-recipient
  delivery. Cryptographic authentication and transport are not mocked.
- Scope proof: at least two canonical source families in one corpus plus
  distinct corpus grants. Test omitted filters, unauthorized similarity seeds,
  empty allowed sets, cluster/status metadata and lifecycle denial before
  backend retrieval; caller membership alone cannot satisfy the test.
- Fresh install/container: actual role-selected distributions, no plugin/cache
  dependency for installed use, independent empty index state, real queued
  ingestion and existing-client/new-service compatibility. A model RPC fixture
  may replace expensive inference only; seeded rows alone are not ingestion.
- Live lane: an explicitly authorized Linux target and a separate subscribed
  controller prove index CLI -> new Gateway -> container queries and distinct
  source-token request/reply/refresh. Existing Gateways, native corpus and warm
  engine remain untouched until a reviewed migration/rollback gate is met.
- Deployment/closure: artifact/release provenance, role-specific health, bounded
  update/restart and actual cleanup/ownership outcomes are recorded separately
  from proposal, mocks, compile or HTTP-health-only success.

Record every exercised tier and explicit reason for a skipped tier. Do not call
this slice complete until its supported CLI reads, credential flow, scoped
negative cases and actual live-venue contract are proven or explicitly transferred
to named tracked objectives. The larger V2 and standalone efforts remain open.
