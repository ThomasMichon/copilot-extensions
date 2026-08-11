# agent-index

`agent-index` is the portable indexing and semantic-search engine plugin for a
harness repo and its immediate ecosystem: it crawls configured sources, chunks
and stores content in a local LanceDB vector store, and serves semantic search
over an MCP tool surface and a CLI. It is a self-contained runtime plugin with
local service supervision and rendezvous-based endpoint discovery.

## Architecture — router adapter + pluggable embedding engine

agent-index is split into a light **router/adapter** and a heavy **embedding
engine**, so the accelerated work is isolated from the always-on service:

- **Router (the service you talk to)** — owns the MCP/CLI surface, the LanceDB
  store, chunking, source connectors, and query orchestration. The versioned
  service runtime is **torch-free and light**.
- **Embedding engine (the core)** — a separate HTTP worker
  (`agent_index.engine.app`, `/embed`, `/embed/batch`, `/spinup`, `/health`) that
  runs the embedding model. Heavy (PyTorch), GPU-capable, isolatable, restartable.

The router reaches the engine over HTTP and never embeds the heavy model in its
own process by default.

## Engine modes — where the core runs

`AGENT_INDEX_ENGINE_MODE` selects how the engine is provided:

| Mode | The engine is… | For |
|------|----------------|-----|
| **`external`** *(default)* | owned by a durable/containerized daemon the service only probes for reachability | the shipped, torch-free service against a persistent engine |
| `subprocess` | spawned as a local child by the service on demand | a single-venv install that manages its own engine |
| `systemd` | started via a systemd unit (socket-activation supported) | Linux system deployments |
| `auto` | `systemd` if a unit is configured and `systemctl` is present, else `subprocess` | — |

The engine endpoint is `http://$AGENT_INDEX_ENGINE_HOST:$AGENT_INDEX_ENGINE_PORT`
(default `127.0.0.1:8421`); set `AGENT_INDEX_ENGINE_URL` to point at a wired
engine (e.g. a container, or `host.docker.internal` from inside one).

## Standalone operation & the user-mode CPU path

The **shipped default is the external engine daemon** (a deliberate, torch-free
service design — see the `agent-index-engine-daemon` effort): the light service
routes all embedding, including query embedding, through the daemon
(`AGENT_INDEX_SEARCH_IN_PROCESS=0`).

A fully **self-contained, user-mode CPU** deployment — no GPU, no container, no
external daemon — is **available as an opt-in single-venv install**:

- install the embedding runtime with the **`[engine]` extra** (adds PyTorch), then
- run in-process query embedding and a local engine:
  `AGENT_INDEX_SEARCH_IN_PROCESS=1` and `AGENT_INDEX_ENGINE_MODE=subprocess`.

In that configuration agent-index indexes and searches on CPU using only what its
own installer put on the machine — the plugin never *requires* an external engine
to be reachable on its own host. Absent an optional wired engine, the opt-in local
path still performs the plugin's own function; a missing engine degrades an
accelerated feature, not the whole service.

## Endpoints

- `GET /health` — service liveness.
- `GET /status` — plugin/version plus index counts.

Plus the MCP tool surface and the `agent-index` CLI for indexing and search.
