---
name: agent-index
description: >
  Operate the agent-index runtime service: a portable indexing and semantic-search
  engine shell for a harness repo and its immediate ecosystem. Use it to check
  service health, inspect the local runtime status, read the installed version,
  or start/stop the Phase 1 service shell. Trigger phrases include 'agent-index',
  'semantic search index', 'index service status', and 'portable repo search'.
---

# agent-index

`agent-index` is the portable indexing and semantic-search engine plugin. Phase 1
ships the runtime service shell only; indexing and retrieval arrive in later
slices.

## CLI verbs

- `agent-index start` -- run the local service (normally supervised by the installer).
- `agent-index status` -- resolve the rendezvous endpoint and print service status.
- `agent-index version` -- print the running service version, or the local package version.
- `agent-index stop` -- stop the process advertised by the rendezvous file.

## HTTP surface

- `GET /health` returns `{ "status": "ok" }`.
- `GET /status` returns plugin/version metadata plus placeholder index counts.
