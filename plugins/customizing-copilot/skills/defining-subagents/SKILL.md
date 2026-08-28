---
name: defining-subagents
description: >
  Defines and reviews Copilot CLI custom-agent and sub-agent definitions -- the
  .agent.md format, frontmatter, tool aliases, bounded execution contracts,
  per-agent MCP ownership, materialized CLI fallback, and anti-recursion /
  MCP-readiness rules. Use when creating, defining, editing, or reviewing a
  custom agent or its frontmatter, not when deciding how to route the current
  runtime task.
  Trigger phrases include:
  - 'custom agent'
  - 'create a custom agent'
  - 'define a custom agent'
  - 'review a custom agent'
  - 'create a sub-agent'
  - 'define a sub-agent'
  - 'review a sub-agent'
  - 'custom-agent frontmatter'
  - 'agent definition'
  - '.agent.md'
  - 'anti-recursion guard'
  - 'MCP readiness section'
---

# Defining Sub-Agents

Use the exact `argv[0]` from the agent-mcp session command catalog for MCP
fallback operations below. Replace `<agent-mcp catalog argv[0]>` with the raw
path and quote it at the shell call site on POSIX; in PowerShell invoke it as
`& "<agent-mcp catalog argv[0]>" <args>`.

Custom agents are specialized profiles Copilot can delegate to. Each runs in its
own subagent process with a separate context window. They are for **delegation**
-- not host/machine identity (which a control harness handles through its own
`AGENTS.md` / host-specific skills).

This skill owns **authoring and review**, not runtime task routing. To decide
whether the current task should be delegated, use
`delegation-guidance:delegating-work`; do not give this skill bare runtime
phrases such as `sub-agent`, `delegate`, or `which agent`.

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

## Execution contract

Author every agent to **execute its assigned scope directly** and return an
integration-ready result. Its prompt should define a bounded responsibility,
inputs, exclusions, and output shape; the agent should return compact findings,
citations, changed-file lists, and validation results rather than raw source,
tool catalogs, or verbose API payloads.

A leaf agent does not create child agents. A coordinator-style custom agent may
delegate distinct child scopes only when its definition and invocation prompt
explicitly authorize that role, but it still must not spawn another copy of
itself. The caller retains final synthesis, integration, and completion unless
the contract explicitly assigns one of those responsibilities.

## Per-agent MCP ownership

Domain or service agents that depend on MCP tools should **define those servers
in their own frontmatter** via the `mcp-servers` block. Copilot CLI starts the
server when the sub-agent is spawned and manages the lifecycle automatically.
The owning agent absorbs the service schema, authentication particulars,
verbose round trips, and raw payloads, then returns only the bounded result its
caller needs.

Project-level `.mcp.json` is reserved for servers the **main coordinator** uses
directly. Keep compact, broadly useful research and orchestration tools there
when their results support decomposition, arbitration, shared history, or task
state and delegating every lookup would cost more than it saves. Domain-specific
service catalogs and calls belong in the domain agent. For the full registration
hierarchy, see the **registering-mcp-servers** skill.

An **agent-mcp-backed** agent needs two authorization-equivalent surfaces over
one bridge config: the primary `mcp-servers` catalog and an explicit materialized
CLI fallback. Both surfaces preserve the same auth source, identity, upstream,
protocol, and top-level `tools:` allow/deny filter. Identity-affecting values
belong in the shared bridge config/overlay, **not** only in
`mcp-servers.env` (shell fallback does not inherit frontmatter-only env).

Decorator-enforced behavior is not equivalent on the one-shot CLI path today.
Static name restrictions must be duplicated in top-level `tools:`. Conditional
authorization (`gate`, or argument-dependent redaction) cannot be represented by
that filter, so those agents must not permit materialized fallback. Shape-only
decorators (`rename`, `defer`, `code-mode`, `transform`, `storage`) also vanish;
their fallback is the wider raw catalog and must be documented honestly. A raw
`curl`/HTTP/product-API bypass is never this fallback.

## Anti-recursion and tool access

Give agents `tools: ["*"]` (or omit the field) so they have full access to file
I/O, shell, grep, and other tools. Do **not** restrict `tools` as an
anti-recursion mechanism -- it cripples agents that need to read docs, inspect
config, or run commands. Narrow tools only when the agent genuinely must not
have a capability. An agent whose explicit tools list omits `agent` / Task is
Task-disabled and exempt from the self-delegation guard.

