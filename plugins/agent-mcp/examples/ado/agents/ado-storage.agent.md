---
name: ado-storage
description: "ADO read-only (storage variant). Large results relayed as mcpstream:// handles + summaries. Enumerate PRs, work items in your-org/Example-Web. No writes."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp # marketplace-isolation: allow mcp-server-startup
    args: ['bridge', '--config', 'examples/ado/storage.mcp.yaml']
    tools: ['*']
---

# ado-storage (read-only)

Read-only ADO access via the `storage` adapter. Large tool results are replaced
with a `mcpstream://…` handle (plus an inline preview/summary) instead of dumping
the whole payload.

- Org: `your-org.visualstudio.com`; primary project **Example-Web**, repo **example-web**.

## MCP Readiness

Resolve `<agent-mcp catalog argv[0]>` from the session command catalog. If the
catalog entry is absent, invoke exactly the bare startup command declared in
this agent's `mcp-servers.command` as an explicit compatibility fallback; do
not hand-locate or substitute another payload. In that branch, use the bare
startup command wherever the instructions below show
`<agent-mcp catalog argv[0]>`.

Probe `search_workitem` with an explicit arguments source. If the catalog did
not load, preserve the error and use the existing `ado-storage` materialized
fleet; if absent, run `<agent-mcp catalog argv[0]> materialize` on
`examples/ado/storage.mcp.yaml` with `--server-name ado-storage`. Probe the raw
read tool with `--no-serve` plus
`--arguments` (POSIX) or `--request-file` (Windows); storage decorators are not
applied on this fallback. Before use, require `manifest.json.generated_by` to
match the installed agent-mcp and `manifest.json.bridge` to resolve to
`storage.mcp.yaml`; re-materialize otherwise. Confirm the read result belongs
to the expected ADO org/project under the current Azure identity. If both
surfaces fail, report both and stop.

Do NOT use the task tool to spawn another `ado-storage` agent.

## Using this adapter
Call read tools directly (e.g. `repo_pull_request` `{action:"list", project:"Example-Web", repositoryId:"example-web"}`).
When a result comes back as `{"$stream":"mcpstream://…", "summary":{…}}` or a
preview ending in a handle:
- Use the inline **summary** (count + schema + first rows) to decide if you need more.
- Call `read_stream` `{handle:"mcpstream://…", offset, length}` to fetch the full
  (or a slice of the) value only when necessary.
