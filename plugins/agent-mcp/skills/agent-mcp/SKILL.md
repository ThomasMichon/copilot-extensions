---
name: agent-mcp
description: >-
  Bridge an upstream MCP server (HTTP or stdio) as a local stdio MCP server and
  inject host credentials, and set up repo-scoped Copilot sub-agents backed by
  it. Use when asked to "wrap an MCP", "bridge an MCP", "add auth to an MCP
  server", "proxy an MCP", "use an MCP that needs az/gh login", "set up a
  sub-agent for <service>", or to expose a remote/authenticated MCP to Copilot.
---

# agent-mcp

`agent-mcp` wraps one upstream MCP server as a local **stdio** MCP server and
injects host credentials, driven by a single per-bridge config file. It
replaces single-purpose, hardcoded MCP wrapper scripts with a config-driven,
multi-transport, multi-auth bridge.

## When to use

- An MCP server requires an OAuth/broker login flow (Entra/`az`, `gh`) that
  Copilot CLI can't perform itself.
- You want to wrap a third-party stdio MCP and feed it a host-acquired token.
- You want to allow/deny which upstream tools are exposed.
- You want a **repo-scoped sub-agent** (e.g. `@ado-data`) whose MCP tools come
  from an authenticated upstream -- see the setup flow below.

## Config location -- in-repo vs. user-global

A bridge config can be referenced two ways:

| Form | Reference | Lives in | Use for |
|------|-----------|----------|---------|
| **In-repo `--config`** (preferred) | `bridge --config <path>` | the repo (e.g. `.github/agents/<name>.mcp.yaml`) | **repo-scoped agents** -- config is version-controlled, travels with the repo, needs no deploy |
| **Named bridge** | `bridge <name>` | `~/.agent-mcp/bridges/<name>.{yaml,yml,json}` | **personal / cross-repo** MCPs not tied to one repo |

> **Prefer the in-repo `--config` form for any agent that ships inside a repo.**
> Reserve named bridges (user-global `~/.agent-mcp/bridges/`) for MCPs you use
> across many repos or that do not belong to a checkout. Both forms read the
> same config schema; only the lookup differs.

## Set up a repo-scoped sub-agent (the common case)

This is the end-to-end flow for giving a Copilot sub-agent authenticated MCP
tools -- e.g. an `@ado-data` agent backed by the Azure DevOps MCP.

**1. Write the bridge config in the repo**, next to the agent
(`.github/agents/<name>.mcp.yaml`). It holds the upstream `server` launch info
(same shape as a `.mcp.json` entry) plus `auth` and overrides:

```yaml
# .github/agents/ado.mcp.yaml
server:
  type: http                       # http | stdio
  url: https://mcp.dev.azure.com/your-org
auth:
  kind: entra                      # entra|az | gh | git-credential | env|static | none
  resource: 2a72489c-aab2-4b65-b93a-a91edccf33b8   # az resource/scope
tools: { allow: ["repo_*", "wit_*"], deny: [] }    # optional upstream filter
```

Validate before wiring: `agent-mcp validate .github/agents/ado.mcp.yaml`.

**2. Point the sub-agent at it** in `.github/agents/<name>.agent.md`
front-matter. The MCP server is `agent-mcp` running the bridge over stdio:

```yaml
---
name: ado-data
description: "Azure DevOps data access ... Use when ADO information is needed."
tools: ["*"]
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp.cmd          # see Windows note below
    args: ['bridge', '--config', '.github/agents/ado.mcp.yaml']
    tools: ['*']
---
```

The `--config` path is resolved relative to the process cwd, which is the repo
root when Copilot spawns the sub-agent's MCP server -- so an in-repo relative
path just works.

**3. Verify end-to-end** by invoking the sub-agent and having it call an upstream
tool (e.g. fetch a repo). A clean way to prove the bridge -- not a stale runtime
-- is in use is to exercise a real query and confirm a live result.

> **Windows: use `command: agent-mcp.cmd` (explicit `.cmd`).** Copilot spawns
> the MCP server directly; the `.ps1` binstub adapter does **not** forward piped
> stdin on Windows, which an stdio MCP server depends on. The `.cmd` binstub
> forwards stdin correctly. On Linux/WSL, plain `command: agent-mcp` is fine.

## Auth kinds

| kind | acquires via | injects |
|------|--------------|---------|
| `entra` / `az` | `az account get-access-token` | `Authorization: Bearer` (http) / env (stdio) |
| `gh` | `gh auth token` | `Authorization: Bearer` / env |
| `git-credential` | Git Credential Manager | `Authorization: Basic` / env |
| `env` / `static` | host env var or literal | templated header / target env |
| `none` | -- | nothing |

Token acquisition reuses the `credential-relay` sources; the bridge refreshes the
credential and retries once on an upstream `401`.

## Commands

```
agent-mcp bridge --config FILE    # run the bridge from an in-repo config (preferred)
agent-mcp bridge <name>           # run a named bridge (~/.agent-mcp/bridges/<name>.*)
agent-mcp validate <name|FILE>    # parse + schema-check
agent-mcp status                  # prerequisites + available named bridges
```

## Install

`./scripts/init.sh` (Linux/WSL) or `.\scripts\init.ps1` (Windows) -- creates the
venv at `~/.agent-mcp` and the `agent-mcp` binstub (`.cmd` + `.ps1` on Windows)
in `~/.local/bin`.
