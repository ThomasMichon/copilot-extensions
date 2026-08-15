---
mcp:
  name: agent_index_find_similar
  description: "Find items similar to an already-indexed chunk (the 'more like this' pivot) from an agent_index_search result. Returns a JSON array of neighbour hits. Use to gather related material once one good hit is found."
  inputSchema:
    type: object
    properties:
      chunk_id:
        type: string
        description: Reference chunk id (the `chunk_id`/`id` of an agent_index_search hit).
      limit:
        type: integer
        description: Max neighbours (default 10).
      source:
        type: string
        description: Filter neighbours by source name.
    required: [chunk_id]
  invoke:
    command: agent-index
    args:
      - "similar"
      - "{chunk_id}"
      - { flag: "--limit", value: "{limit}", when: limit }
      - { flag: "--source", value: "{source}", when: source }
---
# agent_index_find_similar

Invokes `agent-index similar <chunk_id> [--limit N] [--source ..]`. The CLI
subcommand is `similar`; the tool name mirrors the historical
`agent_index_find_similar`. Routed to the indexer by the CLI transport (local on
the host, over SSH from a client). Output is the raw JSON neighbour array.
