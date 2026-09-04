# agent-machines

Portable **`restore-machinestate`** for the Copilot CLI. `agent-machines`
discovers machine-state **requirement packages** from adopted projects on the
current machine and reconciles the machine-global `~/.copilot/` state to their
union: Copilot settings first, then repo-local modules for OS-mutating work.

The engine is generic and public. Sensitive modules (install SSH, change power
settings, bootstrap WSL, install package managers, and similar machine-specific
actions) and per-machine data stay in the consuming repo.

## What works today

- **Runtime is standalone.** With absent or false installation-mode policy, the
  CLI installs under `~/.agent-machines` exactly as before and can run without
  `agent-worktrees`; without the sibling registries, discovery simply returns no
  packages. An explicitly activated, validated installation cell instead uses
  its own immutable runtime slots through the payload-local command.
- **Discovery is relationship-aware and registry-based.** Adopted projects come
  from `~/.agent-worktrees/projects.yaml`; paths are resolved through
  `~/.agent-worktrees/repos.yaml`. When a project declares `stateless` or
  `requires_external_state_root`, its project-local (then machine-global
  fallback) `knowledge_repo` relationship contributes that canonically
  registered repository to the same
  package union even when it is not independently adopted. Duplicate adoption
  collapses to one canonical source, and an active but unresolved relationship
  fails loudly. Canonically registered repos may use either an explicit
  per-platform path or the registry's declared source root; an unregistered
  conventional checkout is not accepted. A repo does not have to enable this
  plugin to contribute packages;
  `discover` annotates enablement, but the CLI does not require it by default.
- **Restore is relationship-aware.** Bare `plan`, `validate`, and `restore`
  reconcile the adopted project containing CWD plus its directly bound required
  supplemental repository. This keeps a stateless harness and its knowledge
  packages together without pulling in unrelated projects. `--repo` selects one
  physical repository exactly; `--all-projects` selects the full machine union.
- **Restore is on-demand.** Session start reconciles the **agent-machines
  runtime** only; it never applies machine state.
- **Declarative resources.** Beyond Copilot settings, a package can declare typed
  `resources:` -- package-manager packages, config files (whole-file or a marked
  `managed-block`), Windows registry values, OS features, and Windows power
  settings -- that the
  engine installs/pins/writes itself (with cross-package collision detection),
  instead of hiding them in per-repo scripts. See
  [`docs/resources.md`](docs/resources.md).

For implementation details, see [`docs/architecture.md`](docs/architecture.md).

## Install / update

Enable the plugin in Copilot settings. Its session-start hook runs from the
installed plugin payload:

- on a fresh machine, it performs a cheap **stamp** so the binstub is on PATH;
- on first command use, the binstub self-provisions the venv;
- after a payload version change, it reconciles the runtime in the background.

Manual bootstrap/repair from the plugin directory:

```powershell
scripts\init.ps1 stamp      # Windows: install binstub only; venv builds on first use
scripts\init.ps1            # Windows: build/update the runtime now
```

```bash
scripts/init.sh stamp       # Linux / WSL / macOS: install binstub only
scripts/init.sh             # Linux / WSL / macOS: build/update the runtime now
```

The installer also exposes explicit fixed-identity installation-context
adapters:

```powershell
scripts\init.ps1 -Action slot-provision -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
scripts\init.ps1 -Action slot-validate -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
scripts\init.ps1 -Action slot-cutover -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id> -ExpectedNamespaceGeneration <n> -ExpectedInstallGeneration <n> -ExpectedCurrentVersion <version>
```

```bash
scripts/init.sh slot-provision --context /path/to/install.json --expected-marketplace-id <marketplace-id>
scripts/init.sh slot-validate --context /path/to/install.json --expected-marketplace-id <marketplace-id>
scripts/init.sh slot-cutover --context /path/to/install.json --expected-marketplace-id <marketplace-id> --expected-namespace-generation <n> --expected-install-generation <n> --expected-current-version <version>
```

`cell-provision` is reserved for the payload-local dispatcher and bootstrap
reconciler after the shared resolver proves an already-active Agent Machines
cell. It snapshots the owning payload, reserves the fixed-identity slot, builds
and health-checks the runtime, publishes immutable build completion, and
compare-and-swaps the cell-local current/LKG markers while holding one
cell-root provisioning lock across the complete transaction. It never creates
an activation or migrates legacy state. `slot-cutover` provides the same
ownership-checked marker transaction for an explicitly selected completed
historical slot, enabling rollback without changing another cell; after a
successful cutover it atomically republishes schema-4 `deploy-manifest.json`.
The manifest keeps the latest reconciled payload provenance in `source` and the
active slot in `runtime` (`version`, slot/interpreter paths, and selecting
payload). Historical rollback changes only `runtime`, so session-start bootstrap
does not reverse it; a later different payload provenance still causes a forward
reconcile. Cell snapshot copy is staged in an owned sibling and atomically
published before provenance stamping. Retry cleans only a marker-proven
unfinished publication and never removes a pre-existing snapshot.

