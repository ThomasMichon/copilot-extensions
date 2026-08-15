---
name: agent-index
description: >
  Operate or use the agent-index runtime: semantic + lexical retrieval over a
  repo/corpus, index health/status, host/client setup, indexing refresh, and the
  @agent-index MCP read toolset. Trigger phrases include 'agent-index',
  'semantic search index', 'index service status', 'portable repo search',
  'agent_index_search', 'agent_index_status', and 'find similar indexed content'.
---

# agent-index

Use this skill when the task is about the **agent-index** plugin, its runtime
service, or retrieval through its MCP tools.

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

Prefer the **`@agent-index` sub-agent** for semantic retrieval within configured
scopes. Its `agent-mcp bridge agent-index` surface exposes four read-only tools:

- `agent_index_search(query, limit?, source?, language?, repo?)` — meaning +
lexical hybrid search. Use for conceptual/code/doc searches when exact tokens are
unknown.
- `agent_index_find_similar(chunk_id, limit?, source?)` — pivot from a returned
hit (`id` / `chunk_id`) into related material.
- `agent_index_clusters(source?, bucket?, model?, exact_dupes_only?, limit?)` —
list near-duplicate clusters.
- `agent_index_status()` — health and coverage map; probe this before relying on
results.

The read agent intentionally does **not** expose `agent_index_reindex`. Reindexing
is an operator/runtime action, not a retrieval side effect.

## CLI/operator path

Use the CLI directly when operating the runtime:

- Setup/routing: `agent-index setup --single`, `agent-index setup --indexer
  <machine> --ssh <alias>`, `agent-index role`, `agent-index capability --json`.
- Service: `agent-index start`, `agent-index stop`, `agent-index status`,
  `agent-index deploy --recover`.
- Index refresh: `agent-index index [--source S] [--full]`. Incremental is the
default; `--full` is explicit.
- Search without the sub-agent: `agent-index search "<query>" --json`,
  `agent-index similar <chunk_id>`, `agent-index clusters`.
- Engine daemon: `agent-index engine status|start|stop|run`.

## Scope and fallback

- The session-start scope-binding hook emits configured scopes from the current
repo's `.agent-index/config.yaml` `corpus.sources` when present.
- For a plain repo with no corpus config, `agent-index index` defaults to the
current git checkout (`git`) and its commits.
- Use direct `rg`/`glob` for exact strings, files outside indexed scopes, or when
`agent_index_status` shows the index is unavailable.
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