# Opt-in scoped fleet backend

This consumes `fleet-manager`'s `fleet-index-scoped-v1` contract through the
existing native search-engine instance, drain ownership and explicitly selected
[source-aware cold store](source-aware-store.md). It creates no second store,
task ledger, credential provider or deployment controller. Legacy ID-partition
retrieval is not supported: measured full-corpus queries exceeded the unchanged
five-second Gateway transport deadline.

Install `agent-index[store,server,fleet]` with a compatible
`fleet-manager[auth]` artifact. The standalone extra is
`agent-index-service[fleet]`. Release/image owners must include both first-party
distributions in the verified artifact closure; source integration is not
registry publication or live deployment.

## Explicit configuration

The standalone host selects both features:

```yaml
store:
  layout: source-aware-v1
fleet:
  policy: <absolute-path-to-non-secret-backend-policy.json>
```

Fresh-domain initialization remains the owning writer's explicit operation.
Never point this selection at an incumbent legacy database. Native hosts require
`AGENT_INDEX_SOURCE_MODE=explicit`, `AGENT_INDEX_STORE_LAYOUT=source-aware-v1`
and an explicit `AGENT_INDEX_FLEET_POLICY`. Unselected native hosting remains
unchanged. The standalone environment discards ambient selections.

The existing server owns one additional TLS ingress thread in the same process;
the native API and embedding engine remain private loopback listeners.
Only `POST /v1/index/admit` and `POST /v1/index/query` are exposed. There is
no public legacy search/status/lifecycle route or arbitrary forwarding.
No configured policy means no ingress or enrollment.

Strict bounded JSON policy `agent-index.fleet-backend`, version 1, contains:

- `backend`: the fleet `Backend` descriptor, including stable identity, service,
  installation, corpus, generation/expiry, exact grant sources, HTTPS endpoint,
  CA path, issuer, public signing JWKS and `fleet-index-scoped-v1` contract.
- `fleet`, `gateway_keys`, `gateway_issuer`, `gateway_subject`: explicit execution
  trust. Supply only Gateway signing keys, not general access-token issuer keys.
- `signing_key`: external Ed25519 PEM path; `signing_kid` must match its public
  descriptor key. Never embed secret key/token bytes.
- `listener`: host, port, boolean `remote`, certificate/key paths. A remote
  listener requires explicit opt-in. Host/port must match the backend origin.
- `source_bindings`: exactly the descriptor's grant names, each with an exact
  configured `declared_source` and explicit canonical stored `family`. The
  source owner declares alias/remote-derived identity mappings; queries do not
  infer them from remote URLs or arbitrary source prefixes.

Policy is a startup snapshot. Offer generation/expiry or trust changes require
restart and fresh admission. Pending admission references do not survive restart.

## Execution and isolation

The fleet-owned `ScopedIndexBackend` verifies grant signatures, issuer/audience,
validity, Gateway subject, fleet, backend generation/installation, operation,
corpus, sources and request correlation at admission and execution. A one-use
admission reference is consumed before engine access. `index.search` is the only
supported operation; similarity, clusters, scoped status and lifecycle require
separate contracts and are rejected.

`SearchEngine.search_authorized()` uses the reviewed store-native source
projection. It applies exact-family or `family + ':'` descendant predicates to
vector/FTS candidates and hydration before retrieval, with bounded global
candidate windows and no legacy ID enumeration. `git:alpha` never authorizes
`git:alphabet`. Omitted selectors mean the granted families, not every row.
Dirty, busy, unselected, incomplete or stale-FTS domains fail closed; errors
never become successful empty results or unrestricted legacy queries.

The existing source-domain read fence covers inference, candidate retrieval and
hydration. Reads do not prepare indexes or repair projection state. Cold store
unit and explicit large-corpus portfolio tests own schema, ownership and
post-setup retrieval performance; ingress regressions prove their actual HTTP
consumer, not another duplicate store implementation.

Results preserve native fields plus `corpus` and `stored_source`. `source` is
the authorized grant name required by the signed receipt consistency contract;
`stored_source` retains the canonical native source/commit descendant.
Clients must preserve that distinction.

Passive/draining native state closes execution. HTTPS, bounded wire bodies,
16 active ingress admission slots and owned shutdown remain unchanged.
Raw TCP admission caps all accepted connections at 32 before starting TLS.
TLS handshake and initial HTTP headers share one absolute five-second deadline;
withholding ClientHello cannot evade the cap or retain a handshake task after
expiry or shutdown. The maintained asyncio TLS upgrade is the only path to
Uvicorn's H11 parser, with no cleartext route. Subsequent keep-alive headers
also have an absolute five-second deadline; dripping bytes cannot renew it.
Headers must complete before ASGI starts the
separate five-second body deadline. Overdue header connections are aborted
and removed from the 32-connection concurrency budget. No WebSocket upgrade
or alternate protocol bypass is enabled. Credentials are never stored in manifests,
arguments, source rows or task receipts.

Renewable source callbacks run in an owned, joined worker with copied context
variables. Ordinary callback exceptions become a static
`source-credential-unavailable` error without the original diagnostic or
exception context entering task results/logged tracebacks. Process-control
exceptions propagate, and successful, failed and interrupted acquisitions leave
no callback worker running. The callback retains its transport deadline; this
is not a new credential daemon, timeout extension or identity provider.

## Remaining evidence and ownership

Tests use real cold-domain storage, cryptographic grants, verified TLS,
source/corpus denial and dirty/unselected refusal; only embedding inference is
a deterministic fixture. They do not prove real-model five-second latency,
released image closure, index CLI routing, source-token refresh, persistent
Komodo deployment or live source/chunk parity. Those remain #6383/#6443 and
the retained provider/deployment owners' completion gates. No incumbent data
transfer or prototype retirement is authorized by this adapter.
