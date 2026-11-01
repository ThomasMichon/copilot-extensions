# Configuration reference

The executable schemas are [coordinator configuration](../src/agent_dispatch/config.py),
[registrar](../src/agent_dispatch/registrar.py),
[document expansion](../src/agent_dispatch/registrar_document.py), and
[reusable worker identities](../src/agent_dispatch/worker_identities.py).
Worked authoring examples remain in [repository issue loops](repository-issue-loop.md),
[pool instances](pool-instances.md), and [worker instances](worker-instances.md).
Do not maintain a second independently evolving registrar schema.

## Coordinator environment (not portable authority)

| Variables (`AGENT_DISPATCH_` prefix) | Type / default |
|---|---|
| `HOST`, `PORT` | String/integer; `127.0.0.1` / 9847 |
| `DB` | Path; installation-owned `tasks.db` |
| `TOKEN`, `CONTROL_TOKEN` | Secret string; absent; never source portable YAML |
| `CONTROL_TOKEN_COMMAND`, `SHARED_TOKEN_COMMAND`, `SHARED_CONTROL_TOKEN_COMMAND` | On-demand credential command; never fetched by unrelated configuration reads |
| `GC_INTERVAL` | Number; 60 seconds; 0 disables; canonical name |
| `SWEEP_INTERVAL` | Deprecated alias; used only when `GC_INTERVAL` absent/empty |
| `ORPHAN_GRACE` | Number; 86400 seconds |
| `HANDOFF_FALLBACK` | Truthy `1/true/yes/on`; false by default; independent launch authority remains required |
| `HANDOFF_FALLBACK_GRACE` | Number; 3600 seconds |
| `URL`, `SHARED_URL` | URL; native local discovery / absent shared |
| `SHARED_TOKEN`, `SHARED_CONTROL_TOKEN` | Secret string; absent; independent shared authority |
| `TASK_AUTHORITY` | `central` / `local`; unset retains native routing; no cross-queue fallback for explicit authority |
| `PRODUCER_CAPABILITY`, `PRODUCER_CAPABILITY_COMMAND` | Capability/fetch selector; invocation-owned, never portable authority |
| `NO_AUTOSTART` | Any nonempty value disables lazy start |
| `RUN_DIR`, `ROUTING_DIR`, `ENDPOINT`, `OVERRIDES` | Owner runtime/discovery paths/selectors; native defaults; not preference tiers |
| `WSL_WINDOWS_CLIENT` | Truthy opt-in; false; otherwise each execution environment owns its coordinator |
| `REGISTRAR_DIR` | Path; installation-owned `registrar` directory |

Explicit CLI/environment selection remains outside composition. No config read
starts the coordinator, fetches a token, activates producer scope or grants
network bind permission. Wildcard bind still requires token admission.

## Human-authored registrar and identity sources

Canonical repository directories are
`.copilot-extensions/agent-dispatch/{registrar,identities}`, with
`.agent-dispatch/{registrar,identities}` legacy fallback and explicit
`marketplaces/<marketplace-id>/{registrar,identities}` overlay.
Registrar files are YAML (`.yaml`, `.yml`) or JSON; identity files are
`<name>.identity.md` with YAML frontmatter and a nonempty Markdown body.
Packaged identities are builtin reusable configuration, not generated state.
Machine-wide authored documents use `<runtime>/config/{registrar,identities}`;
they are separate from pointer registries and active instance envelopes.
The owner-resolved `repository_machine_root` uses native canonical/legacy
surfaces. Without a separate root, explicit local companions are
`.copilot-extensions/agent-dispatch/{registrar.local,identities.local}`.

Ordinary configuration precedence is defaults → selected base repository →
bound knowledge graft → machine → exact repository+machine. Qualified
repository/logical-machine/installation-cell context comes from the Worktrees
owner, not pointer names, hostnames or filter dimensions. Explicit unresolved
binding is an error. Human-authored identities and reusable worker profiles
participate; registration indexes, coordinator DB rows, active user-instance
envelopes and installed-generation receipts do not.
`AGENT_CONFIGURATION_CONTEXT` is the native launcher's version-1 read-data
projection, not a grant. Namespaced lookup uses the existing ready-only
same-cell boundary; optional legacy owner absence retains native local/
packaged defaults without a PATH, provisioning or service-start fallback.

