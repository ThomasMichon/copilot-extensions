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
agent-machines plan               # read-only: managed surfaces + modules + drift key
agent-machines validate           # run the conflict validator
agent-machines restore            # DRY-RUN by default: preview what would change and why
agent-machines restore --apply    # actually apply (surfaces + modules)
agent-machines restore --only ssh --apply   # review + apply one section at a time
agent-machines restore --only ssh --json    # machine-readable result (incl. module output)
agent-machines version
```

**Restore is deliberate and reviewable.** It defaults to a dry-run that shows the
per-key/-location diff; `--apply` makes changes; `--only` scopes to named
surfaces/modules. Surfaces back up before writing; a module runs during a dry-run
only if it declares `dry_run_args`. A **module's own output** (its step-by-step
`[OK]/[PLAN]/[CHANGE]` preview) is surfaced under its result line **by default in
a dry-run**, and behind `--verbose` for `--apply`; `--json` returns the full
structured result (plan + surface results + module `stdout_tail`/`stderr_tail`).

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
**repo-local module runner**, and **all three Copilot surfaces** — `settings`
(enforce scalars + ensure-present union), `permissions` and `trustedFolders`
(by-location-class ensure-present floors) — apply, with dry-run-default, per-change
diffs, `--only` scoping, and backup-before-write. Automatic run at session launch
is intentionally **not** wired: restore stays on-demand. The `capture` / `prune`
verbs are the next slice.

## Install

Runtime CLI (venv + `~/.local/bin/agent-machines` binstub). After a one-time
bootstrap, a **session-start hook** (`hooks.json` → `~/.agent-machines/bin/bootstrap-check`)
keeps the runtime current: it compares the deployed version to the plugin payload
and re-runs the installer **only when they drift** (e.g. after `copilot plugin
update`), in the background so session start never blocks. This reconciles the
**tool**, never machine state — it never runs `restore` (that stays on-demand, per
*Status* above). One-time / manual bootstrap:

```
scripts/init.sh        # Linux / WSL / macOS
scripts/init.ps1       # Windows
```
