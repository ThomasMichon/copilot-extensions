# agent-index-service

A separately installable Python API/master-controller program, **not a Copilot
plugin and not `agent-index-engine`**. This first slice composes the existing
`agent_index` library's hosted API, native persistence, query coordination,
SQLite task queue, detached workers and zdd deployment. It does not require
Copilot, plugin discovery, a repository checkout or agent-worktrees when given
its explicit host configuration.

## Install and run

Install `agent-index-service[native]` from the selected release/package source.
The base dependency is the normal `agent-index` Python library. The `native`
extra adds that library's existing `store,server` extras; it does **not** install
torch or the embedding engine. There is no escaping uv path dependency to the
plugin tree. A source build needs the coordinator to provision the compatible
core library and its shared dependencies in a dedicated test/release environment.
The library must include `AGENT_INDEX_SOURCE_MODE` explicit-source support;
an older library is rejected rather than silently scanning adopted repositories.

```console
agent-index-service --help
agent-index-service --version
agent-index-service config --config /absolute/path/host.yaml
agent-index-service serve --config /absolute/path/host.yaml
agent-index-service status --config /absolute/path/host.yaml
agent-index-service deploy --config /absolute/path/host.yaml
```

Use native absolute paths on Windows. `start` is a foreground alias for `serve`,
not a detached supervisor. `deploy` delegates to the existing core zdd command:
passive successor, health gate, promotion/routing flip, read drain, worker
adoption and previous-instance retirement. The legacy `python -m agent_index`
successor/worker entry points remain intentional compatibility surfaces, running
from the **installed core in the same interpreter**. A PYTHONPATH-only checkout
cannot deploy: its `-I` successors cannot import that checkout, and the command
reports this before starting a cutover.
Explicit-source mode keeps successors in the current complete native interpreter
and bypasses legacy sibling server-venv discovery/foreground redispatch.

## Explicit host configuration (schema 1)

```yaml
schema_version: 1
home: /absolute/service-home
# Optional absolute overrides; otherwise data=home/data and routing=home.
data: /absolute/service-data
routing: /absolute/service-routing
listener:
  host: 127.0.0.1
  port: 0
sources:
  - name: git:documents
    path: /absolute/documents-checkout
    ref: HEAD
  - name: github:owner/repo
    auth:
      account: selected-account
engine:
  host: 127.0.0.1
  port: 8421
  mode: external
  model: jinaai/jina-embeddings-v2-base-code
  device: cpu
limits:
  batch_size: 16
  stream_batch_size: 64
  indexer_nice: 10
```

Only `schema_version`, `home` and a nonempty `sources` list are required. The
example shows defaults for listener, engine and limits. Git sources require
an absolute `path`; GitHub sources name `github:owner/repo`. Optional `type`,
`auth.account` and `trust_domain` preserve core source metadata. Unknown keys,
duplicate YAML keys/source names, invalid paths, types, ports, limits and
non-local endpoints are errors. Empty sources are rejected because the current
core would otherwise synthesize an implicit `git` source.

`config` validates without importing the hosted stack or creating directories.
Every operational command requires `--config`; there is no implicit read of
`~/.agent-index`, no repository grafting, no registry resolution and no ambient
plugin installation-cell selection. Core environment selections are scoped to
the operation and inherited by its children. Data, routing, run, logs, cache,
backups and local provider discovery are selected from this configuration,
not ambient `AGENT_INDEX_*` values. Treat the home/provider directory as
operator-owned, trusted state. Credentials remain external; account selection
uses the existing source connector behavior, not bundled secrets.

Selecting the legacy home/data/routing paths explicitly allows legacy native
colocation. **Do not run `serve` over an existing active instance:** use the
authorized lifecycle owner and `deploy` instead. No migration, installation,
engine startup, model spinup/spindown, autostart registration or configuration
rewrite is performed here. Service and worker runtimes must have the native
extras provisioned. Missing native dependencies fail before hosting.

## Compatibility and honest boundaries

The running marker, routing entry and HTTP health/status versions use this
distribution's version via `AGENT_INDEX_RUNTIME_VERSION`, not the plugin/core
version. `status` also reports `invoked_version` separately from the observed
service version and exits nonzero if not running. The legacy `plugin:
agent-index`, client paths, HTTP payloads, configuration module, rendezvous and
zdd protocols remain unchanged for existing clients.

Native persistence means existing LanceDB files plus local SQLite job state;
the API and workers still open them through the core. **Database separation is
not complete.** Warm query embedding uses the existing external `EngineClient`;
this distribution never provisions or cold-restarts an engine. Engine readiness
and task progress are not implied by `/health` process/read-admission health.
Only the existing local API listener is exposed; no new component/control/DB
ports or authenticated remote adapters are provided.

Remaining extraction seams are controller-to-persistence enqueue/claim/query,
execution-to-persistence checkpoint/batch reconciliation, controller-to-worker
assignment/progress/cancellation and query embedding/storage composition.
Future authenticated remote adapters need identity/role/job/source authorization
tests; a shared SQLite mount is not such an adapter.

Version-slot installation, release descriptors/provenance, CI integration,
candidate validation, durable supervision and rollback/schema policy belong to
the coordinator's next lifecycle/release slice. `deploy` is not an installer
or continuous restart authority. Never infer rollback safety from executable
version slots alone.

## Focused tests

In the coordinator-provisioned test environment:

```console
python -m pytest agent-index-service/tests plugins/agent-index/tests/test_standalone_explicit_sources.py -q
```

The tests select temporary homes/config/data/routing and an isolated loopback
embedding-protocol fixture; they never read the real native service home or
use the production engine. They cover light imports, strict configuration,
native failure reporting, source isolation, existing-client interoperability,
real hosted startup and no model lifecycle requests. With the core installed in
the test interpreter, the real deployment test performs two zdd deployments,
checks serving after each updater exits, verifies custom data/routing/engine
configuration despite contaminated ambient settings, retains a queued task and
adopts a live **synthetic** worker PID across cutover. An assertion-failure test
verifies owned-tree teardown; Windows tests use direct windowless worker identity
and kill-on-close Job containment rather than trusting venv supervisor PIDs.
The wheel test requires the declared `test` extra's setuptools/wheel dependencies.
Actual source-ingestion/embedding workers, first-touch installation, updater-Job
breakaway and release-slot lifecycle remain separate validation requirements;
synthetic-worker adoption does not prove all of those contracts.
