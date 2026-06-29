---
name: defining-subagents
description: >
  Define Copilot CLI custom agents (sub-agents) for delegation -- the .agent.md
  file format, frontmatter properties, tool aliases, invocation, owning MCP
  servers per-agent, and the anti-recursion / MCP-readiness pattern. Use when
  creating or editing a custom agent, a .agent.md file, or configuring sub-agent
  delegation.
  Trigger phrases include:
  - 'custom agent'
  - 'sub-agent'
  - 'subagent'
  - 'agent definition'
  - '.agent.md'
  - 'delegate to an agent'
  - 'create an agent'
  - 'anti-recursion'
---

# Defining Sub-Agents

Custom agents are specialized profiles Copilot can delegate to. Each runs in its
own subagent process with a separate context window. They are for **delegation**
-- not host/machine identity (which a control harness handles through its own
`AGENTS.md` / host-specific skills).

Reference: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli
· config reference: https://docs.github.com/en/copilot/reference/custom-agents-configuration

## Locations

| Scope | Path |
|-------|------|
| Project | `.github/agents/<name>.agent.md` |
| Personal | `~/.copilot/agents/<name>.agent.md` |

Personal agents override project agents with the same name.

## Agent file format

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

### Frontmatter properties

| Property | Required | Purpose |
|----------|----------|---------|
| `description` | **yes** | Purpose and capabilities. Drives auto-delegation. |
| `name` | no | Display name (file stem used for dedup). |
| `tools` | no | Allowed tools. Omit or `["*"]` for all. |
| `model` | no | Override the default model. |
| `mcp-servers` | no | MCP servers spun up only for this agent. |
| `disable-model-invocation` | no | If `true`, no auto-delegation. |
| `user-invocable` | no | If `false`, programmatic-only. |

### Tool aliases

Standard aliases: `execute` (shell/bash/powershell), `read`, `edit`, `search`
(grep/glob), `agent` (Task), `web` (WebSearch/WebFetch), `todo`. Grant MCP server
tools with `'server-name/*'` or `'server-name/tool-name'` in the tools list.

## Invocation

- **Slash command:** `/agent` then select from the list
- **Explicit:** "Use the security-auditor agent on src/"
- **By inference:** prompt matches the agent description, Copilot auto-delegates
- **Programmatic:** `copilot --agent agent-name --prompt "..."`

## Per-agent MCP ownership

Sub-agents that depend on MCP tools should **define those servers in their own
frontmatter** via the `mcp-servers` block. Copilot CLI starts the server when the
sub-agent is spawned and manages the lifecycle automatically. Project-level
`.mcp.json` is reserved for servers the **main agent** uses directly;
domain-specific MCP servers belong in the sub-agent that uses them. For the full
registration hierarchy, see the **registering-mcp-servers** skill.

If an agent's MCP tools fail to load, report the problem to the administrator and
stop -- don't attempt workarounds via bash, curl, or other fallbacks.

## Anti-recursion and tool access

Give agents `tools: ["*"]` (or omit the field) so they have full access to file
I/O, shell, grep, and other tools. Do **not** restrict `tools` as an
anti-recursion mechanism -- it cripples agents that need to read docs, inspect
config, or run commands. Instead, prevent self-delegation via
**instruction-based guards**:

1. **MCP readiness check.** Every agent with MCP servers must probe one tool on
   startup. If the tools are unavailable, report the failure and stop
   immediately.
2. **Anti-self-delegation rule.** Every agent's instructions must include: "Do
   NOT use the task tool to spawn another `<agent-name>` agent." This prevents
   the recursive loop where an MCP failure makes the agent delegate to a fresh
   copy of itself, which also fails, ad infinitum.

Both guards belong in the agent's `## MCP Readiness` section.
