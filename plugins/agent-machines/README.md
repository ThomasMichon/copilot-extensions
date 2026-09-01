# agent-machines

Portable **`restore-machinestate`** for the Copilot CLI. `agent-machines`
discovers machine-state **requirement packages** from adopted projects on the
current machine and reconciles the machine-global `~/.copilot/` state to their
union: Copilot settings first, then repo-local modules for OS-mutating work.

The engine is generic and public. Sensitive modules (install SSH, change power
settings, bootstrap WSL, install package managers, and similar machine-specific
actions) and per-machine data stay in the consuming repo.

## What works today

- **Runtime is standalone.** The CLI installs under `~/.agent-machines` and can
  run without `agent-worktrees`; without the sibling registries, discovery simply
  returns no packages.
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
- **Restore is machine-scoped.** `~/.copilot/` is global to the user account, so
  restore uses the union of every discovered package gated to this machine, not a
  single current repo.
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

The installer also exposes an explicit, non-activating adapter for a
pre-stamped installation-context snapshot:

```powershell
scripts\init.ps1 -Action slot-provision -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
scripts\init.ps1 -Action slot-validate -Context C:\path\to\install.json -ExpectedMarketplaceId <marketplace-id>
```

```bash
scripts/init.sh slot-provision --context /path/to/install.json --expected-marketplace-id <marketplace-id>
scripts/init.sh slot-validate --context /path/to/install.json --expected-marketplace-id <marketplace-id>
```

These actions only reserve or validate the current plugin version's empty,
owned cell-local slot. They do not build it, switch the normal legacy install
path, migrate legacy state, or write current/LKG/activation state.

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
agent-machines plan                     # current repo (from CWD)
agent-machines validate                 # current repo conflict validation
agent-machines restore                  # current repo dry-run preview
agent-machines restore --all-projects   # full machine-scoped union
agent-machines restore --repo myrepo    # another single repo
agent-machines restore --only ssh       # preview one surface/module
agent-machines restore --only ssh --apply
agent-machines restore --json           # structured plan/surface/module result
agent-machines version
```

`plan`, `validate`, and `restore` default to the package-owning Git repository
containing CWD. Use `--repo <name-or-path>` for another single repository, or
`--all-projects` for the full adopted-project plus supplemental-repository
machine union. A CWD outside Git fails rather than silently broadening scope.
`restore` defaults to a dry-run. `--apply` writes changes. `--only` filters by
logical surface (`settings`, `permissions`, `trustedFolders`) or module name.
Module stdout is shown by default in dry-runs, hidden during apply unless
`--verbose`, and always present in `--json`.

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

`<machine>` is the raw host name returned by `platform.node()` (on Windows,
`%COMPUTERNAME%`), matched case-insensitively. It is not a display name from an
external machine registry.

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

A package under `.agent-machines/all/` has this shape:

```yaml
schema_version: 2
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

Recognized dispositions are `enforce`, `ensure-present`, `capture-only`,
`ignore`, `exclude`, `prune`, and `prerequisite-check`. Current restore applies
`enforce` and `ensure-present`; `capture` and `prune` are placeholder CLI verbs
today.

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
