---
name: registering-mcp-servers
description: >
  Register MCP servers for the Copilot CLI -- the per-agent / project / global
  registration hierarchy, config formats (.mcp.json vs the VS Code file),
  environment-variable substitution, the MCP CLI commands, and writing a server
  in Python. Use when adding, configuring, or debugging an MCP server, or wiring
  one into an agent or repo.
  Trigger phrases include:
  - 'MCP server'
  - 'register an MCP'
  - 'add an MCP server'
  - '.mcp.json'
  - 'mcp-servers'
  - 'mcpServers'
  - 'write an MCP server'
  - 'wire up an MCP'
---

# Registering MCP Servers

MCP (Model Context Protocol) servers expose external tools to the Copilot CLI.
There are multiple registration points -- **prefer the narrowest scope that
works.**

Reference: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers

## Registration preference order

1. **Per-agent** (in `.agent.md` frontmatter) -- **preferred for sub-agents**.
   Each sub-agent defines its own MCP servers; Copilot CLI manages the server
   lifecycle tied to the sub-agent's lifespan. See the **defining-subagents**
   skill.
2. **Project** (`.mcp.json` in repo root) -- for servers the **main agent** uses
   directly. Available session-wide.
3. **Global** (`~/.copilot/mcp-config.json`) -- available everywhere. **Avoid**
   unless the server is truly universal; it pollutes the tool namespace across
   all repos and sessions.

## Config formats

### Project (Copilot CLI)

File: `.mcp.json` in the repo root, top-level key `"mcpServers"` (same key as
`~/.copilot/mcp-config.json`). Copilot CLI does **not** read `.vscode/mcp.json`.

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@some/mcp-server"],
      "env": {}
    }
  }
}
```

### Per-agent

Defined in the `mcp-servers` block of an agent's YAML frontmatter. Types:
`stdio`/`local` (equivalent), `http`, `sse`. Use `stdio` for cross-client
compatibility. Tools are namespaced `server-name/tool-name`; grant access in the
agent's `tools` list with `'server-name/*'`.

### VS Code (editor only)

File: `.vscode/mcp.json` -- top-level key `"servers"` (not `"mcpServers"`). Read
by VS Code only, **not** by Copilot CLI. Maintain both files if needed.

### Global

File: `~/.copilot/mcp-config.json` -- same `"mcpServers"` key. The GitHub MCP
server is built in and need not be configured here.

## Environment-variable syntax

All MCP configs support `$VAR`, `${VAR}`, and `${VAR:-default}` substitution in
string fields. Coding-agent contexts also support `${{ secrets.VAR }}` and
`${{ vars.VAR }}` (secrets must be prefixed `COPILOT_MCP_`).

## CLI commands

`/mcp add` (setup wizard), `/mcp show [NAME]` (list/details), `/mcp edit NAME`,
`/mcp delete NAME`, `/mcp enable|disable NAME`.

## Writing a server (Python)

Install `pip install "mcp[cli]"`; use `FastMCP` from `mcp.server.fastmcp`,
decorate functions with `@mcp.tool()`, and call `mcp.run()`.

To wrap an **authenticated upstream** MCP server and inject host credentials
without baking the secret tool into the config, the `agent-mcp` plugin is a
ready-made bridge.

## MCP tool references in skills/agents

Always use fully qualified tool names (`ServerName:tool_name`) when referencing
MCP tools in instructions, so the agent can locate the tool when multiple MCP
servers are available.
