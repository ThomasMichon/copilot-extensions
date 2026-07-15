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
- **Decorator stack** — an ordered list of middleware that transforms the MCP
  traffic in both directions: filter, rename, defer behind a tool-finder,
  expose a typed `run_code` tool, or relay large payloads through a stream
  buffer. See [Decorator stack](#decorator-stack).

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

**`command` vs `git-credential`.** `git-credential` is the special case that
reads the host's **Git Credential Manager** (`git credential fill`); `command`
runs **any other** secret printer — a vault CLI, 1Password's `op`, a custom
binstub. There is **no built-in `vault` (or other vendor) auth kind by design**:
vault access is simply a `command` that runs your own `vault` CLI, so no
facility- or vendor-specific secret tool is hard-coded into agent-mcp.

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

npm example (name the **package**, let agent-mcp pick the fastest **available**
runner — `bunx` if present, else `npx -y` — instead of hardcoding a launcher):

```yaml
server:
  type: stdio
  npm: "@scope/some-mcp"   # runner chosen at spawn: bunx -> npx -y
  args: ["--flag"]         # optional, appended after the package
```

`bunx` reaches the server's `initialize` roughly twice as fast as `npx -y` (npx
re-walks the cached dependency tree on every spawn) and falls back to its cache
when the registry is unreachable. **agent-mcp never requires bun** — `npx` is
always a valid runner, so this stays package-manager-neutral; `bunx` is a
transparent optimization used only when the host already provides it. Force a
runner with `AGENT_MCP_NPM_RUNNER=<name>`; use `server.command` for full control.

command example (fetch the token on demand from a vault CLI — never stage it in
the session env):

```yaml
server:
  type: stdio
  command: ["npx", "-y", "@scope/some-mcp"]
auth:
  kind: command
  command: ["vault", "get", "My Vault/Some API", "password"]
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
    command: ["vault", "get", "My Vault/Service Controller", "password"]
    parse: raw
    target_env: SERVICE_PASSWORD
  - kind: command
    command: ["vault", "get", "My Vault/Service API Key", "password"]
    parse: raw
    target_env: SERVICE_API_KEY
```

## Decorator stack

Beyond transport + auth, a bridge can apply an ordered **decorator stack** — MCP
middleware that rewrites the JSON-RPC traffic in both directions. This turns
`agent-mcp` into a general MCP adapter: shrink a 100+ tool catalog, namespace a
partner's tools, expose a typed code-execution tool, or relay large payloads out
of the model's context.

> **Worked example:** [`examples/ado/`](examples/ado/) adapts the real Azure
> DevOps MCP six ways and hands each variant to a dedicated read-only agent,
> with live measurements (`tools/list` 74 KB → 1.4 KB; a 100-PR list 51 KB →
> 451 B). Start there for a concrete, runnable tour.


```yaml
server: { type: http, url: https://mcp.example.com }
auth:   { kind: entra, resource: <guid> }
decorators:                 # listed client -> upstream (outermost first)
  - type: defer             # hide a big catalog behind find_tool/execute_tool
    mode: lazy
    expose: ["search_*"]
  - type: rename            # namespace what remains
    namespace: partner
  - type: filter            # drop tools entirely
    deny: ["*_delete", "*_admin"]
  - type: storage           # relay large results through a stream buffer
    backend: file
    threshold: 8192
```

**Ordering.** Decorators are listed **client → upstream**. A request flows *down*
the list (first entry first); the response bubbles back *up* (last entry first).
Each decorator reaches the upstream by calling the next link, and may transform
the request, transform the response, or **synthesize a response** for a tool it
owns (e.g. `find_tool`) without calling upstream. Recommended order:
context-reducers that add their own tools (`defer`, `code-mode`) **outermost**,
then `rename`, then `filter`, with `storage` **innermost** (closest to upstream,
so it sees real payloads). The legacy top-level `tools:` filter, if present, is
applied as an implicit `filter` at the upstream end.

> **Composition just works** because each decorator calls *through* the ones
> below it: a `defer` `execute_tool` for a renamed name still passes back down
> through `rename`, which restores the real upstream name.

### `filter` — allow/deny tools

