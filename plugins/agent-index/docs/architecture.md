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
  an atomic `current-version` marker publish (`scripts/versioned_runtime.py`). A
  version is never mutated in place; a new version installs beside the old and is
  selected atomically (on Windows via the marker alone — no junction).
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


## Zero-downtime cutover (Phase 2d)

`agent-index` now consumes the shared `zdd` library for active/passive runtime
cutover. Each service process publishes its bound endpoint to the stable routing
table at `~/.agent-index/active.json`, which lives under `install_dir()` rather
than a versioned runtime slot, so every installed version reads and writes the
same client-followed record. Startup publishes the active endpoint after the
listening socket is bound; shutdown clears the record only when this process
still owns it. The legacy rendezvous file remains as a fallback for older
installations and diagnostics.

The drain surface is `POST /drain` and `POST /undrain`. Draining leaves
`/search` and `/similar` available for in-flight and already-routed callers, but
`GET /health` reports `{"status": "draining"}` so deploy health checks do not
select a draining process. The drain gate waits for in-flight searches and pauses
the `TaskRunner` after its current indexing task; queued indexing work remains in
the durable SQLite `task_store` under `~/.agent-index/data/`, so the successor
version (sharing the same durable data directory) resumes scheduled work without
dropping or double-running queued tasks. `POST /undrain` releases the gate and
wakes the runner for rollback.

`agent-index deploy` wires `zdd.cutover.CutoverOrchestrator`: it spawns a passive
`python -m agent_index start --port <p> --passive`, waits for `/health` to return
`ok`, flips the routing table, drains the old process, asks it to shut down, and
keeps the new process as active. `agent-index deploy --recover` runs the shared
breadcrumb recovery path, undraining any survivor left behind by an interrupted
cutover.

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

## Hosted work-tracking connector (Phase 2e)

`agent-index` includes a first-class hosted work-tracking + pull-request feed
connector for Azure DevOps, a public SaaS source system. The source name is
`ado:<org>/<project>`; `azure-devops:<org>/<project>` is accepted as an alias.
The connector indexes work items and pull requests only. Repository files and
commits remain the responsibility of the git connector.

The connector is deliberately operator-query-driven. It never synthesizes a
whole-project WIQL query or unfiltered pull-request crawl. The operator supplies
the exact work-item and pull-request subsets to index through kwargs, a config
file, or single-query environment shortcuts, with precedence in that order:

- `work_item_queries=[...]` and `pull_request_queries=[...]` kwargs passed to the
  connector factory.
- A JSON config file at `AGENT_INDEX_ADO_CONFIG`, or `~/.agent-index/ado.json`
  by default (under the plugin install root).
- Convenience environment variables: `AGENT_INDEX_ADO_WIQL` for one raw WIQL
  query, and `AGENT_INDEX_ADO_PR_STATUS`, `AGENT_INDEX_ADO_PR_REPOSITORY`,
  `AGENT_INDEX_ADO_PR_CREATOR`, and `AGENT_INDEX_ADO_PR_REVIEWER` for one
  pull-request filter. The legacy `AGENT_INDEX_ADO_REPOSITORY_ID` is still
  accepted as a direct repository-id filter.

The config file schema is:

```json
{
  "work_item_queries": [
    { "name": "team-backlog-a", "wiql": "SELECT [System.Id] FROM WorkItems WHERE ..." },
    { "name": "area-x", "saved_query_id": "<guid-or-path>" }
  ],
  "pull_request_queries": [
    { "name": "my-prs", "reviewer": "me", "status": "active" },
    { "name": "key-repo", "repository": "<repo-name-or-id>", "status": "all" }
  ]
}
```

Each work-item query sets exactly one of `wiql` (posted as-is) or
`saved_query_id` (run through the Azure DevOps saved-query WIQL endpoint). The
connector unions and dedupes ids across all configured queries, then batch-fetches
work-item fields and comments. Incremental discovery does not rewrite WIQL; it
runs the operator's queries as-is and filters the fetched items client-side by
`System.ChangedDate` using the incremental marker plus the overlap window.

Each pull-request query maps to Azure DevOps Git `searchCriteria.*`: `status`,
`repository`/`repository_id`, `creator`, `reviewer`, `source_ref`, and
`target_ref`. A repository name is resolved to an id through the repositories
API; a direct `repository_id` or GUID is passed through. `creator: "me"` and
`reviewer: "me"` resolve the authenticated user's id once through
`/_apis/connectionData` and reuse it for the run. Pull-request incremental scans
keep the existing client-side creation/closed-date window filter.

