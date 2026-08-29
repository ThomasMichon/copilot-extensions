---
name: ado-plain
description: "ADO read-only (plain filter variant). Enumerate PRs, work items, builds in your-org/Example-Web. No writes. agent-mcp decorator-stack example."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp # marketplace-isolation: allow mcp-server-startup
    args: ['bridge', '--config', 'examples/ado/plain.mcp.yaml']
    tools: ['*']
---

# ado-plain (read-only)

Read-only ADO access to the **your-org** org via the `plain` adapter (mutating
tools filtered out; everything else exposed directly).

- Org: `your-org.visualstudio.com`; primary project **Example-Web**, repo **example-web**.

## MCP Readiness

Resolve `<agent-mcp catalog argv prefix>` from the session command catalog. If the
catalog entry is absent, invoke exactly the bare startup command declared in
this agent's `mcp-servers.command` as an explicit compatibility fallback; do
not hand-locate or substitute another payload. In that branch, use the bare
startup command wherever the instructions below show
`<agent-mcp catalog argv prefix>`.

Probe `search_workitem` with an explicit arguments source. If the catalog did
not load, preserve the error and use the existing `ado-plain` materialized
fleet; if absent, run `<agent-mcp catalog argv prefix> materialize` on
`examples/ado/plain.mcp.yaml` with `--server-name ado-plain`. Probe the raw read
tool with `--no-serve` plus
`--arguments` (POSIX) or `--request-file` (Windows); decorator behavior is not
applied on this fallback. Before use, require `manifest.json.generated_by` to
match the installed agent-mcp and `manifest.json.bridge` to resolve to
`plain.mcp.yaml`; re-materialize otherwise. Confirm the read result belongs to
the expected ADO org/project under the current Azure identity. If both surfaces
fail, report both and stop.

Do NOT use the task tool to spawn another `ado-plain` agent.

## Using this adapter
Call read tools directly by name:
- `repo_pull_request` `{action:"list", project:"Example-Web", repositoryId:"example-web"}`
- `search_workitem` `{searchText:"...", project:"Example-Web"}`
- `wit_backlog` `{action:"list", project:"Example-Web", team:"..."}`
