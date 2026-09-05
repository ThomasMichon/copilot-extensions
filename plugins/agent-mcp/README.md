# agent-mcp

A reusable **MCP bridge**: wrap an upstream MCP server as a local **stdio** MCP
server and inject host credentials. One config file describes one bridge.

It replaces single-purpose wrapper scripts -- e.g. a script hardcoded to one
upstream endpoint and one auth command -- with a config-driven, multi-transport,
multi-auth bridge packaged as a Copilot CLI plugin.

## Responsibility boundary

agent-mcp owns MCP **transport mechanics**: upstream launch and protocol
negotiation, credential injection, filtering and catalog reshaping,
materialization, and bridge-equivalent fallback. It does not own the authoring
policy for custom agents or the runtime decision to delegate. Use
`customizing-copilot:defining-subagents` for agent structure, domain-service
ownership, readiness sections, and anti-self-delegation; use
`delegation-guidance:delegating-work` for task decomposition.

## Standalone quick start

agent-mcp is the **standalone** MCP-wrapper path: it is launched directly from a
Copilot agent's `mcp-servers` entry, imports no agent-bridge code, uses no
worktree/repo resolver, and needs no resident daemon. The optional `serve`
command only warms repeated shell `call`/materialized-stub usage; it is not part
of normal agent wiring.

1. Enable/install the `agent-mcp` plugin. Session-facing skills receive an exact
   payload-local command through the session command catalog; static MCP
   frontmatter and generated materialized fleets continue using the
   compatibility global wrapper because they start outside that session
   context. If the wrapper is missing, run `scripts/init.* stamp`; the first
   real call may self-provision the runtime.
2. Write one bridge config (`.mcp.yaml`/`.json`) that names the upstream
   `server`, `auth`, and any filters/decorators.
3. Point the consuming agent at that file:

```yaml
mcp-servers:
  my-upstream:
    type: stdio
    command: agent-mcp # marketplace-isolation: allow mcp-server-startup
    args: ['bridge', '--config', '.github/agents/my-upstream.mcp.yaml']
    tools: ['*']
```

For setup steps use the bundled **`agent-mcp`** skill; for per-machine tuning of
an existing bridge use **`customizing-bridges`**.

The literal `mcp-servers.command` above is deliberate. Copilot starts the stdio
server from repository-committed, machine-portable frontmatter that has no
plugin-root interpolation contract and may run before or independently of
session command-catalog context. That surface cannot consume the payload-local
argv until the host provides a plugin-root-capable MCP launcher contract.

## Reliable transport: one bridge, two surfaces

After `customizing-copilot:defining-subagents` establishes the agent contract,
an agent-mcp-backed agent pairs its primary `mcp-servers` catalog with a
materialized CLI fleet over the **same bridge config**. When Copilot fails to
register or retain the catalog, the agent preserves that error, probes the
identity-matched fleet, and continues through the CLI surface. It stops only
when both surfaces fail.

This is equivalent transport only when authorization is expressed by the shared
bridge auth and top-level `tools:` allow/deny filter. `call`/`materialize` do
not apply decorator stacks. Duplicate static name restrictions in top-level
`tools:`; conditional `gate`/argument-dependent authorization cannot be
represented there and therefore forbids this fallback. Shape-only decorators
yield a wider raw fallback catalog that the agent must document. A credential
failure, denied operation, confirmation gate, or unavailable upstream still
fails honestly.

Identity-affecting values belong in the bridge config/overlay, not only in
`mcp-servers.env`, because shell fallback does not inherit frontmatter-only env.

Use an existing fleet first. Re-materialize when the expected stub is absent or
`manifest.json.generated_by` differs from `agent-mcp --version`; repositories
that need config/schema/overlay drift detection should own a digest of the
effective post-overlay config.
Materialize standing fleets from a stable checkout or plugin-shipped named
bridge; use `--windows` for PowerShell/CMD shims. On Windows pass arguments via
`--request-file` to the `.ps1` shim. Use `--no-serve` for identity-sensitive
readiness/fallback calls unless the deployment lifecycle evicts warm sessions
after config/auth changes. Warmth can amortize cold starts, but cannot rescue an
upstream that does not initialize.

The agent-mcp-specific wiring, platform commands, failure matrix, security
boundaries, and deploy/drift checklist are in
[Reliable agent-mcp transport and fallback](skills/agent-mcp/references/reliable-agent.md).

## Concepts

