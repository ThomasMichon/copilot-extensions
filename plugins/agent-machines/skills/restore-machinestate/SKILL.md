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

The agent-machines runtime converges the current machine to desired state declared in
**requirement packages** carried by adopted repos under `.agent-machines/all/`
and `.agent-machines/machines/<machine>/`.
Restore is **machine-scoped**: it reconciles the union of every discovered
package, not one anchor repo. The CLI itself is standalone; if the project
registries are absent, discovery returns no packages instead of
failing.

Invoke the exact `argv` from the agent-machines session command catalog. Append
the arguments shown below to that `argv`; do not search `PATH` or substitute a
same-named command from another payload. If the catalog reports the command as
unavailable, surface that failure rather than improvising an install.

## Workflow

1. **Doctor** the package layout:
   ```
   <catalog argv[0]> doctor
   ```
   Reports canonical, legacy, mixed, malformed, unavailable, and absent layouts.
   For a legacy repo, preview migration with
   `<catalog argv[0]> migrate --repo <name-or-path>` and apply only after
   reviewing the byte-preserving move plan.

2. **Discover** what applies here:
   ```
   <catalog argv[0]> discover
   ```
   Lists the registered repos that carry gated requirement packages for this
   machine. The candidate set is `~/.agent-worktrees/projects.yaml`; paths are
   resolved from `~/.agent-worktrees/repos.yaml` when present.

3. **Plan** (read-only) -- the managed surfaces and a content drift key:
   ```
   <catalog argv[0]> plan
   ```

4. **Validate** -- detect cross-package conflicts before applying:
   ```
   <catalog argv[0]> validate
   ```
   Scalar `enforce` disagreements and bootstrap-floor violations are errors; the
   validator reports, it does not auto-arbitrate. Fix conflicting packages.

5. **Restore** -- deliberate + reviewable (dry-run is the default):
   ```
   <catalog argv[0]> restore                       # DRY-RUN: what would change and why
   <catalog argv[0]> restore --only ssh            # preview one section
   <catalog argv[0]> restore --only ssh --apply    # apply just that section
   <catalog argv[0]> restore --apply               # apply everything
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
