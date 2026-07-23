---
name: ado-transform
description: "ADO read-only (transform variant). List/search results slimmed to key fields. Enumerate PRs, work items in your-org/Example-Web. No writes."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp
    args: ['bridge', '--config', 'examples/ado/transform.mcp.yaml']
    tools: ['*']
---

# ado-transform (read-only)

Read-only ADO access via the `transform` adapter. Verbose list/search endpoints
are reshaped server-side to only the key fields.

- Org: `your-org.visualstudio.com`; primary project **Example-Web**, repo **example-web**.

## Using this adapter
Call read tools directly; results are already slimmed:
- `repo_pull_request` `{action:"list", project:"Example-Web", repositoryId:"example-web"}`
  returns rows of `{pullRequestId, title, status, isDraft, createdBy.displayName, sourceRefName, targetRefName}`.
- `search_workitem` `{searchText:"...", project:"Example-Web"}` returns rows of
  `{fields:{system.id, system.title, system.state, system.workitemtype}}`.
