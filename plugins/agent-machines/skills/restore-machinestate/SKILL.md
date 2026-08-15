---
name: restore-machinestate
description: >
  Converge the current machine to desired state declared in a repo's
  requirement packages via the agent-machines engine -- Copilot settings first,
  then repo-local modules. Use this skill to inspect or apply machine state:
  discover which packages apply, plan the change, validate for conflicts, and
  restore.
  Trigger phrases include:
  - 'restore machine state'
  - 'restore-machinestate'
  - 'converge this machine'
  - 'apply my machine config'
  - 'what requirement packages apply here'
  - 'validate machine state'
  - 'agent-machines'
---

# restore-machinestate

`agent-machines` converges the current machine to desired state declared in
**requirement packages** carried by adopted repos at `.github/machine-state/`.
Restore is **machine-scoped**: it reconciles the union of every discovered
package, not one anchor repo. The CLI itself is standalone; if the
agent-worktrees registries are absent, discovery returns no packages instead of
failing.

## Workflow

1. **Discover** what applies here:
   ```
   agent-machines discover
   ```
   Lists the registered repos that carry gated requirement packages for this
   machine. The candidate set is `~/.agent-worktrees/projects.yaml`; paths are
   resolved from `~/.agent-worktrees/repos.yaml` when present.

2. **Plan** (read-only) -- the managed surfaces and a content drift key:
   ```
   agent-machines plan
   ```

3. **Validate** -- detect cross-package conflicts before applying:
   ```
   agent-machines validate
   ```
   Scalar `enforce` disagreements and bootstrap-floor violations are errors; the
   validator reports, it does not auto-arbitrate. Fix conflicting packages.

4. **Restore** -- deliberate + reviewable (dry-run is the default):
   ```
   agent-machines restore                       # DRY-RUN: what would change and why
   agent-machines restore --only ssh            # preview one section
   agent-machines restore --only ssh --apply    # apply just that section
   agent-machines restore --apply               # apply everything
   ```
   Restore previews by default; `--apply` makes changes; `--only` scopes to named
   surfaces/modules so you review and apply section by section. Surfaces back up
   before writing; a module runs in a dry-run only if it declares `dry_run_args`.
   Restore refuses to run (dry-run or apply) while the validator reports errors.

## Dispositions

Each managed key declares one: `enforce`, `ensure-present`, `capture-only`,
`ignore` (default -- the manifest is an allowlist), `exclude` (secret guard),
`prune` (opt-in GC), `prerequisite-check` (assert a prerequisite, never store a
secret). Maps/lists compose by union (`ensure-present`); scalar singletons are
`enforce` and are the validator's conflict domain. Current restore applies
`enforce` and `ensure-present`; `capture` and `prune` are placeholder CLI verbs
today.
