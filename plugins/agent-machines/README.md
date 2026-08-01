# agent-machines

Portable **`restore-machinestate`** for the Copilot CLI. Point it at a repo that
carries **requirement packages** and `agent-machines restore` converges *the
current machine* to that declared desired state -- Copilot settings first, then
(via repo-local modules) applications, services, and custom configuration.

The **engine is generic and public**; the sensitive, OS-mutating modules
(install a package manager, change power settings, install SSH, ...) and the
per-machine data live in each harness repo, never in this plugin.

## Model

- **Requirement packages** -- named, opt-in YAML files under a repo's
  `.github/machine-state/`. The plugin defines the schema; each repo supplies data.
- **Seven dispositions** govern each managed key: `enforce` (authoritative),
  `ensure-present` (union floor, never clobbers live additions), `capture-only`
  (harvest live into a promotable diff), `ignore` (default -- the manifest is an
  allowlist), `exclude` (hard secret guard), `prune` (opt-in GC), and
  `prerequisite-check` (assert a prerequisite without storing a secret).
- **Machine-scoped union restore.** `~/.copilot/` is machine-global, so restore
  reconciles the *union* of every discovered package (not one anchor repo).
  Discovery is scoped to **adopted projects** (`~/.agent-worktrees/projects.yaml`)
  and resolves their paths via `repos.yaml`; it degrades gracefully when those are
  absent (à la carte -- no sibling plugin required).
- **Conflict validator.** Multiple repos may declare overlapping state; the
  validator *detects and reports* clashes (it does not auto-arbitrate). Scalar
  `enforce` disagreements are errors; a bootstrap-floor assertion keeps the
  stack-critical plugins/marketplaces from being disabled by a bad edit.

## Commands

```
agent-machines discover           # this machine's requirement-package set
agent-machines plan               # read-only: managed surfaces + drift key
agent-machines validate           # run the conflict validator
agent-machines restore --dry-run  # converge the machine (apply lands next; see below)
agent-machines version
```

## Repo-local modules

The engine converges Copilot settings directly, but sensitive OS-mutating work
(install a package manager, bootstrap WSL, configure SSH, change power settings)
stays in each harness repo as a **module** the engine invokes — so this public
plugin ships no such logic. A package declares modules that run a repo-local
command, gated per machine, cross-platform, and **dry-run-safe** (a module runs
during `restore --dry-run` only if it declares `dry_run_args`):

```yaml
modules:
  - name: ssh
    gate: [my-box]                 # optional; defaults to the package gate
    windows:
      command: ["pwsh", "-File", "tools/restore/Restore-MachineState.ps1", "-Section", "SSH"]
      dry_run_args: ["-DryRun"]
    linux:
      command: ["bash", "tools/restore/restore-machine-state.sh", "--section", "ssh"]
      dry_run_args: ["--dry-run"]
```

Commands are argv lists run with the package's repo root as the working
directory. This is how a monolithic restore engine gets **modularized** into the
framework: each section becomes a declared module.

## Status

Engine core (discover / manifest + layering / locations / validator / plan), the
**repo-local module runner**, and the **`copilot.settings` surface apply**
(enforce scalars + ensure-present union, backup-before-write, dry-run) are in
place. The `copilot.permissions` / `copilot.trustedFolders` surfaces (their
location-class model) and the `capture` / `prune` verbs are the next slice.

## Install

Runtime CLI (venv + `~/.local/bin/agent-machines` binstub); reconciled at session
launch on its gated machines like the other runtime plugins. Manual bootstrap:

```
scripts/init.sh        # Linux / WSL / macOS
scripts/init.ps1       # Windows
```
