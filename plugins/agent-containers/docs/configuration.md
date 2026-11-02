# Configuration reference

The executable schema is [`ContainersConfig` and `FleetConfig`](../src/agent_containers/config.py);
[schema migrations](../src/agent_containers/config_migrations.py) run in memory
on reads, never rewriting repository sources. The [restricted recipe](../templates/ci-repair/README.md)
and README examples are the maintained authoring examples.

## Files and selection

`AGENT_CONTAINERS_CONFIG` explicitly selects one YAML document (including its
native missing-file/default behavior). Otherwise the canonical repository file
is `.copilot-extensions/agent-containers/containers.yaml`, with root
`containers.yaml` as a legacy fallback, not an additional fragment.
Machine-local configuration is `<runtime>/containers.yaml`.
The owner-resolved `repository_machine_root` uses the same native canonical/
legacy filenames, scoped to that exact repository and logical execution machine.
If no separate local root is supplied, the selected base's
`.copilot-extensions/agent-containers/containers.local.yaml` (or root
`containers.local.yaml`) is the explicit local companion.
`AGENT_CONTAINERS_HOME` chooses the runtime root; `AGENT_CONTAINERS_STATE_DIR`
chooses mutable coordination state independently. These environment selectors
are not ordinary preference layers.

Ordinary preference precedence is builtins → selected base repository → bound
knowledge repository → machine → exact repository+machine override. Knowledge
is a graft, not a fallback conditioned on absent harness config. Binding and
qualified repository/logical-machine/cell identities belong to Worktrees;
malformed explicit binding is not optional absence. No configuration read
installs, launches, claims a resource or fetches credentials.
Native launchers may deliver the version-1 owner report as
`AGENT_CONFIGURATION_CONTEXT`; otherwise namespaced reads use the existing
ready-only same-cell peer boundary. A standalone read without that optional
owner projection retains native local files/defaults and never invokes an
ambient self-provisioning Worktrees front door.
Fresh native caller qualification precedes every delivered report. A genuinely
absent same-cell Worktrees sibling or recognized older `--configuration`
rejection retains native files/defaults. Present invalid peers (including exit
126), binding refusals, foreign-cell data and ambiguous inspection/execution
errors remain blocking; they are never treated as optional absence.

Mappings merge recursively; lists, scalars and null replace atomically. `{}` does
not erase a map. Native parsing still determines whether null is meaningful for
a field; it is not permission to skip security validation. Unknown keys retain
the native parser's ignored-key behavior, except independently strict protected
fields. Reads are live; running containers do not change posture merely because
a file changed. A later admitted lifecycle operation must reconcile them.

## Plugin-shipped fleet fragments

An active plugin may declare `"containersConfig": "<relative path>"` in its
`plugin.json`. Such fragments form the lowest layer, **beneath the builtins and
every tier above** (or beneath the single `AGENT_CONTAINERS_CONFIG` /
compatibility file when no owner context applies). Activation is resolved with
the canonical identity-verified resolver for the invoking repository: global
activation plus that checkout's own settings, which can also disable a globally
enabled plugin. Without an invoking checkout only global activation applies.

- Fragments may define `fleets:` only (`schema_version` is accepted); other
  top-level keys are ignored with a warning.
- Operator values override a shipped fleet per key; nested mappings merge per
  key. Unlike ordinary tier composition, an explicit `null` over a shipped
  value removes it, so the field falls back to its default/global value.
- The plugin whose `name@marketplace` sorts first owns a duplicated fleet name;
  the other definition is ignored as a whole, with a warning.
- Relative `devcontainer_path`, `host_workspace_path` and `host_home_path`
  (and `devcontainer_config` without a `devcontainer_path`) resolve against the
  fragment's directory.
- Unreadable, invalid, escaping or cyclic (self-referencing YAML anchor)
  fragments are skipped with a warning, including in strict reads.

Contributions extend `configuration_provenance` with `tier: plugin` owners
(`source` names the plugin root and fragment path), and contributing fragment
IDs (`plugin:<name>@<marketplace>`) are prepended to `configuration_layers`.
`agent-containers config-sources [--json]` prints the layers, fragments and
per-pointer owners; `fleet --json` reports each member's `fleet_source`.