Verify:

```bash
agent-machines version
```

## Daily usage

```bash
agent-machines discover                 # packages gated to this machine
agent-machines doctor                   # layout health across adopted repos
agent-machines migrate --repo myrepo    # preview legacy -> canonical moves
agent-machines migrate --repo myrepo --apply
agent-machines plan                     # current project + required supplement
agent-machines validate                 # project-scope conflict validation
agent-machines restore                  # project-scope dry-run preview
agent-machines restore --all-projects   # full machine-scoped union
agent-machines restore --repo myrepo    # another single repo
agent-machines restore --only ssh       # preview one surface/module
agent-machines restore --only ssh --apply
agent-machines restore --json           # structured plan/surface/module result
agent-machines version
```

`plan`, `validate`, and `restore` default to the adopted project containing CWD
plus its directly required supplemental repository. The relationship must be
active, explicitly configured, and canonically registered; an unavailable
required repository fails loudly. Use `--repo <name-or-path>` for exactly one
physical repository (including an intentional local-only recovery), or
`--all-projects` for the full adopted-project plus supplemental-repository
machine union. Entering a supplemental repository directly does not pull its
requiring project back into scope. A CWD outside Git fails rather than silently
broadening scope.
`restore` defaults to a dry-run. `--apply` writes changes. `--only` filters by
logical surface (`settings`, `permissions`, `trustedFolders`) or module name.
Module stdout is shown by default in dry-runs, hidden during apply unless
`--verbose`, and always present in `--json`.

## Unreachable-machine maintenance

The `performing-machine-maintenance` skill connects declarative convergence to
machine-scoped maintenance issues. When an SSH target is unavailable, recurring
state belongs in a requirement package (or another explicitly declared
auto-update owner) before residual local work is filed in an explicitly
identified user repository. A later session on the target uses the skill to
resolve the exact queue, acquire one execution claim, re-derive the action from
trusted repository state, preview, preserve confirmation gates, apply through
the normal owner, verify, and close the issue.

The issue is a tracker, not executable input. agent-dispatch supplies the
optional atomic claim lifecycle; without it, the issue provider must expose an
unambiguous single-owner claim before mutation.

Modules may also reference another active plugin's declared payload command
without embedding its installed path:

```yaml
modules:
  - name: ssh-host
    invocation:
      plugin: agent-ssh@copilot-extensions
      command: agent-ssh
      platforms: [windows]
      arguments: [restore-host, --transport, dtssh, --alias, example-host, --port, "2222"]
      dry_run_arguments: [--dry-run]
      apply_arguments: [--apply]
```

The resolver requires the exact source-qualified active plugin, validates its
`payload-invocation.json`, invokes its payload-local shim with matching
`COPILOT_PLUGIN_ROOT`, and fails the module when that attributable provider is
unavailable.

## Requirement packages

Requirement packages use a repo-owned namespace:

```text
.agent-machines/
├── all/                       # packages evaluated on every machine
│   └── copilot-defaults.yaml
└── machines/
    └── my-box/                # packages discoverable only on my-box
        └── laptop-policy.yaml
```

Every file is an independent, complete package with a unique `package` name.
The machine directory is an implicit scope; an explicit package `gate` is still
honored. Partial overrides of a shared package remain in that package's existing
`per-machine` block rather than being a cross-file merge.
Multi-machine packages belong in `all/` with an explicit `gate`.

`<machine>` is the canonical topology key when an adopted repository provides a
matching `machines.yaml` entry. The raw `platform.node()` host name (Windows
`%COMPUTERNAME%`) is matched case-insensitively against the entry key plus its
`hostname`, `alias`, and `display_name`; all four remain accepted for package
gates, `per-machine` overlays, nested module/resource gates, and machine
directory selection. Ambiguous cross-entry matches fail before reconciliation.
Without usable topology, the raw host name remains the standalone fallback.

For migration, `.github/machine-state/` remains a bounded legacy fallback only
when `.agent-machines/` is absent. Move a repo atomically: once the canonical
root exists, legacy files in that repo are ignored.

