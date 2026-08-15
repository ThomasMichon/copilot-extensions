# agent-index

`agent-index` is a runtime plugin that builds and serves a local semantic +
lexical index for a repo and its configured corpus. The shipped plugin is no
longer just a service shell: it includes the service, CLI, indexing pipeline,
source connectors, LanceDB-backed stores, a warm embedding-engine split, query
surfaces, and the `@agent-index` read-only retrieval agent.

## What ships today

- **Runtime service**: FastAPI service on loopback with an OS-assigned port,
published through `~/.agent-index/active.json` (zdd routing) and the legacy
`~/.agent-index/run/endpoint.json` rendezvous file.
- **Durable data**: index state, LanceDB tables, task queue, clusters, and worker
logs live under `~/.agent-index/data/`, outside the versioned service runtime.
- **Indexing pipeline**: incremental-by-default crawl/chunk/embed/store/reconcile
for local git files + commits, GitHub issues/PRs, and Azure DevOps work
items/PRs. `--full` is explicit.
- **Search surfaces**: CLI, HTTP, direct `agent-index mcp`, and a read-only
`agent-mcp bridge agent-index` used by the `@agent-index` sub-agent.
- **Engine split**: the light service runtime is torch-free by default; embedding
runs through a durable engine daemon at `127.0.0.1:8421` unless an operator opts
into another engine mode.
- **Lifecycle hooks**: session-start hooks stamp/self-provision the binstub,
ensure the host-side user-mode daemon is healthy, and emit configured scope
binding when the current repo has `.agent-index/config.yaml` `corpus.sources`.

## Minimal setup

1. Enable the plugin from the `copilot-extensions` marketplace.
2. Start a new Copilot session. The `sessionStart` hook performs a fast **stamp**
when needed and installs a self-provisioning `agent-index` binstub under
`~/.local/bin`.
3. First CLI use may provision the runtime (`::agent-provisioning::`, usually
~30-120s). Let it finish.
4. Pick a role:
   - single-machine/local indexer: `agent-index setup --single`
   - remote indexer: run `agent-index setup --indexer <machine> --ssh <alias>`
     from the repo; clients route read commands to that host over SSH.

A machine whose resolved role is `client` runs no local indexer daemon. A host
runs the local service and, when provisioned, the durable engine daemon.

## Usage

| Need | Use |
|------|-----|
| Search by meaning within indexed scopes | Delegate to `@agent-index` and call `agent_index_search` |
| Pivot from one result | `agent_index_find_similar` |
| Find near-duplicate clusters | `agent_index_clusters` |
| Check coverage/health | `agent_index_status` or `agent-index status` |
| Refresh the index | `agent-index index [--source S] [--full]` or `POST /reindex` |
| Manage runtime | `agent-index start`, `stop`, `status`, `deploy --recover` |
| Manage the engine daemon | `agent-index engine status|start|stop|run` |
| Adopt host/client routing | `agent-index setup`, `role`, `capability --json` |

The `@agent-index` agent intentionally exposes only the four read tools. The
lower-level `agent-index mcp` server also has `agent_index_reindex`, but the
retrieval agent does not expose it; reindexing is an operator/runtime action.

## Corpus configuration

The runtime belongs to this plugin; scope is data/config:

- With no corpus config, `agent-index index` indexes the current git checkout
(`git`) and its commit history.
- `AGENT_INDEX_SOURCES` overrides the default with a comma-separated source list.
- For multi-repo harness-style use, each repo may carry
`.agent-index/config.yaml` with `corpus.sources`; the runtime grafts sources from
locally adopted projects plus any machine-local supplement in
`~/.agent-index/config.yaml`.
- The session-start scope-binding hook reads the current repo's
`.agent-index/config.yaml` directly and tells agents which configured scopes are
safe to prefer `@agent-index` for.

## Troubleshooting quick checks

- `agent-index status` — service reachability, version, chunk count, sources,
and indexing state.
- `agent-index role` — whether this machine is acting as `host` or `client`.
- `agent-index engine status` — durable engine health, PID, endpoint, and venv.
- `agent-index deploy --recover` — recover an interrupted zdd cutover.
- `~/.agent-index/deploy-manifest.json`, `active.json`, and `data/worker.log` —
local diagnostics.

For the architecture details, see `docs/architecture.md`. For the reusable
patterns this plugin follows rather than restating them here, see
`../../docs/patterns/durable-vs-versioned-runtime.md`,
`../../docs/patterns/graceful-daemon-cutover.md`,
`../../docs/patterns/service-lifecycle-supervision.md`, and
`../../docs/patterns/local-endpoint-discovery.md`.