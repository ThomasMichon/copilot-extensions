# agent-index

`agent-index` is a runtime plugin that builds and serves a local semantic +
lexical index for a repo and its configured corpus. The shipped plugin is no
longer just a service shell: it includes the service, CLI, indexing pipeline,
source connectors, LanceDB-backed stores, a warm embedding-engine split, query
surfaces, and a read-only retrieval surface that every agent calls directly
through the `agent-index` CLI.

## What ships today

- **Runtime service**: FastAPI service on loopback with an OS-assigned port,
published through `~/.agent-index/active.json` (zdd routing) and the legacy
`~/.agent-index/run/endpoint.json` rendezvous file.
- **Durable data**: index state, LanceDB tables, task queue, clusters, and worker
logs live under `~/.agent-index/data/`, outside the versioned service runtime.
- **Indexing pipeline**: incremental-by-default crawl/chunk/embed/store/reconcile
for local git files + commits, GitHub issues/PRs, and Azure DevOps work
items/PRs. `--full` is explicit.
- **Search surfaces**: the `agent-index` **CLI** (the primary, agent-facing
surface — every agent calls it directly), HTTP, and the direct `agent-index mcp`
server. agent-index is a uniform retrieval capability every agent may use, so it
is deliberately **not** wrapped in a sub-agent or an MCP tool; agents learn how
to search from the sessionStart scope-binding hook's `additionalContext`.
- **Engine split**: the light service runtime is torch-free by default; embedding
runs through a durable engine daemon at `127.0.0.1:8421` unless an operator opts
into another engine mode.
- **Lifecycle hooks**: session-start hooks stamp the setup-gated binstub, ensure
an already-configured host-side daemon is healthy, and emit configured scope
binding when the current repo has `.agent-index/config.yaml` `corpus.sources`.

## Minimal setup

1. Enable the plugin from the `copilot-extensions` marketplace.
2. Start a new Copilot session. The `sessionStart` hook performs a fast **stamp**
when needed and installs a setup-gated `agent-index` binstub under
`~/.local/bin`. `agent-index status` is safe here: it reports
`state: setup_required` without installing a runtime or starting a service.
3. Pick a role explicitly:
   - single-machine/local indexer: `agent-index setup --single`
   - remote indexer: run `agent-index setup --indexer <machine> --ssh <alias>`
     from the repo; clients route read commands to that host over SSH.
   In automation, add `--yes`; omitting both `--single` and `--indexer` is an
   error rather than silently choosing a role.
4. The setup command provisions the light runtime, writes the selected role, and
reconciles the role-specific runtime/service. Provisioning emits
`::agent-provisioning::` and may take ~30-120s.

A machine whose resolved role is `client` runs no local indexer daemon. A host
runs the local service and, when provisioned, the durable engine daemon.

The installer also exposes explicit, non-activating `slot-provision` and
`slot-validate` actions for a pre-stamped installation-context snapshot:

```powershell
scripts\install.ps1 -Action slot-provision -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
scripts\install.ps1 -Action slot-validate -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
```

```bash
scripts/install.sh slot-provision --context /path/to/install.json --expected-marketplace-id <marketplace-id>
scripts/install.sh slot-validate --context /path/to/install.json --expected-marketplace-id <marketplace-id>
```

These actions only reserve or validate the current plugin version's empty,
owned cell-local slot. They do not build a runtime, migrate legacy state, change
current/LKG/activation state, or install, start, stop, or rename a service.

## Usage

| Need | Use |
|------|-----|
| Search by meaning within indexed scopes | `agent-index search "<query>" [--source S] [--limit N] --json` |
| Pivot from one result | `agent-index similar <chunk_id> [--source S] [--limit N]` |
| Find near-duplicate clusters | `agent-index clusters [--source S] [--exact-dupes-only]` |
| Check coverage/health | `agent-index status` |
| Refresh the index | `agent-index index [--source S] [--full]` or `POST /reindex` |
| Manage runtime | `agent-index start`, `stop`, `status`, `deploy --recover` |
| Manage the engine daemon | `agent-index engine status|start|stop|run` |
| Adopt host/client routing | `agent-index setup`, `role`, `capability --json` |

The `agent-index` read subcommands (`search`, `similar`, `clusters`, `status`)
are the read-only, agent-facing surface. The lower-level `agent-index mcp` server
and the CLI also have an `index` path (`agent_index_reindex` over MCP), but
reindexing is an operator/runtime action, not something agents trigger.

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
safe to prefer `agent-index search` for (and how to invoke it).

## Troubleshooting quick checks

- `agent-index status` — service reachability, version, chunk count, sources,
and indexing state. Before setup it returns a structured, non-mutating
`setup_required` result.
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