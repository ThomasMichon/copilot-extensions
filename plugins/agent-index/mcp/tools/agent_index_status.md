---
mcp:
  name: agent_index_status
  description: "Index health — plugin/version, index availability, total chunk count, per-source breakdown (the coverage map), and indexing state. A clean reply confirms the toolset + service are ready. Returns a JSON status object."
  inputSchema:
    type: object
    properties: {}
    required: []
  invoke:
    command: agent-index
    args:
      - "status"
---
# agent_index_status

Invokes `agent-index status`. Routed to the indexer by the CLI transport (local
on the host; over SSH from a client, returning the HOST's live status). Output is
the raw JSON status object — probe this once on startup to confirm readiness.
