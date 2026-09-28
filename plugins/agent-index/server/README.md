# agent-index-engine

The heavy embedding-engine **server** for `agent-index` -- a separate,
independently-installable Python program that owns the torch /
sentence-transformers stack. It runs as a durable, persistent daemon
(`agent-index engine {start,stop,status,run}`, managed by `agent-index`'s
`engine.daemon` module) reached over localhost HTTP by the light `agent-index`
service's `engine.client.EngineClient` wrapper.

This package is installed **only** on a machine whose `agent-index` role
resolves to `host` (see `agent-index role`), into its own **durable venv**
(`AGENT_INDEX_ENGINE_HOME`, default `~/.agent-index/engine`) -- never into the
versioned service venv, and never on a search client. See
`efforts/active/agent-index-server-package-split/README.md` and the parent
`efforts/active/agent-index-engine-daemon/README.md` for the design history.

It depends on the base `agent-index` package (for `IndexConfig`,
`engine.generation`, and `engine.client` -- the shared identity/config surface
both programs read) plus `torch`, `transformers`, and `sentence-transformers`.
