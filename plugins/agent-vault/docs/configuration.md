# Vault configuration reference

The qualified Worktrees configuration report is optional: a recognized older
peer without `--configuration` contributes no extra layer, preserving native
files/defaults. Binding/refusal, malformed or unsafe data and wrong-cell reports
still fail. Optional absence never qualifies an unvalidated delivered projection.

[`src/agent_vault/config.py`](../src/agent_vault/config.py) owns the human-authored
JSON schema and `resolve_context`; configuration reads do not open a database,
unlock a vault, start a daemon, fetch a credential, or install anything.

## Sources and selection

The global file is selected by `AGENT_VAULT_CONFIG`, otherwise
`%APPDATA%/agent-vault/config.json` on Windows or
`${XDG_CONFIG_HOME:-~/.config}/agent-vault/config.json` elsewhere.
`_discover_repo_config` walks the calling directory's parents for the first
`.agent-vault.json`. Registered extension configuration is collected separately
by `extensions.py`; it is not a knowledge-binding or repository-identity engine.
Missing files contribute nothing. Strict readers refuse malformed-present JSON,
non-object documents and invalid known fields rather than using defaults.
Non-strict legacy reads tolerate malformed configuration as empty.

## Runtime activation

`enabled` is an optional strict boolean, default **false**. Plugin enablement
loads the capability; only effective `enabled: true` opts this repository into
runtime bootstrap, installation/update participation and ordinary command
provisioning. For example, the repository or its qualified bound knowledge
repository can contain:

```json
{"enabled": true}
```

Activation uses the same ordinary composition order as selection: default false
then repository/base and bound knowledge `.agent-vault.json`, native machine
configuration, and the qualified repository+machine file. Standalone operation
without Worktrees uses repository then native machine `enabled`; an unavailable
optional owner does not become a mandatory dependency. Existing vault records,
database selectors, environment variables and plugin visibility do not imply
opt-in. Strings, numbers and null are not booleans. Malformed files, unsafe
portable overrides, unresolved bindings and wrong-cell reports refuse rather
than silently returning inactive.
Activation does not load extension configuration providers or treat invocation
database/credential environment variables as an opt-in declaration.

The payload reads activation with its bundled configuration SDK before requiring
a Vault runtime or backend. Inactive readiness is `configuration-empty`, not a
service-health claim. Configured readiness retains strict database and service
checks; it does not install, start or unlock anything. The hook's 30-second
budget accommodates the existing configuration owner's bounded 20-second read.
Installer re-execution retains the invoking project through the existing
`COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT` context, not its staging directory.
Relative native configuration-file selectors resolve against that invoking
project, even after the installer self-stages in HOME. The Windows no-task
start fallback also executes the CLI from the preserved project.
Activation accepts the selected completed runtime's interpreter and uses
ambient Python discovery only for a cold payload. This does not bypass the
canonical installation-context owner's independent bootstrap prerequisites;
its current Windows front door still requires Python on PATH before resolving
an installation. The POSIX payload control qualifies the full no-global-Python
path; Windows separately qualifies the activation reader with owned Python.

Removing opt-in or setting `enabled: false` withdraws this scope's automatic
participation; it does not stop a shared service, delete data, uninstall
KeePassXC or migrate credentials. Explicit stop/lock operations and validated
pinned identity-cache requests retain their independent native custody and do
not gain provisioning authority. Native database/registry/service placement
remains machine-local; portable activation carries no secrets or database paths.
Provisioned management commands also check current activation before starting
a resident; an explicit `start` checks before probing a backend. Payload and
readiness dispatch use the validated selected runtime/cell, including automatic
cell selection without inherited routing variables. Queued provisioning retains
the accepted credential-free configuration-file and native configuration-root
selectors rather than substituting the worker's environment.

Native active-vault precedence is `AGENT_VAULT` → repository `vault` →
extension `vault` → global `default_vault` → the sole registered vault → none.
Explicit environment selections are invocation selectors, not portable layers.
An ordinary selection must not overwrite the machine-local vault registry or
database, credential, installation, cache, KEK, or rendezvous custody.

## Human-authored field families

