# Index service integration with fleet-manager

Back to the [canonical effort](README.md). Coordination: #6383, with #5768,
#5789, #6030 and #6236 retaining their existing authority.

This is a proposed implementation slice, not documentation of a deployed
Gateway, credential subscription or container. It closes the standalone index
vision's deployment-backend parity and lightweight-client/declared-host intent,
using the reviewed machine-fleet routing and integrated-product boundaries.

## Requested path

The operator requests a Linux container serving index queries through the fleet
Gateway, with its source-token requests routed through that Gateway to a
subscribed controller. Ordinary native use remains supported. Exact placement
and provider identity are adopter configuration, not upstream constants.

The target is the new fleet-manager Gateway, not an older adopter Gateway.
Existing Gateway deployments remain untouched; implementing this slice does not
authorize their replacement, port takeover, data import or credential reuse.

The following topology and sequencing are **agent-recommended elaborations**:

```text
index CLI -- query principal --> Gateway -- fixed service route --> index container
index worker -- source credential request --> Gateway --> subscribed controller
                                                       <-- scoped token reply
```

These are distinct authorization paths. A query principal must not acquire
source-ingestion credentials; service enrollment must not imply token-issuance
authority. The Gateway routes a credential request, while the subscribed
controller's configured credential provider remains the issuer/custodian.

## Ownership and dependencies

| Surface | Owner and boundary |
|---------|--------------------|
| Index image, hosted composition, query-client adapter and source-token consumption | This effort, #6383; preserve the existing index API, queue and native installation |
| Gateway endpoint, service offers, connector authentication and fixed-service routing | Routing foundation #5789; reuse its contracts, including the existing native-response slice |
| Controller subscription, credential policy and delivery authority | Fleet campaigns must assign the provider/transport interface; no parallel index-owned broker |
| Role deployment, release reconciliation, client lifecycle and provider enrollment | Linux role fleet #6030 / #5861; index supplies a service package and adapter, not a manager replacement |
| Integrated product release and durable command/receipt authority | Fleet control V2 #6236 / #6240 / #6241; no second authoritative operation ledger |
| Concrete host, credentials, capacity and adoption | Downstream adopter; never embedded in this public proposal |

Resident Gateway routing and subscribed-controller credential delivery are not
established by the existing pure records, offline planner or local operation
ledger. Obtain owner agreement on those interfaces before building against them.
Contract mocks may let the index adapter progress independently, but a mock-only
route is not the requested deployed system.

## First container topology

Use the ordinary standalone `agent-index-service` distribution; do not create
another marketplace plugin. Pin the build's first-party artifacts/revision and
third-party dependencies through the supported release process. The image runs
non-root with explicit configuration and durable state paths.

Initially keep API/master, detached indexing workers, LanceDB and SQLite task
state in one execution namespace with one writable local persistent volume.
The embedding engine may share that private namespace with an independent
model-cache volume. This preserves the current loopback-only API/engine config
without weakening it to expose unauthenticated listeners.

The fleet connector may enter that namespace or use an explicitly authenticated
index ingress adapter. Select that boundary with the routing owner before
implementation. Do not change `127.0.0.1` to a wildcard merely to make Docker
port publishing work. Separating the engine into another network namespace
requires its own authenticated adapter and remains outside the first slice.

Never concurrently mount the native host's live database into a second writer.
Build a small independent validation corpus first. Production data transfer,
writer cutover, backup compatibility and rollback need a later explicit,
reviewed migration procedure. Container image rollback alone does not prove
corpus/task-schema rollback safe.

## Index-client query transport

Add an explicit Gateway route mode rather than disguising it as an SSH host
alias or teaching the client placement/manager internals. Reuse the existing
typed query arguments, response serialization and fixed logical service identity.
The index adapter must never accept arbitrary downstream URL/method/header
forwarding.

Consume fleet-owned endpoint discovery, trust and query credential contracts.
Bind service identity, fleet scope and permitted operations. Preserve all
supported retrieval commands and their health/failover behavior; lifecycle
mutators remain separately authorized, not automatically exposed by query access.

Every query also binds the authenticated principal to its permitted corpus and
source scope, independently of fleet membership, service enrollment or operation
permission. Both Gateway admission and the index execution boundary must verify
that binding. Omitted filters mean the authorized scope, never all indexed
sources; explicitly requested scope outside that grant is rejected. Apply this
to search, similar/cluster retrieval and query-derived counts/metadata before
retrieval, not merely by stripping unauthorized hits afterward.

Status reads, including source histograms, chunk/table counts and task/source
metadata, require the same corpus/source binding at the index execution boundary.
Return only an authorized scoped view, or reject the operation when the backend
cannot produce one; never expose global metadata as a scoped success. Fleet-wide
diagnostics require a separate management grant. Admission-only health must not
disclose corpus/source inventory.

Reject unavailable, stale, mismatched, unauthorized and malformed responses
explicitly. Gateway authentication failure or outage must not silently retry
through SSH, a local corpus or another account. Keep ordinary native/SSH client
routes unchanged when no Gateway mode is selected.

## Refreshable source credentials

Introduce an explicit index-side provider seam usable by detached workers.
Requests identify the source/account, required audience/scopes and correlated
request identity using the agreed fleet contract. The controller selects an
authorized provider; query input cannot nominate an arbitrary credential issuer.

Responses need bounded validity, correlation, audience and scope checks before
connector use. Refresh credentials rather than capturing one token forever at
connector construction. Preserve already accepted queue ownership, but report
credential exhaustion/controller disconnection as a truthful task failure or
bounded wait under the existing queue contract. Do not silently downgrade to
anonymous ingestion when the selected provider is unavailable.

Do not persist tokens in source manifests, task receipts, argv, image layers,
diagnostics or plan output. Secret delivery, revocation and controller
subscription authority are fleet/provider contracts, not invented by this
consumer adapter. Test with a deterministic mock issuer and synthetic tokens;
the live subscribed controller must separately prove the real contract.

## Sequence and acceptance gates

1. Review this amendment and coordinate the Gateway, credential-subscription
   and deployment owners. Record exact supported request/response/config
   contracts and who owns each missing implementation.
2. Build the service image and index-side adapters against those contracts.
   Keep mock infrastructure test-only and mark unsupported runtime paths
   explicitly until their fleet dependency is available.
3. In an isolated Linux container, prove first-touch startup, one synthetic
   ingestion/query cycle, persistent corpus/task state after restart and graceful
   termination. Distinguish process/read admission, task/data health and loaded
   model readiness.
4. On an authorized fleet venue, enroll only the named service and prove a real
   CLI query through the Gateway plus credential acquisition/refresh through a
   separately subscribed controller. Verify unauthenticated, cross-source,
   cross-role, expired, revoked and disconnected cases without secret output.
   Use at least two sources in one corpus and distinct corpus grants to prove
   that fleet/service/query permission alone cannot read another scope, including
   omitted filters and similarity/cluster/metadata operations.
5. Only after those proofs, propose native-host offload and data cutover. Retain
   the healthy native service and warm engine until migration and rollback
   eligibility are demonstrated. No Windows Scheduled Task is introduced.

Record unit, simple real-process E2E, fresh-container and available live-venue
evidence separately. Compilation, mocks, health-only checks and a merged PR
cannot substitute for the operator's actual CLI/Gateway/container path.