Use `agent-machines doctor` to find legacy, mixed, or malformed layouts across
adopted repos. `agent-machines migrate --repo <name-or-path>` previews a
behavior-preserving migration: YAML files move byte-for-byte into
`.agent-machines/all/`, preserving gates, and a legacy `README.md` moves to the
canonical root. Re-run with `--apply` to perform it. Migration refuses mixed
layouts, destination collisions, nested content, and unknown legacy entries
rather than guessing. Reorganizing a migrated package into `machines/<machine>/`
is a separate explicit edit.

A package under `.agent-machines/all/` has this shape. Schema v4 is required
only when the package uses `authority`; runtimes continue to read v1-v3
packages that do not:

```yaml
schema_version: 4
package: myrepo/copilot-defaults
gate: [my-box]                         # omit or ["*"] for all machines
manage:
  copilot.settings:
    disposition: enforce
    values: { model: gpt-5.4, effortLevel: high }
  copilot.settings.plugins:
    disposition: ensure-present
    values:
      enabledPlugins:
        agent-machines@copilot-extensions: true
        agent-worktrees@copilot-extensions: true
      extraKnownMarketplaces:
        copilot-extensions: { source: { source: github, repo: ThomasMichon/copilot-extensions } }
  copilot.permissions:
    disposition: ensure-present
    by-location-class:
      - match: "$REPO(myrepo)"
        tool_approvals:
          - { kind: commands, commandIdentifiers: [git, gh, pwsh] }
  copilot.trustedFolders:
    disposition: ensure-present
    by-location-class: ["$REPO(myrepo)"]
per-machine:
  my-box:
    manage:
      copilot.settings:
        values: { effortLevel: low }
modules:
  - name: ssh
    gate: [my-box]
    windows:
      command: ["pwsh", "-File", "tools/restore/Restore-MachineState.ps1", "-Section", "SSH"]
      dry_run_args: ["-DryRun"]
resources:
  - type: package                      # install + pin a package-manager package
    id: marlocarlo.psmux
    manager: winget
    version: "3.3.5"
    pin: true
  - type: file                         # own a marked block inside a user-owned file
    path: "$HOME/.psmux.conf"
    strategy: managed-block
    block: "agent-worktrees mux keybinds (opt-in)"
    content: |
      set -g prefix C-b
      set -g paste-detection off
  - type: power-setting                # converge AC/DC values in a Windows scheme
    id: lid-close
    scheme: SCHEME_CURRENT
    subgroup: SUB_BUTTONS
    setting: LIDACTION
    ac: do-nothing
    dc: sleep
```

Package gates and `per-machine` overlay keys both match machine identities
case-insensitively. Defining two overlay keys that normalize to the same
case-insensitive identity is invalid, as are empty or surrounding-whitespace
keys.

Within an `ensure-present` `enabledPlugins` map, `true` remains an additive
floor and preserves an existing operator `false`. A declared `false` is a
per-plugin tombstone: it authoritatively disables that one identity while
preserving every undeclared operator plugin. Tombstones cannot disable
bootstrap-critical plugins. Packages that rely on tombstones must declare
`schema_version: 2`; older exact-v1 runtimes reject them before restore. Current
runtimes continue to read legacy v1 packages that do not use tombstones.

For a migration that must remain executable by older schema-v1 runtimes, use
the backward-compatible false-only group. Existing runtimes already apply
`copilot.settings.*` enforce groups through the same deep merge, while current
validators enforce this exact shape:

```yaml
copilot.settings.plugin-tombstones:
  disposition: enforce
  values:
    enabledPlugins:
      retired-plugin@example-marketplace: false
```

This group may contain only non-bootstrap plugin identities set to `false`.
Undeclared operator plugins remain untouched.

Schema v3 adds precise desired absence for user-global plugin activation. A
private requirement package can remove selected keys from
`~/.copilot/settings.json` without uninstalling their inventory:

```yaml
schema_version: 3
package: example/plugin-activation
manage:
  copilot.settings.plugin-activation:
    disposition: ensure-absent
    keys:
      enabledPlugins:
        - optional-plugin@example-marketplace
        - repo-focused-tools@example-marketplace
```

Restore previews exact `enabledPlugins` removals by default; `--apply` backs up
`settings.json` before deleting only the listed keys. Duplicate removal requests
compose across packages, while any true/false value declaration for the same
identity is a validation conflict. `agent-worktrees`, `agent-machines`, and
plugins named by any package's `bootstrap-floor.plugins` are protected from
removal. This is intentionally distinct from `exclude` (a capture secret guard)
and `prune` (out-of-reconcile garbage collection).

