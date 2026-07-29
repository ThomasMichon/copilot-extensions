# agent-index architecture

`agent-index` is a runtime service plugin. Phase 1 provides the deployable
service shell that later indexing slices will fill in.

## Phase 1 service shell

- Python package installed into the plugin runtime venv by the install contract.
- `agent-index` CLI with `start`, `status`, `version`, and `stop` verbs.
- FastAPI app exposing `GET /health` and `GET /status`.
- Discoverable local endpoint via the runtime rendezvous file under
  `~/.agent-index/run/endpoint.json`.
- Platform-native supervision through the plugin installers.

The service binds to `127.0.0.1` with an OS-assigned ephemeral port and advertises
that endpoint through rendezvous. There is no fixed well-known port.

## Intent

The standing intent lives in `visions/plugins/agent-index/README.md` and honors
`visions/plugin-services/README.md`: self-contained runtime, discoverable local
endpoint, platform-native lifecycle, à-la-carte installability, and minimal
network exposure.

## Later slices

Phase 2 will introduce the indexing engine core, source connectors, durable work
state, embedding/retrieval surfaces, and good-citizen ingestion controls. The
Phase 1 `/status` response intentionally reports `index.chunks = 0` until that
engine exists.
