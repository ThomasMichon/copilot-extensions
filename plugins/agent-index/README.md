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
- **Safe activation**: session-start contributes the command catalog and scope
binding only when one effective repository configuration is active. It never
stamps a runtime, installs packages, or starts a service.

## Minimal setup

1. Enable the plugin from the `copilot-extensions` marketplace.
2. Opt the repository in by authoring `.agent-index/config.yaml`. A config must
contain an `indexer`/`indexers` designation or at least one `corpus.sources`
entry. A malformed, unsafe, ambiguous, or empty config is inactive.
3. Start a new Copilot session. The command catalog and scope guidance appear
only for an active config. Session start performs no runtime provisioning or
service startup.
4. Pick a role explicitly:
   - single-machine/local indexer: `agent-index setup --single`
   - remote indexer: run `agent-index setup --indexer <machine> --ssh <alias>`
     from the repo; clients route read commands to that host over SSH.
   In automation, add `--yes`; omitting both `--single` and `--indexer` is an
   error rather than silently choosing a role.
5. The setup command provisions the light runtime, writes the selected role, and
reconciles the role-specific runtime/service. Provisioning emits
`::agent-provisioning::` and may take ~30-120s.

A machine whose resolved role is `client` runs no local indexer daemon. A host
runs the local service and, when provisioned, the durable engine daemon.
Provisioning is command-driven after opt-in; no session-start path downloads
dependencies or starts a daemon.

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
`cell-provision` additionally requires an already-active, validated Agent Index
cell. It snapshots the attributable payload, builds the light runtime in the
cell's immutable profile-qualified version slot, publishes the canonical
four-field build completion marker plus a separate strict role/extras receipt,
publishes current/LKG selection, and writes a schema-4 deploy manifest. Host and
client profiles therefore never mutate the same slot at one payload version.
It does not create activation, migrate
legacy state, provision the embedding engine, or install machine-global
commands, scheduled tasks, or systemd units.

Marker selection and schema-4 manifest publication are one crash-recoverable
installation transaction. The cell keeps a random transaction receipt until
service reconciliation succeeds; bootstrap, retry, and `cell-recover` either
finish the validated target or restore the prior selection. After a passive
target becomes healthy, governance is rechecked immediately before pre-route
promotion. The route changes only after that exact target is read-ready.
Maintenance, deactivation, or blocked ownership retires the passive target and
restores the prior marker/manifest without draining, rerouting, or stopping the
old service.

Cell mode derives durable state, backup snapshots/status, run/rendezvous, zdd
routing, logs, cache, configuration, engine home, service identity, and
launchers from the validated plugin root. The service launcher is
installation-local, routes through the latest reconciled payload dispatcher,
and uses an OS-assigned port, so two cells can run concurrently. Windows
cold-start also assigns the selected interpreter to an owned Job before it can
spawn, so pre-receipt readiness failure retires the whole exact child tree.
`/health` and `/status` attest the exact installation and process instance;
namespaced lifecycle controls require the exact per-process token and reject
stale, same-cell, or foreign routing evidence. A cutover generation starts passive: it
publishes only an instance-specific ownership receipt, does not start or adopt
the shared task runner, and does not publish shared endpoint or running-version
evidence until an ownership-checked promotion makes it read-ready; only then is
the shared route atomically published.
Successful reconciliation gracefully shuts down only exact, attested
superseded instances and requires one owned PID to remain. The exemplar leaves the heavy
embedding engine unprovisioned and blocks cell-mode engine `start`/`run`;
service and engine status remain available without it. Historical
`slot-cutover` is managed by the latest reconciled payload with an explicit
target payload, snapshot, and completed runtime slot; it preserves reconciled
source provenance while changing only the selected immutable runtime. Missing,
requested-only, foreign, malformed, maintained, orphaned, or stale context
fails closed without legacy fallback. Deactivation-pending cells may use an
existing runtime and ownership-checked stop path, but cannot provision, start,
restart, or deploy. In namespaced mode, deploy, promotion, and recovery are
management-only: `cell-runtime` passes a random live transaction receipt to the
selected runtime, and an ordinary payload invocation without that exact receipt
is rejected. Legacy mode keeps its public `deploy [--recover]` compatibility.
Absent/default/explicit-false policy retains legacy behavior.

## Usage

| Need | Use |
|------|-----|
| Search by meaning within indexed scopes | `agent-index search "<query>" [--source S] [--limit N] --json` |
| Pivot from one result | `agent-index similar <chunk_id> [--source S] [--limit N]` |
| Find near-duplicate clusters | `agent-index clusters [--source S] [--exact-dupes-only]` |
| Check coverage/health | `agent-index status` |
| Refresh the index | `agent-index index [--source S] [--full]` or `POST /reindex` |
| Manage runtime | Legacy: `agent-index start`, `stop`, `status`, `deploy --recover`; namespaced: installer `cell-provision`, `slot-cutover`, `cell-recover` |
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
- Legacy mode: `agent-index deploy --recover` recovers an interrupted zdd cutover.
- Namespaced mode: use the owning payload installer's `cell-recover` action;
  direct payload deploy/recovery is rejected outside its live transaction.
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