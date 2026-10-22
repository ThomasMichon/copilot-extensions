# PR-service consumer contract proposal

Back to the [effort](README.md) and [fleet service plan](fleet-pr-service.md).
Tracking: #7188; retained Gateway/routing owners: #6236/#5789/#6443/#6809.

**Driver-owned contract proposal for normal PR review, not an implemented API.**
The names below identify logical operations, not published routes, wire schemas
or new `OperationIntent` commands. Review of this document does not transfer
Gateway, authentication, credential-provider or deployment ownership.

## Coordination without an approval rendezvous

The PR-service driver owns closing the consumer contract and its implementation
gaps through ordinary reviewed source changes. Consult the related campaigns
and inspect current contracts and overlapping unmerged work, but do not wait
for separate owner acknowledgements before designing, testing or proposing a
shared-interface change. Silence is neither approval nor a veto; normal review
and required checks decide whether a concrete source change can land.

Keep campaign/resource ownership distinct from contribution authority. Reuse
existing components and contribute a missing shared seam in its owning module
rather than inventing a parallel controller or claiming an unavailable API
exists. Resolve actual overlapping edits, incompatible contracts or competing
external PRs explicitly; a tracker with a retained owner is not itself such a
conflict. Missing implementation is work to deliver, not an indefinite
permission dependency.

The checklist below records required contract decisions and executable proof,
not signatures to collect. Runtime activation, credentials, placement and live
resource changes still require their existing authorization; source review
does not grant any of them.

## Available transport and implementation gaps

The current [opt-in service Gateway](../../../fleet-manager/docs/gateway-service-profile.md)
supplies authenticated TLS HTTP routing through outbound service channels,
explicit caller/slot/method grants and bounded response streaming. It is not
the older [static index/credential Gateway](../../../fleet-manager/docs/gateway-runtime.md);
ordinary Gateway invocation retains that older profile unchanged. Neither
profile already implements the PR operations proposed below.

The service profile currently bounds request bodies to 32 KiB, requests to
60 seconds and pending requests to 32 globally / 8 per channel. PR batches
and polling must fit those limits; a Gateway request is not a durable wait.
Its HTTP proxy strips caller credentials, cookies and client identity headers.
The optional `caller-context-v1` connector handler now supplies Gateway-derived
fleet/Gateway/slot/principal and bounded expiry over the admitted TLS channel;
it never forwards identity headers to the HTTP backend. The service must still
map that admitted principal to its configured consumer/account/repository scope
at its real operation boundary. This SDK seam alone does not implement PR
routes, persistent namespace/writer custody or worker forge credentials.
Do not infer authority from stripped headers or request-body labels.

The [adoption readiness record](../../../fleet-manager/docs/gateway-adoption-readiness.md)
separates generic source and isolated native amd64/ARM64 qualification from
actual provider/live-edge interoperability, durable accepted-revision custody,
install/update/drain and managed placement. The initial generic campaign
#7257 is archived via #7343, with native qualification in #7393/#7436;
its provider/live-edge follow-ons and #5789/#6804, #6240/#6424 and #6030
remain retained gates. Those proofs are not PR-service acceptance or deployment
approval. The [runtime admission library](../../../fleet-manager/docs/runtime-admission.md)
and its isolated authority snapshot/serialized permit are not a substitute
for the actual serving, backend delegation and external-effect boundary.

## First vertical slice

One operator's clients register PR watches with one explicitly selected PR
service. That service shares authorized forge observations and retains durable
registrations and pending terminal events. A dispatch coordinator consumes
its own events through one shared outbound connection or bounded polling loop,
not a process per waiting task.

The PR service owns this durable event state. The Gateway routes the reviewed
service contract; its in-memory credential broker is not the PR queue. Native
callbacks remain a separate supported delivery adapter for local use. Remote
service registration accepts no callback executable, shell expression or
caller-selected delivery URL.

This first contract covers observation and subscription delivery. It does not
grant remote merge, publication or review authority. Those PR-domain operations
retain the parent effort's provider/policy conformance work and existing owners.

## Scope and authority

Every admitted request binds the selected fleet, PR service/installation
namespace, consumer identity and authorized provider account/repository scope.
All belong to the fleet's one operator. Different applications or accounts of
that operator are not additional operator tenants and do not automatically gain
each other's subscriptions, event metadata or forge permissions.

