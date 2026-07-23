---
name: ado-plain
description: "ADO read-only (plain filter variant). Enumerate PRs, work items, builds in your-org/Example-Web. No writes. agent-mcp decorator-stack example."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp
    args: ['bridge', '--config', 'examples/ado/plain.mcp.yaml']
    tools: ['*']
---

# ado-plain (read-only)

Read-only ADO access to the **your-org** org via the `plain` adapter (mutating
tools filtered out; everything else exposed directly).

- Org: `your-org.visualstudio.com`; primary project **Example-Web**, repo **example-web**.

## Using this adapter
Call read tools directly by name:
- `repo_pull_request` `{action:"list", project:"Example-Web", repositoryId:"example-web"}`
- `search_workitem` `{searchText:"...", project:"Example-Web"}`
- `wit_backlog` `{action:"list", project:"Example-Web", team:"..."}`