Prune `tools/list` *and* reject `tools/call` for hidden tools (so a hidden name
can't be invoked even if it leaks). `deny` wins over `allow`; patterns are
shell-style globs.

```yaml
- type: filter
  allow: ["repo_*", "wit_*"]   # set allow OR deny, not both
  # deny: ["*_delete"]
```

### `rename` — namespace / prefix / suffix / regex

Rewrite tool **names** and **descriptions**; calls to the rewritten name are
mapped back to the real upstream name. Namespace/prefix/suffix are reversible by
construction; regex renames are learned from `tools/list` (clients list first).

```yaml
- type: rename
  namespace: ado          # get -> ado__get   (separator: "__")
  prefix: ""              # prepended to the name
  suffix: ""              # appended to the name
  patterns:               # regex substitutions on names
    - { match: "^wit_", replace: "workitem_" }
  description:
    prefix: "[ADO] "
    suffix: ""
    patterns:
      - { match: "internal", replace: "" }
```

### `defer` — hide a large catalog behind meta-tools

Models choke on 100+ tool definitions. `defer` exposes a few **meta-tools** and
keeps the real catalog searchable (the [UniFi MCP](https://github.com/sirkirby/unifi-mcp)
pattern):

- `find_tool` — search the catalog by `query`/`category`; returns compact
  `{name, description}` (set `include_schemas: true` for input schemas).
- `execute_tool` — invoke any catalog tool by `tool` name + `arguments`.
- `load_tools` *(lazy mode)* — promote named tools into `tools/list` and emit
  `notifications/tools/list_changed` so capable clients can call them directly.

```yaml
- type: defer
  mode: lazy              # lazy (default) | eager | meta_only
  expose: ["search_*"]    # always-visible tools (optional)
  max_results: 20
  # find_tool / execute_tool / load_tool: override the meta-tool names
```

| Mode | `tools/list` shows |
|------|--------------------|
| `lazy` | exposed + loaded tools + `find_tool`/`execute_tool`/`load_tools` |
| `eager` | the full catalog + `find_tool`/`execute_tool` |
| `meta_only` | exposed tools + `find_tool`/`execute_tool` only |

### `code-mode` — a typed `run_code` tool

Instead of N tool defs, expose a single `run_code` tool whose description carries
a generated **TypeScript `Tools` interface** for the whole catalog. The model
writes a short JS/TS snippet that calls tools as async methods and chains results
in **one** round-trip; the snippet runs in a Node child and each call is relayed
upstream. A companion `code_apis` tool returns the interface on demand.

```yaml
- type: code-mode
  tool: run_code          # the execution tool name
  apis_tool: code_apis    # returns the TS interface text
  runtime: node           # Node executable
  timeout: 30
  expose: []              # tools to also list directly (optional)
```

```js
// example run_code body the model writes:
const clients = await tools.list_clients({ limit: 50 });
const offline = clients.filter(c => !c.online);
return { offlineCount: offline.length, names: offline.map(c => c.name) };
```

Requires Node on `PATH` (or set `runtime:` to a Node path). `console.log` is
captured; a lone JSON tool result is auto-parsed for ergonomic chaining.

For a large catalog, code-mode also exposes **`find_tool`**: rather than embedding
every signature in `run_code`'s description, the model calls `find_tool(query)` to
get the typed TS signatures for just the tools it needs, then writes `run_code`.
The full interface is embedded inline only when the catalog is at/below
`interface_limit` (default 40).

### `storage` — relay large I/O through a stream buffer

Keep big payloads out of the model's context:

- **Outputs** larger than `threshold` bytes are written to a backing store; the
  client gets a short preview + a `mcpstream://…` **handle**.
- **Inputs** containing a handle (a bare handle string, or `{"$stream": "<handle>"}`)
  are rehydrated to the stored value before the call is forwarded — so one tool's
  output pipes into another's input without passing through the model.
- A `read_stream` meta-tool fetches a stored value (optionally a slice).

```yaml
- type: storage
  backend: file                  # file (default) | http
  dir: ~/.agent-mcp/storage      # file backend
  # url: https://buffer.example  # http backend (POST to store, GET to read)
  threshold: 8192                # bytes; outputs above this are externalized
  max_preview: 200               # preview chars left inline
  read_tool: read_stream
```

#### Field-level rules (per-tool, per-field)

The blanket `threshold` externalizes whole text blocks. For finer control, add
`rules:` that target **specific tools** (glob) and **specific JSON paths** within
their inputs/outputs — exactly the parts worth streaming:

```yaml
- type: storage
  rules:
    - tool: get_list_items          # glob over tool names
      outputs:
        - path: items               # dotted path into the result (structuredContent
          summary: { head: 3 }      #   or a JSON text block); summary is on by default
      inputs:
        - path: filter              # this input becomes a stream URL (schema rewritten)
          note: a query filter object
```

**Output field externalization.** For each `outputs[].path`, the value at that
path is replaced with `{"$stream": "<handle>", "bytes": N, "summary": {…}}`, while
siblings are left intact. For the example above, a `get_list_items` result of
`{"items": [ …1000s… ], "total": 1240}` becomes:

```json
{"items": {"$stream": "mcpstream://…", "bytes": 98231,
           "summary": {"count": 1240,
                       "schema": {"type": "array", "items": {"type": "object", …}},
                       "head": [ {…}, {…}, {…} ]}},
 "total": 1240}
```

so the model can reason over the **schema + count + first rows** and decide what
to do with the full stream (fetch via `read_stream`, or pipe the handle into
another tool). Summary is on by default (`count` + inferred `schema` + first 3);
customize with `summary: {count, schema, head}` or disable with `summary: false`.
Use a **command summarizer** for custom logic — the value is piped to its stdin
and stdout becomes the summary:

```yaml
      outputs:
        - path: items
          summary: { command: ["jq", "{count: length, ids: [.[].id]}"] }
```

**Input param → stream URL.** For each `inputs[].path`, that property's schema in
`tools/list` is rewritten to a stream-URL string and its description annotated
(*"URL to a stream containing a JSON-serialized object…"*), preserving the
original type/description. At call time a handle passed for that param is
rehydrated to the original value. An externalized output handle can be passed
straight back in (`{"$stream": "<handle>", …}`), so large data flows tool→tool
entirely by reference.

### `transform` — reshape tool results

Slim deeply-nested or enveloped results before they reach the model. Each rule
targets a tool (glob) and applies ops to its JSON document (`structuredContent`
and/or a JSON text block):

```yaml
- type: transform
  rules:
    - tool: repo_list_pull_requests
      extract: value                      # unwrap {count, value:[...]} -> [...]
    - tool: wit_get_work_item
      pick: ["id", "fields.System.Title", "fields.System.State"]   # keep only these
    - tool: "*"
      drop: ["_links", "url"]             # strip noise everywhere
    - tool: noisy_tool
      command: ["jq", "{n: (.items|length)}"]   # jq-style escape hatch (stdin->stdout)
```

- `extract: <path>` — replace the result with the value at a path.
- `pick: [paths]` — keep only these dotted paths (matched key shape preserved).
- `drop: [paths]` — remove these dotted paths.
- `command: [argv]` — pipe the result JSON to a filter's stdin; its stdout
  (parsed as JSON) replaces the result.

Dotted paths match **literal dotted keys** too (e.g. ADO `fields.System.Title`
where `fields` is `{"System.Title": …}`) as well as genuine nesting. Ops apply
`extract → pick → drop` (or `command` alone); multiple rules for a tool chain in
order. A single inline rule may be written without the `rules:` wrapper.



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

## MCP → CLI: `call` and `materialize`

The bridge exposes an upstream MCP *to an MCP client*. The **`call`** and
**`materialize`** verbs expose it *to the shell* instead — the same upstream, the
same auth + `tools:` filtering, projected as command-line tools for agents (or
people) who would rather `ls`/`cat`/pipe than speak JSON-RPC.

### `call` — one-shot invoke one tool

```sh
agent-mcp call <bridge> <tool> '<arguments-json>'
```

Connects to the bridge's upstream, runs the MCP `initialize` handshake, invokes
one tool, and prints its result — then exits. This is the **stateless cold
path**; when an `agent-mcp serve` daemon is running (see below), `call`
transparently routes to it and skips the per-call cold-start instead. Force the
cold path with `--no-serve` or `AGENT_MCP_NO_SERVE=1`.

- **Arguments** are the tool's **raw MCP `arguments` object** as JSON. Supply it
  inline, via `--arguments '<json>'`, via `--request-file PATH` (a file holding
  the bare object or `{"arguments": {...}}`), or on **stdin**. No `--flag` grammar
  is synthesized — the schema *is* the interface.
- **Output** is **raw passthrough**: the upstream's text content verbatim, or its
  advertised `structuredContent` as JSON when there is no text. Nothing is
  wrapped in a synthetic envelope.
- **Errors** are a non-zero exit + a stderr message. The wait is bounded by the
  config `timeout`, so a dead or silent upstream fails fast instead of hanging.

```sh
agent-mcp call gitea list_issues '{"owner":"me","repo":"x"}'
echo '{"owner":"me","repo":"x"}' | agent-mcp call gitea list_issues
agent-mcp call gitea create_issue --request-file req.json
```

### `materialize` — project the whole catalog into a stub fleet

```sh
agent-mcp materialize <bridge> [--server-name NAME] [--dest DIR] [--windows]
```

Introspects `tools/list` and writes a **hierarchical, discoverable, pipeable**
command fleet under `~/.agent-mcp/materialized/<server>/`:

```
bin/    one short-named stub per tool
        POSIX:   symlinks to a single `_amcp-dispatch` (argv[0] dispatch)
        Windows: a `.ps1` + `.cmd` shim per tool (`--windows` to force)
doc/    a plated sidecar per tool: upstream description + raw inputSchema + TS sig
index.md      the server's tool table
manifest.json stub → tool + bridge reference (read by `call`)
```

Generation is **purely mechanical — no LLM**: sidecars plate the raw MCP
definition, stubs accept the raw `arguments` JSON, and structure is emitted only
when the upstream advertises it. Each stub forwards to `agent-mcp call`, so a
materialized tool is invocable by short name from `PATH` and pipes like any CLI:

```sh
agent-mcp materialize gitea            # -> ~/.agent-mcp/materialized/gitea/
export PATH="$HOME/.agent-mcp/materialized/gitea/bin:$PATH"
list_issues '{"owner":"me","repo":"x"}' | jq '.[].number'
```

Re-running `materialize` rebuilds the tree in a temp dir and swaps it in
atomically, so it doubles as a drift refresh (no partial-write window). The
bridge's `tools:` allow/deny filter gates which tools are materialized.

### `serve` — the resident warmth tier

```sh
agent-mcp serve [--socket PATH] [--idle-timeout SECONDS]
```

`call` (and every materialized stub, unchanged) pays a fresh upstream
cold-start — spawn the runner + MCP `initialize` — on **every** invocation.
`serve` runs a resident daemon that keeps one **warm session per bridge** and
answers `call`/`list` requests over a unix socket (default
`$AGENT_MCP_HOME/serve.sock`), so repeated calls skip the cold-start entirely.

- **Transparent** — a running `call` auto-detects the socket and routes to it;
  when the daemon is absent it **falls back to the stateless one-shot path**.
  So `serve` is an *optional accelerator, never a dependency*. Bypass it with
  `--no-serve` / `AGENT_MCP_NO_SERVE=1`; point elsewhere with
  `AGENT_MCP_SERVE_SOCKET`.
- **Warm pool** — sessions open lazily on first use, are reused, serialized
  per-bridge, evicted after `--idle-timeout` (default 300s), and reopened if the
  upstream dies.
- **No secrets held** — each warm session fetches credentials through the
  bridge's own auth injector at open time; per-bridge sessions preserve identity
  separation.

```sh
agent-mcp serve &                       # start the daemon (e.g. per session/host)
list_issues '{"owner":"me","repo":"x"}' # now warm: no per-call cold-start
```

> A **server-launched** upstream inherits the daemon's working directory, so a
> bridge whose `server.env` uses **relative** paths should make them absolute —
> the daemon's CWD may differ from where you materialized.

## Install

```powershell
.\scripts\init.ps1     # Windows -- venv at ~/.agent-mcp, binstub in ~/.local/bin
```
```bash
./scripts/init.sh      # Linux/WSL
```

## Architecture

```
stdin/stdout        Bridge        Decorator pipeline           UpstreamClient        Transport
(JSON-RPC)   <->   loop   <->   d0 <-> d1 <-> ... <-> dN  <->  (id correlation)  <->  (http|stdio)  <->  upstream MCP
                                 ^                                                         ^
                          filter/rename/defer/                                      Auth injector -> credential-relay
                          code-mode/storage
```

- `config.py` — load + validate the per-bridge config file (incl. `decorators:`).
- `auth/` — `AuthInjector` protocol + injectors (reuse `credential_relay.sources`).
- `transports/` — `http` (Streamable HTTP + SSE) and `stdio` (child process).
- `pipeline.py` — `UpstreamClient` (JSON-RPC id correlation over a transport) +
  `Pipeline` (compose decorators around the upstream core call).
- `decorators/` — `base` (Decorator + BridgeContext), `_catalog` (catalog
  pagination + JSON-Schema→TS), and the `filter`/`rename`/`defer`/`code-mode`/
  `storage` decorators.
- `bridge.py` — stdio framing, per-request dispatch through the pipeline,
  unsolicited-message passthrough.
- `client.py` — `OneShotSession`: connect + `initialize` + one `tools/list` /
  `tools/call` against an upstream, then exit (the engine under `call` and the
  introspection step of `materialize`).
- `materialize.py` — project a `tools/list` catalog into the on-disk stub fleet
  (symlink farm on POSIX, `.ps1`/`.cmd` shim farm on Windows) + plated sidecars.

