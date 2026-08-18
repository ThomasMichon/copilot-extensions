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

- The plugin self-provisions. A new session's hook stamps a self-provisioning
`agent-index` binstub when needed; the first binstub call provisions the runtime
(`::agent-provisioning::`, usually ~30-120s). If the command is missing, the
stamp/PATH step did not run; surface that exact failure rather than improvising
an install.
- `agent-index status` is the first health check. It reports service reachability,
version, index availability, total chunks, per-source coverage, and indexing
state.
- `agent-index role` tells whether this machine is a `host` (local daemon) or a
`client` (read commands route to the designated indexer over SSH).

## Retrieval path

agent-index is a uniform retrieval capability **every agent calls directly via
the `agent-index` CLI** — there is no sub-agent and no MCP-tool wrapper. The
sessionStart scope-binding hook injects how-to-search guidance (covered scopes +
the commands below) into each session's context. The read subcommands:

- `agent-index search "<query>" [--source S] [--language L] [--repo R] [--limit N] --json`
  — meaning + lexical hybrid search. Use for conceptual/code/doc searches when
  exact tokens are unknown. Each hit has `chunk_id`, `source`, `file_path`,
  `line_start`/`line_end`, `content`.
- `agent-index similar <chunk_id> [--source S] [--limit N]` — pivot from a
  returned hit into related material.
- `agent-index clusters [--source S] [--bucket B] [--exact-dupes-only] [--limit N]`
  — list near-duplicate clusters.
- `agent-index status` — health and coverage map; probe this before relying on
  results.

Reindexing is **not** a retrieval action: `agent-index index` is an
operator/runtime step, never an agent side effect.

## CLI/operator path

Use the CLI directly when operating the runtime:

- Setup/routing: `agent-index setup --single`, `agent-index setup --indexer
  <machine> --ssh <alias>`, `agent-index role`, `agent-index capability --json`.
- Service: `agent-index start`, `agent-index stop`, `agent-index status`,
  `agent-index deploy --recover`.
- Index refresh: `agent-index index [--source S] [--full]`. Incremental is the
default; `--full` is explicit.
- Engine daemon: `agent-index engine status|start|stop|run`.

## Scope and fallback

- The session-start scope-binding hook emits configured scopes from the current
repo's `.agent-index/config.yaml` `corpus.sources` when present.
- For a plain repo with no corpus config, `agent-index index` defaults to the
current git checkout (`git`) and its commits.
- Use direct `rg`/`glob` for exact strings, files outside indexed scopes, or when
`agent-index status` shows the index is unavailable.
- Query-time trust-domain enforcement is not implemented; when a request is
clearly scoped, pass `source` or `repo` rather than doing an unscoped search.

## Troubleshooting

- Service down on a host: run/check `agent-index status`; session-start
`ensure-service` should start the user-mode daemon in the background.
- Client cannot search: run inside a repo with `.agent-index/config.yaml`
`indexer.ssh` or set `AGENT_INDEX_REPO`; the CLI read transport needs a project
to choose the SSH target.
- Engine issues: `agent-index engine status` shows durable engine health, PID,
endpoint, and venv provisioning state.
- Interrupted cutover: `agent-index deploy --recover`.

Architecture details live in `plugins/agent-index/docs/architecture.md`.