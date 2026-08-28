---
name: agent-mcp
description: >-
  Bridge an upstream MCP server (HTTP or stdio) as a local stdio MCP server and
  inject host credentials, then wire an existing Copilot agent to that bridge.
  Use when asked to "wrap an MCP", "bridge an MCP", "add auth to an MCP
  server", "proxy an MCP", "use an MCP that needs az/gh login", "set up a
  bridge for <service>", "wire an agent to agent-mcp", "materialized fallback",
  "troubleshoot an agent-mcp bridge", or to expose a remote/authenticated MCP
  to Copilot. For creating or reviewing the agent definition itself, use
  `customizing-copilot:defining-subagents`.
---

# agent-mcp

> **Before you start — use the payload-local session command.**
> The agent-mcp session command catalog supplies an exact `argv[0]` owned by
> this plugin payload. For every shell invocation below, replace
> `<agent-mcp catalog argv[0]>` with that exact path; do not search `PATH` or
> substitute a same-named command from another payload. In PowerShell, invoke
> it as `& "<agent-mcp catalog argv[0]>" <args>` so paths containing spaces stay
> one command token. The shim provisions its own runtime on first use and works
> without agent-worktrees. If the catalog is unavailable because the host did
> not run session-start hooks, use the
> compatibility readiness path only after resolving exactly one installed
> agent-mcp payload. On POSIX, enumerate
> `~/.copilot/installed-plugins/*/agent-mcp/scripts/init.sh`, fail if more than
> one exists, then run the sole path with `stamp`; on Windows, apply the same
> single-match rule to `scripts\init.ps1`. Never choose the first match from
> multiple marketplaces. Then use the wrapper the installer reports. The first
> runtime call may take ~30–120s (watch for
> `::agent-provisioning::`); let it finish. Preserve any provisioning failure
> exactly instead of improvising a toolchain install.

`agent-mcp` wraps one upstream MCP server as a local **stdio** MCP server and
injects host credentials, driven by a single per-bridge config file. It is
standalone: a sub-agent launches `agent-mcp` directly from its `mcp-servers`
entry; there is no agent-bridge integration, repo resolver, or required daemon.
It replaces single-purpose, hardcoded MCP wrapper scripts with a config-driven,
multi-transport, multi-auth bridge.

## Responsibility boundary

This skill owns bridge transport, authentication, filtering, catalog
reshaping, materialization, and bridge-equivalent fallback. The normative
custom-agent contract -- whether a domain MCP belongs in a sub-agent, bounded
execution, top-level tools, `## MCP Readiness`, and anti-self-delegation -- is
owned by **`customizing-copilot:defining-subagents`**. Runtime task decomposition
is owned by **`delegation-guidance:delegating-work`**.

## When to use

- An MCP server requires an OAuth/broker login flow (Entra/`az`, `gh`) that
  Copilot CLI can't perform itself.
