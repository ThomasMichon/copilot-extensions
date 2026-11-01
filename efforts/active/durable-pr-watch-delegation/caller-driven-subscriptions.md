# Caller-driven backend and PR-set subscriptions

Back to the [canonical effort](README.md) and
[service contract](pr-service-contract.md). Coordination remains #7188 under
#6007. This is an **agent-recommended proposal**, not an implemented route,
service activation or accepted deployment.

## Required usage

The operator expects PR-creating agents to ask the central service to connect
to different backends and subscribe to PR sets. Reviewer task emitters also
request subscriptions to ranges of PRs that need watching. The verbatim
clarification is in the effort's Request.

Interpret reviewer ranges as declared PR populations selected within supported
repository/provider criteria, rather than inventing a numeric-interval API or
an unrestricted query language. Exact selection syntax follows the existing
provider and emitter contracts. Continuing membership, initial population
delivery and change delivery are explicit parts of the proposed contract.

Native/fleet **client access** and forge **backend connections** are different
choices. Configuring the former, or exposing only a trusted programmatic
consumer factory, does not implement the latter.

## Reuse and current boundaries

| Existing owner | Reuse or extend |
|---|---|
| PR service host and Gateway | Existing connector, admitted request context, namespace, bounded admission and drain; current startup-only scope/observation maps need an intake seam. |
| PR registry, replay store and observer | Durable acceptance, identities, baseline/continuous-watch foundations, replay/ACK and shared native observation; keep one PR-subscription authority, not a second watcher or process per subscriber. |
| Native provider/inventory | Provider-qualified PR facts and repository discovery. Extend supported bounded enumeration and renewable credentials where missing; unsupported legacy listing is not evidence that no inventory owner exists. |
| Credential provider and Fleet | Existing backend/account/resource admission, issuance, renewal, revocation and placement; client request labels grant none of these. |
| Worktrees and PR clients | Confirmed PR identity, publication policy, caller claim and uncertain-creation reconciliation; subscription failure cannot undo or repeat publication. |
| Reviewer producer and Dispatch | Eligibility, durable task deduplication, revision handling and one reviewer lineage; the service supplies observations, not task or verdict authority. |

This does not take over PR conformance, generic producer caching/batching,
repository fleet sweeps, fleet controllers or the independent periodic
evaluator campaign.

## Logical operations (proposed)

These are operation families, not published command or wire names.

| Family | Required behavior |
|---|---|
| Connect/reconcile backend | Accept an attributable caller request for a supported backend reference; resolve its current admitted provider/endpoint/account and credential source, retain stable connection identity and report actual availability. |
| Subscribe/reconcile exact set | Durably accept confirmed backend/repository/PR identities, consumer criteria and lifetime as one bounded set; preserve independent member identities and original acceptance across partial work or lost results. |
| Subscribe/reconcile selection | Retain a bounded repository/criteria selection and its discovery progress, membership and baseline; observe eligible additions, changes and departures for the subscription lifetime. |
| Read selected PR facts | Supply bounded current native PR facts and selection/member status needed by existing eligibility and review consumers, with honest freshness and unsupported/unknown fields rather than another client-side forge poll. |
| Consume/ACK | Expose only the admitted consumer's bounded pending view; ACK only the matching subscription/member/event after its consumer commits the intended effect. |
| Update/cancel | Reconcile the exact connection/selection revision and original request; cancellation stops that subscription without withdrawing other subscribers' shared observations or ACKing unrelated events. |

An empty service can be ready to accept supported demand without predeclaring
every future PR in its startup configuration. Connecting to an already-admitted
backend or adding a PR does not require a service restart. Unsupported
providers, unavailable credentials and an unhealthy connection remain distinct
from successful registration or a complete empty selection.

## Backend and selection admission

Backend references resolve through existing trusted provider/credential
configuration. Requests do not supply executable argv, arbitrary callback
destinations, token values or authority to enroll another account. A caller may
ask for a connection within its admitted scope; it does not become a fleet
administrator or credential issuer. Do not add a separate human-signoff
ceremony for each request that existing authority already permits.

Preserve the exact provider endpoint, account, repository, service namespace
and consumer boundaries through connection reuse, registration, discovery,
observation, replay and cancellation. Recheck current authority at existing
lookup/effect boundaries. A changed configuration cannot silently adopt an
older connection's unsettled registrations; withdrawal behaves as disconnect,
not proof that an in-flight write rolled back.

Selectors use declared, bounded provider-backed criteria. Unsupported filters
refuse rather than broaden to every PR. Bound pages, results, in-flight work,
storage and retries through the existing owners. A partial, truncated,
rate-limited or failed inventory is not a complete empty population and cannot
remove every prior member or fabricate PR closure.

