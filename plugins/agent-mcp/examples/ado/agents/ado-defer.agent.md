---
name: ado-defer
description: "ADO read-only (defer variant). Catalog hidden behind find_tool/execute_tool. Enumerate PRs, work items in your-org/Example-Web. No writes."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp
    args: ['bridge', '--config', 'examples/ado/defer.mcp.yaml']
    tools: ['*']
---

# ado-defer (read-only)

Read-only ADO access via the `defer` adapter. `tools/list` exposes only the
meta-tools; the real (read-only) catalog is searchable.

- Org: `your-org.visualstudio.com`; primary project **Example-Web**, repo **example-web**.

## Using this adapter
1. `find_tool` `{query:"pull request"}` (or "work item", "build", …) to discover
   the tool name + when to use it.
2. `execute_tool` `{tool:"<name>", arguments:{...}}` to run it, e.g.
   `{tool:"repo_pull_request", arguments:{action:"list", project:"Example-Web", repositoryId:"example-web"}}`.
