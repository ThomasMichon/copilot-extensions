---
name: ado-code
description: "ADO read-only (code-mode variant). A typed run_code tool over the ADO catalog; aggregate server-side. Enumerate PRs, work items in OneDrive/ODSP-Web. No writes."
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

- Org: `onedrive.visualstudio.com`; primary project **ODSP-Web**, repo **odsp-web**.

## Using this adapter
1. `find_tool` `{query:"pull request"}` to get the TypeScript signatures for the
   tools you need.
2. `run_code` `{code:"<js>"}` — an async function body. Call tools as
   `await tools.<name>(args)` and **return an aggregated value** so only a small
   result reaches you, e.g.
   `const prs = await tools.repo_pull_request({action:"list", project:"ODSP-Web", repositoryId:"odsp-web"}); return {count: prs.length, active: prs.filter(p=>p.status==="Active").length};`