## Top-level YAML fields

All fields are optional; defaults are below. `fleets` is a name→mapping.

| Fields | Type / default |
|---|---|
| `schema_version` | Migration-owned integer; omitted legacy documents migrate in memory |
| `exec_user`, `workspace_folder` | String; `vscode`, `/workspace` |
| `acp_command` | String/null; generated `copilot --acp --stdio --allow-all --experimental` in workspace |
| `forward_gh_token` | Native boolean coercion; true; restricted fleets always refuse forwarding |
| `copilot_cli_account` | String/null; null; host-Copilot launch requires a separately selected account |
| `copilot_auth_mode` | `host-copilot` (default) or explicit `legacy`; migrated legacy documents retain explicit legacy mode |
| `image_prefixes` | List of strings; `[vsc-]`; replaces discovery prefixes |
| `relay.enabled`, `relay.host`, `relay.port` | Boolean/string/integer; true / `host.docker.internal` / 9857 |
| `relay.azure_resources` | String list; `["*"]`; eligibility still requires independent relay admission |
| `relay.deploy_ado` | Deprecated, ignored with warning |
| `dotfiles.repo`, `harness.repo` | Host path string; absent/null disables respective copy |
| `dotfiles.target` | Remote string; `/workspaces/.codespaces/.persistedshare/dotfiles` |
| `dotfiles.install_command`, `harness.install_command` | String/null; `bash install.sh` / null; null or empty skips command |
| `rescue.max_member_bytes`, `rescue.max_capture_bytes`, `rescue.max_total_bytes` | Positive integers; 64 MiB / 256 MiB / 1 GiB; member ≤ capture ≤ total |
| `rescue.retain_per_container` | Positive integer; 3 |
| `rescue.operation_timeout_seconds` | Positive finite number; 600 |

## `fleets.<name>`

No alternate fleet schema or implicit resource enrollment is introduced.

| Fields | Type / default / constraints |
|---|---|
| `repo` | String; empty |
| `devcontainer_path`, `devcontainer_config`, `image` | String/null; null; config relative to devcontainer path under native resolution |
| `size`, `name_prefix` | Integer 1 / string-null (fleet name) |
| `workspace_folder`, `exec_user`, `acp_command` | String/null; inherit global values |
| `security_profile` | `trusted` (default) or `restricted`; unknown profile refused |
| `lifecycle_owner` | Native owner enum; `agent-containers`; external ownership never transfers on read |
| `dispatch_only` | Strict boolean false; quoted booleans refused |
| `network`, `memory`, `workspace_size`, `home_size` | String/null; null; restricted native defaults/validation apply |
| `cpus`, `pids_limit` | Number/integer-null; null; restricted defaults 2 / 256 |
| `environment` | String→string map; empty; secret-shaped names refused by protected validation |
| `native_inference` | Optional mapping with exactly `endpoint` and `reference` strings; HTTP(S) single-label non-localhost broker origin, nonzero valid port; `openshell:resolve:env:<NAME>` public reference; see [parser](../src/agent_containers/inference.py); credential-free restricted admission stays mandatory |
| `forward_gh_token`, `relay_enabled` | Boolean/null; inherit; restricted effective values always false |
| `copilot_cli_account` | String/null; inherit; protected launch account checks remain |
| `copilot_auth_mode` | Optional `host-copilot` or `legacy`; absent inherits, explicit null refuses; protected launch mode checks remain |
| `code_model` | String; `clone`; native restricted validation applies |
| `host_workspace_path`, `host_home_path`, `home_folder` | String/null; null; only trusted image-backed fleets; home pair required |
| `systemd_capable` | Strict boolean false; only trusted image-backed fleets |

Host source paths retain their declaring-source ownership; remote paths and
commands are not host paths. `devcontainer_config` remains relative to
`devcontainer_path`. Explicit absolute/native `~` paths retain owner semantics,
not composer expansion. Do not put secrets, generated leases, logs, transport
receipts, installed generations or evidence into portable preferences.
Restricted network/resource/mount/environment validation, inference admission,
installation-cell ownership and lifecycle custody veto composed values exactly
as they veto one-file configuration.
