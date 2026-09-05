# agent-machines architecture

This document records how `agent-machines` works today. Broader prescriptions
live in the repo-root patterns:

- [`install-vs-adopt-boundary`](../../../docs/patterns/install-vs-adopt-boundary.md)
- [`config-schema-migration`](../../../docs/patterns/config-schema-migration.md)
- [`runtime-self-provisioning`](../../../docs/patterns/runtime-self-provisioning.md)

## Runtime provisioning

`hooks.json` registers a `sessionStart` command that runs
`scripts/bootstrap-check.ps1` or `scripts/bootstrap-check.sh` from the installed
plugin payload. The hook reconciles the **runtime**, not machine state:

- no deploy manifest: run `scripts/init.* stamp` to place a self-provisioning
  `agent-machines` binstub on PATH;
- manifest present and version current: exit quickly;
- manifest present but payload version changed: launch `scripts/init.*` in the
  background.

The installers build a versioned venv under `~/.agent-machines/versions/<version>`
and publish the active runtime with `current-version` plus version-pinned
binstubs. Windows uses no junction; POSIX also maintains a `.venv` symlink for
runtime-facing paths. The binstub self-provisions on first use when a stamp has
deferred the venv build.

## Discovery and standalone behavior

Discovery is implemented in `src\agent_machines\discover.py`:

1. Adopted project roots come from `~/.agent-worktrees/projects.yaml`.
2. Their paths are resolved from `~/.agent-worktrees/repos.yaml`.
3. For projects whose committed config declares `stateless` or
   `requires_external_state_root`, the project-local `knowledge_repo` binding
   (falling back to machine-global config) adds that canonically registered
   repository as a required supplemental source. Independently adopted duplicates are collapsed
   case-insensitively under the registry's canonical name; an unresolved active
   binding is an error rather than an empty package set.
   Canonical registration may resolve through an explicit platform path or the
   registry's declared source root; filesystem discovery without a registry
   entry is not accepted.
4. Each repo contributes `*.yaml` / `*.yml` files from
   `.agent-machines/all/` plus `.agent-machines/machines/<machine>/`; package
   gates then apply as an additional filter.

If the registries are missing or unreadable, discovery returns an empty set; the
CLI still runs. `repo_enables_agent_machines()` annotates whether the repo has an
enabled `agent-machines` plugin, but `discover()` does not require enablement
unless called with `require_enable=True`.

Machine-directory and gate matching are case-insensitive. Files in `all/` and
the matching machine directory are independent complete packages and must carry
unique package names; the engine does not cross-file merge them. Partial
overrides stay in the existing package-local `per-machine` block.

The machine directory key is the canonical key from a matching adopted
repository `machines.yaml` entry. The raw `platform.node()` host name (Windows
`%COMPUTERNAME%`) resolves through the entry key, `hostname`, `alias`, and
`display_name`; that accepted identity set applies to package, module, resource,
overlay, and directory gates. Ambiguous matches fail before package loading.
When no topology matches, the raw host remains the standalone identity.

The legacy `.github/machine-state/` directory is read only when the canonical
`.agent-machines/` root is absent. This makes migration atomic per repo and
prevents duplicate settings, resources, or module executions.

`src\agent_machines\layout.py` owns layout diagnosis and migration.
`agent-machines doctor` inspects canonical and legacy locations without
activating modules; `agent-machines migrate --repo ...` is dry-run by default
and moves legacy YAML byte-for-byte into `all/`. Its preflight rejects mixed
layouts, unknown entries, invalid manifests, and destination collisions before
writing. Apply rolls back completed moves if a later filesystem operation
fails. It never guesses machine scoping from package gates.

## Requirement-package schema

`src\agent_machines\manifest.py` implements schema version `4` and retains read
compatibility for versions `1`, `2`, and `3`. Required keys are:

- `schema_version: 1|2|3|4`
- `package: <name>`

Optional keys include `authority`, `gate`, `aliases`, `manage`, `per-machine` /
`per_machine`, `bootstrap-floor` / `bootstrap_floor`, `exclude`, `modules`, and
`resources`. `per-machine.<machine>` is deep-merged onto `manage`; a `null` leaf
unsets a key. Machine overlay keys resolve case-insensitively, matching package
gate semantics; keys are normalized once at load time for constant-time lookup,
and case-duplicate or surrounding-whitespace keys are rejected as ambiguous.

