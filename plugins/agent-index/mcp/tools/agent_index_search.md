---
mcp:
  name: agent_index_search
  description: "Search the agent-index corpus semantically (meaning + lexical hybrid) across the harness repo's code, docs, commits, issues, and PRs. Returns a JSON array of hits (chunk_id, score, source, file_path, line_start/line_end, content). Prefer over grep/glob when searching by concept/behavior rather than an exact string."
  inputSchema:
    type: object
    properties:
      query:
        type: string
        description: Natural-language or code query.
      limit:
        type: integer
        description: Max results (default 10).
      source:
        type: string
        description: Filter by source name (e.g. "git:my-repo").
      language:
        type: string
        description: Filter by language (e.g. "python", "markdown").
      repo:
        type: string
        description: Filter by repository metadata.
    required: [query]
  invoke:
    command: agent-index
    args:
      - "search"
      - "{query}"
      - { flag: "--source", value: "{source}", when: source }
      - { flag: "--language", value: "{language}", when: language }
      - { flag: "--repo", value: "{repo}", when: repo }
      - { flag: "--limit", value: "{limit}", when: limit }
      - "--json"
---
# agent_index_search

Invokes `agent-index search <query> [--source ..] [--language ..] [--repo ..]
[--limit N] --json`. The CLI transport routes the call to the designated indexer
(local on the host; over SSH from a client), so the caller needs no endpoint
configuration. Output is the raw JSON hit array on stdout.
