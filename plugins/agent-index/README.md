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
stdio adapter requires the explicit `[stdio-mcp]` extra and reports unavailable
without it on either role, without installing anything. agent-index is a uniform retrieval capability every agent may use, so it
is deliberately **not** wrapped in a sub-agent or an MCP tool; agents learn how
to search from the sessionStart scope-binding hook's `additionalContext`.
- **Engine split**: the light service runtime is torch-free by default; embedding
runs through a durable engine daemon at `127.0.0.1:8421` unless an operator opts
into another engine mode.
- **Safe activation**: session-start contributes the command catalog and scope
binding only when one effective repository configuration is active. The session
hook cheaply stamps metadata and a callable binstub; it never installs packages,
creates a venv, deploys, registers autostart, or starts a service/engine.
The bootstrap hook emits exactly `{}` on both success and failure. PowerShell
runtime-gate exceptions report a diagnostic on stderr and exit nonzero; normal
child exit codes are forwarded across the hook process boundary.
- **Bounded POSIX migration:** legacy startup and installer stamp writers can
  remove group-write access from the creator-owned runtime root and the exact
  `~/.local`/`~/.local/bin` destinations after pinning their no-follow identities.
  Known group-writable markers and launchers are atomically regenerated only
  when their bytes match the admitted payload/version or canonical launcher.
  The publication lock keeps its inode and is never truncated during repair.
  No arbitrary ancestor, foreign owner, world-writable path, link or multi-link
  file is repaired. Writable deployed-version markers and unexpected marker or
  launcher bytes fail explicitly: they require independently verified operator
  recovery, not an automatic chmod that would make untrusted bytes look safe.
  Namespaced cell stamps retain strict, non-repairing admission.
  Installer publication callbacks also mask off group/other write bits while
  activating runtime markers, without weakening a stricter inherited umask
  or changing the caller's mask. Thus freshly activated `current-version`
  and `last-known-good` markers remain readable by strict startup admission.
- **Self-supervised host lifecycle**: the light host runtime is installed into
its own versioned slot, and the plugin's installer/runtime commands supervise
it directly. `install`/`update` rebuild only the light `[store,server]` service
slot, then cut traffic over with zdd; the durable engine remains on its separate
explicit lifecycle and is never rebuilt by a routine service update.

Both canonical installers use the shared installer engine for uv acquisition,
venv/package retries and deploy manifests. Native installs keep the versioned
primary CLI base-only for both roles. A host installs `[store,server]` once,
in its required slot-owned server sibling. Both the primary CLI and any required
server sibling must pass isolated CLI `--help` probes before fresh or retained
activation, in addition to host dependency/server-import checks.
Host search, indexing, workers
and service starts execute in that complete runtime, preserving original
arguments and exit codes. A missing host runtime is reported explicitly rather
than falling back to a client environment.

The legacy `agent-index mcp` stdio adapter is an HTTP client, not part of the
host's `[store,server]` profile. It requires the explicit `[stdio-mcp]` extra
and runs in its invoking runtime on either role. Normal retrieval commands and
the agent CLI bridge need no MCP SDK. Without the extra, `mcp` exits with a
diagnostic rather than installing packages or launching a host.
For an isolated opt-in development adapter, from a source checkout:

```text
uv run --project plugins/agent-index --extra stdio-mcp agent-index mcp
```

Point `AGENT_INDEX_ENDPOINT` at an existing reachable service, or use local
endpoint discovery. Do not add packages to an already completed managed runtime
slot; source-development environments and versioned production slots have
different ownership.

Content statistics scan only the source/language columns in batches of at most
1,024 rows; they do not materialize chunk text or require pandas. Counts exclude
null metadata while retaining empty strings. Storage errors propagate instead of
reporting an apparently empty or partially counted index.

