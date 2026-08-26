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
- **Discovery is registry-based.** The candidate set is
  `~/.agent-worktrees/projects.yaml`; paths are resolved through
  `~/.agent-worktrees/repos.yaml`. A repo does not have to enable this plugin to
  contribute packages; `discover` annotates enablement, but the CLI does not
  require it by default.
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

Verify:

```bash
agent-machines version
```

## Daily usage

```bash
agent-machines discover                 # packages gated to this machine
agent-machines plan                     # read-only surfaces/modules + drift key
agent-machines validate                 # detect cross-package conflicts
agent-machines restore                  # dry-run preview; refuses on validator errors
agent-machines restore --only ssh       # preview one surface/module
agent-machines restore --only ssh --apply
agent-machines restore --json           # structured plan/surface/module result
agent-machines version
```

`restore` defaults to a dry-run. `--apply` writes changes. `--only` filters by
logical surface (`settings`, `permissions`, `trustedFolders`) or module name.
Module stdout is shown by default in dry-runs, hidden during apply unless
`--verbose`, and always present in `--json`.

## Requirement packages

A requirement package is a YAML file under `.github/machine-state/`:

```yaml
schema_version: 1
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

There is no `doctor` command today. Use the shipped read-only commands:

1. `agent-machines discover --json` — confirm packages were discovered and gated
   to this machine.
2. `agent-machines validate --json` — inspect fail-loud conflicts before restore.
3. `agent-machines plan --json` — confirm surfaces/modules and drift key.
4. `agent-machines restore --json` — capture exact surface diffs and module
   stdout/stderr tails.

If the runtime is not built yet, the first command prints a provisioning message
(POSIX also emits `::agent-provisioning::`) and may take 30–120 seconds.