Every Task-capable agent -- including a coordinator that may spawn other agent
types -- must include this literal, agent-specific line:

```markdown
Do NOT use the task tool to spawn another `<agent-name>` agent.
```

This prevents recursive copies without forbidding a deliberately authorized
coordinator from delegating distinct scopes. For MCP-owning agents, keep that
line in the required `## MCP Readiness` section beside the readiness and
fallback behavior:

1. **MCP readiness check.** Every agent with MCP servers must probe one tool on
   startup and preserve the exact observed error if the catalog does not load.
2. **Same-bridge CLI fallback.** After a catalog/load failure, use the existing
   materialized fleet named by the agent. Re-materialize when the expected stub
   is missing or `manifest.json.generated_by` differs from
   `<agent-mcp catalog argv[0]> --version`;
   config/schema drift needs a deploy-owned digest. Probe a read-only stub with
   `--no-serve` and verify identity/capability before acting. If both surfaces
   fail, report both errors and stop. This fallback does not bypass an auth
   failure, authorization boundary, confirmation gate, or upstream outage.
Start from this compact body template, then substitute the real fleet, probe,
and identity:

```markdown
## MCP Readiness

1. Probe `<read-only-tool>`.
2. If the catalog did not load, preserve the exact error and use the existing
   `<fleet>` materialized fleet; materialize the same bridge only if the stub is
   absent or its `generated_by` version differs.
3. Probe the fallback with `--no-serve` and verify `<expected-identity>`.
4. Verify `manifest.json.bridge` resolves to the frontmatter's bridge config.
5. If both surfaces fail, report both errors and stop.
6. If fallback succeeds, include the preserved primary error in the final output.

Do NOT use the task tool to spawn another `<agent-name>` agent.
```

For the full bridge YAML, Windows/POSIX commands, request-file invocation, and
drift policy, load **`agent-mcp:agent-mcp`** and read *Reliable MCP-backed
sub-agent*.

> **Keep an explicit "Do NOT … spawn / delegate" line — a reworded guard slips
> past the scanner as *missing*.** The `reviewing-customizations` scan detects
> this guard with a loose regex over the collapsed agent body — roughly *do not …
> (task tool | spawn | delegate)* within a short span, followed by the explicit
> `<agent-name> agent` type. It does **not** require the exact canonical sentence,
> but it **does** require that construction: paraphrases that drop the "do not"
> opener, those anchor words, or the named agent type — "never call `task`", "no
> sub-agent fallback", "don't re-invoke myself" — do not match, so the agent is
> flagged as missing the guard (BLOCKING). Safest is to keep the canonical
> literal line `Do NOT use the task tool to spawn another <agent-name> agent`;
> rewording it out is a common regression.

### Hard-rule validation checklist

These are **not** suggestions — they are conformance gates. The
**`reviewing-customizations`** scan machine-checks the presence of readiness,
fallback, and anti-recursion text; reviewers must still verify bridge/identity
equivalence. An agent **fails** review if any applicable box is unchecked:

- [ ] **Every Task-capable agent forbids self-spawn.** Agents with omitted
      `tools`, `tools: ["*"]`, or an explicit `agent` / Task grant include the
      canonical line naming their own agent type. A Task-disabled agent is
      exempt. A coordinator may delegate other types when explicitly authorized
      but may not create another copy of itself.
- [ ] **Tools are not narrowed for anti-recursion.** `tools` is omitted or
      `["*"]` (or lists only *additive* MCP grants); it is **never** trimmed to
      "prevent recursion" — that cripples the agent, it doesn't protect it.
- [ ] **Every MCP-owning agent has a `## MCP Readiness` section.** If the
      frontmatter declares `mcp-servers`, the body must carry the section that
      houses the readiness, equivalent-fallback, and anti-recursion guards.
- [ ] **Readiness probe present.** The section instructs the agent to probe one
      MCP tool on startup and preserve the specific error (or, absent one, name
      the server/tool that failed), then report it even if fallback succeeds.
- [ ] **Equivalent CLI fallback present.** Every agent-mcp-backed server names
      its materialized fleet, uses the same bridge config/identity/top-level
      `tools:` filter, probes a read-only stub with `--no-serve`, and stops only
      after both surfaces fail. Raw product/API bypasses are not accepted.