Schema version 4 is the fail-closed boundary for authority metadata. Authority
is an integer from `-1000` through `1000`, defaults to `0` at package level,
and may be overridden by a manage spec, resource, or module. Any authority field
under an older schema is rejected so an older runtime cannot silently ignore
the selection contract. Per-machine overlays remain manage-only: they may
override or remove a manage-spec authority, but do not introduce per-machine
package authority, resources, or modules.

Authority is rejected on `ensure-absent`,
`copilot.settings.plugin-tombstones`,
`copilot.settings.plugin-activation`, and any manage payload containing
`enabledPlugins` or `extraKnownMarketplaces`. Package authority is inherited,
so it is also rejected when the package contains one of those sensitive specs.
This keeps plugin tombstones, activation, marketplace bootstrap, and removal
protections outside authority arbitration.

Schema version 2 is the fail-closed capability boundary for
`enabledPlugins.<plugin>: false` tombstones. A v1 package cannot rely on that
behavior: current validators reject the declaration, and an older exact-v1
runtime rejects a v2 package before any surface is applied.

The exact `copilot.settings.plugin-tombstones` enforce group is the
backward-compatible migration form. It contains only
`enabledPlugins.<plugin>: false` leaves, uses the settings surface's existing
deep merge, and therefore works on older schema-v1 runtimes without replacing
undeclared operator plugins. Current validators reject any other disposition,
key, or value in that group.

The accepted dispositions are:

| Disposition | Current behavior |
| --- | --- |
| `enforce` | Applied by the settings surface as an authoritative merge for declared keys. |
| `ensure-present` | Applied as a union floor for settings, permissions, and trusted folders. In `enabledPlugins`, a declared `false` is an authoritative per-plugin tombstone; `true` remains additive. |
| `capture-only` | Accepted by the schema; no capture implementation in this plugin yet. |
| `ignore` | Default for undeclared/unhandled keys; restore is allowlist-based. |
| `exclude` | Accepted as capture guard data; no capture implementation yet. |
| `prune` | Accepted by the schema; `agent-machines prune` is a placeholder today. |
| `prerequisite-check` | Accepted by the schema; no live prerequisite checker today. |

## Reconcile pipeline

`src\agent_machines\reconcile.py` owns the restore flow:

1. Resolve each package for the target machine.
2. Compute `drift_key` from normalized effective operations (ordered floors,
   authority-resolved enforce values, explicit removals, resources, and
   modules) and `provenance_hash` from the full resolved package union,
   including package authority.
3. Apply Copilot surfaces first.
4. Apply declarative resources second (packages/files; see below).
5. Run repo-local modules third.

`plan`, `validate`, and `restore` default to the adopted project containing CWD
plus its directly required supplemental repository. This relationship-aware
scope is one hop and requires an explicit active binding to a canonically
registered repository. `--repo` selects exactly one physical repository;
`--all-projects` explicitly selects the full machine union. Entering a
supplemental repository directly does not pull its requiring project back into
scope. `agent-machines restore` runs the validator over the selected scope
before both dry-runs and applies. If a required supplement is unavailable or
the validator reports any error, restore refuses to continue.

## Managed Copilot surfaces