If no work-item queries are configured, the work-item side returns nothing and
logs an actionable warning. If no pull-request queries are configured, the
pull-request side does the same. No explicit query means no indexing for that
kind.

Authentication uses an Azure DevOps PAT from `AGENT_INDEX_ADO_TOKEN`, sent as
HTTP Basic with an empty username. The API base defaults to
`https://dev.azure.com` and may be overridden with `AGENT_INDEX_ADO_BASE` for
compatible Azure DevOps Server deployments. No organization, project, collection,
or host is hardcoded.

Azure DevOps shares the same good-citizen HTTP discipline as the GitHub connector:
minimum inter-request spacing, `Retry-After` handling for `429`, rate-limit reset
headers, bounded jittered retries for transient server errors, and sequential
pagination (including Azure DevOps continuation-token pages). Managed upstreams
should see a small, polite, project-scoped indexer rather than an organization
firehose.


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
- `agent-index clusters [--source S] [--bucket B] [--model M] [--exact-dupes-only]
  [--limit N]` lists similarity clusters of near-duplicate items (largest/tightest
  first) as JSON.
- `GET /search?q=<query>&source=&language=&repo=&limit=10` returns
  `{query, available, hits}`.
- `GET /similar?id=<chunk_id>&limit=10` returns `{id, available, hits}`.
- `GET /clusters?source=&bucket=&model=&exact_dupes_only=&limit=50&offset=0`
  returns `{available, count, clusters}`.
- `POST /reindex` starts a best-effort background reindex when the optional
  indexing dependencies are installed, returning `{accepted: true}` immediately.

The query surface degrades cleanly: missing optional dependencies, an unavailable
store, or an empty/unbuilt index produce JSON error payloads with `hits: []` (and
HTTP 200 from the service) rather than raw tracebacks or service crashes.

### Discoverable MCP toolset

`agent-index mcp` runs a stdio FastMCP server (`agent_index/mcp_app.py`) that
exposes the query surface as five discoverable MCP tools — `agent_index_search`,
`agent_index_find_similar`, `agent_index_clusters`, `agent_index_status`, and
`agent_index_reindex` — so an agent finds semantic retrieval directly without
knowing the HTTP shape. The tools are a transport-agnostic HTTP client over a
**configurable endpoint** (`AGENT_INDEX_ENDPOINT`, else local endpoint
discovery), so each consumer wires its own transport (direct local HTTP,
SSH-forwarded port, or a gateway URL).

The similarity-cluster artifact backing `agent_index_clusters` / `GET /clusters`
is refreshed **post-index** inside the indexing pipeline (`run_reindex` calls the
cluster pass over the just-updated vectors, reusing stored embeddings — no
re-embedding). Clustering is best-effort and guarded by `AGENT_INDEX_CLUSTER_ENABLED`;
a clustering failure never fails the reindex it follows. The artifact lives in its
own `clusters.db` (separate from LanceDB and `tasks.db`) so a recluster never
touches the index.

Query-time embedding runs in-process on CPU by default, so search does not need a
warm accelerator. Indexing is heavier: it embeds batches through the on-demand GPU
engine subprocess and stores them in the durable index under `~/.agent-index/data/`.
Full end-to-end runtime validation of the optional stack (`torch`, GPU access,
LanceDB) is therefore performed on a deployment host that has those dependencies,
while development tests mock the store and embedder surfaces.

### Indexing defaults (good-citizen, capability-aware)

Per the engine vision's good-citizen ingestion, indexing is **incremental by
default**: `run_reindex(full=False)` and every surface that triggers it
(`agent-index index`, `POST /reindex`, `agent_index_reindex`) default to reading
only what changed since the last commit marker. A **full** reindex is explicit
opt-in (`--full` / `full=true`).

- **Source GC is connector-derived.** The full-reindex source GC keeps every
  source whose scheme a **registered connector** owns (`git:*`, `github:*`,
  `ado:*`, ...), so it never purges a live index generation as connectors are
  added (#116). Disable with `AGENT_INDEX_REINDEX_GC=0`; extra keeps via
  `AGENT_INDEX_GC_KEEP_SOURCES`.
- **Embed batch size is capability-aware** (`AGENT_INDEX_STREAM_BATCH_SIZE`,
  else device-derived): GPU hosts keep the high-throughput 500; CPU hosts use a
  small 64 so each `/embed/batch` completes within the read timeout instead of
  tripping it and emptying the index (#115).
- **Embed read timeout is generous + configurable** (`AGENT_INDEX_EMBED_READ_TIMEOUT`,
  default 300s) — a slow CPU embed must not abort mid-batch; a longer timeout
  never slows a fast GPU embed.
