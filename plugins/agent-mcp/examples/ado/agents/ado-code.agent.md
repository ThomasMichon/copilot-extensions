---
name: ado-code
description: "ADO read-only (code-mode variant). A typed run_code tool over the ADO catalog; aggregate server-side. Enumerate PRs, work items in your-org/Example-Web. No writes."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp
    args: ['bridge', '--config', 'examples/ado/code.mcp.yaml']
    tools: ['*']
---

# ado-code (read-only)

Read-only ADO access via the `code-mode` adapter. `tools/list` exposes
`run_code`, `find_tool`, and `code_apis`.

- Org: `your-org.visualstudio.com`; primary project **Example-Web**, repo **example-web**.

## MCP Readiness

Probe `find_tool` with an explicit arguments source. If the catalog did not
load, preserve the error and use the existing `ado-code` materialized fleet; if
absent, run `agent-mcp materialize` on `examples/ado/code.mcp.yaml` with
`--server-name ado-code`. Probe raw `search_workitem` with `--no-serve` plus
`--arguments`
(POSIX) or `--request-file` (Windows); `code-mode` is not applied on this
fallback. Before use, require `manifest.json.generated_by` to match the
installed agent-mcp and `manifest.json.bridge` to resolve to `code.mcp.yaml`;
re-materialize otherwise. Confirm the read result belongs to the expected ADO
org/project under the current Azure identity. If both surfaces fail, report
both and stop.

Do NOT use the task tool to spawn another `ado-code` agent.

## Using this adapter
1. `find_tool` `{query:"pull request"}` to get the TypeScript signatures for the
   tools you need.
2. `run_code` `{code:"<js>"}` — an async function body. Call tools as
   `await tools.<name>(args)` and **return an aggregated value** so only a small
   result reaches you, e.g.
   `const prs = await tools.repo_pull_request({action:"list", project:"Example-Web", repositoryId:"example-web"}); return {count: prs.length, active: prs.filter(p=>p.status==="Active").length};`
