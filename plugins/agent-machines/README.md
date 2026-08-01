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

## Status

Engine core (discover / manifest + layering / locations / validator / plan) is in
place. The mutating surface handlers (`copilot.settings` / `permissions` /
`trustedFolders`) and the `capture` / `prune` verbs are the next slice.

## Install

Runtime CLI (venv + `~/.local/bin/agent-machines` binstub); reconciled at session
launch on its gated machines like the other runtime plugins. Manual bootstrap:

```
scripts/init.sh        # Linux / WSL / macOS
scripts/init.ps1       # Windows
```
