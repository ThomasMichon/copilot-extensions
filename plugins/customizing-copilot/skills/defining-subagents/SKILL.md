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

### Hard-rule validation checklist

These are **not** suggestions — they are conformance gates. Run this checklist
against every `.agent.md` you author or review (it is the machine-checkable core
the **`reviewing-customizations`** scan enforces). An agent **fails** review if
any applicable box is unchecked:

- [ ] **Tools are not narrowed for anti-recursion.** `tools` is omitted or
      `["*"]` (or lists only *additive* MCP grants); it is **never** trimmed to
      "prevent recursion" — that cripples the agent, it doesn't protect it.
- [ ] **Every MCP-owning agent has a `## MCP Readiness` section.** If the
      frontmatter declares `mcp-servers`, the body must carry the section that
      houses both guards below.
- [ ] **Readiness probe present.** The section instructs the agent to probe one
      MCP tool on startup and, on failure, **report and stop** — no bash/curl/
      HTTP fallback, no silent degradation.
- [ ] **Anti-self-delegation line present, verbatim intent.** The section
      contains "Do NOT use the task tool to spawn another `<agent-name>` agent"
      (with the agent's own name substituted).
- [ ] **No MCP agent silently omits the guard.** An agent with `mcp-servers` but
      no readiness/anti-recursion text is a **blocking** finding, not a nit — a
      single missing guard is the exact failure this rule exists to prevent.

An agent with **no** `mcp-servers` still owes the tools rule (row 1) but is
exempt from the MCP-readiness rows.