The implemented surfaces live in `src\agent_machines\surfaces\`:

| Logical key | File | Behavior |
| --- | --- | --- |
| `copilot.settings` | `~/.copilot/settings.json` | `ensure-present` floors first, then `enforce`; enforce contributions use ascending effective authority and a source/package/manage-key tiebreak, while ensure-present remains an authority-neutral union floor with stable source ordering. Only declared keys are touched. `enabledPlugins.<plugin>: false` tombstones one plugin without replacing the map. |
| `copilot.permissions` | `~/.copilot/permissions-config.json` | Adds declared `tool_approvals` to existing concrete locations resolved from location classes. |
| `copilot.trustedFolders` | `~/.copilot/config.json` | Adds concrete trusted folders while preserving other config keys. |

Writes are dry-run-safe and create backups under `~/.agent-machines/backups/`
before mutation. Location classes are resolved by
`src\agent_machines\locations.py`; `$WORKTREES` applies to worktrees that exist at
restore time.

## Repo-local modules

`src\agent_machines\modules.py` is a generic runner. Module commands are argv
lists executed from the package repo root. Platform blocks are `windows`,
`linux`, and `wsl` with `wsl -> linux` fallback. A module runs during dry-run only
when its platform block declares `dry_run_args`; otherwise it is safely skipped.
Module failures are reported in the result but do not prevent later modules from
running.

The public plugin does not ship OS-mutating modules. Those stay repo-local.
Module authority is reporting-only and uses the explicit
`authority_mode: opaque-additive`: all applicable modules remain additive,
including same-named modules from different packages.

## Machine-local Playwright CLI provisioning

`src\agent_machines\playwright_cli.py` owns a focused,
source-neutral user-home provisioner. The directly callable
`agent-machines provision-playwright-cli` subcommand is also available through
the plugin's existing payload command, so a requirement package can use an
`invocation` module without introducing a second PATH command.

The provisioner resolves `node` and `npm`, then accepts npm's JavaScript CLI
entry point only from a standard package layout physically contained by the
resolved Node installation prefix. This trust rule intentionally does not
require npm itself to live under the user home: npm is part of the detected
runtime prerequisite, while the state it manages is separately constrained to
the selected user prefix. The common POSIX npm command symlink is accepted only
when it resolves to that trusted layout; a symlinked or reparse-point package
root is rejected. All npm operations execute as a direct Node argv; no shell or
batch shim is involved.

The provisioner queries `npm prefix -g`, normalizes the result, and accepts it
only when it is contained by the requested user home. Otherwise it selects
`~/AppData/Roaming/npm` on Windows or `~/.local` on POSIX/WSL without mutating
npm's global configuration, then resolves and revalidates that fallback against
the resolved home. Every subsequent npm operation uses that same explicit
prefix: registry latest lookup, installed package query, install, and
global-root discovery. Dry-run therefore performs a required network read;
inability to obtain registry `latest` is a failure.

A missing package or an installed version different from registry `latest`
plans or applies the prefix-scoped `@latest` install. Apply re-queries the
installed package and requires an exact match to the version observed before
mutation. The expected installed command may be reported from the selected
prefix when it resolves within the prefix and home, but command shims are
informational only and never executed.

The npm-reported package root must remain within both the selected prefix and
user home. It locates the canonical Playwright JavaScript entry point at
`@playwright/cli/playwright-cli.js` and the bundled skill at
`@playwright/cli/skills/playwright-cli`; both are resolved and validated within
that package tree. Before planning or applying registration, every existing
component beneath the resolved home in `~/.agents`, the target skill path,
`~/.playwright`, and the config path is inspected without traversing links;
symlinks, junctions, and other reparse points fail both dry-run and apply.
Skill traversal applies the same rule to every directory and file, and the
bundled tree must remain contained by the validated Playwright package, npm
root, selected prefix, and home. The skill must contain a non-empty `SKILL.md`.
Health compares the complete regular-file tree against
`~/.agents/skills/playwright-cli` by relative path and SHA-256 content, so
missing, extra, empty, unreadable, or byte-different files are stale. Stale
Hashing is incremental and bounded by file count, total bytes, and the shared
provision deadline. Stale state and every package update run
`node <playwright-cli.js> install --skills agents`, then require complete tree
equality. The CLI-created `~/.playwright/cli.config.json` remains reported
workspace state, but browser profiles, credentials, navigation, and product
policy are not owned here.

Default and explicit `--dry-run` modes are read-only. `--apply` verifies the
package, JavaScript entry point, and skill postconditions; a nonzero command or
missing postcondition is a failed result with bounded stdout/stderr evidence.
Each command runs in its own process group/tree. A timeout terminates the
group/tree, waits through the bounded cleanup path, preserves partial output,
and reports exit code `124`, including cleanup errors without replacing the
original timeout. The complete provision operation has a 1500-second deadline;
individual commands are capped at 600 seconds with a reserved cleanup margin,
while the module runner remains capped at 1800 seconds. On Windows a gated
launcher is assigned to a kill-on-close Job Object before it can spawn Node, so
root exit cannot orphan descendants; POSIX retains a new process group and
verifies it is empty after normal or abnormal root exit, escalating from
termination to forced cleanup within bounded waits. Any exact-PID Windows
fallback is bounded and evidence-bearing. The stable JSON
result is schema version 1 and reports
prerequisites, observed prefix/root/package/CLI/skill/config state, planned or
attempted actions, command evidence, and the literal failure.
Unexpected subprocess I/O failures terminate the same contained tree and return
structured exit code `126` evidence.

Requirement invocations use the established exact platform keys `windows`,
`linux`, and `wsl`. The payload shim starts from the package repository because
that is the generic module-runner contract, but Playwright workspace
initialization itself always receives the user home as `cwd`; no repository
content is registered or mutated.

## Declarative resources

`src\agent_machines\resources.py` sits between surfaces (which converge
`~/.copilot/`) and modules (the arbitrary-mutation escape hatch): typed,
identity-bearing declarations of *common* machine state the generic engine can
converge itself, so facts like "this package must be installed and pinned" or
"this config file must exist" move out of opaque per-repo scripts and into
reviewable data. A package declares them under a top-level `resources:` list.

Five types are fully handled:

| Type | Identity | Behavior |
| --- | --- | --- |
| `package` | `(manager, id)` | Install / pin / remove via a package manager (`winget`, `apt`, `pipx`, `uv-tool`, `pip`). |
| `file` | `(path, block)` | Converge a config file: whole-file `enforce`/`ensure-present` (`text`/`json`) or a `managed-block` that owns only a marked block. |
| `registry` | `(key, value-name)` | Converge a Windows registry value via `reg.exe` (typed value/state). |
| `feature` | `(manager, id)` | Enable/disable a Windows optional feature/capability (DISM) or a Linux/WSL unit (`systemctl`), selected by a `manager` field. |
| `power-setting` | `(scheme, subgroup, setting)` | Converge AC/DC indexes through `powercfg`, reactivate only when targeting the active Windows scheme, and verify the stored postcondition. |

Identity for a `file` carries a `block` id (empty for whole-file strategies), so
distinct managed blocks in one file are separate, compatible resources while a
whole-file owner and a block on the same path conflict. `registry` folds key and
value-name case-insensitively and expands hive short names (`HKCU` ->
`HKEY_CURRENT_USER`). Adding a type is a new `ResourceHandler` subclass
registered in `HANDLERS` -- nothing else in the engine changes.

Apply is dry-run-safe throughout: package, registry, and feature operations run
through an injectable runner (default `subprocess`, argv lists only,
`shutil.which` guarded, skipped on unsupported platform/manager/backend), and
file operations reuse the surfaces' atomic backup-before-write helpers. See
`docs/resources.md` for the full schema, the collision rules, and the adopter
guide.

## Conflict validation

`src\agent_machines\validator.py` is detect-not-arbitrate:

- conflicting scalar `enforce` values for the same leaf are errors;
- nested maps under `enforce` are traversed because settings restore deep-merges
  them; their scalar leaves participate in normal conflict detection;
- grouping suffixes such as `copilot.settings.sandbox` normalize to the physical
  `copilot.settings` root, and incompatible scalar/map/list shapes at one path
  are errors;
- setting identity preserves raw JSON path components (a dotted key never aliases
  a nested path), and scalar equality includes the JSON/Python type;
- known union maps (`enabledPlugins`, `extraKnownMarketplaces`) stay opaque and
  retain the collection-shape advisory under `enforce`;
- list/opaque collection leaves under `enforce` are shape advisories because
  they should be `ensure-present` unions;
- explicitly disabling a bootstrap-critical plugin is an error;
- enabled-plugin tombstones under schema v1 are errors;
- the false-only `copilot.settings.plugin-tombstones` enforce group is accepted
  under schema v1 and validated as a distinct backward-compatible contract;
- if any package manages marketplaces, omitting the bootstrap-critical
  `copilot-extensions` marketplace is an error.

For `resources:`, authority is applied at each existing semantic conflict
field rather than filtering whole declarations. Only highest-authority
participants decide package state/version, file format/content/block
state/content, registry state/value/type, feature state, and power AC/DC.
Equal-highest disagreement retains the field's existing error or advisory.
Compatible safety fields still merge from every declaration (`pin` OR and
case-folded `process_guard.names` union), and whole-file versus managed-block
ownership plus managed-block marker disagreement remain hard errors regardless
of authority.

For `copilot.settings*`, conflicting same-shape enforced scalar/collection
leaves use the same highest-authority rule. A valid lower-authority disagreement
emits an informational `authority-supersession` finding; equal-highest value
disagreement retains the existing error. Incompatible settings shapes remain
errors at every authority because declaration ordering cannot safely reconstruct
the effective tree. Disposition classes do not arbitrate against each other: an
`ensure-present` declaration cannot defeat `enforce` because it has more
authority.

The validator reads only manifests, not live `~/.copilot/` state.
Both CLI and library restore paths run it before any surface, resource, or
module mutation; direct callers receive `RestoreValidationError` on errors.
