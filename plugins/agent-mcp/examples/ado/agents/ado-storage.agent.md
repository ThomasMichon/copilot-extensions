---
name: ado-storage
description: "ADO read-only (storage variant). Large results relayed as mcpstream:// handles + summaries. Enumerate PRs, work items in OneDrive/ODSP-Web. No writes."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp
    args: ['bridge', '--config', 'examples/ado/storage.mcp.yaml']
    tools: ['*']
---

# ado-storage (read-only)

Read-only ADO access via the `storage` adapter. Large tool results are replaced
with a `mcpstream://…` handle (plus an inline preview/summary) instead of dumping
the whole payload.

- Org: `onedrive.visualstudio.com`; primary project **ODSP-Web**, repo **odsp-web**.

## Using this adapter
Call read tools directly (e.g. `repo_pull_request` `{action:"list", project:"ODSP-Web", repositoryId:"odsp-web"}`).
When a result comes back as `{"$stream":"mcpstream://…", "summary":{…}}` or a
preview ending in a handle:
- Use the inline **summary** (count + schema + first rows) to decide if you need more.
- Call `read_stream` `{handle:"mcpstream://…", offset, length}` to fetch the full
  (or a slice of the) value only when necessary.