## Shared observation and durable meaning

Use the existing service cadence and observer, with discovery incorporated
under the same bounded owner rather than a polling loop in each client or
emitter. Overlapping sets and selectors share only authorized identical
backend/account/target observation. One slow backend or selection cannot
starve unrelated admitted work.

Persist acceptance and recoverable progress before reporting success. Preserve
connection, subscription, member, request and event identities through restart,
lost responses and partial set expansion. An accepted definition is not
evidence that every provider read is available; expose that uncertainty.

Initial selection membership is candidate discovery, not evidence of a PR
transition. Later matching additions, meaningful PR changes, departures,
timeouts and explicit re-evaluation have distinguishable reasons. Repeated
unchanged inventories or snapshots produce no duplicate task or false resume.
Provider gaps do not become an invented gap-free event history.

Independent consumers keep their own baseline, criteria, deadlines and ACK
progress. Updating a selector does not discard its previous revision's
unsettled deliveries. Connection reuse and service restart do not extend
original one-shot wait deadlines or manufacture a new subscriber.

## Author and reviewer consumers

After native creation/tracking confirms a PR and its existing claim, the
configured author-side integration requests the connection and exact-set
subscription. Record recoverable subscription intent beside the existing
owning PR state. A later registration failure reports an already-created PR
with unresolved observation work; it must not trigger another create/push,
lose its PR claim or pretend central watching succeeded.

Reviewer emitters register their repository/criteria population and consume
shared discovery/change deliveries and selected PR facts. The contract must
supply the facts their existing eligibility policy needs; a generic wake or PR
number alone is not equivalent integration. Missing facts stay unavailable or
unsupported, not an implicit eligibility decision. Existing eligibility and
policy decide which observations warrant tasks. Commit through the existing
target/revision deduplication and reviewer-lineage owner before ACK. A lost emit result
reconciles that task, not a replacement review or a second task queue.

When central observation is selected, those consumers do not retain a hidden
independent forge poller for the same scope. Unselected supported native paths
remain usable. This amendment does not rewrite repository-specific reviewer
policy or activate an existing live emitter.

## Compatibility and delivery sequence

1. Land the revised vision and this contract before implementation.
2. Expand the existing service provider with admitted backend connection and
   exact-set intake, durable recovery and actual capability advertisement.
3. Add bounded native/provider-backed discovery and continuing selections.
4. Conditionally migrate author creation/tracking and reviewer emitters through
   supported service capabilities, preserving their current owners.
5. Qualify integrated current/prior/rollback paths and then perform separately
   authorized image, provider and installed/fleet acceptance.

Keep strict v1/v2 requests, responses, defaults, durable identities and legacy
native behavior unchanged. Select an explicitly supported extension before
new effects; old providers must refuse unsupported intake meaningfully.
Rollback retains unsettled new state and obligations rather than converting
it into legacy watches, silently dropping selection progress or creating a
fallback poller. Persisted-schema migration and writer/lifecycle ownership
remain with their established contracts.

## Validation (agent-recommended)

- Actual authenticated client/service intake for two admitted backends, a ready
  empty service, repeated/lost connection results and refusal of foreign or
  unsupported scope before later effects; no supplied secrets or command code
  enter durable registration.
- Exact sets and overlapping reviewer selections share native observation;
  one subscriber's cancellation does not remove another's watch. Measure the
  owning fetch counts and bounded work, not merely a helper invocation.
- Initial population, a newly matching PR, a meaningful existing-PR change,
  departure and repeated unchanged observations have distinct consumer effects.
  Partial pages, provider loss and unsupported filters never masquerade as
  complete membership or terminal PR facts.
- Real author create/claim followed by registration failure/lost result
  reconciles the original PR and subscription without another publication.
  Real reviewer discovery/delivery and sufficient scoped native facts reach
  existing task deduplication and preserve one task/worktree/session lineage
  after replay and restart.
- Preserve original identities/deadlines across crashes during set expansion,
  selector updates, effect-before-ACK, stale ACK/cancel and provider/credential
  loss. Validate namespace/account separation and current-authority refusal.
- Exercise supported old/new clients and providers, rollback, unknown
  capabilities and persisted-state compatibility through actual front doors.
  Fresh isolated native and client/service tests precede any approved container
  or live provider lane; unavailable image/installed/fleet evidence stays open.

These obligations do not expand required CI into an exhaustive native matrix
or waive any existing provider, image, installation or deployment gate.