- **Bridge** — one upstream MCP server exposed locally over stdio. Defined by a
  single JSON/YAML config file.
- **`server` block** — the *original upstream launch info*, the same shape as a
  `.mcp.json` / `mcpServers` entry. `server.type` is one of `http`, `stdio`, or
  `cli`: `http` wraps a Streamable-HTTP/SSE upstream, `stdio` wraps a child MCP
  process, and `cli` has no upstream at all — it exposes a set of native CLIs
  *as* MCP tools (see [CLI → MCP](#cli--mcp-the-cli-server-type)).
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
multi-machine system- or vendor-specific secret tool is hard-coded into agent-mcp.

Set `source_env` on a `command` auth to make it **env-first**: if that host
variable is already set (e.g. a no-vault/push machine's static `.env`), it is
used and the command is **not** run; otherwise the command runs. One bridge
config then works on both vault-enabled and daemon-less hosts.

Set `auth.repair` on a `command` auth to make it **self-healing**. When the mint
command *hard-fails* (non-zero exit, missing binary, or timeout), agent-mcp runs
the `repair` command (string or argv list) **once** and then retries the mint
**once**. Use it to recover from broken mint *tooling* — e.g. a browser-minting
bridge whose Playwright is too old to drive an updated Edge can point `repair` at
a reinstall/refresh command. It is strictly bounded (a single repair + single
retry, never a loop), fully opt-in (no `repair` = unchanged behavior), and does
**not** fire on a clean-but-token-less response — only on a tooling failure. The
repair runs with no stdin under a more generous timeout, since a reinstall is
slower than a mint.

> **Security — bridge configs are executable code.** `server.command` and
> `auth.command` run with the host environment and can execute arbitrary local
> programs. Treat a bridge config like a script: do **not** run an unreviewed or
> untrusted `.mcp.yaml`. Prefer in-repo, version-controlled bridge configs.

> **Secret rotation.** A stdio bridge injects env credentials into the MCP child
> **once at spawn**. After rotating a command/env-sourced secret, refresh that
> source (and any `auth.cache` entry) **and restart the MCP/agent** so the child
> re-reads it. http bridges invalidate cached credentials and retry once on a
> `401`.

### Token caching (`auth.cache`) — opt-in, shared across sessions

By default a token injector caches its acquired token **in-process** (re-minted per
bridge process, refreshed on a `401`). Add a `cache` policy to any token auth
(`command`/`entra`/`gh`/`env`) to make that caching **shared and persistent**, so a
new session reuses a still-valid token instead of re-acquiring — no per-plugin
token-cache code required:

```yaml
auth:
  kind: command
  command: [ mint-my-token, --resource, R ]
  parse: raw
  cache:
    scope: shared     # shared = on-disk, cross-process/session | memory (default) | none
    ttl: auto         # auto = derive expiry from the token's JWT `exp` | <seconds>
    skew: 60          # refresh this many seconds before expiry
    # key: <override>  default = a stable hash of kind + command/resource + tenant + header
```

Shared entries live under `<AGENT_MCP_HOME|~/.agent-mcp>/token-cache/<key>.json` and
are served only while unexpired (minus `skew`); a `401` `invalidate` drops the entry
so the next call re-acquires. With `ttl: auto` a non-JWT secret (no derivable expiry)
is **not** persisted — set an explicit `ttl` to cache such secrets. *(v1 writes
plaintext with `0600` perms; sealing at rest is a planned follow-up.)*

### Plugin-shipped commands (`${config_dir}`)

A command arg (`server.command` or `auth.command`) may contain the token
`${config_dir}`, which expands to the **directory of the bridge config file**. This
lets a plugin-shipped bridge run a **plugin-shipped sibling script** — an auth
minter or a stdio server launcher next to the `.mcp.yaml` — with **no PATH deploy
and no install**:

```yaml
auth:
  kind: command
  command: [ "${python}", "${config_dir}/mint.py", --resource, https://... ]
  parse: raw
```

Invoke the sibling through an interpreter (`${python}`/`node`/`pwsh`) rather than
as `argv[0]` directly (a bare `.ps1`/`.py` is not itself executable). `${python}`
resolves to a full interpreter path (an absolute `sys.executable` when neither
`python`/`python3` is on `PATH`); `node`/`pwsh` are looked up on `PATH`. The
`${config_dir}` token is only expanded when the config is loaded from a file (its
directory is known); a `--config <path>` or plugin-discovered bridge both qualify.

#### Portable interpreter (`${python}`)

Prefer the `${python}` token over a bare `python` in the command: it expands at
load time to a **working Python 3 interpreter for the current platform** — probing
`python3` then `python` on POSIX (many Linux/CodeSpaces installs ship only
`python3`), and `python` then `python3` on Windows, falling back to the interpreter
running agent-mcp itself (`sys.executable`) if neither is on `PATH`. This keeps a
plugin's bridge YAML portable across Windows and POSIX without a per-OS launcher.
`${python}` is path-independent, so it resolves even for a bare-dict config (where
`${config_dir}` is left intact).

## Config location — in-repo vs. user-global

A bridge config can be referenced two ways (both read the same schema; only the
lookup differs):

| Form | Reference | Lives in | Use for |
|------|-----------|----------|---------|
| **In-repo `--config`** (preferred) | `bridge --config <path>` | the repo (e.g. `.github/agents/<name>.mcp.yaml`) | **repo-scoped agents** — version-controlled, travels with the repo, no deploy |
| **Named bridge** | `bridge <name>` | `~/.agent-mcp/bridges/<name>.{yaml,yml,json}` | **personal / cross-repo** MCPs not tied to one checkout |
| **Plugin-shipped** | `bridge <name>` | `<plugin>/agents/<name>.mcp.yaml` (installed under `~/.copilot/installed-plugins/*/*/`) | **a plugin that ships its own sub-agent + MCP** — no user-space copy needed |

Prefer the in-repo `--config` form for any agent that ships inside a repo;
reserve named bridges for MCPs you use across many repos.

**Plugin-shipped bridges (no install step).** A Copilot CLI plugin can ship its
bridge config *inside the plugin* at `agents/<name>.mcp.yaml` (or `mcp/…`) and its
sub-agent just runs `agent-mcp bridge <name>`. A bare name resolves in order:
(1) `~/.agent-mcp/bridges/<name>.…` (user-space override wins), then (2) the
installed-plugins tree `*/*/{agents,mcp}/<name>[.mcp].{yaml,yml,json}`. The spawned
MCP's cwd is the session repo (not the plugin), so a plugin-relative `--config`
path can't work — the named-bridge search is what makes a plugin's MCP resolve
with **zero** setup. Override the search roots with `AGENT_MCP_PLUGIN_ROOTS`
(path-separated); a name that appears in two plugins raises an ambiguity error.

## Protocol versions (MCP 2.x / dual-era)

agent-mcp speaks **both** eras of the MCP wire protocol and bridges between them
transparently:

- **Modern** — revision `2026-07-28` and later. Stateless: there is no
  `initialize` handshake and no `Mcp-Session-Id`; every request self-describes,
  carrying its protocol version, client identity, and capabilities in
  `params._meta` (`io.modelcontextprotocol/*`). Over Streamable HTTP that
  metadata is also mirrored into the `MCP-Protocol-Version`, `Mcp-Method`, and
  `Mcp-Name` headers so gateways can route without parsing the body. Capabilities
  can be fetched up front with the optional `server/discover` RPC.
- **Legacy** — revision `2025-11-25` and earlier. The stateful
  `initialize` / `notifications/initialized` handshake plus `Mcp-Session-Id`.

Both **directions** of the bridge are dual-era:

- **Consuming an upstream** (a `call` / `materialize` / warm `serve`): the
  one-shot client negotiates the upstream's era. In `auto` (default) it probes
  with `server/discover` — a valid `DiscoverResult` (or a recognized
  `UnsupportedProtocolVersionError`) means modern; any other error or a timeout
  means legacy, and it falls back to the `initialize` handshake. Modern requests
  are then stamped with `_meta`; the HTTP transport classifies **each message**
  independently, so a probe-then-fallback works over one connection. The probe
  failure is **quiet** — a legacy Streamable-HTTP server that rejects the modern
  `MCP-Protocol-Version` header (commonly `400`) is logged at debug and falls
  back cleanly, never an error on every call.
- **Exposing an adapter**: the `cli` responder (see [CLI → MCP](#cli--mcp-the-cli-server-type))
  is served as a dual-era MCP server — it answers `server/discover`, negotiates
  `initialize` for legacy clients, advertises cacheable `tools/list` results
  (`ttlMs`/`cacheScope`), and rejects an unsupported modern version with the
  standard error. A proxying (`http`/`stdio`) bridge is a transparent pass-through:
  the client's own negotiation flows end-to-end to the upstream.

Set the era per bridge with **`server.protocol`**:

| Value | Behavior |
|-------|----------|
| `auto` (default) | Probe + fall back. Best for unknown upstreams. |
| `modern` | Force `2026-07-28` — skip the probe, always stamp `_meta`. |
| `legacy` | Force the `initialize` handshake (`2025-06-18`). |
| `<YYYY-MM-DD>` | Force an exact revision (modern if `>= 2026-07-28`, else legacy). |

Forcing an era skips the auto-probe round-trip: use `modern`/`legacy` when you
already know what the upstream speaks, or to **pin** the version a `cli` adapter
advertises. In particular, set **`legacy`** on a known legacy Streamable-HTTP
endpoint (e.g. an Azure DevOps MCP that doesn't yet speak `2026-07-28`) to skip
the `server/discover` probe entirely — the probe otherwise costs one extra
round-trip per cold `call` (amortized within a session, and eliminated by a warm
`serve`). Same-era pass-through needs no configuration; cross-era **translation**
by a proxying bridge (e.g. a legacy client against a modern upstream *through* the
bridge) is not yet performed — set `server.protocol` to match the client instead.

## Config file

```yaml
# .github/agents/ado.mcp.yaml   (in-repo)  ->  agent-mcp bridge --config <path>
# ~/.agent-mcp/bridges/ado.yaml (named)     ->  agent-mcp bridge ado
server:                                  # original launch info (lift from .mcp.json)
  type: http
  url: https://mcp.dev.azure.com/your-org
  protocol: auto                         # auto | modern | legacy | <YYYY-MM-DD>  (see Protocol versions)
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

### Machine-local overlays (per-host override, no env vars)

Any committed config can be overridden **per machine** by a deep-merged overlay
file — the by-convention way to vary a field on one host (a local endpoint URL, a
token / vault-entry name, headers) **without editing the shared config or
exporting an environment variable**. Drop a file at:

```
~/.agent-mcp/overrides/<id>.{yaml,yml,json}
```

`<id>` is the config's explicit top-level `id:`, or — absent that — its filename
stem with a trailing `.mcp` stripped (`vei.mcp.yaml` → `vei`). At load time the
overlay is deep-merged over the committed config: **mappings merge recursively;
scalars and lists in the overlay replace** the base (a list is restated, not
appended). This applies to every config field, not just the URL.

Example — the committed config holds the honest gateway default:

```yaml
# .github/agents/vei.mcp.yaml   (committed, shared across machines)
server:
  type: http
  url: https://gateway.example:1958/vei-search/mcp/
auth:
  kind: command
  command: ["vault", "get", "Gateway API Token", "password"]
  parse: raw
  header: Authorization
tools: { allow: ["vei_*"] }
```

On the host where the service runs locally, an overlay points it at the on-box
endpoint (and drops the now-unneeded gateway auth) — no other host is affected,
and nothing is exported:

```yaml
# ~/.agent-mcp/overrides/vei.yaml   (this machine only)
server:
  url: http://localhost:8420/mcp/
auth:
  kind: none
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

**Prerequisites — when code-mode pays off.** code-mode is worth the indirection
only when the upstream MCP has **all** of: (1) genuinely **chained** calls (one
tool's output feeds the next), (2) **large intermediate payloads** better filtered
in-snippet than surfaced, (3) a **catalog large enough** that `find_tool`
discovery earns its keep, and (4) **predictable, documented output shapes.**

Criterion (4) is the one most integrations forget, and it is a hard gate. The generated
`Tools` interface types **inputs only** — every method is rendered
`name(args: <from inputSchema>): Promise<any>`. MCP tools seldom carry an
`outputSchema`, and the renderer ignores it regardless, so **return shapes are
untyped**. In a snippet the model must therefore know each tool's result shape
*in advance*; a direct tool call does not (it sees the result and adapts). The
auto-parse convention also differs **per call**: a *lone* JSON text result is
auto-parsed to an object, but a *multi-item* content result arrives as the raw
MCP content array (`[{ type: "text", text: "…" }]`) the snippet must unwrap and
`JSON.parse` itself. Against undocumented shapes, chained snippets fail blind —
often on the *second* tool after the first was probed — and the probe rounds
erase the round-trip win.

The remedy is to **schematize the output first**: put a
[`transform`](#transform--reshape-tool-results) decorator **innermost** that
`pick`/`extract`/`drop`s each read tool's result into a small, stable, *named*
shape, and document that shape in the consuming sub-agent's `.agent.md`. Then
code-mode snippets are written against a known contract. Without a `transform`
companion for an undocumented upstream, prefer **direct tool calls** behind a
plain `tools:` filter — the model-sees-then-adapts loop is the more reliable
default, and additive code-mode (`expose: ["*"]`) over blind shapes mostly adds a
tool the model can't use confidently.

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

### `gate` — preflight-conditional tool access

Allow or deny a tool **per-call**, based on a fact that lives in a *different*
upstream response. When a client calls one of `match_tools`, `gate` first issues a
**preflight** lookup (keyed off the gated call's own arguments), evaluates a
boolean **predicate** over the lookup result, and then either lets the real call
through or returns a policy **stub** / empty **drop** / JSON-RPC **error**. This is
the structural way to enforce a rule whose signal is *out-of-band* for the gated
tool itself — something a static `filter`/`transform` can't see.

```yaml
- type: gate
  match_tools: [get_details, get_discussion]   # globs; which tools/call to gate
  preflight:
    tool: get_record_by_id                     # the out-of-band lookup
    args_from: { id: "$args.recordId" }        # map preflight args from the gated call
    cache: per-key                             # cache the lookup per resolved args
  allow_when:                                  # boolean predicate over the lookup result
    all:
      - any:
          - { path: "tags[*]", in: ["public", "internal"] }
          - { path: "title", matches: "\\[OK\\]" }
      - { path: "isSensitive", equals: false }
  on_deny: stub                                # stub | drop | error
  stub: { blocked: true, reason: "withheld by policy" }
```

- **`match_tools`** — globs; only a matching `tools/call` triggers a preflight
  (everything else, including `tools/list`, passes straight through — the gated
  tools stay visible).
- **`preflight`** — one upstream `tools/call`. `args_from` maps each preflight
  argument from a `$args.<path>` reference into the gated call's arguments
  (`$args` alone = the whole arguments object); any non-`$args` value is a
  literal. `cache: per-key` reuses the lookup across tools that gate off the same
  key (one round-trip per distinct key).
- **`allow_when`** — a predicate tree of `all` / `any` / `not` combinators over
  leaf tests `{ path: <p>, <op>: <value> }`. Paths support dotted keys, `[*]`
  array wildcards, and `[n]` indices. Ops: `in` / `not_in`, `equals` /
  `not_equals`, `matches` / `not_matches` (regex), `contains`, `exists`. A
  positive op holds when *any* addressed value matches; its negative twin holds
  when *none* does (vacuously true if the path is absent).
- **`on_deny`** — `stub` (return the `stub` payload as the result, default),
  `drop` (empty result), or `error` (JSON-RPC error). **`on_error`** (`deny`
  default, or `allow`) decides what happens if the preflight itself fails —
  **fail-closed** by default, so a policy gate denies when it can't prove the
  allow condition.

> **Placement.** The preflight reaches the upstream *through the decorators below
> `gate`*, so put `gate` above any `filter`/`transform` whose output the predicate
> must not see stripped — or below them if the predicate should read the reshaped
> result. `gate` never prunes `tools/list`; a gated tool stays advertised and
> returns the deny action for calls that fail the predicate.



## Use from a Copilot agent

```yaml
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp            # marketplace-isolation: allow mcp-server-startup
    args: ['bridge', '--config', '.github/agents/ado.mcp.yaml']
    tools: ['*']
```

> **`command: agent-mcp` works on every platform.** The Windows binstub is a
> single `.cmd` (no competing `.ps1`), so a bare `agent-mcp` resolves to it under
> PowerShell, `where`/PATHEXT, and `cmd` alike, and the `.cmd` forwards stdin to
> the stdio MCP child. (A `.ps1` shim would win PowerShell's command discovery but
> doesn't reliably stream stdin -- hence the deliberate `.cmd`-only layout.) On
> Linux/WSL the binstub is the usual bash script.

This remains an explicit startup compatibility boundary: static
`mcp-servers.command` cannot consume a session command catalog.

On Windows the session catalog names the payload-local `.cmd`, not its sibling
`.ps1`, so shell pipelines enter through a native process and preserve stdio.
Operator-facing commands elsewhere in this README intentionally use the
compatibility global wrapper; agent-facing skills use the exact catalog argv.
Because CMD reparses argv, Windows agent-facing calls pass structured input
through stdin or `--request-file`, never as inline JSON.

## Troubleshooting

There is no special bridge resolver, agent-bridge daemon, or
`agent-mcp-troubleshooting` command. Diagnose the exact bridge process the agent
will spawn:

```sh
agent-mcp validate .github/agents/ado.mcp.yaml
agent-mcp --log-level debug call .github/agents/ado.mcp.yaml some_tool '{"x":1}'
```

- **Config/load failures are fail-fast.** `validate` loads the same config path,
  plugin-shipped bridge, and machine-local overlay that `bridge`/`call` will use;
  schema errors (unknown `server.type`, bad `server.protocol`, invalid
  decorator, mismatched `server.url_secrets`, unsupported auth list) fail before
  any upstream is contacted.
- **Protocol negotiation is dual-era.** In `server.protocol: auto`, a
  `server/discover` probe that gets a non-modern result, timeout, or legacy HTTP
  rejection falls back to the legacy `initialize` handshake. Use
  `server.protocol: legacy` to skip the probe for a known legacy endpoint, or
  `modern`/an explicit date to pin a modern upstream.
- **Credential failures surface at their injection point.** `server.url_secrets`
  must resolve before the first HTTP connect and raises if a secret is missing.
  `command` auth logs command-not-found, timeout, and non-zero exit to stderr; if
  no token is produced, an HTTP bridge sends no auth header and normally returns
  the upstream's 401/error (with one invalidate+retry on 401). A stdio bridge has
  no 401 channel: it spawns the child with whatever env could be injected, so a
  missing `target_env`/secret is diagnosed from the child MCP's stderr/error.
- **Runtime provisioning is separate from bridge config.** If `agent-mcp` itself
  is not found, install/stamp the plugin binstub first; if the binstub prints
  `::agent-provisioning::`, let the first-use provision complete and preserve the
  exact failure text if it cannot build the runtime.

### Bridge lifecycle — clean teardown (no leaked processes)

A `bridge` normally shuts down when its **stdin closes** (the runtime's terminal
signal). That signal is defeated when an intermediate launcher interposes — most
notably the Windows `agent-mcp.cmd` shim, where the tree is
`runtime → cmd.exe → python -m agent_mcp bridge`: the runtime terminates only the
`cmd.exe` it spawned, leaving the grandchild `python` with an inherited stdin that
never sees EOF, so it (and its helpers) leak. Two guards close this gap:

- **Parent-death watchdog** — a daemon thread polls the launch-time parent's
  liveness and, when it goes away, drives the *same* graceful teardown as stdin
  EOF, with a hard-exit backstop if teardown wedges.
- **Descendant reaping** — on Windows the bridge assigns itself to a
  kill-on-close **Job Object**, so the upstream stdio child and any `az`/`gh`/`git`
  mint helpers die when the bridge exits. On POSIX the graceful path already
  closes the upstream child.

Tunables (all optional):

| env var | default | effect |
|---------|---------|--------|
| `AGENT_MCP_PARENT_WATCHDOG` | on | `0`/`false`/`off` disables the watchdog |
| `AGENT_MCP_PARENT_WATCHDOG_INTERVAL` | `5` | parent-liveness poll interval, seconds (`<=0` disables) |
| `AGENT_MCP_PARENT_WATCHDOG_GRACE` | `10` | hard-exit backstop after signalling, seconds (`0` = graceful-only) |
| `AGENT_MCP_REAP_DESCENDANTS` | on | `0`/`false`/`off` disables the Windows kill-on-close job |
| `AGENT_MCP_NO_VERSION_REAP` | unset | set to skip the on-upgrade reap of stale-version bridges (see below) |

**Why the watchdog and not a direct launch?** The obvious "just don't interpose
`cmd.exe`" fixes — a native `agent-mcp.exe` console-script launcher, or launching
the versioned `python.exe` directly — are not viable here: unsigned console-script
`.exe` trampolines are **stripped at install** because Smart App Control blocks them
(CodeIntegrity 3077) on managed devices, and the versioned interpreter path is only
known after the binstub resolves `current-version` at spawn time (so a static
`.agent.md` can't name it). A PowerShell shim was rejected for cold-start latency
and unreliable stdin streaming. The single self-provisioning `.cmd` is therefore
deliberate, and the **parent-death watchdog is the portable teardown mechanism** —
it fires no matter which launcher (the `cmd` shim, or a `uv`/venv `python` trampoline
above the bridge) is the one the runtime kills.

**Version-reap on upgrade.** A leaked/orphaned bridge — or a `serve` warmth daemon —
from a *previous* runtime version would otherwise keep its whole `~/.agent-mcp/versions/<old>`
tree resident across an upgrade (garbage collection deliberately *protects* any slot
with a live process). So when the installer activates a new version it also **reaps
processes still running from any non-current slot** (`scripts/reap_versions.py`, an
agent-mcp-local companion that reuses the shared versioned-runtime primitive's
slot-attribution helpers to map each live pid to a slot and terminate the stale
trees). The now-current slot and the installer itself are never touched. Set
`AGENT_MCP_NO_VERSION_REAP` to opt out.

## CLI → MCP: the `cli` server type

The `http`/`stdio` bridges proxy an upstream MCP. The **`cli`** server type is
the *inverse*: it exposes a curated set of **native CLIs as MCP tools**, for an
agent (or MCP client) that reaches tools only over MCP and would otherwise need a
heavyweight per-tool MCP server. There is no upstream and no network — `tools/list`
is synthesized from tool **sidecars** and each `tools/call` binds the arguments to
an **argv** and spawns the CLI as a subprocess (no shell — a param value can never
inject a command).

Each tool is one sidecar Markdown file with an `mcp:` frontmatter block:

```yaml
---
mcp:
  name: vei_search
  description: Semantic search across the monorepo, logs, and Gitea via VEI.
  scope: shared                 # optional execution-policy tag (see below)
  inputSchema:                  # raw MCP inputSchema (same shape materialize plates)
    type: object
    properties:
      query: { type: string, description: Search text }
      limit: { type: integer, description: Max results }
    required: [query]
  invoke:                       # params -> argv (never a shell string)
    command: vei-search
    args:
      - "{query}"                                           # required positional
      - { flag: "--limit", value: "{limit}", when: limit }  # optional flag
---
# vei-search  (human doc body — ignored by the bridge)
```

A path-qualified `invoke.command` (for example `scripts/vei-search`) resolves
relative to the declaring sidecar. A bare command such as `vei-search` keeps the
normal `PATH` lookup. This lets a plugin ship a helper beside its sidecar without
hardcoding its installed location.

The bridge config points at the sidecar set:

```yaml
server:
  type: cli
  tools_from:                   # sidecar paths (relative to this config file)
    - tools/vei-search.md
    - tools/vei-status.md
  scopes: [shared, anomalous-potato] # optional; gate tools by their mcp.scope tag
tools: { allow: ["vei_*"] }     # the usual allow/deny filter still applies
```

**Argv binding rules** (small and unambiguous):

- A **bare string** is a required token (positional/literal); `{name}`
  placeholders are substituted, and a referenced param that is absent is an error
  (use the mapping form with `when` for optional args).
- A **mapping** `{flag?, value?, when?, repeat?}`: `when` skips the entry unless
  that param is present; `repeat` names a list param and emits `flag`+value per
  item; `flag` alone is a boolean-as-presence flag; `flag`+`value` (or `value`
  alone) emit the substituted value as a single argv token.

**Execution-scope gating.** A sidecar may carry `mcp.scope` (a free-form tag). If
the bridge config lists `server.scopes`, a tool whose `scope` is set and *not* in
that list is neither advertised nor runnable; an untagged tool is always allowed,
and an empty `scopes` disables gating. This is the generic mechanism a control
plane maps its per-host execution policy onto (e.g. `scopes: [shared, <machine>]`).

**Result shaping.** A tool's stdout is returned as text content; a non-zero exit
becomes an MCP tool error (`isError: true`) carrying the stderr tail — a failing
tool yields an error, never a hang.

**Windows arg fidelity.** When spawning the tool the `cli` transport prefers a
sibling `.ps1` (invoked via `pwsh -NoProfile -File`) over a `.cmd`/`.bat` shim,
because `cmd.exe` re-parses a batch shim's forwarded arguments and mangles
metacharacters (`&`, `|`, `%`, `^`, quotes). A `.cmd` is used only as a last
resort. (The stdio-MCP transports are unaffected — they still launch
`npx`-style servers, which need stdin streaming a `.ps1` doesn't provide.)

**Self-sourced credentials.** A `cli` bridge may carry an `auth` block; the
transport merges the injector's `child_env()` into the spawned tool's
environment. This lets the bridge **fetch a credential in its own process** (a
clean context where `vault`/`gh`/`az` helpers work) and hand it to the tool via
its env — instead of the credential having to be present in the session that
launched the bridge. The env-first form of the `command` injector uses an
existing env var when set and only runs the helper otherwise:

```yaml
server:
  type: cli
  tools_from: [tools/vei-search.md]
auth:
  kind: command
  command: [vault, get, "Gateway API Token", password]
  parse: raw
  source_env: GATEWAY_TOKEN      # use this env var if already set...
  target_env: GATEWAY_TOKEN      # ...else run the command; inject either way
```

## MCP → CLI: `call` and `materialize`
The bridge exposes an upstream MCP *to an MCP client*. The **`call`** and
**`materialize`** verbs expose it *to the shell* instead — the same upstream, the
same auth + `tools:` filtering, projected as command-line tools for agents (or
people) who would rather `ls`/`cat`/pipe than speak JSON-RPC.

### `call` — one-shot invoke one tool

```sh
agent-mcp call <bridge> <tool> '<arguments-json>'
```

Connects to the bridge's upstream, **negotiates its protocol era** (per
`server.protocol`; see [Protocol versions](#protocol-versions-mcp-2x--dual-era)),
invokes one tool, and prints its result — then exits. This is the **stateless cold
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
cold-start — spawn the runner + protocol negotiation (`server/discover` or
legacy `initialize`) — on **every** invocation.
`serve` runs a resident daemon that keeps one **warm session per bridge** and
answers `call`/`list` requests over a local IPC socket (default handle
`$AGENT_MCP_HOME/serve.sock`), so repeated calls skip the cold-start entirely.

- **Cross-platform IPC** — on POSIX the daemon binds an **AF_UNIX** socket at
  that path (gated by file permissions). On **Windows** (no asyncio AF_UNIX) it
  binds a **loopback TCP** listener and publishes the port + a per-daemon auth
  **token** in a `<socket>.endpoint` sidecar (port-discovery); the client reads
  it to dial `127.0.0.1` and presents the token on every request, preserving the
  same single-user gating. This is automatic and needs no configuration.
- **Transparent** — a running `call` auto-detects the daemon and routes to it;
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

Normal plugin enablement installs the runtime reconcile hook. For direct setup
from a checkout, run the installer directly; for a cheap first-use setup, pass
`stamp` to deploy only the self-provisioning binstub:

```powershell
.\scripts\init.ps1     # Windows -- venv at ~/.agent-mcp, binstub in ~/.local/bin
```
```bash
./scripts/init.sh      # Linux/WSL
```

No daemon is required for a Copilot agent. `agent-mcp serve` is only an optional
warmth tier for repeated shell `call`/materialized-stub use.

## Architecture

```
stdin/stdout        Bridge        Decorator pipeline           UpstreamClient        Transport
(JSON-RPC)   <->   loop   <->   d0 <-> d1 <-> ... <-> dN  <->  (id correlation)  <->  http|stdio  <->  upstream MCP
                                 ^                                                   or cli responder
                          filter/rename/defer/                                      Auth injector -> credential_relay.sources
                          code-mode/storage/
                          transform/gate
```

- `config.py` — load + validate the per-bridge config file (incl. `decorators:`).
- `auth/` — `AuthInjector` protocol + injectors (reuse `credential_relay.sources`).
- `transports/` — `http` (Streamable HTTP + SSE), `stdio` (child process), and
  `cli` (local sidecar-backed responder; no upstream).
- `pipeline.py` — `UpstreamClient` (JSON-RPC id correlation over a transport) +
  `Pipeline` (compose decorators around the upstream core call).
- `decorators/` — `base` (Decorator + BridgeContext), `_catalog` (catalog
  pagination + JSON-Schema→TS), and the `filter`/`rename`/`defer`/`code-mode`/
  `storage`/`transform`/`gate` decorators.
- `bridge.py` — stdio framing, per-request dispatch through the pipeline,
  unsolicited-message passthrough.
- `protocol.py` — the dual-era version model: modern (`2026-07-28`, per-request
  `_meta`) vs. legacy (`initialize` handshake) constants, `_meta` builders, HTTP
  metadata headers, `server/discover` result + `UnsupportedProtocolVersionError`
  builders, and version negotiation. Shared by the client and cli-responder sides.
- `client.py` — `OneShotSession`: connect + **negotiate era** (`server/discover`
  probe / forced) + one `tools/list` / `tools/call` against an upstream, then exit
  (the engine under `call` and the introspection step of `materialize`).
- `materialize.py` — project a `tools/list` catalog into the on-disk stub fleet
  (symlink farm on POSIX, `.ps1`/`.cmd` shim farm on Windows) + plated sidecars.
