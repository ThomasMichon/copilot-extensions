# agent-index

`agent-index` is the portable indexing and semantic-search engine plugin for a
harness repo and its immediate ecosystem.

Phase 1 ships only the service shell: a self-contained runtime plugin, local
service supervision, endpoint discovery, and a minimal HTTP surface:

- `GET /health` returns `{"status":"ok"}`.
- `GET /status` returns plugin/version plus placeholder index counts.

The indexing engine, connectors, embeddings, and retrieval API are Phase 2+ work.