The service derives identity and allowed account/repository selection from the
implemented authenticated route and trusted configuration. A request body cannot
nominate a different operator, credential issuer, provider endpoint or account
merely by supplying its name. Sharing an observation requires the same authorized
target/account scope; a common PR number or repository string alone is insufficient.

Clients' native installation receipts do not authorize a remote fleet role.
The remote service validates its own selected identity and the scoped caller.
The exact current `runtime_admission` API supports observation branches and
existing operation intents, not these PR requests. Do not construct an unsupported
intent or serialize an internal `Permit` to manufacture backend authority.
Implement and exercise the actual admission/delegation boundary; if a shared
seam is missing, propose that extension in its owning module through normal review.

## Logical operations to deliver

| Operation | Required semantics |
|-----------|--------------------|
| Capabilities/health | Advertise only implemented versioned behavior and the caller's supported scope; no global registration or credential inventory |
| Register | Durably accept the caller's request identity, target, criteria, original deadline semantics and consumer binding before success |
| Inspect registration | Reconcile that same caller/request or registration identity; lost registration responses are not evidence of absence |
| Cancel | Affect only the matching caller and registration generation; a stale cancellation cannot remove its replacement |
| Read pending events | Return a bounded batch from the admitted consumer's durable pending view, preserving event/registration identity; reading is not ACK |
| Acknowledge event | Commit removal/completion only for the exact consumer, registration and event; never acknowledge a newer registration by subscriber name |

The existing local registry already has stable registration/event identities
and acknowledged pending events. The remote adapter should reuse that domain
behavior. It must add explicit request reconciliation and remote delivery
semantics rather than assuming that repeating today's replace-by-subscriber-ID
registration is an idempotent network retry.

The internal [PR-owned replay core](../../../plugins/agent-pull-requests/docs/watch-replay.md)
now supplies that source-domain transaction behavior. Its caller-provided scope,
single-writer custody and commit callback are embedding obligations, not already
implemented serving/Gateway authority. The optional
[PR Gateway adapter](../../../plugins/agent-pull-requests/docs/watch-gateway.md)
now supplies configured admitted-principal scope mapping and versioned bounded
operations around an explicitly provisioned complete-envelope disk store. Its
existing per-operation lock prevents overlapping transactions; that is not
active placement/lifecycle custody or an atomic enrollment-revocation fence.
Neither component activates a production role or dispatch consumer; the full
delivery checklist remains open.

Revocation uses the same failure semantics as a network disconnect. Current
Gateway policy stops admission and emission and cancels the affected channel
request; publication does not wait for an uncertain service-side effect.
Connector-owned local request lifetime must be checked before subsequent
lookups/commits/results, including offloaded disk work. An already-started
commit can still finish. Retain its transaction custody and original request
identity for reconciliation under fresh authorization. No distributed revocation
barrier, retroactive rollback or proof of nonacceptance is promised.

Delegated dispatch waits retain their existing finite positive timeout
requirement. This slice does not impose a new mandatory timeout on supported
native subscriptions that previously allowed no timeout.

ACK results and request reconciliation need an explicit bounded replay/retention
contract. Once an outcome is outside that supported window, return uncertainty
rather than treating an unknown record as permission to register a second wait.
Do not promise unlimited tombstones, exactly-once external effects or an
unimplemented global event log.

## Consumer flow and failure boundary

1. Dispatch prepares its existing generation-fenced suspended wait before
   registering. Bind the intended PR request to that prepared generation.
2. Register through the selected service and retain the acknowledged registration
   identity. An ambiguous response is reconciled by the original request identity;
   do not blindly repeat a replacement registration or arm a process fallback.
3. The service records a terminal transition or timeout durably. Multiple
   subscriptions may observe one shared snapshot but retain independent criteria,
   deadlines and delivery identities.
4. The coordinator's shared consumer loop reads its bounded pending view. It
   applies the existing idempotent completion fence to the original task generation.
   It ACKs only after its own effect is durably committed or its own lifecycle
   proves that the delivery is no longer applicable.
5. Reconnect and ACK-response loss replay or reconcile that same event identity.
   Service/coordinator restart must not move the deadline or invent a new task
   generation. A duplicated callback effect remains the consumer's idempotency
   responsibility, not an exactly-once transport claim.

