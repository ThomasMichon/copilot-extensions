---
name: agent-mcp
description: >-
  Bridge an upstream MCP server (HTTP or stdio) as a local stdio MCP server and
  inject host credentials. Use when asked to "wrap an MCP", "bridge an MCP",
  "add auth to an MCP server", "proxy an MCP", "use an MCP that needs az/gh
  login", or to expose a remote/authenticated MCP to Copilot CLI.
---

# agent-mcp

`agent-mcp` wraps one upstream MCP server as a local **stdio** MCP server and
injects host credentials, driven by a single per-bridge config file. It
generalizes the old `ado-mcp-proxy` into a config-driven, multi-transport,
multi-auth bridge.

## When to use

- An MCP server requires an OAuth/broker login flow (Entra/`az`, `gh`) that
  Copilot CLI can't perform itself.
- You want to wrap a third-party stdio MCP and feed it a host-acquired token.
- You want to allow/deny which upstream tools are exposed.

## Define a bridge

Create a config file (named bridge at `~/.agent-mcp/bridges/<name>.yaml`, or any
path you pass with `--config`). It has the upstream `server` launch info (same
shape as a `.mcp.json` entry) plus `auth` and overrides:

```yaml
server:
  type: http                       # http | stdio
  url: https://mcp.dev.azure.com/onedrive
auth:
  kind: entra                      # entra|az | gh | git-credential | env|static | none
  resource: 2a72489c-aab2-4b65-b93a-a91edccf33b8
tools: { allow: ["repo_*", "wit_*"], deny: [] }   # optional
```

Validate it: `agent-mcp validate <name|file>`.

## Wire it into an agent

```yaml
mcp-servers:
  my-mcp:
    type: stdio
    command: agent-mcp
    args: ['bridge', '--config', '.github/agents/my.mcp.yaml']
    tools: ['*']
```

## Auth kinds

| kind | acquires via | injects |
|------|--------------|---------|
| `entra` / `az` | `az account get-access-token` | `Authorization: Bearer` (http) / env (stdio) |
| `gh` | `gh auth token` | `Authorization: Bearer` / env |
| `git-credential` | Git Credential Manager | `Authorization: Basic` / env |
| `env` / `static` | host env var or literal | templated header / target env |
| `none` | — | nothing |

Token acquisition reuses the `credential-relay` sources; the bridge refreshes the
credential and retries once on an upstream `401`.

## Commands

```
agent-mcp bridge <name|--config FILE>   # run the bridge (what an agent invokes)
agent-mcp validate <name|FILE>          # parse + schema-check
agent-mcp status                        # prerequisites + available bridges
```

## Install

`./scripts/init.sh` (Linux/WSL) or `.\scripts\init.ps1` (Windows) — creates the
venv at `~/.agent-mcp` and the `agent-mcp` binstub in `~/.local/bin`.
