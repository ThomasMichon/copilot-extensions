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

1. Candidate repos come from `~/.agent-worktrees/projects.yaml`.
2. Their paths are resolved from `~/.agent-worktrees/repos.yaml`.
3. Each repo contributes `*.yaml` / `*.yml` files under
   `.github/machine-state/` whose package gate applies to the target machine.

If the registries are missing or unreadable, discovery returns an empty set; the
CLI still runs. `repo_enables_agent_machines()` annotates whether the repo has an
enabled `agent-machines` plugin, but `discover()` does not require enablement
unless called with `require_enable=True`.

Package gates are case-insensitive. Module gates and `per-machine` overlay keys
are exact string matches today.

## Requirement-package schema

`src\agent_machines\manifest.py` parses schema version `1` packages. Required
keys are:

- `schema_version: 1`
- `package: <name>`

Optional keys include `gate`, `aliases`, `manage`, `per-machine` /
`per_machine`, `bootstrap-floor` / `bootstrap_floor`, `exclude`, `modules`, and
`resources`. `per-machine.<machine>` is deep-merged onto `manage`; a `null` leaf
unsets a key.

The accepted dispositions are:

| Disposition | Current behavior |
| --- | --- |
| `enforce` | Applied by the settings surface as an authoritative merge for declared keys. |
| `ensure-present` | Applied as a union floor for settings, permissions, and trusted folders. |
| `capture-only` | Accepted by the schema; no capture implementation in this plugin yet. |
| `ignore` | Default for undeclared/unhandled keys; restore is allowlist-based. |
| `exclude` | Accepted as capture guard data; no capture implementation yet. |
| `prune` | Accepted by the schema; `agent-machines prune` is a placeholder today. |
| `prerequisite-check` | Accepted by the schema; no live prerequisite checker today. |

## Reconcile pipeline

`src\agent_machines\reconcile.py` owns the restore flow:

1. Resolve each package for the target machine.
2. Compute a drift key from the resolved package union.
3. Apply Copilot surfaces first.
4. Apply declarative resources second (packages/files; see below).
5. Run repo-local modules third.

`agent-machines restore` runs the validator before both dry-runs and applies. If
the validator reports any error, restore refuses to continue.

## Managed Copilot surfaces

The implemented surfaces live in `src\agent_machines\surfaces\`:

| Logical key | File | Behavior |
| --- | --- | --- |
| `copilot.settings` | `~/.copilot/settings.json` | `ensure-present` floors first, then `enforce`; only declared keys are touched. |
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

## Declarative resources

`src\agent_machines\resources.py` sits between surfaces (which converge
`~/.copilot/`) and modules (the arbitrary-mutation escape hatch): typed,
identity-bearing declarations of *common* machine state the generic engine can
converge itself, so facts like "this package must be installed and pinned" or
"this config file must exist" move out of opaque per-repo scripts and into
reviewable data. A package declares them under a top-level `resources:` list.

Four types are fully handled:

| Type | Identity | Behavior |
| --- | --- | --- |
| `package` | `(manager, id)` | Install / pin / remove via a package manager (`winget`, `apt`, `pipx`, `uv-tool`, `pip`). |
| `file` | `(path, block)` | Converge a config file: whole-file `enforce`/`ensure-present` (`text`/`json`) or a `managed-block` that owns only a marked block. |
| `registry` | `(key, value-name)` | Converge a Windows registry value via `reg.exe` (typed value/state). |
| `feature` | `(manager, id)` | Enable/disable a Windows optional feature/capability (DISM) or a Linux/WSL unit (`systemctl`), selected by a `manager` field. |

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
- list/opaque collection leaves under `enforce` are shape advisories because
  they should be `ensure-present` unions;
- explicitly disabling a bootstrap-critical plugin is an error;
- if any package manages marketplaces, omitting the bootstrap-critical
  `copilot-extensions` marketplace is an error.

For `resources:`, cross-package collisions on the same identity are reported
(delegated to `resources.detect_conflicts`): incompatible `present`/`absent`
states, disagreeing version pins, and conflicting `enforce` file content or
formats are errors; enforce-over-ensure-present precedence and differing
ensure-present content are advisories. Compatible declarations merge
deterministically (pins OR together; the deterministic pick is stable across
package order).

The validator reads only manifests, not live `~/.copilot/` state.
