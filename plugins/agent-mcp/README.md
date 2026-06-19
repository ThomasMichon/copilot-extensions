# agent-mcp

A reusable **MCP bridge**: wrap an upstream MCP server as a local **stdio** MCP
server and inject host credentials. One config file describes one bridge.

It replaces single-purpose wrapper scripts -- e.g. a script hardcoded to one
upstream endpoint and one auth command -- with a config-driven, multi-transport,
multi-auth bridge packaged as a Copilot CLI plugin.

## Concepts

- **Bridge** — one upstream MCP server exposed locally over stdio. Defined by a
  single JSON/YAML config file.
- **`server` block** — the *original upstream launch info*, the same shape as a
  `.mcp.json` / `mcpServers` entry. `server.type` (`http` | `stdio`) selects the
  transport. Lift an existing server entry in unchanged.
- **Auth injector** — declares *what form of auth to inject*. Token acquisition
  reuses the `credential-relay` host-credential sources (`az_login`, `gh_auth`,
  `git_credential`) — this plugin does not re-implement `az`/`gh`/GCM shell-outs.

| `auth.kind` | Source | http injects | stdio injects |
|-------------|--------|--------------|---------------|
| `entra` / `az` | `az account get-access-token` | `Authorization: Bearer` | env var |
| `gh` | `gh auth token` | `Authorization: Bearer` | env var |
| `git-credential` | Git Credential Manager | `Authorization: Basic` | env var |
| `command` | any git-credential-fill-shaped command | templated header | target env var |
| `env` / `static` | host env var or literal | templated header | target env var |
| `none` | — | nothing | nothing |

The `command` kind is the extensible escape hatch: it runs **any** external
command that behaves like `git credential fill` — the `auth.request` fields are
written to its stdin as git-credential `key=value` text and its stdout supplies
the secret. Use it to source credentials from a vault CLI, a custom helper, or a
password manager without baking that tool into this plugin.

Set `source_env` on a `command` auth to make it **env-first**: if that host
variable is already set (e.g. a no-vault/push machine's static `.env`), it is
used and the command is **not** run; otherwise the command runs. One bridge
config then works on both vault-enabled and daemon-less hosts.

> **Security — bridge configs are executable code.** `server.command` and
> `auth.command` run with the host environment and can execute arbitrary local
> programs. Treat a bridge config like a script: do **not** run an unreviewed or
> untrusted `.mcp.yaml`. Prefer in-repo, version-controlled bridge configs.

> **Secret rotation.** Vault reads are cache-first and a stdio bridge injects the
> secret into the MCP child **once at spawn**. After rotating a secret, refresh
> the cache (`vault get … --refresh` / re-populate) **and restart the MCP/agent**
> so the child re-reads it. http bridges auto-refresh and retry once on a `401`.

## Config location — in-repo vs. user-global

A bridge config can be referenced two ways (both read the same schema; only the
lookup differs):

| Form | Reference | Lives in | Use for |
|------|-----------|----------|---------|
| **In-repo `--config`** (preferred) | `bridge --config <path>` | the repo (e.g. `.github/agents/<name>.mcp.yaml`) | **repo-scoped agents** — version-controlled, travels with the repo, no deploy |
| **Named bridge** | `bridge <name>` | `~/.agent-mcp/bridges/<name>.{yaml,yml,json}` | **personal / cross-repo** MCPs not tied to one checkout |

Prefer the in-repo `--config` form for any agent that ships inside a repo;
reserve named bridges for MCPs you use across many repos.

## Config file

```yaml
# .github/agents/ado.mcp.yaml   (in-repo)  ->  agent-mcp bridge --config <path>
# ~/.agent-mcp/bridges/ado.yaml (named)     ->  agent-mcp bridge ado
server:                                  # original launch info (lift from .mcp.json)
  type: http
  url: https://mcp.dev.azure.com/your-org
auth:
  kind: entra
  resource: 2a72489c-aab2-4b65-b93a-a91edccf33b8   # mcp.dev.azure.com
  header: Authorization
  format: "Bearer {token}"
# overrides
headers: {}
tools: { allow: ["repo_*", "wit_*", "search_*"], deny: [] }
timeout: 30
retries: 1
```

stdio example (wrap a child-process MCP, inject a token by env):

```yaml
server:
  type: stdio
  command: ["npx", "-y", "@scope/some-mcp"]
auth:
  kind: env
  source_env: SOME_PAT     # read from host env
  inject: env
  target_env: API_KEY      # set on the child
```

command example (fetch the token on demand from a vault CLI — never stage it in
the session env):

```yaml
server:
  type: stdio
  command: ["npx", "-y", "@scope/some-mcp"]
auth:
  kind: command
  command: ["vault", "get", "Aperture Science/Some API", "password"]
  parse: raw               # stdout IS the secret (no adapter needed)
  inject: env
  target_env: API_KEY      # set on the child

# Or wrap a `git credential fill`-shaped helper (default parse: keyvalue):
# auth:
#   kind: command
#   command: ["git-credential-vault", "get"]
#   request: { protocol: https, host: home.example.com }
#   field: password        # which output key to extract (default: token||password)
#   inject: env
#   target_env: API_KEY
```

multi-secret example (`auth` as a **list** -- inject two vault-sourced secrets
into two env vars on the same child; each entry is a normal auth block and
**must** set a distinct `target_env`):

```yaml
server:
  type: stdio
  command: ["uvx", "some-mcp@latest"]
  env:
    SERVICE_HOST: host.example.com      # non-secret config stays here
auth:
  - kind: command
    command: ["vault", "get", "Aperture Science/Service Controller", "password"]
    parse: raw
    target_env: SERVICE_PASSWORD
  - kind: command
    command: ["vault", "get", "Aperture Science/Service API Key", "password"]
    parse: raw
    target_env: SERVICE_API_KEY
```

## CLI

```
agent-mcp bridge <name>            # run a named bridge (~/.agent-mcp/bridges/<name>.*)
agent-mcp bridge --config <file>   # run an explicit config file
agent-mcp validate <name|file>     # parse + schema-check, no run
agent-mcp status                   # prerequisites + available bridges
```

## Use from a Copilot agent

```yaml
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp            # cross-platform: same on Linux/WSL and Windows
    args: ['bridge', '--config', '.github/agents/ado.mcp.yaml']
    tools: ['*']
```

> **`command: agent-mcp` works on every platform.** The Windows binstub is a
> single `.cmd` (no competing `.ps1`), so a bare `agent-mcp` resolves to it under
> PowerShell, `where`/PATHEXT, and `cmd` alike, and the `.cmd` forwards stdin to
> the stdio MCP child. (A `.ps1` shim would win PowerShell's command discovery but
> doesn't reliably stream stdin -- hence the deliberate `.cmd`-only layout.) On
> Linux/WSL the binstub is the usual bash script.

## Install

```powershell
.\scripts\init.ps1     # Windows -- venv at ~/.agent-mcp, binstub in ~/.local/bin
```
```bash
./scripts/init.sh      # Linux/WSL
```

## Architecture

```
stdin/stdout (JSON-RPC)  <->  Bridge core  <->  Transport (http | stdio)  <->  upstream MCP
                                   |                  ^
                              tool filter        Auth injector  ->  credential-relay sources
```

- `config.py` — load + validate the per-bridge config file.
- `auth/` — `AuthInjector` protocol + injectors (reuse `credential_relay.sources`).
- `transports/` — `http` (Streamable HTTP + SSE) and `stdio` (child process).
- `bridge.py` — stdio framing, request forwarding, `tools/list` filtering.
