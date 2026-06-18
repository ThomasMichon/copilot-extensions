# agent-mcp

A reusable **MCP bridge**: wrap an upstream MCP server as a local **stdio** MCP
server and inject host credentials. One config file describes one bridge.

It generalizes the bespoke `ado-mcp-proxy` (a Node script hardcoded to
`mcp.dev.azure.com` + `az`) into a config-driven, multi-transport, multi-auth
bridge packaged as a Copilot CLI plugin.

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
| `env` / `static` | host env var or literal | templated header | target env var |
| `none` | — | nothing | nothing |

## Config file

```yaml
# ~/.agent-mcp/bridges/ado.yaml   ->   agent-mcp bridge ado
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
    command: agent-mcp
    args: ['bridge', '--config', '.github/agents/ado.mcp.yaml']
    tools: ['*']
```

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
