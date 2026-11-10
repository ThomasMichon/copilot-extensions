# M1: standalone repository PR cache and events

Back to the [canonical effort](README.md), umbrella #6007 and bounded planning
claim #11779. This is a review-first milestone amendment, not runtime delivery
or operational authority. The effort's existing integration owner retains
completion and assigns implementation slices; provider/native, credential,
Gateway/SDK, replay, workspace, publication and lifecycle owners remain.

## Request

Operator's selected first milestone, verbatim:

> Without an agent-dispatch emitter hooked up, or agents told to call it directly, it should basically just be a locally-qeueryable cache of PR state for target repos, as well as a subscribable event-emitter. That would be a great first milestone.

The requirements and staging below are **architectural recommendations**, not
additional operator quotations. They make this first milestone independently
useful without reducing the full parent acceptance. Explicitly configured
GitHub and Gitea repositories are the initial provider targets; no downstream
deployment topology or particular repository is prescribed.

## Scope and retained parent

M1 supplies bounded repository discovery, durable normalized **current PR
state**, a supported local query front door and meaningful resumable
subscriptions on one observation owner. It must work before agent consumers,
Dispatch task emission, a pane, complete CLI parity or production writer/merge
cutover. No forge writes, task emissions or automatic consumer enrollment are
part of its read-only profile.