- You want to wrap a third-party stdio MCP and feed it a host-acquired token.
- You want to allow/deny which upstream tools are exposed.
- You want to **reshape a large or partner MCP**: shrink a 100+ tool catalog
  behind a tool-finder, namespace/rename tools, expose a typed `run_code` tool,
  or relay big payloads through a stream buffer — see [Decorator stack](#decorator-stack).
- You want to wire an existing repo-scoped agent (e.g. `@ado-data`) to MCP
  tools from an authenticated upstream -- see the setup flow below.

## Config location -- in-repo vs. user-global

A bridge config can be referenced two ways:

| Form | Reference | Lives in | Use for |
|------|-----------|----------|---------|
| **In-repo `--config`** (preferred) | `bridge --config <path>` | the repo (e.g. `.github/agents/<name>.mcp.yaml`) | **repo-scoped agents** -- config is version-controlled, travels with the repo, needs no deploy |
| **Named bridge** | `bridge <name>` | `~/.agent-mcp/bridges/<name>.{yaml,yml,json}` | **personal / cross-repo** MCPs not tied to one repo |
| **Plugin-shipped bridge** | `bridge <name>` | installed plugin `agents/` or `mcp/` directory | a plugin ships its own sub-agent + bridge config; user-space bridge file is not required |

> **Prefer the in-repo `--config` form for any agent that ships inside a repo.**
> Reserve named bridges (user-global `~/.agent-mcp/bridges/`) for MCPs you use
> across many repos or that do not belong to a checkout. Both forms read the
> same config schema; only the lookup differs.

> **Customizing an existing bridge on one machine** — to change a committed or
> plugin-shipped bridge (which tools it exposes, its decorators, headers, auth)
> for *this host only*, without editing the shared file, use the
> **`customizing-bridges`** skill: it writes a deep-merged overlay at
> `~/.agent-mcp/overrides/<id>.yaml`.

## Wire an existing repo-scoped agent (the common case)

First author the agent with
**`customizing-copilot:defining-subagents`**. This section covers only the
bridge-specific transport and fallback wiring for giving that agent
authenticated MCP tools -- e.g. an `@ado-data` agent backed by the Azure DevOps
MCP.

**1. Write the bridge config in the repo**, next to the agent
(`.github/agents/<name>.mcp.yaml`). It holds the upstream `server` launch info
(same shape as a `.mcp.json` entry) plus `auth` and overrides. Copy the full
annotated example, [`references/ado.mcp.yaml`](references/ado.mcp.yaml), and
adapt -- at a glance:

```yaml
# .github/agents/ado.mcp.yaml
server:
  type: http                       # http | stdio | cli
  url: https://mcp.dev.azure.com/your-org
  protocol: auto                   # auto | modern | legacy | <YYYY-MM-DD>  (MCP 2.x, see below)
auth:
  kind: entra                      # entra|az | gh | git-credential | env|static | none
  resource: 2a72489c-aab2-4b65-b93a-a91edccf33b8   # az resource/scope
tools: { allow: ["repo_*", "wit_*"], deny: [] }    # optional upstream filter
```

Validate before wiring:
`<agent-mcp catalog argv[0]> validate .github/agents/ado.mcp.yaml`.

> **MCP 2.x (dual-era).** agent-mcp speaks both the **modern** stateless
> revision (`2026-07-28`+ — per-request `_meta`, no `initialize`/session,
> optional `server/discover`) and the **legacy** `initialize` handshake, in both
> directions: it negotiates an upstream's era when consuming one, and exposes a
> `cli` adapter as a dual-era server (answers `server/discover`, cacheable
> `tools/list`). `server.protocol` defaults to `auto` (probe + fall back to
> legacy); set `modern`/`legacy`/an explicit `YYYY-MM-DD` to force or pin an era.
> See the plugin README (§ *Protocol versions (MCP 2.x / dual-era)*).

> **stdio launch — `command` vs `npm`.** A stdio bridge either lists an explicit
> `server.command` (full control) or names an npm package with `server.npm:
> <pkg>` and lets agent-mcp pick the fastest **available** runner at spawn
> (`bunx` → `npx -y`). `npm` mode stays package-manager-neutral (always works via
> `npx`; uses `bunx` only where present). See the plugin README for details.

**2. Point the existing agent at it** in the `mcp-servers` field that
`customizing-copilot:defining-subagents` owns. The transport stanza is
`agent-mcp` running the bridge over stdio:

```yaml
mcp-servers:
  ado-remote-mcp:
    type: stdio
    command: agent-mcp              # marketplace-isolation: allow mcp-server-startup
    args: ['bridge', '--config', '.github/agents/ado.mcp.yaml']
    tools: ['*']
```

The `--config` path is resolved relative to the process cwd, which is the repo
root when Copilot spawns the sub-agent's MCP server -- so an in-repo relative
path just works.

The literal `mcp-servers.command` is an explicit startup compatibility
boundary. Repository-committed agent frontmatter must stay machine-portable,
has no plugin-root interpolation contract, and may be launched before or
independently of session command-catalog context. It therefore cannot consume
`<agent-mcp catalog argv[0]>`. Keep it literal until the host offers a
plugin-root-capable MCP launcher contract.

**3. Verify end-to-end** by invoking the sub-agent and having it call an upstream
tool (e.g. fetch a repo). A clean way to prove the bridge -- not a stale runtime
-- is in use is to exercise a real query and confirm a live result.

**4. Add the equivalent CLI fallback mechanics.** In the readiness section
owned by `customizing-copilot:defining-subagents`, name a materialized fleet
over the same bridge config, probe a read-only stub after catalog failure,
preserve identity and top-level `tools:` filtering, and stop only after both
surfaces fail. Decorator-only restrictions are not applied by the CLI path, and
frontmatter-only env is not inherited. Use an existing fleet first;
re-materialize when the expected stub is absent or
`manifest.json.generated_by` differs from
`<agent-mcp catalog argv[0]> --version` (config drift needs a deploy-owned
digest). Use `--no-serve` for identity-sensitive fallback calls. The
bridge-specific platform commands, failure matrix, warmth guidance, and
drift contract live in
[Reliable agent-mcp transport and fallback](references/reliable-agent.md).

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

Token acquisition reuses the `credential-relay` sources. HTTP bridges invalidate
the cached credential and retry once on an upstream `401`; stdio/cli bridges
inject env at child spawn and rely on the child tool/server error if a required
secret is missing.

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

## Secret in the URL (`server.url_secrets`)

Some upstreams carry the secret **in the URL itself** rather than a header — e.g.
an add-on that gates access on a secret URL path (`/private_<token>`). For those,
a `${name}` placeholder in `server.url` is resolved at **spawn time** from a
matching `server.url_secrets` source. Each source is an ordinary auth block
(same kinds as above — typically `command` + `parse: raw`), so the secret is
fetched on demand and never committed or exported into the session env. This
lets a committed config carry a secret URL **by reference** instead of forcing a
machine-local override to hardcode the full secret URL.

```yaml
server:
  type: http
  # Whole URL from one secret (the vault entry stores the full URL):
  url: "${ha_url}"
  url_secrets:
    ha_url:
      kind: command
      parse: raw
      command: ["vault", "get", "My Vault/HA MCP Add-on", "password"]
```

You can also interpolate just **part** of the path (store only the token in the
vault):

```yaml
server:
  type: http
  url: "http://homeassistant.example:9583/${ha_path}"
  url_secrets:
    ha_path:
      kind: command
      parse: raw
      command: ["vault", "get", "My Vault/HA MCP Add-on", "secret-path"]
```

Rules:

- Only valid for `type: http`. Every `${name}` in the URL must have a matching
  `url_secrets` source, and vice versa — a mismatch is a load-time config error.
- Resolution is **lazy** (first connect), so the payload-local command's
  `validate` / `status` operations never touch the credential source. If the
  source can't be resolved at connect (e.g. the vault is locked), the bridge
  fails loudly rather than connecting to a half-formed URL.
- Prefer this over a machine-local override whenever the secret is the *only*
  per-host difference — the committed config then works on every host with no
  override file.

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
| `code-mode` | Expose a `run_code` tool with a generated TypeScript `Tools` interface; snippets run in Node and chain tool calls. Adds `find_tool` for typed signatures on big catalogs. Needs Node on `PATH`. **Best only when calls chain, payloads are large, *and* output shapes are documented — see [prerequisites](#is-an-mcp-a-good-code-mode-candidate-prerequisites); pair with `transform` when they aren't.** |
| `storage` | Externalize large outputs to `mcpstream://…` handles; rehydrate handle inputs; `read_stream` fetches them. **Field-level `rules:`** target specific tool input/output JSON paths, attach a summary (count + schema + head, or a command), and rewrite a stream-mode input param's schema to a URL. |
| `transform` | Reshape tool results per tool: `extract`/`pick`/`drop` dotted paths (literal-dotted keys like ADO `fields.System.Title` supported) or a `command` (jq-style) filter. |
| `gate` | Allow/deny a tool **per-call** by a **preflight** upstream lookup + boolean predicate (`all`/`any`/`not`; `in`/`matches`/`equals`/`contains`/`exists` over `[*]`/dotted paths). Deny → `stub`/`drop`/`error`; fail-closed on preflight error. For rules whose signal is out-of-band for the gated tool. |

Decorators compose because each calls *through* the ones below it. Recommended
order: `defer`/`code-mode` outermost, then `rename`, then `filter`, with
`storage` innermost. The legacy `tools:` filter still works (applied as an
implicit `filter`). Full reference + per-decorator options:
[README → Decorator stack](../../README.md#decorator-stack).

### Is an MCP a good `code-mode` candidate? (prerequisites)

`code-mode` is not a free win — reach for it only when **all four** hold:

1. **Chained calls** — one tool's output feeds the next (resolve → fetch →
   filter). One-shot tools (a single search with server-side filtering) gain
   nothing.
2. **Large intermediate payloads** — better filtered inside the snippet than
   dumped into context.
3. **A catalog big enough** that typed discovery (`find_tool`) earns its keep
   (roughly `interface_limit`, 40+).
4. **Predictable, documented output shapes.** ← the easy one to miss.

Criterion **4** is the gate. The generated `Tools` interface types **inputs
only** (from each tool's `inputSchema`); every method returns **`Promise<any>`**
— MCP tools rarely ship an `outputSchema`, and the renderer ignores it if they
do. So in a snippet the model must commit to a result shape *before it runs*,
with no types to lean on. Direct tool calls sidestep this (the model sees each
result and adapts turn-by-turn); `code-mode` cannot. Result-shape surprises also
vary *per tool*: a **lone** JSON text result is auto-parsed to an object, but a
**multi-item** content result arrives as the raw MCP content array
(`[{type:"text", text:"…"}]`) that the snippet must unwrap and `JSON.parse`
itself. With undocumented shapes, every snippet becomes a guess (or burns a probe
round), erasing the round-trip savings.

**Fix — pair `code-mode` with `transform`.** When outputs are undocumented or
messy, add a `transform` decorator **innermost** (upstream end) that
`pick`/`extract`/`drop`s each read tool's result into a small, **stable, named**
schema — then document that schema in the sub-agent's `.agent.md`. Now snippets
are written against a known shape, payloads shrink (helps 2), and `code-mode`
delivers. Without a `transform` companion for undocumented upstreams, prefer
**direct tool calls** (a plain `tools:` allow/deny filter) — they are the more
reliable default.

## Commands

```
<agent-mcp catalog argv[0]> bridge --config FILE    # run an in-repo bridge
<agent-mcp catalog argv[0]> bridge <name>           # run a named bridge
<agent-mcp catalog argv[0]> validate <name|FILE>    # parse + schema-check
<agent-mcp catalog argv[0]> status                  # prerequisites + bridges
<agent-mcp catalog argv[0]> call <bridge> <tool> [JSON]
<agent-mcp catalog argv[0]> materialize <bridge>
<agent-mcp catalog argv[0]> serve [--socket PATH]
```

## Troubleshooting checklist

- Start with `<agent-mcp catalog argv[0]> validate <name-or-config>`; it loads
  the same in-repo, named, plugin-shipped, and machine-overlay config path as
  `bridge`/`call`, but does not contact the upstream or credential source.
- Reproduce outside Copilot with
  `<agent-mcp catalog argv[0]> --log-level debug call <bridge> <tool>
  '<arguments-json>'`. A `server/discover` rejection in `auto` is normal for a
  legacy server; set `server.protocol: legacy` to skip that probe.
- Separate runtime provisioning from bridge config: catalog `availability`
  reports whether the payload shim and installer exist, not whether the runtime
  is already built. If invocation prints `::agent-provisioning::`, let first-use
  provisioning finish and preserve the exact failure. If the catalog entry is
  absent, follow the readiness path above.
- Credential failures differ by transport: `server.url_secrets` fail before HTTP
  connect; `command` auth logs command-not-found/timeout/non-zero exit; HTTP gets
  a 401/error path with one retry, while stdio/cli children must report missing
  env/credential themselves.

## MCP -> CLI: `call` and `materialize`

Besides serving an MCP client, agent-mcp can expose an upstream MCP **to the
shell** -- for agents (or humans) that prefer to `ls`/`cat`/pipe tools instead
of speaking JSON-RPC.

- **`call`** is the one-shot engine: it connects to the bridge's upstream,
  negotiates the configured protocol era (`server/discover` in modern/auto, or
  legacy `initialize`), invokes one tool, and prints the result. Arguments are
  the tool's **raw MCP `arguments` object** as JSON -- via an inline arg, `--arguments`,
  `--request-file PATH`, or stdin. Output is **raw passthrough** (the upstream's
  text content verbatim; the advertised `structuredContent` as JSON when there is
  no text). A tool error is a non-zero exit + a stderr message -- never a hang
  (the wait is bounded by the config `timeout`).

  ```sh
  # POSIX. On Windows, put structured arguments in a request file.
  <agent-mcp catalog argv[0]> call gitea list_issues '{"owner":"me","repo":"x"}'
  echo '{"owner":"me","repo":"x"}' | <agent-mcp catalog argv[0]> call gitea list_issues
  <agent-mcp catalog argv[0]> call gitea create_issue --request-file req.json
  ```

  The Windows catalog intentionally names the payload `.cmd` so stdio reaches
  the child through a native process. Do not pass inline JSON through CMD;
  provide it on stdin or with `--request-file` at a simple temporary path.

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
  guessed by a model. Each stub intentionally forwards through the legacy
  global management wrapper because a generated fleet can outlive the session
  catalog. <!-- marketplace-isolation: allow materialized-stub-management -->
  The stub is therefore an explicit compatibility boundary, not a
  payload-local invocation claim. Until materialized fleets gain attributable
  management context, ambient `PATH` can still select a different installation
  cell; do not treat this fallback as cross-cell-safe.

  ```sh
  <agent-mcp catalog argv[0]> materialize gitea
  list_issues '{"owner":"me","repo":"x"}' | jq '.[].number'
  ```

  Re-running `materialize` rebuilds the tree atomically (temp dir + swap), so it
  doubles as a drift refresh. The bridge's `tools:` allow/deny filter gates which
  tools are materialized.

For sub-agent reliability, this fleet is the **same-bridge fallback** after a
Copilot catalog/registration failure -- not a raw bypass. Authorization must be
captured by bridge auth + top-level `tools:`; decorator stacks are not applied
on the one-shot CLI path. Preserve the primary error, verify identity/capability
through a read-only stub, and report both errors if the upstream also fails.
See
[Reliable agent-mcp transport and fallback](references/reliable-agent.md).

## CLI -> MCP: the `cli` server type

The inverse crossing: expose **native CLIs as MCP tools** for an MCP-only
consumer, with no upstream MCP and no per-tool server. `server.type: cli` lists
tool **sidecars** (`.md` files with an `mcp:` frontmatter block carrying
`name`/`description`/`inputSchema` plus an `invoke` argv template); `tools/list`
is synthesized from them and `tools/call` binds params to an **argv** and spawns
the CLI (no shell). A sidecar's optional `mcp.scope` tag, matched against
`server.scopes`, gates which tools a host may advertise/run. See the plugin
README (§ *CLI -> MCP: the `cli` server type*) for the sidecar schema, argv-binding
rules, and scope gating.

```yaml
server:
  type: cli
  tools_from: [tools/vei-search.md, tools/vei-status.md]
  scopes: [shared, anomalous-potato]
tools: { allow: ["vei_*"] }
```

## Install

`./scripts/init.sh` (Linux/WSL) or `.\scripts\init.ps1` (Windows) -- creates the
venv at `~/.agent-mcp` and the `agent-mcp` binstub (a single `.cmd` on Windows)
in `~/.local/bin`.
