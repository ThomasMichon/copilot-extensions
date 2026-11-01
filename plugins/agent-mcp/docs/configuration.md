# MCP bridge configuration reference

[`src/agent_mcp/config.py`](../src/agent_mcp/config.py) owns `_read_file`,
`resolve_config_path`, `parse_config`, and `validate_config`. Read/validate never
mints credentials, runs a command, installs, repairs, connects to an upstream or
starts a service.

## Files, precedence and ownership

Explicit `--config` paths/extensions are loaded directly. Bare names select the
first `.yaml`, `.yml`, `.json` under the installation's `bridges` directory,
then a unique plugin bridge under `agents`/`mcp` (including `.mcp` filename
suffixes). Live directory marketplaces from nearest workspace settings precede
copied plugin roots; distinct same-tier matches refuse ambiguity.
`AGENT_MCP_PLUGIN_ROOTS`, `AGENT_MCP_MARKETPLACE_ROOT`, and `COPILOT_HOME` retain
native explicit source-selection roles.

The installation's `overrides/<id>.{yaml,yml,json}` applies after the chosen
declaration; `id` is the top-level value or filename stem with `.mcp` removed.
Native mapping merge is recursive, lists/scalars/null replace atomically.
Malformed-present/non-mapping sources fail. Missing override is optional;
missing explicitly selected bridge fails. There is no write-on-read migration.
Unknown ordinary parser keys are ignored; routing and decorator validators
reject unsupported fields where their native schema requires it.

`${config_dir}` expands command arguments against the declaring config's
directory; `${python}` names MCP's own absolute console interpreter.
It is not general shell/environment interpolation. Preserve source ownership
when composing arguments from another document. Reload is a new `load_config`
call; a running bridge retains its parsed snapshot.

## Single-server schema

| Family | Type/default/requirement |
|---|---|
| `id` | Optional overlay identity string; not runtime authorization. |
| `server` | Required object for a non-routed bridge. |
| `server.type` | `http` (default), `stdio`, `cli`. |
| `server.protocol` | `auto` (default), `modern`, `legacy`, or `YYYY-MM-DD` revision. |
| `server.url` | Required for HTTP; optional string otherwise. |
| `server.command`, `server.args` | String or argv list for command, argument list; required launcher for stdio unless `npm` supplied. Explicit command wins over `npm`. |
| `server.npm` | Optional package string; `args` become package arguments when no command. |
| `server.env` | Optional string-valued map, default empty. |
| `server.reap` | `child` default, `tree`, `none`; nondefault valid only for stdio. Native process custody still governs actual cleanup. |
| `server.tools_from`, `server.scopes` | String lists, default empty; nonempty sidecar sources required for CLI. Empty scopes means no scope filtering, not new execution privilege. |
| `server.url_secrets` | Optional map of placeholder names to auth-source objects, HTTP only; URL placeholders and sources must correspond exactly. |
| `headers` | Optional string map, default empty; never publish credential values. |
| `tools.allow`, `tools.deny` | Optional string lists, default empty; native tool filtering patterns. |
| `timeout`, `retries` | Numeric seconds default `30.0`, integer default `1`; native validation applies. |
| `idle_timeout` | Numeric seconds, explicit value then `AGENT_MCP_BRIDGE_IDLE_TIMEOUT`, default `300.0`; nonpositive disables idle reap. |
| `decorators` | Ordered object list, default empty; each entry requires `type`. |

`auth` may be one object, an ordered object list, null, absent or empty list
(last three mean no injection). Each `server.url_secrets` value uses the same
source parser. Credential declarations are native protected adapter inputs,
not arbitrary portable secret merge data.

| Auth fields | Type/default |
|---|---|
| `kind` | `none` default; `entra` (`az` alias), `gh`, `git-credential`, `command`, `env` (`static` alias). |
| `resource`, `scope`, `tenant` | Optional strings for Entra source; required source-specific combinations are enforced by `validate_config`. |
| `source_env`, `value` | Optional environment variable name or static source string; never put a real secret in a committed example. |
| `command`, `args`, `repair` | Command string/argv and argument lists, empty default; repair is explicit opt-in at acquisition, not executed by parsing. |
| `request` | Optional string map for credential request, empty default. |
| `parse`, `field` | `keyvalue` default or `raw`; optional extracted output-field string. |
| `inject`, `header`, `format`, `target_env` | `header` for HTTP / `env` otherwise unless selected; header defaults to Authorization; format is the native token-template string; target env required for environment injection. |
| `cache` | Object or scope string; `scope` = `memory` default / `shared` / `none`, `ttl` = `auto` default or positive finite seconds, `skew` nonnegative integer default `60`, optional `key` string. |

Resolved `identity_context`, `routed_target`, source path/name and token-cache
receipts are internal state, never accepted user authority. Auth repair, cache,
tool execution and network retries retain their separate admission/custody.
The default auth format consists of scheme `Bearer`, one space, and the
`{token}` substitution placeholder. It is a template, not a stored token.

## Routing, decorators and predicates

