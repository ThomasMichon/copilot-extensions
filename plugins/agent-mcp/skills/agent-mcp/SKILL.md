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
- You want to **reshape a large or partner MCP**: shrink a 100+ tool catalog
  behind a tool-finder, namespace/rename tools, expose a typed `run_code` tool,
  or relay big payloads through a stream buffer — see [Decorator stack](#decorator-stack).
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
(same shape as a `.mcp.json` entry) plus `auth` and overrides. Copy the full
annotated example, [`references/ado.mcp.yaml`](references/ado.mcp.yaml), and
adapt -- at a glance:

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

> **stdio launch — `command` vs `npm`.** A stdio bridge either lists an explicit
> `server.command` (full control) or names an npm package with `server.npm:
> <pkg>` and lets agent-mcp pick the fastest **available** runner at spawn
> (`bunx` → `npx -y`). `npm` mode stays package-manager-neutral (always works via
> `npx`; uses `bunx` only where present). See the plugin README for details.

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
    command: agent-mcp              # cross-platform (Linux/WSL + Windows)
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

> **`command: agent-mcp` is cross-platform.** The Windows binstub is a single
> `.cmd` (no competing `.ps1`), so a bare `agent-mcp` resolves to it under
> PowerShell, `where`/PATHEXT, and `cmd`, and the `.cmd` forwards stdin to the
> stdio MCP child. Use plain `command: agent-mcp` on every platform -- no `.cmd`
> suffix needed.

## Auth kinds

| kind | acquires via | injects |
|------|--------------|---------|
| `entra` / `az` | `az account get-access-token` | `Authorization: Bearer` (http) / env (stdio) |
| `gh` | `gh auth token` | `Authorization: Bearer` / env |
| `git-credential` | Git Credential Manager | `Authorization: Basic` / env |
| `command` | any `git credential fill`-shaped command | templated header / env |
| `env` / `static` | host env var or literal | templated header / target env |
| `none` | -- | nothing |

Token acquisition reuses the `credential-relay` sources; the bridge refreshes the
credential and retries once on an upstream `401`.

The `command` kind runs **any** external command that speaks the git-credential
protocol — `auth.request` fields are fed on stdin, and stdout supplies the
secret. Two parse modes:

- `parse: raw` (stdout is the secret verbatim) wraps a plain printer such as
  `vault get "<entry>" password` with no adapter.
- `parse: keyvalue` (default; extract `auth.field`, default `token`/`password`)
  wraps `git credential fill`, a vault `git-credential` helper, or a password
  manager CLI.

This is the path for vault-backed secrets: the token is fetched on demand and
injected only into the wrapped child, instead of being exported into the whole
session environment.

**Multiple secrets:** set `auth` to a **list** of auth blocks to inject several
secrets into one child (e.g. a controller password *and* an API key). Each entry
is a normal auth block and must set a distinct `target_env`; the bridge merges
them into the child environment.

```yaml
auth:
  - kind: command
    command: ["vault", "get", "My Vault/UniFi Controller", "password"]
    parse: raw
    target_env: UNIFI_NETWORK_PASSWORD
  - kind: command
    command: ["vault", "get", "My Vault/UniFi API Key (Local)", "password"]
    parse: raw
    target_env: UNIFI_API_KEY
```

## Decorator stack

Beyond transport + auth, a bridge can apply an ordered **decorator stack** — MCP
middleware that rewrites the JSON-RPC traffic in both directions. Add a
`decorators:` list to the bridge config (entries are listed **client → upstream**,
outermost first):

```yaml
server: { type: http, url: https://mcp.example.com }
auth:   { kind: entra, resource: <guid> }
decorators:
  - type: defer            # hide a 100+ tool catalog behind find_tool/execute_tool
    mode: lazy             #   lazy (default) | eager | meta_only
    expose: ["search_*"]
  - type: rename           # namespace/prefix/suffix/regex on names + descriptions
    namespace: partner
  - type: filter           # allow/deny tools (also rejects hidden tools/call)
    deny: ["*_delete", "*_admin"]
  - type: code-mode        # one typed run_code tool (TS interface) instead of N defs
    tool: run_code
  - type: storage          # relay large tool I/O through a file/http stream buffer
    backend: file
    threshold: 8192
```

| Decorator | What it does |
|-----------|--------------|
| `filter` | Prune `tools/list` and reject calls to hidden tools (`allow`/`deny` globs). |
| `rename` | Rewrite tool names/descriptions (`namespace`/`prefix`/`suffix`/regex `patterns`); routes calls back to real names. |
| `defer` | Expose `find_tool`/`execute_tool` (+`load_tools` in lazy mode) over a large catalog. The UniFi MCP pattern. |
| `code-mode` | Expose a `run_code` tool with a generated TypeScript `Tools` interface; snippets run in Node and chain tool calls. Adds `find_tool` for typed signatures on big catalogs. Needs Node on `PATH`. |
| `storage` | Externalize large outputs to `mcpstream://…` handles; rehydrate handle inputs; `read_stream` fetches them. **Field-level `rules:`** target specific tool input/output JSON paths, attach a summary (count + schema + head, or a command), and rewrite a stream-mode input param's schema to a URL. |
| `transform` | Reshape tool results per tool: `extract`/`pick`/`drop` dotted paths (literal-dotted keys like ADO `fields.System.Title` supported) or a `command` (jq-style) filter. |

Decorators compose because each calls *through* the ones below it. Recommended
order: `defer`/`code-mode` outermost, then `rename`, then `filter`, with
`storage` innermost. The legacy `tools:` filter still works (applied as an
implicit `filter`). Full reference + per-decorator options:
[README → Decorator stack](../../README.md#decorator-stack).

## Commands

```
agent-mcp bridge --config FILE    # run the bridge from an in-repo config (preferred)
agent-mcp bridge <name>           # run a named bridge (~/.agent-mcp/bridges/<name>.*)
agent-mcp validate <name|FILE>    # parse + schema-check
agent-mcp status                  # prerequisites + available named bridges
agent-mcp call <bridge> <tool> [JSON]     # one-shot: invoke one upstream tool
agent-mcp materialize <bridge>            # project the catalog into a CLI stub fleet
```

## MCP -> CLI: `call` and `materialize`

Besides serving an MCP client, agent-mcp can expose an upstream MCP **to the
shell** -- for agents (or humans) that prefer to `ls`/`cat`/pipe tools instead
of speaking JSON-RPC.

- **`call`** is the one-shot engine: it connects to the bridge's upstream,
  runs `initialize`, invokes one tool, and prints the result. Arguments are the
  tool's **raw MCP `arguments` object** as JSON -- via an inline arg, `--arguments`,
  `--request-file PATH`, or stdin. Output is **raw passthrough** (the upstream's
  text content verbatim; the advertised `structuredContent` as JSON when there is
  no text). A tool error is a non-zero exit + a stderr message -- never a hang
  (the wait is bounded by the config `timeout`).

  ```sh
  agent-mcp call gitea list_issues '{"owner":"me","repo":"x"}'
  echo '{"owner":"me","repo":"x"}' | agent-mcp call gitea list_issues
  agent-mcp call gitea create_issue --request-file req.json   # req.json: {"arguments": {...}}
  ```

- **`materialize`** projects the whole `tools/list` catalog into a discoverable,
  pipeable command fleet under `~/.agent-mcp/materialized/<server>/`:

  ```
  bin/    one short-named stub per tool (POSIX: symlinks to one dispatcher;
          Windows: a .ps1 + .cmd shim per tool). Put bin/ on PATH.
  doc/    a plated sidecar per tool: the upstream description + raw inputSchema.
  index.md, manifest.json
  ```

  Generation is **purely mechanical** -- sidecars plate the raw MCP definition,
  stubs accept the raw `arguments` JSON (no `--flag` synthesis), and nothing is
  guessed by a model. Each stub forwards to `agent-mcp call`, so a materialized
  tool is invocable by name and pipes like any CLI:

  ```sh
  agent-mcp materialize gitea               # writes ~/.agent-mcp/materialized/gitea/
  list_issues '{"owner":"me","repo":"x"}' | jq '.[].number'
  ```

  Re-running `materialize` rebuilds the tree atomically (temp dir + swap), so it
  doubles as a drift refresh. The bridge's `tools:` allow/deny filter gates which
  tools are materialized.

## Install

`./scripts/init.sh` (Linux/WSL) or `.\scripts\init.ps1` (Windows) -- creates the
venv at `~/.agent-mcp` and the `agent-mcp` binstub (a single `.cmd` on Windows)
in `~/.local/bin`.
