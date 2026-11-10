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
The Windows installer resolves an explicit relative selector against its original
filesystem invocation directory before activation or staging changes cwd. The
absolute selection is retained for task-only actions and staged child invocations;
an invalid present file never becomes a missing-file/default-policy result.
`_discover_repo_config` walks the calling directory's parents for the first
`.agent-vault.json`. Registered extension configuration is collected separately
by `extensions.py`; it is not a knowledge-binding or repository-identity engine.
Missing files contribute nothing. Strict readers refuse malformed-present JSON,
non-object documents and invalid known fields rather than using defaults.
Non-strict legacy reads tolerate malformed configuration as empty.

Native `http_bindings` selects the opt-in credential-injection policy for
[authenticated HTTP requests](authenticated-http.md); repository preferences
cannot replace that policy. Its complete schema and matching rules live in the
HTTP reference.

## Dependency selection

The native installer's `-Profile base|ssh` / `--profile base|ssh` selection is
installation policy, not a database selector or portable `.agent-vault.json`
field. It is persisted under the selected runtime home as `runtime-profile.json`:

```json
{"schema":"agent-vault.runtime-profile","version":1,"profile":"ssh"}
```

Omitting the option preserves this desired state; with no file, ordinary setup
keeps its existing base behavior. Explicit source-owned driver handoff retains
`AGENT_VAULT_INSTALL_PROFILE` for this installation request. Neither that input
nor the persisted profile enables Vault, selects a database, starts a credential
operation or authorizes enrollment. Malformed/unsupported profile files refuse
rather than being overwritten. The same transaction lock protects selection
and construction. A failed build retains desired state but leaves the active
runtime unchanged.

