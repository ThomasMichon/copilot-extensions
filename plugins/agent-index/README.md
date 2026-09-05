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
surface — every agent calls it directly) and HTTP. The optional `agent-index mcp`
stdio adapter requires the host dependency profile and reports unavailable in
the base-only client runtime without installing anything. agent-index is a uniform retrieval capability every agent may use, so it
is deliberately **not** wrapped in a sub-agent or an MCP tool; agents learn how
to search from the sessionStart scope-binding hook's `additionalContext`.
- **Engine split**: the light service runtime is torch-free by default; embedding
runs through a durable engine daemon at `127.0.0.1:8421` unless an operator opts
into another engine mode.
- **Safe activation**: session-start contributes the command catalog and scope
binding only when one effective repository configuration is active. It also
publishes an attributed, inert `agent-dispatch` companion candidate. The
session hook never stamps a runtime, installs packages, or starts a service.
- **Declarative host lifecycle**: when `agent-dispatch` is running, the
companion provider activates only for an enabled project whose effective
repository or required-state-root config designates this machine as an
indexer. Dispatch alone materializes the declared `[store]` host dependency
profile, selects an immutable generation, readiness-gates replacement, rolls
back failures, and retains live generations. The plugin's lifecycle adapter
uses the injected `AGENT_INDEX_MANAGED_PYTHON` without installation or fallback.
Client, unconfigured, globally enabled without a project, malformed-config,
and unsupported namespaced host scopes remain inactive.

## Minimal setup

1. Enable the plugin from the `copilot-extensions` marketplace.
2. Opt the repository in by authoring `.agent-index/config.yaml`. A config must
contain an `indexer`/`indexers` designation or at least one `corpus.sources`
entry. A malformed, unsafe, ambiguous, or empty config is inactive.
3. Start a new Copilot session. The command catalog and scope guidance appear
only for an active config. Session start publishes the companion candidate but
performs no runtime provisioning or service startup itself.
4. Pick a role explicitly:
   - single-machine/local indexer: `agent-index setup --single`
   - remote indexer: run `agent-index setup --indexer <machine> --ssh <alias>`
     from the repo; clients route read commands to that host over SSH.
   In automation, add `--yes`; omitting both `--single` and `--indexer` is an
   error rather than silently choosing a role.
5. Setup may provision the lightweight base/client CLI and writes the selected
role. It never installs host dependencies, starts the host, or provisions the
independent embedding engine. Lightweight provisioning emits
`::agent-provisioning::`.

A machine whose resolved role is `client` runs no local indexer daemon. A host
runs its local service only through an already-running dispatch supervisor.
Without dispatch the host is unavailable: commands do not start dispatch or
substitute their own installer. The independent durable engine's existing
explicit lifecycle remains separate and is never coupled to host cutover.

`start`, `serve`, `restart`, and `deploy` report dispatch ownership and return
nonzero rather than launching a host. `install`, `update`, `provision`, and
`init` install only the base/client package, even with a configured host role.
No public command creates or selects dispatch-managed cells. The internal
`__managed-start` entry accepts only the already-selected interpreter and a
host designation; it is a non-installing adapter seam, not a setup command.

The installer exposes explicit installation-context actions for disposable
installation-cell validation:

```powershell
scripts\install.ps1 -Action slot-provision -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
scripts\install.ps1 -Action slot-validate -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
scripts\install.ps1 -Action cell-provision -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
scripts\install.ps1 -Action cell-recover -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
```

```bash
scripts/install.sh slot-provision --context /path/to/install.json --expected-marketplace-id <marketplace-id>
scripts/install.sh slot-validate --context /path/to/install.json --expected-marketplace-id <marketplace-id>
scripts/install.sh cell-provision --context /path/to/install.json --expected-marketplace-id <marketplace-id>
scripts/install.sh cell-recover --context /path/to/install.json --expected-marketplace-id <marketplace-id>
```

The slot actions remain non-activating ownership/validation primitives.
`cell-provision` requires an already-active, validated installation cell and
supports only client/unconfigured runtime profiles. Host provision, replacement,
and recovery are unavailable in namespaced contexts; an existing host receipt
does not grant new install or launch authority. Client marker CAS, strict
completion/profile receipts, source provenance, and schema-4 manifest
transactions remain enforced. These installation cells are distinct from the
dispatch-owned managed host generations.

Missing, requested-only, foreign, malformed, maintained, orphaned, or stale
contexts fail closed without legacy fallback. Deactivation-pending cells retain
their existing-runtime read and ownership-checked stop paths, but cannot
provision or reactivate. Namespaced state/routing/log/cache roots remain
installation-local; engine `start`/`run` remains blocked there. Session hook
compatibility entry points are inert in both installation modes, including
when invoked directly. Absent/default/explicit-false installation policy keeps
the legacy client layout, not legacy host provisioning authority.

## Usage

| Need | Use |
|------|-----|
| Search by meaning within indexed scopes | `agent-index search "<query>" [--source S] [--limit N] --json` |
| Pivot from one result | `agent-index similar <chunk_id> [--source S] [--limit N]` |
| Find near-duplicate clusters | `agent-index clusters [--source S] [--exact-dupes-only]` |
| Check coverage/health | `agent-index status` |
| Refresh the index | `agent-index index [--source S] [--full]` or `POST /reindex` |
| Inspect host/runtime | `agent-index status`; dispatch owns host provisioning, start, replacement, rollback, and retention |
| Install a client | Explicit setup or installer `install`/`update`; namespaced client governance via `cell-provision`, `slot-cutover`, `cell-recover` |
| Manage the engine daemon | `agent-index engine status|start|stop|run` |
| Adopt host/client routing | `agent-index setup`, `role`, `capability --json` |

The `agent-index` read subcommands (`search`, `similar`, `clusters`, `status`)
are the read-only, agent-facing surface. The optional lower-level `agent-index mcp` server
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
- Resolution selects a valid current-repository config first. Only when the
repository declares `stateless: true` or `requires_external_state_root: true`
may it fall back to the valid `.agent-index/config.yaml` in the bound knowledge
repo. A present invalid local config never falls through.
- The session-start scope-binding hook reads that effective config and tells
agents which configured scopes are safe to prefer `agent-index search` for.

## Troubleshooting quick checks

- `agent-index status` — service reachability, version, chunk count, sources,
and indexing state. Outside an opted-in repository it returns a structured,
non-mutating `inactive` result; before setup in an active repository it returns
`setup_required`.
- `agent-index role` — whether this machine is acting as `host` or `client`.
- `agent-index engine status` — durable engine health, PID, endpoint, and venv.
- Host unavailable: inspect the already-running dispatch supervisor and its
  attributed companion registration. Do not retry a plugin installer for host
  dependencies or use `deploy --recover` to bypass dispatch.
- Namespaced mode: `cell-recover` retains client installation-transaction
  recovery; namespaced host lifecycle is unsupported and remains inert.
- Legacy mode: `~/.agent-index/deploy-manifest.json`, `active.json`, and
  `data/worker.log`.
- Cell mode: the validated plugin root's `deploy-manifest.json`,
  `run/zdd/active.json`, `run/endpoint.json`, `run/service-identity.json`,
  `state/`, `logs/`, and `launchers/`.

For the architecture details, see `docs/architecture.md`. For the reusable
patterns this plugin follows rather than restating them here, see
`../../docs/patterns/durable-vs-versioned-runtime.md`,
`../../docs/patterns/graceful-daemon-cutover.md`,
`../../docs/patterns/service-lifecycle-supervision.md`, and
`../../docs/patterns/local-endpoint-discovery.md`.