[`config_routing.py`](../src/agent_mcp/config_routing.py) owns `routing`:
only `parameter` (argument identifier), `catalog` (explicit configured target),
and nonempty `targets` map. Put `server`/`auth` inside targets, not the outer
mapping. Target fields are `server`, `auth`, `headers`, `tools`, `decorators`,
`timeout`, `retries`, `idle_timeout`; targets cannot nest routing. Shared values
merge into each target. Target decorators permit only `input_gate`; outer
decorators/tools apply across routing. A routed gate preflight must forward the
same selector through `args_from`.

The executable decorator schemas remain in `validate_config` and
[`decorators`](../src/agent_mcp/decorators):

| Type | Field families |
|---|---|
| `filter` | String lists `allow`/`deny` (empty default), mutually exclusive. |
| `rename` | Optional string `namespace`, `separator` (`__`), `prefix`/`suffix` (empty); `patterns` and `description` native rewrite mappings. |
| `defer` | `mode` = `lazy` default / `eager` / `meta_only`; `expose` string list; `find_tool` (`find_tool`), `execute_tool` (`execute_tool`), `load_tool` (`load_tools`); integer `max_results` (`20`). |
| `code-mode` | `tool` (`run_code`), `apis_tool` (`code_apis`), `find_tool` (`find_tool`), `runtime` (`node`), positive `timeout` (`30`), `interface` (`Tools`), `expose` string list, integer `interface_limit` (`40`)/`max_results` (`20`). |
| `storage` | `backend` = `file` default / `http`; file `dir` or required HTTP `url`, `timeout` (`30`); nonnegative integer `threshold` (`8192`), `max_preview` (`200`), `read_tool` (`read_stream`), `command_timeout` (`30`); `rules` list. Each rule has `tool` and nonempty `outputs`/`inputs` lists whose entries require `path`; preview policies use `count`/`schema` (true) and `head` (`3`). |
| `transform` | `command_timeout` (`30`), `rules` list or inline `tool`, `extract`, `pick`, `drop`, `command`; at least one transformation operation per rule, `pick`/`drop`/`command` are lists. |
| `gate` | Required nonempty `match_tools` list, `preflight.tool`, `allow_when` predicate; optional preflight `args`/`args_from` mappings and `cache` (`per-key`/`none`); `on_deny` (`stub` default / `drop` / `error`), `on_error` (`deny` default / `allow`), optional `stub`. |
| `input_gate` | Required nonempty `match_tools` list and `deny_when` predicate; `on_deny` (`error` default / `stub` / `drop`), `reason` (`denied by input policy`), optional `stub`. Must follow every `code-mode`/`defer`/`storage`/`rename` decorator. |

[`_predicate.py`](../src/agent_mcp/_predicate.py) owns predicate syntax and
validation; it is not Python evaluation or a credential source. Native per-type
validators retain option-requiredness and their native unknown-field policy. Decorator
order is significant and lists replace rather than concatenate.

Configuration declarations may advertise a supported operation; they never
authorize that operation by themselves. Keep generated daemon discovery,
upstream sessions, cached secrets and installation records outside all ordinary
configuration/provenance examples.

### Ordinary timing composition

The actual `load_config` consumer composes only `timeout`, `retries`,
`idle_timeout`; server/auth/headers/tool/decorator/routing inputs remain under
the native selected bridge and machine-override parsers.
The existing qualified peer-launch adapter validates MCP installation
ownership before reading a same-cell Worktrees configuration report.
Launcher-delivered `AGENT_CONFIGURATION_CONTEXT` is read-data, not authority;
explicit invalid reports/bindings fail rather than falling back.

Ordinary order is built-in timings → selected declaration and base then bound
knowledge `.copilot-extensions/agent-mcp/bridges/<native-id>.{yaml,yml,json}` →
the existing native machine override timing fields → that plugin-relative path
under the report's exact `repository_machine_root`. Ordinary overlay files
contain timing fields only; protected or unknown fields fail. Native
numeric coercion and timing validation remain, with shared finite-JSON checks.
Lists/null are not timing schema values. Existing config name/path selection,
extension priority, machine auth override and source-relative command handling
are unchanged. The selected declaration retains native `source_path`; winning
portable documents retain physical source-root provenance.

No optional configuration owner preserves the standalone native bridge.
No installer, credential source or upstream is exercised by this composition.
`AGENT_MCP_BRIDGE_IDLE_TIMEOUT` retains its native fallback only when no selected
or overlay document supplies `idle_timeout`.

An existing legacy owner is read through the MCP runtime's canonical
`scripts/versioned_runtime.py:resolve_python` and shared `ready_owner_prefix`,
with isolated Python and native peer environment. A missing or incomplete
owner has no launch prefix and preserves standalone behavior; no source read
calls a self-provisioning front door. Namespaced reads retain peer receipts.
Invocation environment choices are not fabricated configuration layers:
`configuration_selectors` names a winning native idle-timeout environment
selector, and that overridden leaf is absent from portable-layer provenance.

Predicate nodes contain exactly one combinator (`all`/`any` list or `not` node),
or a nonempty `path` plus leaf operations. Paths use dotted object keys,
`[*]`, `[n]`/`[-n]` only. `in`/`not_in` require lists; `equals`/`not_equals`,
`contains`, `exists` accept native comparison operands;
`matches`/`not_matches` regexes must compile. Unknown operations, malformed
nodes and paths fail loading. Positive operations need a matching resolved
value; negative operations are true when no resolved value violates them.
