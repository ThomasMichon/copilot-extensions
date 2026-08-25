---
name: registering-mcp-servers
description: >
  Register MCP servers for the Copilot CLI -- the per-agent / project / global
  registration hierarchy, config formats (.mcp.json / .github/mcp.json vs the
  VS Code file), environment-variable substitution, the MCP CLI commands, and
  writing a server in Python. Use when adding, configuring, or debugging an MCP
  server, or wiring one into an agent or repo.
  Trigger phrases include:
  - 'MCP server'
  - 'register an MCP'
  - 'add an MCP server'
  - '.mcp.json'
  - '.github/mcp.json'
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
2. **Project** (`.mcp.json` or `.github/mcp.json`) -- for servers the **main
   agent** uses directly. Available session-wide after the directory is trusted.
3. **Plugin** (`plugin.json` `mcpServers` path/object) -- for a server a plugin
   intentionally contributes to the main agent whenever that plugin is enabled.
   Use sparingly; a domain MCP server usually belongs inside the sub-agent that
   owns it.
4. **Global** (`~/.copilot/mcp-config.json`) -- available everywhere. **Avoid**
   unless the server is truly universal; it pollutes the tool namespace across
   all repos and sessions.

## Config formats

### Project (Copilot CLI)

Files: `.mcp.json` (in the working directory or any parent up to the repo root)
and `.github/mcp.json`. If both exist in the same directory, `.mcp.json` takes
precedence; definitions closer to the working directory override farther ones.
Project-level definitions take precedence over user-level
`~/.copilot/mcp-config.json` definitions with the same server name. They load
only after the directory is trusted (prompt mode skips untrusted project MCP
unless explicitly opted in). Copilot CLI does **not** read `.vscode/mcp.json`.

Both project files may use either the `"mcpServers"` object (same key as
`~/.copilot/mcp-config.json`) or the bare top-level form where each top-level key
is a server name.

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

Bare top-level form:

```json
{
  "server-name": {
    "type": "local",
    "command": "npx",
    "args": ["-y", "@some/mcp-server"]
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

### Plugin-contributed MCP

A plugin can set `mcpServers` in `plugin.json` to an inline server map or a path
such as `.mcp.json` / `.github/mcp.json` inside the plugin. These servers load
when the plugin is enabled and are useful only when the plugin is deliberately
adding main-agent tools; for domain-specific tools, prefer the per-agent
`mcp-servers` block so the tools stay scoped to the sub-agent.

For a plugin-backed stdio server, locate code with `${PLUGIN_ROOT}` in config or
`COPILOT_PLUGIN_ROOT` in the child. The plugin loader defaults `cwd` to the
plugin root even when it is omitted, while the runtime injects the current
session as `COPILOT_AGENT_SESSION_ID`. It does **not** inject Copilot's target
directory or advertise MCP roots. A server that needs repository access must
take a validated tool argument, use an explicitly adopter-configured env value,
or be registered at project scope instead. The full cross-surface contract and
caveats are in
[`references/plugin-runtime-context.md`](../../references/plugin-runtime-context.md).

## Environment-variable syntax

All MCP configs support `$VAR`, `${VAR}`, and `${VAR:-default}` substitution in
string fields. Coding-agent contexts also support `${{ secrets.VAR }}` and
`${{ vars.VAR }}` (secrets must be prefixed `COPILOT_MCP_`).

## CLI commands

Interactive: `/mcp add` (setup wizard), `/mcp show [NAME]` (list/details),
`/mcp edit NAME`, `/mcp delete NAME`, `/mcp enable|disable NAME`.

Terminal: `copilot mcp add`, `copilot mcp list`, `copilot mcp get NAME`, and
`copilot mcp remove NAME` manage user-level servers in
`~/.copilot/mcp-config.json`.

## Writing a server (Python)

Install `mcp[cli]` in the server's own environment (for Python, prefer a venv,
for example `uv venv` + `uv pip install "mcp[cli]"`); use `FastMCP` from
`mcp.server.fastmcp`, decorate functions with `@mcp.tool()`, and call
`mcp.run()`.

To wrap an **authenticated upstream** MCP server and inject host credentials
without baking the secret tool into the config, the `agent-mcp` plugin is a
ready-made bridge.

## MCP tool references in skills/agents

Use fully qualified MCP tool names (`server-name/tool-name`, or
`server-name/*` for grants) when referencing tools in agent frontmatter or
instructions, so the agent can locate the tool when multiple MCP servers are
available. If `/mcp show` displays a different exact tool name, copy that exact
displayed name.