Do not keep one process, thread or long-held Gateway request per task.
The shared consumer loop observes existing request deadlines and bounded
backoff/batches. It must not lengthen the Gateway's request budget to emulate an
unbounded stream. A future push transport is optional only if its owner supplies
equivalent scoped replay and reconnect semantics.

Compatibility negotiation occurs before accepting a wait. An unsupported fleet
backend is explicit; once acceptance is uncertain, only reconciliation may
establish whether any fallback is safe. Losing transport is not permission to
select a different account, local owner or second subscriber.

## Forge credentials

The service's observation worker obtains the minimum authorized forge access
through its operator's configured provider binding, renewing at the actual
HTTP/Git boundary. Watch access is not permission to mutate a PR or administer
the fleet. A token label cannot downscope a provider's broader credential.

The existing credential Gateway now accepts a distinct `pr-worker` enrollment
without an index corpus. Its rules and credential requests require an exact
typed `ForgeScope` (provider, HTTPS endpoint, repository, observation permission
and authentication type), bound through provider policy, verified material and
recipient-sealed delivery. The existing controller/provider retains actual
resource/permission/expiry/revocation validation; target labels alone cannot
downscope a token. The string-only index consumer refuses that typed binding.
The opt-in generic service profile does not itself acquire forge credentials.
A PR worker must not masquerade as `index-worker`, invent corpus/source values
or forward ambient credentials. Wiring the typed exchange into the actual
native provider HTTP/CLI boundary and validating production provider results
remain open before production consumption. Existing provider custody, expiry,
revocation and provider/OS session longevity remain authoritative.

No secret enters registration state, replay records, images, manifests, argv
or diagnostics. Actual provider challenges are visible; no periodic human
presence check or new token issuer is introduced by this consumer.

## Contract delivery checklist

The driver closes these items with reviewed contracts and implementation evidence,
not a separate round of owner sign-offs. Consultation is non-blocking; actual
conflicts and required repository review remain binding.

- [x] Define and test the versioned PR service offer, supported logical
  operations, actual request/response bounds, trusted backend identity and
  admission/delegation verification point (source adapter #7832 and serving
  witness #7915; no installed offer or deployment is claimed).
- [x] Bind and exercise current policy/enrollment authority and its
  lookup/effect/emission checkpoints in the real serving path, with the
  disconnect semantics above; an isolated snapshot/permit DTO does not count
  as serving authority. Prove both refusal before a later effect and original-ID
  reconciliation of a commit already in flight at policy withdrawal
  (#7915, actual TLS/PoP/channel and disk with synthetic issuer material;
  protected provider enrollment and durable lifecycle authority remain separate).
- [ ] Define and test the PR worker's account/repository/action binding,
  provider-result validation and renewal/revocation outcomes without reusing
  an index identity or another operator's credentials.
- [ ] Implement request reconciliation and bounded consumer-scoped replay/ACK
  semantics in PR and dispatch, including uncertainty after retention and ACK
  loss after a committed generation-fenced effect.
- [ ] Supply and validate the service namespace, persistent storage, single
  active writer and supported drain/update boundary through the existing
  placement/lifecycle interfaces. No live migration, volume sharing,
  rescheduling or deployment follows from this document.

## Acceptance to carry into implementation

Use real local client/service processes and synthetic identities first, followed
by the selected isolated container/Gateway path. Tests must distinguish
transport mocks from actual serving integration.

- One authorized target and two independent subscriptions cause one observation
  fetch per poll iteration. Different account scopes cannot share unauthorized
  results; one slow target does not stall another.
- Lost register response reconciles the original identity without replacement.
  Restart before/after event persistence, effect commit and ACK preserves event
  identity, deadline and dispatch generation.
- A stale ACK/cancel cannot affect a re-registered subscriber. A caller cannot
  inspect or acknowledge another consumer's events, even with a known identifier.
- Reads/continuation state expose no unrelated event count, cursor position or
  registration metadata. Unknown/expired replay state is explicit uncertainty,
  not success or proof that no registration exists.
- Credential expiry, revocation and provider disconnect remain scoped and
  visible without changing issuer/account or persisting a secret.
- Native CLI/callback behavior remains compatible. Remote mode never executes
  supplied argv, selects an arbitrary destination, or starts one waiter per task.
- Service/Gateway/coordinator outages preserve PR-owned pending events and
  truthful recovery. A healthy container or an in-memory broker response alone
  does not satisfy this contract.
