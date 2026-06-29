# Authoring Extensions Reference

Detailed reference material for custom agents, MCP servers, custom
instructions, and hooks. See [SKILL.md](../SKILL.md) for the skill overview
and table of contents.

---

## Custom Agents (Sub-Agents)

Specialized agent profiles that Copilot can delegate to. Each runs in its
own subagent process with a separate context window. Custom agents are for
**delegation sub-agents** -- not host/machine identity (which a control
harness typically handles through its own `AGENTS.md` / host-specific skills).

### Locations

| Scope | Path |
|-------|------|
| Project | `.github/agents/<name>.agent.md` |
| Personal | `~/.copilot/agents/<name>.agent.md` |

Personal agents override project agents with the same name.

### Agent File Format

YAML frontmatter followed by a markdown system prompt (max 30,000 chars):

````yaml
---
name: agent-name
description: |
  What this agent does and when to use it.
tools: ['shell', 'read', 'search', 'edit', 'agent', 'skill', 'ask_user']
mcp-servers:
  server-name:
    type: stdio
    command: python3
    args: ['tools/server.py']
    tools: ["*"]
    env:
      KEY: value
      FROM_ENVIRONMENT: $MY_ENV_VAR
---

# System Prompt (instructions, personality, domain knowledge)
````

### Frontmatter Properties

| Property | Required | Purpose |
|----------|----------|---------|
| `description` | **yes** | Purpose and capabilities. Drives auto-delegation. |
| `name` | no | Display name (file stem used for dedup). |
| `tools` | no | Allowed tools. Omit or `["*"]` for all. |
| `model` | no | Override the default model. |
| `mcp-servers` | no | MCP servers spun up only for this agent. |
| `disable-model-invocation` | no | If `true`, no auto-delegation. |
| `user-invocable` | no | If `false`, programmatic-only. |

### Tool Aliases

Standard aliases: `execute` (shell/bash/powershell), `read`, `edit`,
`search` (grep/glob), `agent` (Task), `web` (WebSearch/WebFetch),
`todo`. Grant MCP server tools with `'server-name/*'` or
`'server-name/tool-name'` in the tools list.

### Invocation

- **Slash command:** `/agent` then select from list
- **Explicit:** "Use the security-auditor agent on src/"
- **By inference:** prompt matches agent description, Copilot auto-delegates
- **Programmatic:** `copilot --agent agent-name --prompt "..."`

### MCP Server Ownership

Sub-agents that depend on MCP tools should **define those servers in their
own frontmatter** via the `mcp-servers` block. Copilot CLI starts the
server when the sub-agent is spawned and manages the lifecycle automatically.

Project-level `.mcp.json` is reserved for servers the **main agent** uses
directly. Domain-specific MCP servers belong in the sub-agent that uses them.

If an agent's MCP tools fail to load, report the problem to the
administrator and stop -- don't attempt workarounds via bash, curl, or
other fallbacks.

### Anti-Recursion and Tool Access

Give agents `tools: ["*"]` (or omit the field) so they have full access
to file I/O, shell, grep, and other tools needed for effective workflows.
Do **not** restrict `tools` as an anti-recursion mechanism -- it cripples
agents that need to read docs, inspect config, or run commands.

Instead, prevent self-delegation via **instruction-based guards**:

1. **MCP readiness check.** Every agent with MCP servers must probe one
   tool on startup. If the tools are not available, report the failure and
   stop immediately.
2. **Anti-self-delegation rule.** Every agent's instructions must include:
   "Do NOT use the task tool to spawn another `<agent-name>` agent."
   This prevents the recursive loop where an MCP failure causes the agent
   to delegate to a fresh copy of itself, which also fails, ad infinitum.

Both guards belong in the agent's `## MCP Readiness` section.

---

## MCP Servers

MCP (Model Context Protocol) servers expose external tools to Copilot CLI.
There are multiple registration points. **Prefer the narrowest scope that
works.**

### Registration Preference Order

1. **Per-agent** (in `.agent.md` frontmatter) -- **preferred for sub-agents**.
   Each sub-agent defines its own MCP servers. Copilot CLI manages the
   server lifecycle tied to the sub-agent's lifespan.
2. **Project** (`.mcp.json` in repo root) -- for servers the **main agent**
   uses directly. Available session-wide.
3. **Global** (`~/.copilot/mcp-config.json`) -- available everywhere.
   **Avoid** unless the server is truly universal. Pollutes the tool
   namespace across all repos and sessions.

### Per-Agent Configuration (Preferred)

Defined in the `mcp-servers` block of an agent's YAML frontmatter (see
Agent File Format above). Types: `stdio`/`local` (equivalent), `http`,
`sse`. Use `stdio` for cross-client compatibility.

