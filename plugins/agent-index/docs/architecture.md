# agent-index architecture

`agent-index` is a runtime service plugin. Phase 1 provides the deployable
service shell that later indexing slices will fill in.

## Phase 1 service shell

- Python package installed into the plugin runtime venv by the install contract.
- `agent-index` CLI with `start`, `status`, `version`, and `stop` verbs.
- FastAPI app exposing `GET /health` and `GET /status`.
- Discoverable local endpoint via the runtime rendezvous file under
  `~/.agent-index/run/endpoint.json`.
- Platform-native supervision through the plugin installers.

The service binds to `127.0.0.1` with an OS-assigned ephemeral port and advertises
that endpoint through rendezvous. There is no fixed well-known port.

## Intent

The standing intent lives in `visions/plugins/agent-index/README.md` and honors
`visions/plugin-services/README.md`: self-contained runtime, discoverable local
endpoint, platform-native lifecycle, à-la-carte installability, and minimal
network exposure.

## Runtime vs. durable data (immutable, versioned + ZDD)

The plugin follows the service model's **immutable-versioned-runtime** and
**zero-downtime-cutover** behaviors:

- **Executable logic** installs as an **immutable, versioned runtime** selected by
  an atomic `current` junction swap (`scripts/versioned_runtime.py`). A version is
  never mutated in place; a new version installs beside the old and is selected
  atomically.
- **Durable data** — the index/store, embeddings, and indexing work-state — lives
  in a **separate durable location** (`~/.agent-index/data/`, outside the swapped
  runtime) so a version cutover or rollback never touches it. It stays
  rebuildable from source as the safety net.
- **Zero-downtime cutover** — deploying a new version health-gates the new slot on
  a fresh endpoint, flips the client-followed routing record atomically, drains
  in-flight searches, and **hands off scheduled/queued indexing work** to the new
  version before retiring the old one (reversible up to a commit point). Realized
  with the shared **`zdd`** library (`zdd.routing` table + `zdd.cutover.CutoverOrchestrator`).
  *(Phase 2 wires the drain/handoff of the indexing scheduler; Phase 1 ships the
  versioned-runtime + rendezvous substrate.)*

## Cross-host reach (SSH)

The service is machine-local and opens no new inbound port. A client on **another
host** reaches it over an opt-in **SSH port-forward** of the service's own local
endpoint (service-transport rung 4) — the service's endpoint stays bound to
loopback/rendezvous on its own host; only the SSH session crosses the boundary. A
multi-machine deployment (e.g. a single GPU-backed indexer serving a fleet) is
therefore a fan-in of SSH forwards to one host-resident service, never a fleet of
network listeners.

## Later slices

Phase 2 will introduce the indexing engine core, source connectors, durable work
state, embedding/retrieval surfaces, and good-citizen ingestion controls. The
Phase 1 `/status` response intentionally reports `index.chunks = 0` until that
engine exists.

## Ported core (Phase 2)

Phase 2 ports the reusable indexing/search core into `agent_index` while keeping
it generic and separate from the Phase 1 service shell:

- `chunking` — code, Markdown, YAML, and fallback chunkers.
- `index_config` — generic index/model configuration (`IndexConfig` and `ModelProfile`).
- `store` — content, vector, multi-model, repair, and clustering stores.
- `embedding` — embedding pipeline and in-process query embedder.
- `search` — reusable query/search engine.
- `indexing` — indexing runner, task state, manifests, GC, backups, and cluster pass.
- `engine` — on-demand embedding-engine subprocess and shim.
- `sources` — generic connector protocol and registry.

Deployment-specific connectors, secrets bootstrap, and server surfaces from the
source service are intentionally excluded; a downstream superset provides those
on top of this reusable engine.


## Query + indexing surface (Phase 2c)

Phase 2c exposes the ported search/indexing core through both the CLI and the
local service API:

- `agent-index index [--source S] [--full]` runs the indexing pipeline and emits a
  JSON summary. When `--source` is omitted, the pipeline indexes the configured
  default sources from `AGENT_INDEX_SOURCES` (comma-separated, for example
  `git:myrepo,github:owner/repo`) or, when unset, the local git checkout (`git`).
- `agent-index search "<query>" [--source S] [--language L] [--repo R]
  [--limit N] [--json]` returns JSON hits by default for non-interactive callers.
- `agent-index similar <chunk_id> [--limit N] [--source S]` returns JSON nearest
  neighbors for an indexed chunk.
- `GET /search?q=<query>&source=&language=&repo=&limit=10` returns
  `{query, available, hits}`.
- `GET /similar?id=<chunk_id>&limit=10` returns `{id, available, hits}`.
- `POST /reindex` starts a best-effort background reindex when the optional
  indexing dependencies are installed, returning `{accepted: true}` immediately.

The query surface degrades cleanly: missing optional dependencies, an unavailable
store, or an empty/unbuilt index produce JSON error payloads with `hits: []` (and
HTTP 200 from the service) rather than raw tracebacks or service crashes.

Query-time embedding runs in-process on CPU by default, so search does not need a
warm accelerator. Indexing is heavier: it embeds batches through the on-demand GPU
engine subprocess and stores them in the durable index under `~/.agent-index/data/`.
Full end-to-end runtime validation of the optional stack (`torch`, GPU access,
LanceDB) is therefore performed on a deployment host that has those dependencies,
while development tests mock the store and embedder surfaces.
