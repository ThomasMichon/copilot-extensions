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
| `RELATIONSHIP_ADMISSION`, `RELATIONSHIP_AUTHORITY` | Trusted `module:factory` and explicit `local` / `central` queue authority; both absent by default |
| `PRODUCER_CAPABILITY`, `PRODUCER_CAPABILITY_COMMAND` | Capability/fetch selector; invocation-owned, never portable authority |
| `NO_AUTOSTART` | Any nonempty value disables lazy start |
| `RUN_DIR`, `ROUTING_DIR`, `ENDPOINT`, `OVERRIDES` | Owner runtime/discovery paths/selectors; native defaults; not preference tiers |
| `WSL_WINDOWS_CLIENT` | Truthy opt-in; false; otherwise each execution environment owns its coordinator |
| `REGISTRAR_DIR` | Path; installation-owned `registrar` directory |

Explicit CLI/environment selection remains outside composition. No config read
starts the coordinator, fetches a token, activates producer scope or grants
network bind permission. Wildcard bind still requires token admission.

### Native local standing admission

With both relationship selectors absent, ordinary coordinator construction
supplies native local standing admission. It is assembled on demand, without
a user factory or another enable flag; construction does not discover sources,
start siblings, reserve a task or launch an agent. The finite-task relationship
writer and authority remain unset, preserving ordinary finite claims and pools.
Attachment capability describes the available assembly, not current readiness
of a particular workspace or sibling.

An explicit standing reservation must match one enabled declaration from a
complete trusted registrar source. The actual host must match the workspace.
The assembly checks the original queue event, selected Worktrees CLI and actual
resident, exact workspace incarnation and Bridge's original keyed writer
journal. Missing/changed participants, unqualified historical agents and
unsupported siblings retain the original request rather than receiving
permissive callbacks or a replacement worker.

For acceptance, `materialization_ref` is a file path relative to the trusted
source root. Absolute, traversing and root-escaping paths refuse. The current
enabled `agent-roles` payload supplies its existing materialization inspector;
host-local `OVERRIDES` policy and native head/conclusion checks remain mandatory.
The original native journal identity is retained on the assignment event, not
in a second registry. Source withdrawal closes new admission, but cancellation
and exact-conclusion release still settle through those original owners.

### Explicit alternate relationship admission

The ordinary `serve` construction accepts an explicitly configured, trusted
zero-argument admission factory. It must return
`agent_dispatch.assignment_admission.RelationshipAdmission` with both
`admit_workspace(workspace, writer_identity)` and
`admit_request(workspace, writer_identity, original_request)`. The coordinator
constructs the existing selected Worktrees adapter, retaining its command and
reobserving the actual resident before each relationship call. The queue keeps
its independent durable request/prepared-reservation checks and post-I/O CAS.
The configured queue authority is separate from the client's `TASK_AUTHORITY`
routing default; it must name the actual original queue.

This is executable local service configuration, not portable registrar content,
a task-supplied receipt or a public API that accepts factories. Configuration
reads do not import the factory; only coordinator construction does. Incomplete
selection, a bad factory, unavailable selected writer or construction failure
refuses startup before socket/routing publication, without a legacy/no-writer
fallback. The ordinary `serve` CLI forwards both selectors unchanged.
With both selectors absent, the native standing assembly above is used; it
does not opt finite tasks into governed relationship allocation.
Source startup's configuration identity includes both selectors, so it cannot
adopt a same-build incumbent with a different admission selection.

An explicitly selected factory replaces the native standing assembly. Its actual
callbacks must independently qualify the original workspace, Bridge's future
keyed creation or original continuation journal, and every participating ledger
writer. Missing, changed or unaccounted participants must refuse before effects.
An installed CLI/resident observation alone does not close that cohort.
The factory does not enable pool allocation, invent an ordinary writer
requirement, retrofit a historical agent or replace the existing custody owners.
Ordinary running acceptance remains separate from installing this source
capability; do not install a no-op callback to get past an unavailable owner.

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
Caller qualification precedes delivered-report parsing. A genuinely absent
same-cell Worktrees peer, or its recognized older `--configuration` option
rejection, retains native files/defaults. Wrong-cell reports, explicit binding
refusals and ambiguous readiness/read errors still block rather than degrading
to defaults.

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
## Windows installation controls

The optional installation-root `installation.json` separates scheduling
(`preserve`, `enabled`, `absent`) from coordinator/supervisor start intent.
See [Windows scheduling and readiness](windows-scheduling.md) for its bounded
schema, task-only retirement and downlevel support limits.