Complete service cells and standalone explicit-source native services keep
their independently owned full host runtime; they do not acquire a duplicate
sibling merely to match the legacy layout. The durable embedding venv retains
its separate lifecycle, building into immutable engine slots before completed
selection. Failed selected dependencies cannot replace the healthy engine;
`engine-update` constructs a distinct repair generation. Engine dependency
transactions include both the local base package and engine source, so isolated
resolution and upgrades do not require the unpublished base on a package feed.
These runtimes retain
plugin-owned dependency/signature policy. PowerShell stamps are immutable,
self-contained snapshots; see the
[shared installer contract](../../docs/install-contract.md#shared-installer-engine-helpers-libsinstaller-engine)
for materialization and publication guarantees.

## Minimal setup

1. Enable the plugin from the `copilot-extensions` marketplace.
2. Opt the repository in by authoring the checked-in
   `.agent-index/config.yaml` defaults. A repo-local
   `.copilot-extensions/agent-index/config.yaml` overlay may add private
   sources or an indexer designation, and an explicit marketplace-specific
   override may live at
   `.copilot-extensions/agent-index/marketplaces/<marketplace-id>/config.yaml`.
   A config must contain an `indexer`/`indexers` designation or at least one
   `corpus.sources` entry. A malformed, unsafe, ambiguous, or empty config is
   inactive.
3. Start a new Copilot session. The command catalog and scope guidance appear
only for an active config. Session start stamps only metadata/binstubs after
configuration and installation-governance admission. It performs no runtime
provisioning or service startup itself; a stamp is not runtime readiness.
4. Pick a role explicitly:
   - single-machine/local indexer: `agent-index setup --single`
   - remote indexer: run `agent-index setup --indexer <machine> --ssh <alias>`
     from the repo; clients route read commands to that host over SSH.
   In automation, add `--yes`; omitting both `--single` and `--indexer` is an
   error rather than silently choosing a role.
5. Setup may provision the lightweight base/client CLI and writes the selected
role. It never provisions the durable embedding engine. Lightweight
provisioning emits `::agent-provisioning::`.

A machine whose resolved role is `client` runs no local indexer daemon. A host
runs its local service through the plugin's own installer/runtime lifecycle.
`install`, `update`, and `provision` manage the light host runtime directly and
use zdd cutover when replacing a live service. The independent durable engine's
explicit lifecycle remains separate and is never coupled to routine host
cutover.

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
`cell-provision` requires an already-active, validated installation cell.
Client marker CAS, strict completion/profile receipts, source provenance, and
schema-4 manifest transactions remain enforced. Namespaced installations use
the same local slot/cutover primitives as legacy mode; the durable engine still
stays outside those runtime cells.

Missing, requested-only, foreign, malformed, maintained, orphaned, or stale
contexts fail closed without legacy fallback. Deactivation-pending cells retain
their existing-runtime read and ownership-checked stop paths, but cannot
provision or reactivate. Namespaced state/routing/log/cache roots remain
installation-local; engine `start`/`run` remains blocked there. Session hook
compatibility `ensure-service` entry points are inert in both installation modes.
`bootstrap-check` only invokes the dependency-light session stamp adapter.
Before that adapter can mutate the default installation, the vendored machine
bootstrap-killswitch guard must admit it. An active switch leaves payload pointers,
binstubs and publication locks untouched and still emits exactly `{}`. Missing,
malformed or failed guard scripts report an error without stamping; malformed
switch state retains the canonical fail-open policy. Namespaced cells do not
read the default installation's machine switch.
Absent/default/explicit-false installation policy keeps
the legacy client layout, not legacy host provisioning authority.

### Testing without an assigned indexer machine

A repository's own checked-in `.agent-index/config.yaml` often carries no
`indexer:`/`indexers:` designation at all -- correctly so for a stateless,
shareable harness repo (e.g. `example-harness`): baking a machine-specific
designation into shared, forkable config would defeat the point of sharing
it. A real operator's bound **knowledge repo** supplies that designation at
runtime (its own `.agent-index/config.yaml` carries the `indexers:` block,
grafted in as a config layer -- see `config._knowledge_root_for_repo`). A
container or CI run has neither a real knowledge repo nor a second real
machine to designate, so `transport.plan_route()` resolves `unconfigured`/
`client` there unless you supply one of these two stand-ins:

1. **Pair a temporary, local-only knowledge repo (mocked pytest setup
   only).** `tests/_indexer_assignment_fixtures.py`'s
   `paired_knowledge_repo_indexer` fixture builds a plain directory with its
   own `.agent-index/config.yaml` declaring `indexers:`, then **monkeypatches
   `config._external_state_root`** to resolve straight to it. That monkeypatch
   is what makes this work in a test -- the real resolver requires an actual
   `agent-worktrees state-root --json` response reporting `bound: true` and
   `source: "knowledge_repo"` (`_knowledge_overlay.py`'s `external_state_root`,
   around the `payload.get("bound") is not True` checks), which a bare,
   unregistered directory does **not** satisfy on its own. Creating the
   directory and marking the subject repo's `.agent-worktrees/config.yaml`
   with `requires_external_state_root: true` (also done by this fixture,
   since that's what makes the knowledge-repo layer consulted at all) is not
   sufficient for a real CLI/service run outside a test -- a real run needs
   the knowledge repo genuinely registered and bound via `agent-worktrees`.
2. **A machine-local overlay.** Write
   `<repo>/.copilot-extensions/agent-index/config.yaml` with
   `indexer: {machine: <this-machine>}` directly -- no knowledge repo or
   external-state resolution involved at all. See the
   `machine_local_indexer_overlay` fixture in the same file. This path is
   **not** automatically gitignored by anything in agent-index itself
   (`write_indexers_designation()` writes the file without adding an ignore
   rule) -- add `.copilot-extensions/` (or the specific config file) to the
   target repository's own `.gitignore` before using this in a real repo,
   or the machine-specific designation is left available to commit.

Both fixtures are drop-in `pytest` fixtures; import them and request by name.
For a genuine index -> embed -> store -> search round trip without the heavy
`torch`/`jinaai` model, pair either fixture with
`tests/_fake_engine.py`'s `FakeEmbeddingEngineClient` -- a deterministic,
in-process stand-in matching the real `EngineClient` interface (`embed_texts`,
`embed_query`, `health`, `is_ready`). See
`tests/test_indexer_assignment_workarounds.py` for both fully worked examples.

## Usage

| Need | Use |
|------|-----|
| Search by meaning within indexed scopes | `agent-index search "<query>" [--source S] [--limit N] --json` |
| Pivot from one result | `agent-index similar <chunk_id> [--source S] [--limit N]` |
| Find near-duplicate clusters | `agent-index clusters [--source S] [--exact-dupes-only]` |
| Check coverage/health | `agent-index status` |
| Refresh the index | `agent-index index [--source S] [--full]` or `POST /reindex` |
| Inspect host/runtime | `agent-index status`; installer/runtime verbs own provisioning, start, cutover, rollback, and retention |
| Install a client | Explicit setup or installer `install`/`update`; namespaced client governance via `cell-provision`, `slot-cutover`, `cell-recover` |
| Manage the engine daemon | `agent-index engine status|start|stop|run`; `install.ps1|sh engine-update` rebuilds the durable engine runtime |
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
- For multi-repo harness-style use, each repo may carry checked-in
  `.agent-index/config.yaml` defaults, optionally supplemented by a repo-local
  `.copilot-extensions/agent-index/config.yaml` overlay; the runtime grafts
  sources from locally adopted projects plus any machine-local supplement in
  `~/.agent-index/config.yaml`.
- Resolution selects a valid current-repository config first, layering checked-in
  defaults, the conditional bound-knowledge `.agent-index/config.yaml`, the
  repo-local overlay, and any marketplace overlay using the same precedence
  order documented for the other `.agent-*` configs:
  machine-local > knowledge overlay > in-repo base. The one deliberate
  exception is `corpus.sources`: unlike ordinary list-valued keys, it unions by
  source `name` so those layers can compose, with the higher-precedence layer
  winning any duplicate name. Repositories that declare `stateless: true` or
  `requires_external_state_root: true` may still fall back to the knowledge
  repo's own effective config when no local repo config exists. A present
  invalid local config never falls through.
- The session-start scope-binding hook reads that effective config and tells
agents which configured scopes are safe to prefer `agent-index search` for.

## Troubleshooting quick checks

- `agent-index status` — service reachability, version, chunk count, sources,
and indexing state. Outside an opted-in repository it returns a structured,
non-mutating `inactive` result; before setup in an active repository it returns
`setup_required`.
- `agent-index role` — whether this machine is acting as `host` or `client`.
- `agent-index engine status` — durable engine health, PID, endpoint, and venv.
  A stale migrated selector with no selectable completed interpreter reports
  `venv_python: null` and `provisioned: false`, retaining health and PID
  diagnostics. Launches still refuse rather than falling back to the legacy venv.
- `scripts/maintenance_tick.py` — a host-only deterministic maintenance body
  for agent-dispatch's script embodiment: health-checks the self-supervised
  service + durable engine, starts only what is down, then runs incremental
  `agent-index index` (never engine reprovisioning).
  Its `--schedule-template --repo <lane> --machine <host>` mode prints the
  producer-owned `maintenance` category declaration without registering a
  schedule or running maintenance. Adoption owns that registration; script
  embodiment alone never classifies a task.
- Host unavailable: run `agent-index status`, `agent-index deploy --recover`,
  or the installer `start`/`update` path that owns the service cutover. Routine
  service updates do not rebuild the durable engine.
  Native recovery refusal (including conditional-owner state or transition-lock
  contention) returns nonzero; it never reports a skipped repair as success.
- Namespaced mode: `cell-recover` retains installation-transaction recovery, and
  the local service/runtime still remain installation-owned.
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