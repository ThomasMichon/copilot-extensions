---
name: agent-index
description: >
  Operate or use the agent-index runtime: semantic + lexical retrieval over a
  repo/corpus, index health/status, host/client setup, indexing refresh, and its
  read CLI (search/similar/clusters/status). Trigger phrases include 'agent-index',
  'semantic search index', 'index service status', 'portable repo search',
  'agent-index search', 'agent-index status', and 'find similar indexed content'.
---

# agent-index

Use this skill when the task is about the **agent-index** plugin, its runtime
service, or retrieval through its read CLI.

## Readiness

- The command catalog appears only when the current repository has a valid
`.agent-index/config.yaml`, or when a repository that requires external state
has a valid config in its bound knowledge repo. A present invalid local config
never falls through. Outside that scope, `status` reports `inactive` and other
commands are refused.
- Session start is non-mutating: it does not stamp or provision a runtime and
does not start a service. After repository opt-in, an operator explicitly chooses
`setup --single` or `setup --indexer <machine> --ssh <alias>`; that setup call
may provision only the lightweight client/base CLI (`::agent-provisioning::`).
The configured host's `[store]` dependencies and service are provisioned and
supervised only by an already-running `agent-dispatch`; without it the host
is unavailable. Setup never starts dispatch or the host. Automation must
also pass `--yes` and an explicit role choice. If the command is missing, the
session command catalog reports it as unavailable; surface that exact failure
rather than searching `PATH` or improvising an install.
- `<catalog argv[0]> status` is the first health check in a configured
  repository. It reports service reachability,
version, index availability, total chunks, per-source coverage, and indexing
state.
- `<catalog argv[0]> role` reports `host` (local daemon), `client` (read commands
  route to the designated indexer over SSH), or `unconfigured`.

## Retrieval path

The agent-index capability is a uniform retrieval surface **every agent calls through the
exact `argv` in its session command catalog** — there is no sub-agent, MCP-tool
wrapper, or ambient command lookup. Append the arguments shown below to the
catalog `argv`. The read subcommands:

- `<catalog argv[0]> search "<query>" [--source S] [--language L] [--repo R] [--limit N] --json`
  — meaning + lexical hybrid search. Use for conceptual/code/doc searches when
  exact tokens are unknown. Each hit has `chunk_id`, `source`, `file_path`,
  `line_start`/`line_end`, `content`.
- `<catalog argv[0]> similar <chunk_id> [--source S] [--limit N]` — pivot from a
  returned hit into related material.
- `<catalog argv[0]> clusters [--source S] [--bucket B] [--exact-dupes-only] [--limit N]`
  — list near-duplicate clusters.
- `<catalog argv[0]> status` — health and coverage map; probe this before relying on
  results.

Reindexing is **not** a retrieval action: `<catalog argv[0]> index` is an
operator/runtime step, never an agent side effect.

## CLI/operator path

Use the CLI directly when operating the runtime:

- Setup/routing: `<catalog argv[0]> setup --single`, `<catalog argv[0]> setup
  --indexer <machine> --ssh <alias>`, `<catalog argv[0]> role`,
  `<catalog argv[0]> capability --json`.
- Service: `<catalog argv[0]> status`. `start`, `serve`, `restart`, and
  `deploy` report dispatch ownership and do not launch or provision a host.
  Stop remains ownership-checked, but dispatch may restart an enabled companion;
  change its owning configuration to withdraw supervision durably.
- Index refresh: `<catalog argv[0]> index [--source S] [--full]`. Incremental is the
default; `--full` is explicit.
- Engine daemon: `<catalog argv[0]> engine status|start|stop|run`.

## Scope and fallback

- The session-start scope-binding hook emits `corpus.sources` from the same
effective config used by the CLI gate.
- For a plain repo with no corpus config, the catalog command's `index`
subcommand defaults to the
current git checkout (`git`) and its commits.
- Use direct `grep`/`glob` for exact strings, files outside indexed scopes, or when
the catalog command's `status` subcommand shows the index is unavailable.
- Query-time trust-domain enforcement is not implemented; when a request is
clearly scoped, pass `source` or `repo` rather than doing an unscoped search.

## Troubleshooting

- Service down on a host: run/check `<catalog argv[0]> status`, then inspect the
already-running dispatch supervisor. Plugin installers install only the client
base package, never the host service dependencies. Namespaced installation
contexts do not yet support managed hosts. Session start never starts the daemon.
- Client cannot search: run inside a repo with `.agent-index/config.yaml`
`indexer.ssh` or set `AGENT_INDEX_REPO`; the CLI read transport needs a project
to choose the SSH target.
- Engine issues: `<catalog argv[0]> engine status` shows durable engine health, PID,
endpoint, and venv provisioning state.
- Interrupted host cutover: inspect dispatch's managed-runtime readiness and
rollback state; do not use a plugin command to select a generation.

Architecture details live in `plugins/agent-index/docs/architecture.md`.