Tools are namespaced as `server-name/tool-name`. Grant access in the
agent's `tools` list with `'server-name/*'`.

### Project Configuration (Copilot CLI)

File: `.mcp.json` in the repo root (top-level key `"mcpServers"`). Same
key as `~/.copilot/mcp-config.json`. Copilot CLI does **not** read
`.vscode/mcp.json`.

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

### VS Code Configuration (editor only)

File: `.vscode/mcp.json` -- uses top-level key `"servers"` (not `"mcpServers"`).
Read by VS Code only, **not** by Copilot CLI. Maintain both files if needed.

### Global Configuration

File: `~/.copilot/mcp-config.json` -- same `"mcpServers"` key as `.mcp.json`.
The GitHub MCP server is built in and does not need to be configured here.

### Environment Variable Syntax

All MCP configurations support `$VAR`, `${VAR}`, and `${VAR:-default}`
substitution in string fields. Coding agent contexts also support
`${{ secrets.VAR }}` and `${{ vars.VAR }}` (secrets must be prefixed
with `COPILOT_MCP_`).

### CLI Commands

`/mcp add` (setup wizard), `/mcp show [NAME]` (list/details),
`/mcp edit NAME`, `/mcp delete NAME`, `/mcp enable|disable NAME`.

### Writing an MCP Server (Python)

Install: `pip install "mcp[cli]"`. Use `FastMCP` from `mcp.server.fastmcp`,
decorate functions with `@mcp.tool()`, and call `mcp.run()`. See the
[MCP servers docs](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers)
for the full API. To wrap an *authenticated upstream* MCP server and inject
host credentials, the `agent-mcp` plugin is a ready-made bridge.

---

## Custom Instructions

Always-on context injected into every prompt. Use for repo conventions,
coding standards, and communication preferences.

| Scope | File |
|-------|------|
| Repo (always loaded) | `AGENTS.md` in repo root or cwd |
| Repo (always loaded) | `.github/copilot-instructions.md` |
| Personal (all repos) | `~/.copilot/copilot-instructions.md` |
| Host/machine-scoped (deployed) | a generated instructions directory loaded via `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` |

Suppress with `--no-custom-instructions`.

### Avoiding auto-load in AGENTS.md

Copilot follows valid Markdown links in custom-instruction files and
auto-loads them. **Rule:** Use backtick code spans (`` `docs/tools.md` ``)
instead of `[text](path)` links. Copilot reads files on demand when needed
but won't auto-load them into every session.

---

## Hooks

Shell commands that execute at specific lifecycle points during agent
sessions. Use for guardrails, policy enforcement, audit logging, and
external integrations.

### Locations

| Scope | Path |
|-------|------|
| Project | `.github/hooks/*.json` (any JSON file in the directory) |

For Copilot CLI, hooks are discovered from the current working directory.
For cloud agent, the files must be on the default branch.

### Configuration Format

Every hooks file needs `version: 1` and a `hooks` object containing
arrays of hook definitions:

```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [
      {
        "type": "command",
        "bash": "./scripts/check.sh",
        "powershell": "./scripts/check.ps1",
        "cwd": ".",
        "env": { "LOG_LEVEL": "INFO" },
        "timeoutSec": 15
      }
    ]
  }
}
```

### Hook Types

| Event | Fires when | Can block? |
|-------|-----------|------------|
| `sessionStart` | Session begins or resumes | No |
| `sessionEnd` | Session completes or terminates | No |
| `userPromptSubmitted` | User submits a prompt | No |
| `preToolUse` | Before any tool invocation | **Yes** -- return `{"permissionDecision":"deny","permissionDecisionReason":"..."}` |
| `postToolUse` | After a tool completes | No |
| `agentStop` | Main agent finishes responding | No |
| `subagentStop` | Sub-agent completes | No |
| `errorOccurred` | Error during agent execution | No |

All hooks receive JSON on stdin with at minimum `timestamp` and `cwd`.
Tool hooks also receive `toolName` and `toolArgs`. Post-tool hooks include
`toolResult` with `resultType` and `textResultForLlm`.

### Script I/O

- **Input:** read all of stdin as JSON, parse with `jq` (bash) or
  `ConvertFrom-Json` (PowerShell).
- **Output (preToolUse only):** single-line JSON on stdout. Use `jq -c`
  or `ConvertTo-Json -Compress`.
- **Stderr:** treated as debug logging, does not affect behavior.
- **Exit code:** 0 = success; non-zero = hook failure (tool call proceeds).

### Performance

Hooks run synchronously and block agent execution. Keep them under
5 seconds. Use append-only file I/O and background processing for
expensive operations.

### Detailed Reference

For full input/output schemas, script patterns, debugging, and advanced
examples, see the
[hooks docs](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks)
and [hooks configuration reference](https://docs.github.com/en/copilot/reference/hooks-configuration).