The deploy manifest's `source.version` remains the package version.
`runtime_profile` and `runtime_version` identify the selected dependency slot.
Bootstrap compares desired and deployed profiles, the exact incoming runtime
generation, its full source receipt and the active marker, not package version
alone. A newer prepared source at the same package version is not skipped,
and a healthy SSH runtime is not repeatedly rebuilt as base.
Start/status use the active runtime even when a later desired update failed.
Prepared-source updates bind the selected target through the existing
`COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET` contract and verify immutable slot
profile/completion evidence before reporting current. Explicit profile drift
is refused before desired-state publication.
See [native profile setup](../README.md#dependency-profiles).

## Windows scheduling is separate from activation

The **global native settings file** (selected above) accepts
`"windows_scheduled_task": "preserve"`, `"enabled"` or `"absent"`. This is
machine deployment policy, not a portable `.agent-vault.json` preference.

`preserve` is the new default: install/update/first-use never create a missing
task and do not rewrite existing/custom registrations. `enabled` explicitly
provisions the logon task through the existing installer. `absent` removes
only a validated task owned by this installation; foreign/ambiguous tasks and
removal failures refuse rather than purge. The selection persists across updates.
Task-query permission/CIM failures or an unavailable ScheduledTasks module also
refuse retirement rather than report absence. Default `preserve` still works
without that module.

Run `scripts\install.ps1 schedule` after changing the setting to reconcile task
policy alone. With `absent`, no daemon is stopped or started, no runtime/state
is deleted, and nothing else is enabled.
Task-only absence reads retained configuration without requiring an interpreter,
even when the incoming payload's version has not been built. Task-only opt-in
resolves the selected installed runtime instead of assuming the incoming slot.
It retains the original project selection across payload staging for both runtime
activation and the service configuration snapshot; a repository-only activation
opt-in remains effective. Task ownership validates the actual PowerShell script
operand, including supported conhost wrapping, never a later forwarded path.
Normal install/update still prepare
the native launcher and ensure the selected service generation independently
of task registration. Without a task, `agent-vault start` retains the existing
on-demand user-mode startup path.

An existing disabled task keeps its independent startup hold: it is not
re-enabled or bypassed by installer startup, and `absent` refuses to retire it until that intent is
deliberately cleared. Runtime `enabled`, maintenance inhibition and `-NoService`
remain independent; do not use client-only `-NoService` to select task absence.
No administrative account, Task Scheduler registration or substitute supervisor
is required by the default scheduling policy.

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
Installed readers resolve the optional legacy configuration owner with the
selected interpreter's retained `.runtime-bootstrap` SDK or the installer's
deployed read-only `bin/resolve-runtime.*` helper when no SDK is retained.
For application-style slots, the retained SDK is beside the `.venv`, not inside it.
They do not require a source-checkout or payload-relative scripts directory.
The same completion-gated owner lookup therefore applies to payload activation
and provisioned readiness/client reads.
Reader failures retain their exception class and up to four function/line
locations, without exception text, configuration values or source paths.
Same-cell configuration reads disable bytecode writes in both peer-launch
interpreter hops; caller cache settings alone do not protect immutable slots.
Windows activation errors also retain the reader's exit code and readiness
state, so an installer refusal remains distinguishable from inactive readiness.
Activation does not load extension configuration providers or treat invocation
database/credential environment variables as an opt-in declaration.

When activation comes from repository or bound-knowledge configuration, invoke
Vault from the adopted repository (including its worktrees/subdirectories).
From a neutral directory such as HOME, use `<repo> vault unlock`; the repository
router supplies `--configuration-root <absolute-checkout>` to Vault. This selects
repository and bound-knowledge discovery without changing the caller directory
or rewriting native environment selectors. The same routing applies to other
Vault verbs, including operations with relative input/output files.
`AGENT_VAULT_CONFIG`, `KPDB`, local/core endpoints, discovery paths and KEK/cache
settings therefore keep their native expansion, symlink and Unix-address-length
semantics. The parent environment and process directory are unchanged.
An unavailable checkout, invalid/duplicate root or unsupported native option
fails without retrying against another context.

The CLI advertises `agent-vault.invocation` version 1 and
`configuration-root-v1` through `invocation-contract --json` in the executing
runtime. Ordinary routing does not add a capability-probe subprocess or infer
support from a package version. It submits one required scoped invocation;
strict parsing accepts the scope or rejects it before command dispatch.
Contract queries are read-only, including with inactive or invalid application
configuration. With no prepared runtime the front door reports an empty
capability list and `runtime_prepared: false`; it never installs a runtime just
to discover its capabilities.
The router inhibits legacy first-use provisioning while the option is unknown.
A supporting payload restores only that router-owned inhibition after validating
the root, while an explicit caller `AGENT_VAULT_NO_SELFPROVISION` remains in force.
Thus an older root-unaware front door/runtime declines the new form without
provisioning in the caller's unrelated context or silently executing a CWD alias.
Update Vault through its normal owner workflow if the option is unsupported.
Existing unscoped CLI syntax and first-use behavior remain supported.
The common configuration-root getter composes an explicit discovery argument,
then this CLI operation's root, then an installed resident-source binding, then
the process directory. Native configuration-file selection stays caller-absolute,
independently of that repository discovery root. This lets resident launchers
retain the selected discovery source without reinterpreting a relative native
configuration-file selector.
At a real first-use constructor handoff, the front door retains this native file
selector as a separate caller-absolute value before the worker changes directory.

A missing repository context is reported separately from malformed
configuration, unresolved bindings and installation errors. This does not make
Worktrees a dependency of standalone Vault installations.

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
Residents retain a separate, owner-resolved configuration source through
`agent_vault.service --configuration-root`. The launcher qualifies and retains
the caller's native discovery directory before switching to state cwd; generated
Scheduled Task and systemd launchers persist that explicit source too.
This keeps worktree/subdirectory configuration distinct from the native owner's
base. Persistent launchers also retain the selected absolute `AGENT_VAULT_CONFIG`
path. Windows discovery capture uses ASCII JSON for Unicode-safe transport;
the dynamic Task launcher probes the selected runtime's argument support and
does not pass the new option to retained older parsers.
Resident reads re-query the native owner from that source rather than treating
the state directory as a repository. The option does not change process cwd,
database custody, activation precedence or strict owner/configuration refusals.
The source is not an activation snapshot: a newly launched resident rechecks
current opt-in before service admission, including revocation after its caller's
check. Read-only ping and explicit stop/export controls retain their own gates.
Activation accepts the selected completed runtime's interpreter and discovers
an existing Python 3.10+ reader only for a cold payload. Windows uses the
canonical native helper's read-only discovery, including later PATH candidates,
non-Store launcher entries/selectors and standard installation roots. Its
ordinary installer caller still requires 3.11; activation does not raise the
Vault reader floor or execute an installer to select Python. POSIX qualifies
the existing `python3`/`python` PATH candidates. Unsupported-only or absent
Python is an explicit prerequisite refusal, never inactive readiness.
Discovery stops at a supported PATH candidate without querying unused launchers.
Its child processes disable Python install manager's `automatic_install`
through `PYTHON_MANAGER_AUTOMATIC_INSTALL=false`; the parent policy is unchanged.
Windows reader execution uses the same headless captured-process helper;
discovery and reads neither install Python nor mutate PATH. Explicit runtime
Python remains authoritative without rediscovery. This does not bypass the
canonical installation-context owner's independent bootstrap prerequisites;
its current Windows front door still requires Python on PATH before resolving
an installation. The POSIX payload control qualifies the full no-global-Python
path; Windows separately qualifies the activation reader with owned Python.

### Activation performance

The legacy session bootstrap reads activation once on every eligible session
start, before checking current runtime metadata; inactive scope therefore still
needs the read. Killswitch and cell-context exits precede it. On Windows with a
supported PATH candidate this adds one qualification subprocess to the existing
configuration-reader subprocess. Launcher/root discovery runs only if PATH
qualification fails. The read itself does no network, installation, repository
history or full-tree work; optional configuration-owner composition retains its
existing bounded peer read. An ordinary runtime dispatcher supplies its selected
completed interpreter, avoiding discovery. Explicit install participation also
performs an activation read. Each call covers one selected Vault installation
and configuration context, not a fleet or enabled-plugin roster scan.

Selection evidence is reused within that call for the reader, not cached across
sessions: executable availability and opt-in/configuration may change. Runtime
metadata, process count and a watchdog do not establish latency acceptance.
The plugin's 30-second hook timeout is unchanged; it is not a claim that the
composed hook meets the performance pattern's two-second target.

The explicitly selected `vault-activation` integration portfolio runs
`test_native_bootstrap_performance_samples`. It observes two real native entry
invocations for each absent-runtime and current-metadata fixture, including
shell startup and cleanup. Its driver is a non-installing stub; current metadata
is not an actual installed/healthy runtime. Reports retain individual monotonic
samples, nearest-rank summaries and errors; two samples cannot qualify tails.
Child CPU/spawn totals, live installed latency, first-OS-boot cache conditions,
visible/model-ready and whole-enabled-hook-stack boundaries remain unavailable,
not zero or passing. Instrumentation stays outside production callbacks.
The initial local Windows/Python 3.14 samples were 9.078/5.271s for absent runtime
and 4.620/3.654s for current metadata. These do not meet a two-second composed-hook
target. Local source SHA and before-change delta were not captured; this is
fixture observation, not installed performance or a regression comparison.
The broader latency target remains with the performance-telemetry objective
[#7935](https://github.com/ThomasMichon/copilot-extensions/issues/7935).

Reproduce in an isolated test lane with:

```text
python tools/run-plugin-tests.py agent-vault --allow-explicit-tiers --test-file tests/test_bootstrap_driver.py -k native_bootstrap_performance_samples --plugin-timeout 180 --timeout 120 --test-timeout 60
```

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