Static trusted repository/backend selection at startup is sufficient for M1.
Caller-driven connections and dynamic PR-population intake remain later
requirements in the [PR vision](../../../visions/plugins/agent-pull-requests/README.md#caller-driven-backend-connections)
and [retained parent outcomes](retained-pr-service-outcomes.md); they are not
silently removed or made speculative prerequisites. Later subscriptions may
select admitted configured populations without gaining backend authority.
Existing publication/completion implementation continues independently; it is
not an M1 dependency. Full Author/Reviewer, provider/client/fleet, operational
and original/refined/inherited parent acceptance stays open with its owners.

This closes a first slice of
[centralized watcher/registrar](../../../visions/plugins/agent-pull-requests/README.md#centralized-watcher-and-registrar),
[selected PR populations](../../../visions/plugins/agent-pull-requests/README.md#explicit-sets-and-selected-pr-populations),
[durable delivery](../../../visions/plugins/agent-pull-requests/README.md#durable-transport-appropriate-delivery)
and [one service contract](../../../visions/plugins/agent-pull-requests/README.md#cli-through-one-service-contract).
It follows the existing [patterns and invariants](../../../docs/patterns/README.md),
especially [service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
It neither revises the north star nor asserts that every interface already
implements it.

## Source facts and concrete seams

These are source capability/gap facts, not installed or live qualification:

| Existing owner | Established capability | M1 seam, not an existing guarantee |
|---|---|---|
| `services/pr/src/agent_pull_requests_service/__main__.py`, `config.py`, `hosting.py` | Separately packaged standalone `provision`, foreground `serve --config`, passive `ready --status`; strict trusted host configuration and explicit state adoption | Integrate repository/cache/event selection into this actual host, not only plugin-local `serve` or injected factories |
| `WatchService`, `WatchGateway`, `ServiceConnector` | One host-local writer, fixed admitted serving/observation bindings, Gateway/SDK admission, bounded observation and drain; the actual host currently constructs the connector profile | Add a supported local-ingress profile within this host, not a parallel service/lifecycle/controller or hidden poller; existing optional workspace/attention/report routes stay default-off |
| `ReplayObserver`, `NativeObservationBinding`, `watch_provider.fetch_snapshot` | Shared per-PR native snapshots for `ReplayStore.observation_targets()`; current targets originate in registered waits | Configured repository discovery must supply membership independently of waits; endpoint/provider-qualified isolation must reach all relevant bindings, not rely on account/repo labels alone |
| Worktrees provider `list_open_pulls` and versioned native `pr-watch snapshot` | Existing provider inventory primitive and normalized/current native PR reads | A bounded service inventory interface and executing read-credential support for both GitHub and Gitea still require qualification; existence of the primitive alone proves neither |
| `PRSnapshot`, `Baseline`, replay store/registry | Normalized fields plus `observation`/`native_snapshot`, subscriber baselines, durable pending/fired events, ACK and cancellation | Subscriber baselines are not a repository current-state query model; a one-shot terminal wait is not a continuous subscription |
| `ConversationJournal`, `WorkspaceCache`, optional report readers | Conversation metadata, workspace-owned representations and transient thread/diff reports | None establishes a durable repository PR cache; the selected native report adapter is GitHub.com-only, not Gitea inventory evidence |

The [standalone report-host contract](standalone-report-host.md) and its
[source journal](journal/2026/11.09%20Standalone%20report%20host%20implementation.md)
record the actual host's report adoption. Reuse its lifecycle and configuration
discipline, not report credentials for observation or transient reports as cache.
The corresponding `services/pr/tests/test_host.py`, `test_report_host.py`,
`test_report_host_transport.py` and `test_report_host_lifetime.py` are existing
host qualification anchors, not proof that M1 already works.

## Observation and repository membership

One bounded observation owner shares acquisition for configured repositories
and overlapping subscribers. Inventory and snapshot acquisition retain their
existing provider/native and credential owners; do not introduce a second
forge client or one provider poller per query/subscriber. Define finite page,
PR-count, concurrency, byte, time and cadence budgets and expose coverage when
limits are reached. Increasing subscriber/query count must not multiply forge
fetches. Queries read the projection, not the forge.

Each configured target is qualified by provider, endpoint, account and
repository within the service namespace. PR number alone, or account/repo
without backend identity, cannot merge two forges' state. Trusted startup
selection must map to the executing native/provider and read-credential
contract; caller labels, ambient account selection or GitHub report support
cannot establish Gitea authority. No arbitrary request-supplied executable,
credential command, import or backend connection is accepted.

Inventory outcomes distinguish **complete**, **partial** and **unavailable**
with bounded coverage/error and last-success evidence. A failed page, capped
scan or outage cannot erase known membership or fabricate closure. Initial
empty-complete is distinct from never-populated/unavailable. New identities
enter from observed inventory; a complete scan's omission establishes only
departure from the open selection, not merged/closed status. Reconcile departed
known PRs through bounded native reads to obtain authoritative terminal facts,
or retain explicit unknown/stale departure evidence. Do not lose unsettled
delivery/effect identities when membership changes.

## Durable current-state projection and query

Connect discovery and current snapshots to a service-namespace-owned durable
read model. Reuse `PRSnapshot` and current cache/journal transaction owners
where their semantics fit; do not silently repurpose subscriber baselines,
conversation metadata or workspace storage. Implementation must select the
specific existing store owner and versioned adoption seam before adding a
layout; the logical projection is not permission for another database owner.

The query model must distinguish known membership, last confirmed facts,
observation time/freshness, current candidate/native evidence, coverage and
unknown/partial/error state. Missing evidence is not green checks, approval,
mergeability or closure. Terminal summaries remain queryable under a documented
bounded retention policy; rebuilding observation cache must not lose or replay
unresolved effects, pending events, ACK/cancellation or registration identities.
Persist only the bounded current facts and event/recovery metadata required:
not full diff/chat archives, report bodies or credentials.

Expose repository listing/status and individual current-PR lookup through the
existing service/HTTP contract and an actual supported locally invocable CLI
or HTTP client path. Include target selection, bounded pagination and truthful
stale/unavailable/unsupported results. An injected Python factory, direct
store-file read or passive health/status file is not this front door.
Preserve existing admission, scope isolation and response/capture budgets.
The exact command names, wire fields and capability advertisement are
implemented source contracts in
[the local profile reference](../../../services/pr/LOCAL.md), not claims of
released, installed or running support.

### Local hosting is independently usable

M1 does not presume a running fleet Core or deployed Gateway. The vision
distinguishes local standalone hosting from optional Gateway coordinator
hosting. Current `hosting._serve` loads connector credentials and constructs
`WatchService`, whose constructor creates `ServiceConnector`; that is an
actual local-use seam, not proof of an independent local front door.

The concrete reusable boundary is `WatchGateway.handle`/`handler_binding` and
the SDK's `gateway_service_handler` request/response, caller-context,
request-lifetime and bounded-handler types. Their **current** caller context
is derived from an authenticated Gateway channel. There is no established
local admission API to invoke by name or permission to synthesize a trusted
Gateway identity from request headers.

Implement an explicit local ingress/CLI-or-HTTP hosting profile through the
existing standalone host and service/SDK owners. Select a bounded local
transport, authentication and principal-to-target mapping; enforce strict
read-only admission, response limits, disconnect/expiry and drain through the
canonical handler semantics. Local reachability alone grants no authority.
Reuse/refactor connector-specific construction only as required for that
profile; preserve the current connector profile and its authenticated context.
An ingress adapter in the existing host is legitimate, not a second invented
HTTP service or lifecycle. Qualify its config/wire/admission decisions in
stage 1 and realize them before the local-client vertical.

An actual supported local client must query and subscribe with no running
Core/Gateway deployment. A synthetic Gateway fixture, direct Python handler
call or status-file read cannot satisfy this exit. Optional coordinator-hosted
compatibility remains, but completing a fleet/Core campaign is not an M1
source prerequisite.

## Continuing event subscriptions

M1 requires a continuing logical subscription, not rebranding existing
one-shot waits. Recommended minimum: a durable registration over an admitted
configured population, bounded cursor-based event reads (long-poll or paged
replay through the existing transport), per-consumer ACK and explicit
cancellation/expiry. A new streaming protocol or permanently open connection
is not required. A supported client must actually continue consuming later
events without recreating a terminal wait for each change. These client reads
must never become independent forge polling.

Reuse/extend replay/pending/ACK/cancellation semantics under their current
owner. Existing one-shot registration, lazy baseline and transition meanings
remain unchanged for old consumers; continuing semantics require explicitly
qualified support. Independent consumers and restart retain their original
registration/event identities and independent ACK positions. State publication
and event publication need a coherent recovery boundary so a crash cannot
publish an unqueryable state or lose its corresponding durable event.

Distinguish initial population/baseline delivery, later entry, meaningful
normalized changes, departure, confirmed terminal outcomes and unchanged
observations. Initial population must not impersonate a later change;
unknown-to-known baseline adoption follows explicit semantics rather than
inventing a terminal transition. Identical observations and timestamp-only
refreshes do not produce semantic PR-change events. Coverage/freshness changes
may have separately typed status events, never forged PR outcomes.
Define event ordering, snapshot/cursor handoff, retry/deduplication, bounded
backpressure and history-gap recovery. An expired cursor must report a gap and
an explicit resynchronization path, not silently skip events or manufacture
successful ACKs. Retention cannot evict unresolved pending delivery merely to
make a query look complete.

## Compatibility, storage and lifecycle

Implement provider-first expansion inside existing owners. Declare contract
identity/support range and optional repository-cache/continuing-event
capabilities against the selected executable and running service, not a newer
payload or package equality. Old clients retain old requests/defaults;
new clients choose new forms only after support is established. Missing
metadata uses only a proven legacy baseline; unsupported M1 features are
explicit, without disabling ordinary supported watch/report operations.
Preserve strict CLI/config parsing, required semantic enforcement and
new/old, old/new, new/new and supported rollback tests.

The strict stored-schema/adoption contract is a stage-1 decision: identify
accepted current/prior layouts, explicit provisioning/adoption, any authorized
upgrade and truthful newer-layout/downgrade refusal. Wire tolerance is not
storage tolerance. No automatic production-data migration, deletion, lossy
fallback or silent recreation of an unknown layout is authorized. Source
fixtures use fresh provision and retained synthetic state across restart;
preserve native/replay namespace separation, workspace rules, existing
registrations and unsettled effects. Any later retained-data adoption belongs
to its actual owner with explicit authority.

Keep the actual `services/pr` foreground lifetime, trusted configuration,
single writer, admission and cancellation/drain custody. Provision/config
validation must not probe provider credentials or install dependencies.
Optional repository selection absent preserves current hosting behavior;
selected missing support refuses honestly. Stop/withdrawal prevents new work;
unsettled reads or disk work retain the original writer until joined.
Readiness is passive process evidence, not proof of inventory completeness,
data freshness, live usefulness or permission to replace a retained host.

## Serial implementation roster

The integration owner assigns scoped workers only after reviewed intent.
Stages may use narrow patches within each stage; stage exits are serial
dependencies, not a new sign-off rendezvous or release/installer campaign.

| Stage / responsible existing owner | Bounded patch roster | Exit before the next stage |
|---|---|---|
| 1. Integration contract qualification / service/SDK, native/provider and replay owners | Select actual bounded inventory/read-credential seams for GitHub and Gitea; qualify full target identity; settle independent local ingress/admission, wire/config/capabilities, continuing semantics, projection store/schema/adoption and budgets against executing APIs | Reviewed source contract decisions and focused parser/provider/schema fixtures; honest unsupported paths, no guessed API or credential authority |
| 2. Discovery and durable projection / observer plus store owners | Reuse inventory/current native reads; extend the one observation cycle, membership/completeness and normalized current-state persistence; preserve existing waits/effects | Both synthetic providers populate without any registered wait; partial/outage/departure and restart controls pass with measured shared fetch counts |
| 3. Supported local ingress, query and continuing subscriptions / service/SDK, frontend and replay owners | Add the explicit local hosting profile and qualified reads plus durable continuing registration/replay/ACK/cancel through canonical handlers; preserve connector profile and one-shot defaults | Real admitted local client queries and continues receiving changes after ACK/reconnect/restart without running Core/Gateway; mixed-version and isolation controls pass without new forge readers |
| 4. Integrated source vertical / standalone host and integration owners | Wire strict optional selection into actual `services/pr`; run foreground host/client/persistence/subscriber controls, typing and affected guards; update user-facing operating docs | Reviewed source merges and the complete synthetic vertical below pass; document exact commands/config, footprint, limits and remaining qualification |
| 5. Separate read-only shadow qualification / integration and operational owners | Confirm release, installed/running support, authorized target/credential/hosting selection and footprint; query and subscribe to real admitted targets without writers or Dispatch | Actual released/installed/running read-only evidence satisfies M1; retain full parent Plan/Validation and all later cutover gates |

No separate publication/completion PR is a dependency of these stages.
Record a real newly discovered prerequisite by concrete failure and existing
owner rather than adding speculative dynamic-backend, full-parity or generic
permission gates.

## Source evidence map

The stage-1/2/3 source deliveries are #11790, #11838 and #11866. The
permanent stage-4 tests below qualify the packaged standalone foreground,
not an injected Gateway substitute. Source review and required CI remain
mandatory for every delivery; this map is not a merge waiver.

| M1 source criterion | Permanent proof |
|---|---|
| Both native providers, qualified identity, passive config, selected declarations and rollback | Worktrees `test_pr_inventory.py` and `test_pr_observation_credentials.py`; core `test_repository_contract.py`, `test_repository_schema.py`, `test_repository_subscriptions.py`; standalone `test_repository_config.py` and `test_local_repository.py` |
| Actual independently packaged foreground and CLI without Core/Gateway | Standalone `test_local_foreground.py` runs provision/serve/ready/list/query/subscribe/inspect/events/acknowledge/cancel/follow as isolated module processes; `test_distribution.py` builds a checkout-independent wheel |
| Population without waits, later entry/head/review/check/terminal/departure, no timestamp events | Both provider variants in `test_local_foreground.py`; core `test_repository_discovery.py` preserves unknown-to-known and authoritative native facts |
| Partial/capped/unavailable discovery, stale membership, empty versus unknown, recovery and budgets | Foreground partial/outage/departure and initially-empty controls; native inventory and core discovery budget/cap tests |
| Shared acquisition, independent consumers, no query-triggered readers, no forge writes/tasks | Foreground held-inventory query/inspect call counters and explicit zero write/task counters for both providers; core discovery shared-fetch counters |
| Original-root restart, immutable registration/event IDs, independent ACK, expiry/cancel, gaps/backpressure | Foreground restart and finite follow receiving a later change; core `test_repository_subscriptions.py` gap, delivered-ACK and byte-pressure cases; standalone `test_local_client.py` |
| Persistence failure/recovery and bounded withdrawal joining exact work | Standalone `test_local_recovery.py` interrupts real V2 registration/state+event replacement and recovers original IDs/expiry/ACK; `test_local_lifetime.py` holds inventory/snapshot/disk cleanup while proving withdrawal and writer exclusion; core discovery pre/post-replace controls and `test_repository_local.py` owned-transport controls |
| Default connector compatibility and typing | Existing standalone host/report/installed-process controls remain selected independently; strict `service-pr` targets analyze Linux and Windows; local runtime tests are Linux-only |

These are source qualification surfaces, not host deployment artifacts or a
native deployment exemption. [LOCAL.md](../../../services/pr/LOCAL.md) owns
the exact arguments, UID/socket/root and native/credential-command contracts,
budgets, retention and failure behavior. A retained deployment still needs its
actual artifact/lifecycle and credential custodians; a portable foreground
interface does not select them.

The source vertical's operating flow is documented in
[TESTING.md](../../../TESTING.md#optional-pr-service-lifecycle).
Whole Fleet strict typing remains separately tracked by #9807; the focused
standalone strict result is not a whole-project claim. Release promotion,
installation, running support and real read-only target/event evidence are
**open stage-5 acceptance**, with the integration/operational owners. Dynamic
backend intake, Dispatch consumers/emission, full Author/Reviewer and CLI
parity, fleet placement and production writes remain broader parent work.

## Validation and acceptance

- [ ] Stage 1 decisions use actual executing provider/native and read-credential
  seams for **both** initial providers, full target isolation, strict optional
  config, local profile/admission and declared capability/schema support. Include old/new, new/old,
  new/new, missing/invalid declarations and supported rollback at real parser
  or transport boundaries. Configuration/provisioning performs no credential
  or provider probes.
- [ ] Launch the **actual foreground standalone host** with two synthetic
  providers and fresh explicitly provisioned durable state. Through the
  supported local HTTP/CLI front door, **without running Core/Gateway**,
  discover open populations without registered
  waits, query normalized current state and consume continuing events.
  Prove local authentication/read-only admission, response limits,
  target/principal isolation, unconfigured refusal and old connector-profile
  compatibility. A synthetic Gateway is not the local usability proof.
- [ ] Drive initial population, later entry, head/review/check changes,
  authoritative terminal outcomes, nonterminal departure, unknown-to-known
  evidence, unchanged observations and timestamp-only refresh. Assert distinct
  initial/change/departure semantics, honest partial/freshness fields and no
  semantic nonevents. Provider-native facts remain attributable.
- [ ] Inject paginated partial discovery, caps, read failure and full outage,
  including initially empty-complete versus unavailable. Known membership
  survives uncertainty; omission alone never closes a PR. Restore service and
  reconcile through the same observer within declared budgets.
- [ ] Use independent overlapping consumers and repeated local queries; measure
  inventory/snapshot fetch counts per observation cycle, demonstrating shared
  acquisition and zero query/subscriber-triggered extra forge pollers. Explicit
  counters prove **zero forge writes and zero task emissions**, not merely
  absence of mocked writer assertions.
- [ ] Restart the foreground owner over the same synthetic durable state:
  current facts, namespace, registration/event identities, independent pending
  replay and ACK positions survive. Exercise disconnect, retry, cancellation,
  expiry, history gaps/backpressure and crash between observation/state/event
  publication. Preserve unresolved effect identities without replaying writes.
- [ ] Exercise drain/withdrawal during inventory, snapshot and persistence,
  including delayed cleanup; no later admission/publication or early writer
  release. Existing default watch/report/workspace controls remain valid,
  optional routes remain off unless selected, and strict standalone typing,
  focused affected tests and required review/CI pass.
- [ ] Separately qualify released, installed and actually running read-only
  shadow on authorized real GitHub and Gitea targets. Record executing contract,
  bounded footprint/configuration, inventory coverage/freshness, supported local
  query output and an actual continuing subscription with ACK/reconnect.
  Observe a real later event or controlled authorized read-only change source;
  no forge mutation is granted to manufacture one. An unobserved event remains
  an open qualification item, not success.

Source fixtures, merged code and healthy-but-empty readiness are not live M1
completion. Release promotion is distinct from installation, which is distinct
from running useful service. This planning delivery authorizes none of
deployment, enrollment, retained hosting, credential repair, migration/deletion,
service starts or rollout. The integration owner resolves actual effect
authority when readiness and footprint are known; no generic operator
permission question blocks the source plan. Marking M1 accepted later cannot
close the full canonical effort or erase its remaining acceptance.