| Field | Type/default and scope |
|---|---|
| `enabled` | Optional strict boolean, default false; runtime participation opt-in. Allowed in portable repository/knowledge and native machine configuration. Not inferred from database configuration. |
| `vaults` | Optional global object keyed by nonempty vault names; each value is an object with required nonempty `kpdb`. Registry records are machine-local custody, not ordinary portable preferences. |
| `default_vault` | Optional nonempty string naming a member of that file's `vaults`; strict validation refuses a missing registration. |
| `vault` | Optional nonempty string selecting a registered vault in repository/extension settings; `AGENT_VAULT` wins. |
| `kpdb` | Optional nonempty database-path string (required inside a named record). `KPDB` → repo → extension → selected named record → legacy global flat value → none. |
| `group` | Optional nonempty string; `VAULT_GROUP` → repo → extension → named record → legacy global `group`/`vault_group` → none. Leading/trailing slash/space is removed. |
| `vault_group` | Legacy global alias for `group`, consulted only by the flat fallback. Not a second overriding group layer. |
| `port` | Optional integer or ASCII-digit string in `1..65535`, default `19999`; `AGENT_VAULT_PORT` → repo → extension → named record → legacy global → default. An installation-qualified ephemeral service may use environment `0` under its native contract. |
| `socket_path`, `pid_file`, `log_file` | Legacy global runtime-location fields read by `load_config`: truthy values are converted to string/path; absent/null/empty values use the corresponding import-time environment/platform endpoint default. These are protected runtime locators, not portable selection preferences. |

`_validate_config_data`, `_vault_registry`, `_legacy_flat_config`,
`_pick_vault_name` and `resolve_context` are the canonical schema/selection
sections. Known optional null values behave as absent in this **native selector**;
this is not a generic deep-map merge. Lists have no schema here. Unknown fields
are retained in loaded JSON but do not affect this selector. There is no schema
version field or automatic file rewrite on read. `save_global_config` is a
separate explicit writer.

Database paths expand environment variables and `~`; relative repository paths
use the declaring `.agent-vault.json` directory, global/named paths use the
selected global file's directory. Extension paths retain their native extension
contract. Requests carry absolute paths so daemon CWD cannot reinterpret them.
Reads are fresh per `resolve_context` call; import-time platform endpoint
constants require process restart to change their inherited environment.

## Protected runtime and environment

`AGENT_VAULT_HOME` selects runtime installation home; `AGENT_VAULT_INSTALLATION_ID`,
`AGENT_VAULT_SYSTEMD_UNIT`, `AGENT_VAULT_TASK_NAME` belong to installation
ownership. `AGENT_VAULT_SOCKET`, `AGENT_VAULT_PIPE`, `AGENT_VAULT_PID`,
`AGENT_VAULT_LOG`, `AGENT_VAULT_RUN_DIR`, `AGENT_VAULT_ENDPOINT` select runtime
transport/state paths. They do not become repository preferences.

`AGENT_VAULT_PINNED_CONTEXT` uses the strict `identity_context.py` parser and
installation identity; it additionally requires `AGENT_VAULT_CACHE=1` and an
absolute `AGENT_VAULT_CACHE_DIR`. Pinned database/group/port/source data is an
owner-qualified runtime request, not a user-config schema. Password material,
SSH keys, sealed values, envelope KEKs, persistent cache entries, endpoint tokens
and rendezvous files remain under their existing independent custody rules.
Never include their contents in configuration examples or provenance reports.

SSH-key consumers call `resolve_context(strict=True)` and retain strict
validation regardless of ordinary preference composition.

### Ordinary selection composition

For an admitted configuration context, the real `resolve_context` consumer
composes `vault`, `group` and boolean `enabled`: built-in unset selections and
inactive runtime default → native extension
and selected repository data plus base then bound knowledge `.agent-vault.json`
→ existing global `default_vault`/`group` (`vault_group` compatibility alias)
→ `.agent-vault.json` beneath the owner's exact `repository_machine_root`.
Knowledge and repository+machine files may contain selection/activation fields only;
database, port, registry, cache, endpoint and KEK fields refuse portable override.
Source documents retain physical-root and JSON-pointer provenance.

The native same-cell peer-launch adapter validates Vault ownership before a
Worktrees read. Launcher-delivered configuration reports retain exact identity
and binding validation but do not grant operations. An unavailable optional
owner preserves the existing standalone selector. Pinned key/delegation context
remains a separate early native route and is not rewritten by ordinary values.
Explicit `AGENT_VAULT`/`VAULT_GROUP` invocation choices still win; the chosen
vault's database is resolved through the existing local registry/path parser,
not from portable preference data. Native null-as-absent selection semantics
remain; no generic list union or implicit database open is introduced.

Legacy owner lookup uses the Vault runtime's canonical
`scripts/versioned_runtime.py:resolve_python` and shared `ready_owner_prefix`,
with isolated Python and the native peer environment. An interpreter-shaped
slot without completion evidence has no launch prefix, so an absent/unready
optional owner cannot bootstrap or open a vault. Namespaced peer ownership and
receipt checks remain independent.
Portable provenance covers applied ordinary selections only. Explicit
environment choices and native registry/extension fallbacks retain
`ResolvedVault.sources`; overridden leaves do not incorrectly claim a portable
layer as their winner.
