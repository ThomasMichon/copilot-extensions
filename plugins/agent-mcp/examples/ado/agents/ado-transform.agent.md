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

## MCP Readiness

Probe `search_workitem` with an explicit arguments source. If the catalog did
not load, preserve the error and use the existing `ado-transform` materialized
fleet; if absent, run `agent-mcp materialize` on
`examples/ado/transform.mcp.yaml` with `--server-name ado-transform`. Probe the
raw read tool with `--no-serve` plus
`--arguments` (POSIX) or `--request-file` (Windows); transforms are not applied
on this fallback. Before use, require `manifest.json.generated_by` to match the
installed agent-mcp and `manifest.json.bridge` to resolve to
`transform.mcp.yaml`; re-materialize otherwise. Confirm the read result belongs
to the expected ADO org/project under the current Azure identity. If both
surfaces fail, report both and stop.

Do NOT use the task tool to spawn another `ado-transform` agent.

## Using this adapter
Call read tools directly; results are already slimmed:
- `repo_pull_request` `{action:"list", project:"Example-Web", repositoryId:"example-web"}`
  returns rows of `{pullRequestId, title, status, isDraft, createdBy.displayName, sourceRefName, targetRefName}`.
- `search_workitem` `{searchText:"...", project:"Example-Web"}` returns rows of
  `{fields:{system.id, system.title, system.state, system.workitemtype}}`.
