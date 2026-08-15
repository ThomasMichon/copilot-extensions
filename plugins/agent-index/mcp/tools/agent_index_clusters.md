---
mcp:
  name: agent_index_clusters
  description: "List clusters of near-duplicate indexed items (largest/tightest first) — which filed issues, docs, or snippets basically duplicate each other. Returns a JSON array of clusters."
  inputSchema:
    type: object
    properties:
      source:
        type: string
        description: Scope to a source (collapsed to its bucket, e.g. "git:my-repo").
      bucket:
        type: string
        description: Explicit bucket (e.g. "git", "gitea:issues").
      model:
        type: string
        description: Embedding space ("code" or "prose").
      exact_dupes_only:
        type: boolean
        description: Only clusters that contain a byte-identical pair.
      limit:
        type: integer
        description: Max clusters (default 50).
    required: []
  invoke:
    command: agent-index
    args:
      - "clusters"
      - { flag: "--source", value: "{source}", when: source }
      - { flag: "--bucket", value: "{bucket}", when: bucket }
      - { flag: "--model", value: "{model}", when: model }
      - { flag: "--exact-dupes-only", when: exact_dupes_only }
      - { flag: "--limit", value: "{limit}", when: limit }
---
# agent_index_clusters

Invokes `agent-index clusters [--source ..] [--bucket ..] [--model ..]
[--exact-dupes-only] [--limit N]`. `exact_dupes_only` is a boolean-presence flag.
Routed to the indexer by the CLI transport. Output is the raw JSON cluster array.
