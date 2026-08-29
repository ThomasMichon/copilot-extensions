---
name: ado-defer
description: "ADO read-only (defer variant). Catalog hidden behind find_tool/execute_tool. Enumerate PRs, work items in your-org/Example-Web. No writes."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp # marketplace-isolation: allow mcp-server-startup
    args: ['bridge', '--config', 'examples/ado/defer.mcp.yaml']
    tools: ['*']
---

# ado-defer (read-only)

Read-only ADO access via the `defer` adapter. `tools/list` exposes only the
meta-tools; the real (read-only) catalog is searchable.

- Org: `your-org.visualstudio.com`; primary project **Example-Web**, repo **example-web**.

## MCP Readiness

Resolve `<agent-mcp catalog argv prefix>` from the session command catalog. If the
catalog entry is absent, invoke exactly the bare startup command declared in
this agent's `mcp-servers.command` as an explicit compatibility fallback; do
not hand-locate or substitute another payload. In that branch, use the bare
startup command wherever the instructions below show
`<agent-mcp catalog argv prefix>`.

Probe `find_tool` with an explicit arguments source. If the catalog did not
load, preserve the error and use the existing `ado-defer` materialized fleet; if
absent, run `<agent-mcp catalog argv prefix> materialize` on
`examples/ado/defer.mcp.yaml` with
`--server-name ado-defer`. Probe raw `search_workitem` with `--no-serve` plus
`--arguments`
(POSIX) or `--request-file` (Windows); `defer` is not applied on this fallback.
Before use, require `manifest.json.generated_by` to match the installed
agent-mcp and `manifest.json.bridge` to resolve to `defer.mcp.yaml`;
re-materialize otherwise. Confirm the read result belongs to the expected ADO
org/project under the current Azure identity. If both surfaces fail, report
both and stop.

Do NOT use the task tool to spawn another `ado-defer` agent.

## Using this adapter
1. `find_tool` `{query:"pull request"}` (or "work item", "build", …) to discover
   the tool name + when to use it.
2. `execute_tool` `{tool:"<name>", arguments:{...}}` to run it, e.g.
   `{tool:"repo_pull_request", arguments:{action:"list", project:"Example-Web", repositoryId:"example-web"}}`.
