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
| Project | `.github/agents/<name>.agent.md` or `.claude/agents/<name>.agent.md` |
| Personal | `~/.copilot/agents/<name>.agent.md` |
| Plugin | `plugins/<plugin>/agents/<name>.agent.md` (shipped by an enabled plugin) |
| In-repo plugin (`.ai`) | `.ai/<name>/agents/<name>.agent.md` — a sub-agent packaged as a plugin in the repo's own **local marketplace** (`directory` source). **Preferred** for a repo's own MCP-owning sub-agents so they travel with the repo and compose across contexts; see `installing-plugins`. |

For same-ID collisions, personal agents load before project and plugin agents,
so a personal agent overrides a project agent with the same file-derived ID.
Plugin agents cannot override personal or project agents.

> **Packaging a sub-agent as an `.ai` plugin.** A sub-agent that owns an MCP
> server (per the per-agent MCP pattern below) is a natural unit to ship as an
> `.ai` local-marketplace plugin — one `.ai/<name>/` with
> `agents/<name>.agent.md`, enabled via the repo's `directory` marketplace
> source (**which the repo must declare in `.github/copilot/settings.json` —
> the `.ai` directory is inert until it is declared as a locally-referenced
> marketplace**). This keeps the MCP wrapped in the sub-agent (out of the main
> context) **and** lets the agent load whether the repo is launched directly or
> consumed by another harness. **Prefer this local-plugin form** over a loose
> `.github/agents/<name>.agent.md` when practical (same `.agent.md` either way —
> see `authoring-skills` § *Two ways to add an in-repo skill or agent*). See
> `installing-plugins` → *the `.ai` local marketplace* for the required
> marketplace declaration.

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

> **Namespacing across plugins.** When you refer to (or delegate to) a sub-agent
> **shipped by another plugin**, use the **`plugin:name`** form — e.g.
> `agent-logger:session-log-writer` or `copilot-extensions-harness:clean-room-judge`
> — including as the `task` tool's `agent_type`. A bare name is reserved for an
> agent defined in the *same* plugin. The identical convention covers cross-plugin
> **skill** references (see the authoring-skills skill).

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
   startup. If the tools are unavailable, **report the specific tool/MCP error
   and stop immediately** — do not paraphrase it to a generic "not available."
   Report the *exact observed* failure; if the runtime exposes no lower-level
   error, name the specific server/tool that failed to load and state that no
   further diagnostic was available (don't invent one). The honest, specific
   stop is *load-bearing*: it is what **surfaces the real runtime fault** (a
   broken interpreter/venv, an unstarted daemon, a missing binstub, an expired
   token) so the operator can fix it. Any fallback — a neighboring capability, a
   shell/curl workaround, or delegating to a fresh copy of the agent — **masks**
   that fault and, in the recursion case, burns the sub-agent-depth budget until
   the runtime kills it with a misleading "maximum sub-agent depth reached."
2. **Anti-self-delegation rule.** Every agent's instructions must include, **as
   a literal line**: "Do NOT use the task tool to spawn another `<agent-name>`
   agent." This prevents the recursive loop where an MCP failure makes the agent
   delegate to a fresh copy of itself, which also fails, ad infinitum.

Both guards belong in the agent's `## MCP Readiness` section.

> **Keep an explicit "Do NOT … spawn / delegate" line — a reworded guard slips
> past the scanner as *missing*.** The `reviewing-customizations` scan detects
> this guard with a loose regex over the collapsed agent body — roughly *do not …
> (task tool | spawn | delegate)* within a short span. It does **not** require
> the exact canonical sentence, but it **does** require that construction:
> paraphrases that drop the "do not" opener or those anchor words — "never call
> `task`", "no sub-agent fallback", "don't re-invoke myself" — do not match, so
> the agent is flagged as missing the guard (BLOCKING). Safest is to keep the
> canonical literal line `Do NOT use the task tool to spawn another <agent-name>
> agent`; rewording it out is a common regression.

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
      MCP tool on startup and, on failure, **report the specific error and stop**
      — no bash/curl/HTTP fallback, no neighboring-capability fallback, no silent
      degradation. A generic "tools unavailable, stopping" that hides the
      underlying cause is a weak probe: the specific error (or, absent one, the
      named server/tool that failed) is what lets the operator repair the runtime.
- [ ] **Anti-self-delegation line present.** The section contains an explicit
      "Do NOT … (task tool / spawn / delegate) …" directive — canonically the
      literal line "Do NOT use the task tool to spawn another `<agent-name>`
      agent" (with the agent's own name substituted). The scan requires that
      *construction*, not the exact sentence; a paraphrase that drops the "do
      not" opener or those anchor words (e.g. "never call `task`", "no sub-agent
      fallback") reads as **missing**, so keep the canonical wording.
- [ ] **No MCP agent silently omits the guard.** An agent with `mcp-servers` but
      no readiness/anti-recursion text is a **blocking** finding, not a nit — a
      single missing guard is the exact failure this rule exists to prevent.

An agent with **no** `mcp-servers` still owes the tools rule (row 1) but is
exempt from the MCP-readiness rows.

## Wrapping an MCP server: make the agent reachable

The guards above stop a wrapped-MCP agent from *misbehaving*, but a subtler
failure is a request never reaching it. When you wrap an MCP server in a
sub-agent, that agent becomes the **only** path to its tools — so it must be
**unambiguously routable**, or a superficially similar request is silently
captured by a *neighboring capability that operates a different backend*.

The canonical trap: a live-state MCP agent (e.g. one that manages the operator's
**live, signed-in** browser tabs) sits next to an **automation** capability
(e.g. Playwright driving a **separate, dedicated** browser profile). A request
like "organize my browser tabs" reads as browser work, routes to the automation
skill, and drives the *wrong* browser — or dead-ends when automation can't reach
live state. The two operate different backends for a request that sounds like one
capability.

When wrapping an MCP server, therefore:

- **Give the agent a crisp, distinctive description with concrete trigger
  phrases** for the exact live requests it should own ("organize my Edge tabs",
  "find an open tab"), and state its **backend/scope** explicitly (live
  signed-in session vs. a dedicated automation profile) so the router can tell it
  apart from neighbors.
- **Pair it with a routing skill when a neighbor competes.** A short skill whose
  body just delegates to the agent (and names the boundary vs. the neighboring
  capability) is the reliable way to pin routing — an agent description alone can
  lose to a well-triggered sibling skill.
- **Check for capability collisions.** Run the **`reviewing-customizations`**
  scan over the *loaded* set (`--from-settings`); it surfaces **skill↔skill**
  trigger overlaps, including LOCAL↔PLUGIN. Know its limit: it does **not** put
  *agent* descriptions in the collision map, so an **agent-vs-skill** competition
  (exactly the live-tabs-vs-automation trap) will not show up mechanically. That
  is a further reason to give the MCP agent a **routing skill** — a skill *is*
  scanned, so its triggers join the collision map — and to eyeball neighboring
  skills by hand. Two surfaces that both plausibly answer the same phrase but hit
  different backends is a routing bug, even when neither is "wrong" in isolation.
- **State the boundary in both places.** In the MCP agent *and* its neighbor,
  add a one-line "do not use X for this; that's Y's job" so a mis-route
  self-corrects instead of quietly driving the wrong backend.
