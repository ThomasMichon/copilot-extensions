# Vault configuration reference

The qualified Worktrees configuration report is optional: a recognized older
peer without `--configuration` contributes no extra layer, preserving native
files/defaults. Binding/refusal, malformed or unsafe data and wrong-cell reports
still fail. Optional absence never qualifies an unvalidated delivered projection.

[`src/agent_vault/config.py`](../src/agent_vault/config.py) owns the human-authored
selection schema and `resolve_context`;
[`cache_targets.py`](../src/agent_vault/cache_targets.py) validates declared cache
references. Configuration reads do not open a database,
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

## Native default configuration owner

The native machine JSON may set `default_configuration_root` to an existing
absolute source directory containing `.agent-vault.json`. This is an explicit
machine-local choice, not a path inferred from an installer snapshot, another
project, the current user name, or a conventional checkout location.

The executing runtime advertises `native-default-owner-v1`. Qualify that
capability before adopting the setting; a package version or delivered payload
does not prove an independently installed runtime understands it.

An explicit CLI `--configuration-root`, resident source, delivered configuration
context, or nearest caller `.agent-vault.json` remains authoritative. Otherwise,
the native default supplies the configuration source for neutral-directory and
minimal-PATH callers without starting Worktrees or executing Git. Process cwd,
relative native file selectors and database/cache/installation custody do not
change. The selected default source is standalone configuration data; it does
not implicitly bind a knowledge repository or invent a Worktrees context report.
Its file may contain only `enabled`, `vault` and `group`; named database records,
cache keys, service endpoints and protected runtime settings stay native.

Selection provenance is retained through cold activation and resident launch.
Accepted first-use requests also retain an explicit source across a different
queue worker and installer staging; this transient operation metadata is not
a machine default or a fabricated Worktrees configuration report.
Explicit, resident and caller-file roots still resolve their qualified layers
even when their path equals the configured default. Only an actual fallback
uses the standalone-data restriction and bypasses the optional peer.
Retained native defaults revalidate that exact file and its allowed fields on
every read; removing it cannot redirect a resident to an ancestor's file.
Queued cache warming retains that provenance alongside its root and revalidates
it in the worker. Removal or protected-field replacement refuses the work;
an explicitly selected root keeps its existing non-default semantics.