Native strict unknown-key rejection stays in force. Mappings merge deeply,
lists/null/scalars replace atomically; `extends` retains the existing native
recipe reference, alias, supplier-path and execution-admission contracts.
Only ordinary `supervised-lane` fields merge across authored lane documents.
Recipe/resource kinds and documents with `extends` remain atomic whole native
documents, preserving supplier and admission semantics; identity rules are
likewise atomic native Markdown documents rather than a capability-map merge.
Reading/discovery is not registration or activation. Cross-source duplicate
registration custody and supervisor scan authority remain native vetoes.

## Registrar fields

Optional fields use native defaults below. `name` is required nonempty string.
Nested executable `spec` schemas are owned by the corresponding kind's parser:
follow [issue-loop reference](repository-issue-loop.md), [reviewer-loop reference](repository-issue-loop-adoption.md),
and [worker configuration reference](worker-instances.md), not generic coercion.

| Fields | Type / default |
|---|---|
| `name`, `title`, `description`, `owner` | String; name required; others optional presentation/provenance |
| `labels`, `repos` | String list empty / string `all` or one lane |
| `concurrency`, `max_active_processes` | Positive integer; 1; aliases must agree when both present |
| `priority` | Integer; 0; only worker pools may set it |
| `interval`, `reactive_interval` | Numbers; 30 / 2 seconds; minimum 1 / 0.1 |
| `verify_timeout` | Integer; 0 |
| `max_attempts`, `label_max_attempts` | Integer 3 / label→integer map empty; minimum 0 |
| `heartbeat`, `reactive` | Strict booleans; true / false; native parser currently normalizes reactive scheduling off |
| `body.type`, `body.agent`, `body.charter` | `headless`/`embody` (headless); `task-worker`; string/null charter |
| `body.headless_labels`, `.cli_labels`, `.disposable_cli_labels`, `.idle_nudge_exempt_labels`, `.steering_disallowed_labels` | String lists; empty; native incompatible-body/charter checks |
| `body.no_pair` | Boolean false; rejected with fleet |
| `fleet.pool`, `.origin`, `.headless` | String list empty / string-null / boolean false |
| `filters.permit`, `.reject` | Dimension→string-list mappings; dimensions `repo`, `machine`, `env`, `role`, `worktree`, `task-type`, `capabilities` |
| `evaluator` | String/null; null; native evaluator reference and kind-owned schema |
| `runtime_generation`, `transition_group` | String/null; only attributed plugin-companion declarations may set these |
| `kind`, `spec` | Native kind (`supervised-lane` default) and mapping (empty); kind-specific schema and authority checks |
| `extends` | Native recipe reference(s); document expander owns inheritance, loops and supplier provenance |

Filter alias `task_type` normalizes to `task-type`; capabilities match as sets
and scalar dimensions by membership. A machine filter is *not* the configuration
machine tier. Null clearing is allowed only where the native field accepts it;
required name/body/rules cannot be null. Unknown nested keys refuse.

Identity frontmatter requires `name: <nonempty string>`; `description` is string,
default empty; body rules are required nonempty Markdown. Rules and descriptions
are reusable configuration, not transport/worktree identity or grants.
The native identity parser ignores other frontmatter metadata; registrar
strict unknown-key rejection does not change that Markdown contract.
Native introspection and runtime must share candidate selection. Relative
recipe/script/identity paths retain the defining supplier, not final execution
cwd. No loaded source acquires repository execution authority.

Reads are live; supervisor reconciliation and explicit managed activation own
effects and reload timing. Portable documents exclude secrets, tokens, queue
state, pointers/indexes, overrides, endpoints, active worker instances, claim
receipts and installed runtime generations. Migration/relocation uses existing
owner commands only, never a read-time rewrite.