Recognized dispositions are `enforce`, `ensure-present`, `ensure-absent`,
`capture-only`, `ignore`, `exclude`, `prune`, and `prerequisite-check`.
Current restore applies `enforce`, `ensure-present`, and `ensure-absent`;
`capture` and `prune` are placeholder CLI verbs today.

### Deterministic authority

Schema v4 adds an optional signed integer `authority` from `-1000` through
`1000`. Package authority defaults to `0`; a `manage` spec, resource, or module
may override it:

```yaml
schema_version: 4
package: example/project-policy
authority: 20
manage:
  copilot.settings.model:
    disposition: enforce
    authority: 30
    values: { model: gpt-5.4 }
resources:
  - type: package
    manager: winget
    id: Example.Tool
    authority: 10
modules:
  - name: project-bootstrap
    authority: 5
    windows:
      command: ["pwsh", "-File", "tools/bootstrap.ps1"]
      dry_run_args: ["-DryRun"]
```

For same-shape `copilot.settings*` scalar/collection conflicts and supported
declarative resource conflict fields, a unique highest authority selects the
effective value. Equal-highest disagreement remains a validation error (or the
existing advisory where the field was already advisory), and non-overlapping
lower-authority leaves still apply. Incompatible settings shapes and
managed-block marker changes remain hard conflicts regardless of authority;
neither is safe to migrate by ordering alone.
`enforce` settings are applied in ascending authority with the stable tiebreak
`source_repo`, package, and manage key. `ensure-present` settings retain their
authority-neutral union-floor behavior and use only the stable source/package/key
order; authority never turns a floor into an override. Resource compatibility
fields remain conservative: package `pin` is ORed and every
`process_guard.names` entry is retained.

Authority is forbidden on plugin activation, tombstones, marketplaces, and
desired-removal specs. A package-level authority is inherited, so a package
that manages `enabledPlugins` or `extraKnownMarketplaces` must omit package
authority and keep that safety-sensitive policy separate. Authority never
overrides bootstrap or removal protections.

`per-machine` continues to overlay only `manage`: it may override or remove a
manage-spec authority (`authority: null` falls back to package authority).
Per-machine package authority, resources, and modules are intentionally not
part of schema v4.

Modules are **opaque-additive** in v1 authority semantics. Every applicable
module still runs, including same-named modules from different packages;
authority is reported in plans/results but does not suppress imperative work.

Every restore entry point validates the resolved package union before applying
surfaces, resources, or modules. Direct library callers receive
`RestoreValidationError`; conflict safety does not depend on using the CLI.

Plan and restore JSON expose stable `authority_decisions` with selected and
superseded source-qualified contributors. `drift_key` hashes normalized
effective operations: ordered `ensure-present` floors, authority-resolved
`enforce` values, explicit desired removals, resolved resources, and additive
modules. `provenance_hash` hashes the complete resolved package union, including
package authority. A change only to a losing enforce value therefore leaves the
drift key stable but changes provenance.

The top-level `resources:` list declares typed, identity-bearing machine state
-- package-manager packages, canonical config files (whole-file or a marked
`managed-block`), Windows registry values, OS features (Windows optional
features/capabilities and Linux/WSL units), and Windows power settings -- that
the engine converges itself, with cross-package collision detection. See
[`docs/resources.md`](docs/resources.md) for the full schema and adopter guide.

## Troubleshooting

Start with the layout-aware doctor, then inspect resolved state:

1. `agent-machines doctor --json` — detect canonical, legacy, mixed, malformed,
   unavailable, and absent repo layouts.
2. `agent-machines discover --json` — confirm packages were discovered and gated
   to this machine.
3. `agent-machines validate --json` — inspect fail-loud conflicts before restore.
4. `agent-machines plan --json` — confirm surfaces/modules and drift key.
5. `agent-machines restore --json` — capture exact surface diffs and module
   stdout/stderr tails.

`doctor` exits `0` when no layout errors are present (legacy and unavailable
repos remain advisory), and `1` for malformed or mixed layouts. Command or
manifest errors exit `2`. `migrate` is a no-op with exit `0` for an already
canonical, absent, or empty legacy layout.

If the runtime is not built yet, the first command prints a provisioning message
(POSIX also emits `::agent-provisioning::`) and may take 30–120 seconds.