- [ ] **No frontmatter-only identity.** Auth/identity-affecting env lives in the
      bridge config or overlay, not only in `mcp-servers.env`.
- [ ] **Fleet provenance matches.** `manifest.json.bridge` resolves to the same
      config path used by the primary frontmatter before the fleet is trusted.
- [ ] **Decorator boundary reviewed.** Duplicate static restrictions in
      top-level `tools:`. Conditional `gate`/argument-dependent authorization
      requires the explicit marker `Materialized CLI fallback: disabled
      (conditional authorization gate)`; shape-only decorators yield a wider
      raw catalog that the agent documents.
- [ ] **MCP anti-self-delegation line present.** The section contains an explicit
      "Do NOT … (task tool / spawn / delegate) …" directive — canonically the
      literal line "Do NOT use the task tool to spawn another `<agent-name>`
      agent" (with the agent's own name substituted). The scan requires that
      *construction*, not the exact sentence; a paraphrase that drops the "do
      not" opener or those anchor words (e.g. "never call `task`", "no sub-agent
      fallback") reads as **missing**, so keep the canonical wording.
- [ ] **No MCP agent silently omits the guard.** An agent with `mcp-servers` but
      no readiness/anti-recursion text is a **blocking** finding, not a nit — a
      single missing guard is the exact failure this rule exists to prevent.

An agent with **no** `mcp-servers` is exempt from the MCP-readiness rows but
still owes the Task-capability and anti-self-delegation rules above.

### Invoking MCP tools from the shell (sanctioned CLI path)

When the MCP server is an **`agent-mcp` bridge** (`command: agent-mcp`, as most
wrapped upstreams in this ecosystem are) **and the agent has shell access**
(`execute` — which `tools: ["*"]` grants), the same catalog is invocable straight
from the CLI, no JSON-RPC by hand. `<bridge>` is the bridge's **registered name or
the exact config path** the frontmatter uses — e.g. the `--config <path>` the
`mcp-servers` block passes, given positionally:

- **`<agent-mcp catalog argv[0]> call <bridge> <tool> '<arguments-json>'`** — one-shot: invoke a
  single upstream tool and print the result (pipeable; also reads the args JSON on
  stdin).
- **`<agent-mcp catalog argv[0]> materialize <bridge>`** — project the whole `tools/list` catalog
  into a discoverable CLI stub fleet under `~/.agent-mcp/materialized/<server>/`
  (each stub forwards through the legacy global `agent-mcp call` management <!-- marketplace-isolation: allow materialized-stub-management -->
  wrapper, so tools are invocable by name and pipe
  like any command; `--windows` emits a `.ps1`/`.cmd` shim farm). Re-running
  rebuilds atomically, so it doubles as a drift refresh.

This is a **first-class, sanctioned path** and the required load-failure
fallback for authorization-equivalent agent-mcp-backed agents: it loads the
same bridge configuration and follows the same upstream auth, protocol, and
top-level `tools:` filtering path. It is the bridge's own CLI face, not an
improvised bypass. Shell fallback does not inherit `mcp-servers.env`; move such
values into the bridge config/overlay rather than exporting them ad hoc.

Decorator stacks are intentionally not applied by `call`/`materialize`. The
fleet exposes raw upstream names/results after the top-level `tools:` filter, so
an agent whose safety depends on decorator-only `filter`/`gate` cannot use this
fallback. See **`agent-mcp:agent-mcp`** → *Reliable MCP-backed sub-agent*.

Three boundaries keep this coherent with the readiness rule above:

- **Report the primary failure before falling back.** A probe that fails while
  the same bridge answers on the CLI is an in-process registration fault worth
  surfacing; record it, then continue through the equivalent fleet rather than
  hiding the discrepancy.
- **It cannot rescue a broken bridge.** If the *bridge itself* is broken (upstream
  down, credentials missing, provisioning failed), the generated global
  management call fails
  **identically** — same bridge — so it can't paper over a genuine bridge fault.
  Report the specific error and stop; don't loop.
- **Same bridge, not a raw bypass.** The forbidden fallback (previous section) is
  reaching the upstream by a *different* transport to route around the bridge.
  `call`/`materialize` go *through* the bridge, so they stay inside the policy.

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