A malformed, missing or unsafe selected default refuses rather than selecting
another database; an unrelated default cannot veto an explicit owner.
Without this setting, existing repository and optional-peer
behavior is unchanged. This explicit native source is a bounded part of
[#12042](https://github.com/ThomasMichon/copilot-extensions/issues/12042), not
automatic discovery or aggregation of all registered project owners.

## Dependency selection

The native installer's `-Profile` / `--profile` selection accepts `base`, `ssh`,
`federated-relay`, and `ssh-federated-relay`. This is installation policy, not
a database selector or portable `.agent-vault.json`
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
Relative cache manifests are the exception: they use the selected repository
configuration's directory, as described below, not the caller's directory.
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
Activation accepts the selected completed runtime's interpreter. For a cold
payload, Windows and POSIX inspect the exact prepared UV-managed interpreter
declared by the packaged `installer-instance-python-version.txt`, through the
shared installer bootstrap selector. Neither reader searches PATH Python,
`py` or system installation directories. The selected interpreter still
qualifies the reader's Python 3.10+ floor; a failed UV lookup cannot use partial
stdout as a candidate.

An unmaterialized source checkout uses the support directory named by its
installer's canonical engine reference when both local bootstrap assets are
absent. Promotion rewrites that reference to the payload-local form; an
incomplete shipped payload cannot borrow support from an ancestor repository.
Partially present local support also refuses rather than mixing locations.

UV and the declared interpreter must already be prepared by their explicit
prerequisite setup owner. Missing support or an unprepared interpreter gives
an explicit prerequisite refusal, never inactive readiness or an ordinary-read
installation. POSIX retains activation before installer acquisition. Selector
overrides remain child-scoped and PATH is unchanged. Windows retains its
headless captured-process helper and UTF-8 UV output. Explicit runtime Python
remains authoritative without rediscovery. The canonical installation-context
owner retains its independent prerequisite and admission contract; reader
qualification alone is not full front-door or installed-host qualification.

The POSIX session bootstrap uses that same prepared selection before reading
payload/deployment metadata or source/profile readiness. It reuses the result
for activation and profile inspection within the call, instead of discovering
another interpreter. Missing prerequisites refuse visibly without a driver
handoff or installation; the hook still emits its session-start JSON. Native
killswitch and installation-cell exits precede interpreter selection.

Explicit POSIX setup uses the same prepared managed binding for its bootstrap
and construction readers, including dependency-profile setup. Missing UV or an
unprepared declared interpreter refuses visibly rather than discovering Python
on PATH. These reads do not install Python or create a candidate runtime.

### Activation performance

The legacy session bootstrap reads activation once on every eligible session
start, before checking current runtime metadata; inactive scope therefore still
needs the read. Killswitch and cell-context exits precede it. Cold selection adds
UV version qualification and prepared managed lookup before the physical-base
probe and configuration-reader subprocess. The POSIX selector also reads its
small request using shell utilities. An unmaterialized checkout also reads its
own installer to locate the declared canonical support. The read itself does no network,
installation, repository history or full-tree work; optional configuration-owner composition retains its
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
Historical ambient-reader Windows/Python 3.14 samples were 9.078/5.271s for absent runtime
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
| `cache_entries` | Optional array, default empty. Each item is a nonempty entry string (password field) or a two-string `[entry, field]` pair. Portable allowlist references, not credential values or cache placement. |
| `cache_manifests` | Optional array of nonempty manifest-path strings, default empty. Paths belong to their declaring configuration layer. |
| `cache_maintenance` | Optional strict `local` or `selected-service`, default `local`. Selects the maintenance access owner, not database, cache/key, service or endpoint placement. Uses ordinary preference precedence. |
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
this is not a generic deep-map merge. Cache-reference arrays have the separate
strict schema below; null is not an empty allowlist or maintenance-owner choice. Unknown fields
are retained in loaded JSON but do not affect this selector. There is no schema
version field or automatic file rewrite on read. `save_global_config` is a
separate explicit writer.

### Declared cache targets

For example, a selected repository can declare:

```json
{
  "cache_entries": ["/Root Account", ["Service", "username"]],
  "cache_manifests": ["config/cache.conf"]
}
```

These are references only. `/Root Account` means an entry at the database root;
`Service` uses the selected default group, and an already-qualified path retains
its group. Standard field aliases use the existing credential-field owner.
Collection preserves root anchors until the backend/cache identity boundary;
it never merges a root subject with its default-group namesake.
Absent or empty declarations do not load cache-command or manifest helpers during
cold activation. Declared policy is validated when present, not silently ignored.

Each manifest uses the existing `entry | field` format. Configured manifest
paths expand native `~`/environment references and resolve against the declaring
file's directory before ordinary composition. Arrays replace atomically:
extension and repository/base, bound knowledge, native machine, then qualified
repository+machine; an empty higher-priority array intentionally clears that
field. Without an optional configuration owner, repository values override
extension values, which override global values, matching native selection.
Extension-relative manifest paths use the selected discovery directory.
The native vault registry and cache/key/service locations remain independent.

Population, verification and status share explicit entries, an explicit manifest,
the declared entries/manifests, then extension targets, deduping entry/field pairs.
A relative CLI `--manifest` is anchored to the selected repository configuration
directory, or its discovery directory when no file exists; an absolute manifest
keeps its native path. No other relative input/output selector changes meaning.
Malformed declarations and unreadable manifests fail without substituting
extension targets or selecting another context. `cache-status --json` adds
`configured_target_count`; this count is not proof that those targets are cached.
In default `local` mode, status composes this policy metadata with the native cache owner's
status; it does not change recovery or protected-store behavior.

Resolving declarations does not enable cache, unlock a database or change
protected-storage custody. No-argument population/verification use those
declarations; population's separate prompting/service/cache rules still apply.
The selected resident also
uses the declarations for [bounded unlock warming](cache-warming.md).
The executing CLI advertises `repository-cache-targets-v1` in its existing
invocation contract; the running service's full ping separately advertises that
policy in `cache_warm.target_policy`. Old clients ignore these additive fields.
An older reader may ignore new configuration keys: that is not enforcement.
Adopters requiring declared-target semantics must qualify both selected CLI and
resident support before activation or after rollback; no matching package version
or installed capability substitutes for the running service's evidence.

Set `cache_maintenance: "selected-service"` to use the existing running service's
protected records for population, verification and status without reading or
writing the caller's cache/key. The selected CLI must advertise
`service-cache-maintenance-v1`, and the running service must advertise the
[maintenance contract](cache-maintenance.md). An old reader ignoring this
preference does not enforce it. Qualify both executing sides before activation
and after rollback. This preference neither starts a service nor unlocks a vault;
absent or explicit `local` preserves the previous CLI behavior.

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

The legacy global shortcut binds `AGENT_VAULT_HOME` to its addressed runtime
before the prepared-runtime fast path, matching its cold dispatcher. An enabled
local cache therefore defaults to that runtime's `cache` directory, not the
global configuration directory. An explicit `AGENT_VAULT_CACHE_DIR` still wins;
the shortcut does not enable caching or move existing cache data.

`AGENT_VAULT_BIND_PORT` supplies a daemon-start default in `0..65535`, where
`0` requests an OS-assigned ephemeral listener. It does not change client
discovery or the strictly validated client `port`. An explicit start argument
or selected client port takes precedence; an invalid bind default refuses
before launching a daemon. Launchers needing ephemeral startup should use this
separate default rather than injecting an unqualified client port of `0`.
The selected executable advertises support as `daemon-bind-default-v1` in
`invocation-contract --json`. A launcher requiring this behavior must inspect
that runtime's capability before migrating; older runtimes ignore the new
environment default and are not compatible providers. Deploy the provider
first, then the launcher. Roll back the launcher and provider together, or
refuse a missing capability instead of silently reverting to a fixed listener.
Daemon children still use the existing `--tcp-port` argument, including an
explicit `0`; no new daemon-side protocol or live-service metadata is required.
The shared generation-start boundary also applies and validates the default
before taking custody or binding endpoints, covering direct service invocations
and selected-generation startup as well as CLI-triggered startup.

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
composes `vault`, `group`, boolean `enabled`, the maintenance access owner and cache-reference arrays:
built-in unset selections and
inactive runtime default → native extension
and selected repository data plus base then bound knowledge `.agent-vault.json`
→ existing global `default_vault`/`group` (`vault_group` compatibility alias)
→ `.agent-vault.json` beneath the owner's exact `repository_machine_root`.
Knowledge and repository+machine files may contain selection, activation,
maintenance-access and cache-reference fields only; database, port, registry, cache placement/protection,
endpoint and KEK fields refuse portable override.
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
