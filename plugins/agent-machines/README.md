# agent-machines

Portable **`restore-machinestate`** for the Copilot CLI. `agent-machines`
discovers machine-state **requirement packages** from adopted projects on the
current machine and reconciles the machine-global `~/.copilot/` state to their
union: Copilot settings first, then repo-local modules for OS-mutating work.

The engine is generic and public. Sensitive modules (install SSH, change power
settings, bootstrap WSL, install package managers, and similar machine-specific
actions) and per-machine data stay in the consuming repo.

The PowerShell installer's declared, captured human output initializes UTF-8
only when requested by its admitting parent. Construction diagnostics use
the existing dual-stream helper; the finite Python lifecycle producer honors
the same output request without decorating JSON or changing its error policy.
The legacy decision child suppresses PowerShell module-preparation progress,
not diagnostics or refusal status. Ordinary invocation, native command decoding,
runtime construction and publication authority remain unchanged.

Cell/slot actions do not allocate the legacy `.agent-machines/.install-stage`.
The PowerShell entrypoint retains a compiler-free, caller-owned whole-tree
watchdog, including payload selection/hashing, provisioning-lock waits and
runtime construction. The original PowerShell caller adopts the same shared
shell-lifetime Job/pipe transport as the installation-context SDK before hashing
begins; losing that caller retires the watchdog, not just its child installer.
Its default
deadline is 480 seconds; `AGENT_MACHINES_INSTALL_DEADLINE_SEC`, then
`COPILOT_PLUGIN_INSTALL_DEADLINE_SEC`, may select another positive deadline.
Nonpositive or malformed deadlines are refused instead of disabling containment.
Captured UTF-8 stdout/stderr are returned at completion, with separate 16 MiB
budgets; timeout fails with status 124, and cleanup uncertainty is an explicit
error, never successful completion. The transport explicitly selects UTF-8 for
both its Python producer and its PowerShell decoders, including Python 3.10.
Both native adapters bind the selected package version and every copied regular file
before waiting for the cell lock, recheck the recursive child, and verify copied
snapshot bytes before publication. This also covers ordinary marketplace payloads
without `runtime-source.json`. Generated/build directories and bytecode caches
are included, not silently excluded by source-assembly filtering. Linked,
reparse, nonregular or incompletely scanned inputs are refused; only the two
root publication sidecars retain separate ownership/provenance checks.
Construction, dependency-policy, lifecycle and import-health Python controls
explicitly disable bytecode writes; isolated Python does not honor the
environment-only equivalent. Existing cached bytes stay part of the selection.
Package builds use the dependency installer's existing retained-input copy, so
backend build/egg-info output does not mutate the verified snapshot.

## Legacy runtime removal

The native `scripts/init.ps1 -Action uninstall -InstallDir <root>` and
`scripts/init.sh uninstall --install-dir <root>` actions remove only admitted
runtime artifacts. Config, databases, authentication, state, logs, unknown
entries, the root and coordination lease files remain. Uninstall is not a
purge or an installation-cell retirement operation.

All legacy publishers (including `stamp-binstub`) and uninstall serialize on
the same whole-action `.install.lock`. Fast session-start stamps bound admission
to one second and refuse on contention; they never publish unlocked. Removal holds the canonical
command lifecycle, construction, retention and selector custody; a live runtime
or an unavailable idle proof refuses removal. Bootstrap Python, the native host
and all executing helpers must be outside the root. Paths containing raw `..`,
linked/reparse ancestors, managed/cell roots and foreign/malformed ownership
are refused before staging or removal. Unknown runtime-bin contents are retained,
not selected wholesale. Missing ownership or custody proof refuses removal.

The native adapter calls `installer_engine_uninstall.py` with the trusted
`scripts/uninstall-profile.json`, under the whole-action transaction. The helper
and native engine must be materialized in this payload's own `scripts/`; no
ambient or sibling installation is searched. Its
`--check` is structural/read-only, not removal authority; `--remove-runtime`
reacquires all canonical custody and repeats admission. The finite entry-name
installer-engine API is required; there is no purge fallback. The existing
deploy manifest is removed last, retaining ownership for a failed removal's retry.

Unexpected outer-owner death remains an integration qualification: canonical
writer custody does not transfer a parent's noninherited .NET transaction lock
or PID receipt. A native lifetime/handle bridge is required before claiming
whole-gate custody across that boundary. POSIX kernel-lock release is close-only
so an inherited finite guardian is not unlocked by the caller's exit trap.

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
  Discovery also always scans a home-relative **user-scoped** root
  (`~/.agent-machines/config/`, same `all/`/`machines/<machine>/` shape) that
  needs no adopted repo, registry, or `agent-worktrees` at all -- the fallback
  destination when no knowledge/control repo is bound or reachable.
- **Restore is relationship-aware.** Bare `plan`, `validate`, and `restore`
  reconcile the adopted project containing CWD plus its directly bound required
  supplemental repository. This keeps a stateless harness and its knowledge
  packages together without pulling in unrelated projects. `--repo` selects one
  physical repository exactly; `--all-projects` selects the full machine union.
- **Restore is on-demand.** Session start reconciles the **agent-machines
  runtime** only; it never applies machine state.
- **Declarative resources.** Beyond Copilot settings, a package can declare typed
  `resources:` -- package-manager packages, config files (whole-file or a marked
  `managed-block`), Windows registry values, OS features, Windows power
  settings, and machine-local self-update opt-ins / Scheduled Tasks -- that the
  engine installs/pins/writes itself (with cross-package collision detection),
  instead of hiding them in per-repo scripts. See
  [`docs/resources.md`](docs/resources.md). Declared `git-config` keys use a
  separate preview-bound consent digest and verified rollback; blanket
  `restore --apply` or unattended maintenance cannot consent to those edits.
- **Optional unattended self-update watchdog.** A reachable, logged-in machine
  can opt in (per machine or fleet-wide, via the `self-update` resource) to two
  independently-scheduled OS Scheduled Tasks that converge it between
  sessions: an hourly `watchdog` tier (dtssh launcher liveness, a dtssh host
  repair via `agent-ssh restore-host` when the host is unhealthy or stale, and
  a dtssh mesh refresh -- re-discover live tunnel ids, re-emit this machine's
  SSH profile, and verify reachability to every known alias) and a daily
  `sweep` tier (repo fast-forward + full repo `update` + full restore). Both
  defer around a live session and are a clean no-op when not opted in. See the
  `agent-machines-setup` skill's *Enable a regular unattended maintenance
  schedule* section and [`docs/resources.md`](docs/resources.md#self-update).
- **Optional unattended fleet-update sweep.** A distinct, independently
  scheduled `fleet-update` resource opts a machine into a daily Scheduled
  Task/`systemd --user` timer that runs `worktree-manager update` -- the
  fleet-wide plugin install/update orchestrator the Worktree Manager already
  owns -- asynchronously, without waiting on an interactive session. Uses its
  own state directory, lock namespace, and scheduled-task family from
  self-update's, so a bug in one can never affect the other's already-
  deployed mechanism. See [`docs/resources.md`](docs/resources.md#fleet-update).

For implementation details, see [`docs/architecture.md`](docs/architecture.md).

## Explicit installation repair

`installation-repair` is separate from requirement-package restore. It discovers
enabled, attributable plugin-owned `installer-readiness.json` contracts for the
current project and global settings. Discovery never invokes an installer;
selecting qualified modules observes their transitive same-source prerequisite
closure in read-only/no-self-provision mode:

```text
agent-machines installation-repair --project-root <project>
agent-machines installation-repair --project-root <project> --module <marketplace-id>::<plugin>/<module>
```

Review the returned closure and `planDigest`, then apply that exact selection:

```text
agent-machines installation-repair --project-root <project> --module <marketplace-id>::<plugin>/<module> --apply --expected-plan-digest <digest>
```

Repeat `--module` for independent branches. Apply reconstructs host admission,
checks the bound payload/source/graph/activation inputs and revalidates before
each installer. A digest is a stale-input check, **not** permission or signed
release verification. Only the manifest's native owner actions execute, with
bounded deadlines/output and caller-owned transient descendants. Declared
installer-owned durable services keep their own supervision contract.
CLI `SIGINT`, and POSIX `SIGTERM`, follow the cancellation cleanup path: transient
installer descendants exit before the apply lock is released, and the caller's
previous signal handlers and ignored child dispositions are preserved.
Every selected identity, including
transitive prerequisites, must fit the 256-character bound before any probe.
Unreadable or oversized executables remain unavailable outcomes and cannot run
probes or installers. Each invocation must match its admitted argv, executable
and content digest, even if a previously unavailable executable becomes usable.
Apply re-admits initially-ready outcomes too; readiness
observed while ownership or payload inputs change cannot satisfy a stale plan.
Binding refusal stops further execution but still propagates dependency blockers
through the remaining closure; it cannot report required readiness from stale outcomes.
`--readiness-timeout` and `--installer-timeout` are seconds (positive, at most
3600); defaults are 15 and 300.

After an earlier installer runs, apply refreshes each unblocked module's
readiness before deciding whether to skip or repair it, preserving its initial
observation. Each attempted installer also gets an independent readiness probe.
Zero exit alone is not readiness. Failed/unknown/unsupported, unsatisfied configuration-empty
and restart-required prerequisites block dependents; independent branches may
continue. JSON includes before/after states, installer outcome, blockers and
restart boundaries, never raw subprocess output. Exit 0 means discovery,
observation, or a ready selected apply; exit 2 means incomplete apply (including
optional failure), and exit 1 means invalid/refused admission or stale inputs.
Interrupted execution, including owner revalidation and hashing, retains
completed partial outcomes, exits 130 and does not replay.

Restart obligations accumulate across independent selected steps and subsequent
invocations in the machine home's `.agent-machines/installation-repair` journal.
They are recorded **before** a restart-declaring action starts, so cancellation,
failure or a process crash cannot silently discard a possibly required restart.
Pending modules are not reinstalled and their dependents remain blocked, even
if readiness reports ready. Read-only discovery/observation creates no journal;
apply and confirmation are serialized, and corrupt state refuses execution.

Use `restarts.pending` to present one combined restart opportunity to the
operator rather than restarting between independent steps. No restart is
automatic. After the operator has completed **every** listed shell, session or
machine restart, explicitly confirm the exact batch:

```text
agent-machines installation-repair --confirm-restart <restarts.pendingDigest>
```

This is an operator attestation, not automatically observed restart evidence.
A changed batch refuses confirmation; confirmation neither runs installers nor
declares readiness. Then observe/replan and explicitly apply again. A new CLI
process, changed payload, successful readiness probe or repeated apply is not
restart confirmation. Inspect interrupted/failed actions before attesting;
their conservatively retained obligations must not be dismissed as a retry.

Ordinary downstream owners need declared no-self-provision and legacy-root
protocols; inherited core-cell control variables are cleared. Linked/reparse
targets, ambiguous owners and migration/deactivation/maintenance states refuse
repair rather than guessing an installer. A legacy runtime root must have one
qualified plugin owner, even when the competing plugins share a marketplace.
All enabled runtimes contribute ownership claims independently of readiness or
repair eligibility; an unverifiable runtime declaration blocks ordinary repair.
Enabled peers with ambiguous roots or unavailable provenance also make that
census incomplete; unrelated selected owners cannot install past the uncertainty.
Existing runtime roots and ancestors must be ordinary, unlinked directories;
a missing ordinary root remains eligible for first installation.
Validated active-core cells may be
observed, but **unready core-cell repair is not yet supported**: ordinary `init`
actions are not cell-lifecycle permission. This command never creates cells,
changes mode policy, enrolls machines, acquires capacity, or removes durable
data. It is on-demand only; enabling the plugin does not apply a repair.

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

Cell/slot actions use their explicit receipt transaction without entering the
legacy payload self-stage or creating its legacy runtime footprint. This
exception is selected from the parsed action, not an inherited stage marker;
ordinary legacy installation still stages a replaceable marketplace payload.

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

### Explicit legacy attribution, legacy-wrapper retirement, deactivation, cell repair, and uninstall

`scripts/init.sh cell-attribute-legacy`, `cell-retire-legacy`,
`cell-deactivate`, `cell-repair`, and `cell-uninstall` (PowerShell:
`scripts\init.ps1 -Action cell-attribute-legacy`, `cell-retire-legacy`,
`cell-deactivate`, `cell-repair`, or `cell-uninstall`) use the same
stdlib-only management engine and an existing Python 3.10+ interpreter. They
never self-provision a toolchain or trust an inherited context/action.

Legacy attribution requires these explicit inputs:

| Bash argument | PowerShell parameter |
|---|---|
| `--context` | `-Context` |
| `--durable-home` | `-DurableHome` |
| `--expected-marketplace-id` | `-ExpectedMarketplaceId` |
| `--maintenance-token` | `-MaintenanceToken` |

Repair and uninstall require every value below explicitly:

| Bash argument | PowerShell parameter |
|---|---|
| `--context` | `-Context` |
| `--durable-home` | `-DurableHome` |
| `--expected-marketplace-id` | `-ExpectedMarketplaceId` |
| `--expected-payload-root`, `--expected-payload-version` | `-ExpectedPayloadRoot`, `-ExpectedPayloadVersion` |
| `--snapshot-id`, `--runtime-version` | `-SnapshotId`, `-RuntimeVersion` |
| `--expected-namespace-generation`, `--expected-install-generation` | `-ExpectedNamespaceGeneration`, `-ExpectedInstallGeneration` |
| `--expected-current-version` or `--expect-current-absent` | `-ExpectedCurrentVersion` or `-ExpectCurrentAbsent` |
| `--expected-last-known-good-version` or `--expect-last-known-good-absent` | `-ExpectedLastKnownGoodVersion` or `-ExpectLastKnownGoodAbsent` |
| `--maintenance-token` | `-MaintenanceToken` |

Deactivation requires the explicit activation target below and exactly one
rollback selector:

| Bash argument | PowerShell parameter |
|---|---|
| `--context` | `-Context` |
| `--durable-home` | `-DurableHome` |
| `--expected-marketplace-id` | `-ExpectedMarketplaceId` |
| `--expected-namespace-generation`, `--expected-install-generation` | `-ExpectedNamespaceGeneration`, `-ExpectedInstallGeneration` |
| `--expected-activation-generation` | `-ExpectedActivationGeneration` |
| `--expected-tombstone-activation-generation` or `--expect-tombstone-absent` | `-ExpectedTombstoneActivationGeneration` or `-ExpectTombstoneAbsent` |
| `--maintenance-token` | `-MaintenanceToken` |

Legacy-wrapper retirement requires these explicit inputs:

| Bash argument | PowerShell parameter |
|---|---|
| `--context` | `-Context` |
| `--durable-home` | `-DurableHome` |
| `--expected-marketplace-id` | `-ExpectedMarketplaceId` |
| `--expected-payload-root`, `--expected-payload-version` | `-ExpectedPayloadRoot`, `-ExpectedPayloadVersion` |
| `--snapshot-id`, `--runtime-version` | `-SnapshotId`, `-RuntimeVersion` |
| `--expected-namespace-generation`, `--expected-install-generation` | `-ExpectedNamespaceGeneration`, `-ExpectedInstallGeneration` |
| `--expected-activation-generation` | `-ExpectedActivationGeneration` |
| `--maintenance-token` | `-MaintenanceToken` |

Repair recreates only the derived schema-4 deploy manifest and exact intended
current/LKG selection, from validated immutable completion and snapshot evidence.
It preserves current receipt payload provenance separately from the selected
historical runtime. Missing completion refuses repair. Agent Machines owns no
additional cell-local launch metadata: commands remain in the payload, so repair
does not invent launchers. It never rebuilds payloads, snapshots, ownership,
completion, state, caches, services, tasks, endpoints, or external resources.

Legacy attribution is explicit and idempotent. Under the legacy provisioning
lock, the marketplace genesis lock, the cell install lock, and any applicable
maintenance token, it inventories the declared legacy filesystem footprint,
attributes only clearly owned entries to the named destination cell, writes the
legacy ownership tombstone with an explicit item list, and publishes a
generation-pinned namespaced activation with `legacy.disposition:
retained-inert`. If the legacy root is missing, linked/reparsed, already owned
by another cell, or otherwise ambiguous/orphaned, the command preserves that
state unchanged and reports it for deliberate resolution.

Legacy-wrapper retirement is equally explicit and idempotent. `cell-retire-legacy`
targets only the declared global `agent-machines` compatibility binstubs under
`~/.local/bin/`, and only after the current cell can prove both ownership and
health: the tombstone must still belong to the current activation generation,
and the active runtime must still validate from immutable slot-completion,
current/LKG marker, and schema-4 deploy-manifest evidence. On success it
removes only the ownership-matched wrapper files, leaves legacy durable state
and rollback evidence intact, and writes an auditable record under
`retirements/`. Replaying the same target reports `already-retired`; missing
attribution or unhealthy runtime preserves the wrapper unchanged.

Deactivation is equally explicit and idempotent. `cell-deactivate` always
targets one exact activation generation and either an exact tombstone activation
generation to roll back or an explicit proof that no tombstone exists. Under
maintenance admission and the same ownership locks, it publishes the next
`legacy`/`deactivated` activation generation, writes an auditable deactivation
record under `deactivations/`, and only then clears the matched tombstone. A
replay of the same target reports `already-rolled-back` or
`already-deactivated`; an ambiguous tombstone state is preserved unchanged.

Uninstall requires an absent or explicitly deactivated activation and no live
owned runtime processes. It preflights the entire owned inventory, CAS-clears
both selection markers, removes all owned historical slots and snapshots, then
derived deploy/launcher/run/log/cache artifacts. Each deletion revalidates the
canonical receipt chain, exact generations, selection, ownership, and target
identity. Unknown, foreign, malformed, linked, or otherwise ambiguous artifacts
refuse removal. Ordinary POSIX venv interpreter links and the exact `lib64 -> lib`
scaffolding may be unlinked without following them; all other links are refused.

`state/`, namespace/install/activation receipts, `deactivations/`, lock
infrastructure, and empty attributable plugin directories remain. Namespace
garbage collection and legacy migration are separate operations. Repeated repair reports
`reason: cell-repair-healthy`; repeated uninstall reports `status: preserved`.
Generation or selection drift reports `status: revalidation-required`; callers
must inspect JSON status, not merely exit 0. Interrupted reservation release is
the shared library's explicit `slot-release`, never automatic cleanup.
When applicable user-wide or plugin-scoped maintenance is active, deactivation,
repair, and uninstall also require the exact token returned by the shared
`maintenance-enter` action; a missing or mismatched token is refused without
mutating the cell.

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
agent-machines restore --only git-config --json # exact Git-file preview; no live edit
agent-machines self-update install      # register opted-in Scheduled Tasks
agent-machines self-update status       # show opt-in + task presence
agent-machines self-update uninstall    # remove registered self-update tasks
agent-machines self-update run --tier watchdog
agent-machines self-update run --tier sweep --json
agent-machines fleet-update install     # register the opted-in fleet-update sweep task
agent-machines fleet-update status      # show opt-in + task presence
agent-machines fleet-update uninstall   # remove the registered fleet-update task
agent-machines fleet-update run --tier sweep --json
agent-machines provision-playwright-cli # preview user-home package/skill changes
agent-machines provision-playwright-cli --apply
agent-machines provision-playwright-cli --json
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
requested. `self-update install` resolves the declarative `self-update`
resources first and only attempts Scheduled Task registration for tiers whose
resolved state is `present`; the registered task/timer invokes the stable
`agent-machines` binstub so runtime slot cutovers do not strand it on an old
interpreter. The sweep tier likewise drives the owning repo's `update`
command, then runs `restore --apply` through the same stable `agent-machines`
binstub. `restore --apply` reconciles the same task presence
declaratively, including removing tasks that have since been opted out.
`--verbose`, and always present in `--json`.

## Playwright CLI provisioning

`provision-playwright-cli` converges only machine-local state for the current
user. It detects `node` and `npm`, validates npm's JavaScript CLI entry point
against the detected Node installation, resolves one npm prefix contained by
the requested user home, ensures the registry's current `@playwright/cli`
version, and initializes the Playwright CLI workspace from the user's home
directory:

- `~/.agents/skills/playwright-cli/` -- the personal Agent Skills registration
  recognized by Copilot CLI, byte-matched to the package-bundled skill tree;
- `~/.playwright/cli.config.json` -- Playwright CLI's user-home workspace
  configuration.

The command is a dry-run by default; `--dry-run` is the explicit equivalent and
`--apply` performs missing or stale work. Every run queries `npm prefix -g`,
accepts that configured prefix only when it resolves inside the requested home,
and otherwise uses a user-scoped fallback (`~/AppData/Roaming/npm` on Windows,
`~/.local` on POSIX/WSL). The resolved fallback is revalidated against the
resolved home before use. It does not rewrite npm configuration. Package
detection, registry lookup, installation, and root discovery all receive the
same explicit `--prefix`, so a system-wide installation cannot satisfy the
user-scoped postcondition.

npm's CLI entry point is trusted as part of the detected Node/npm prerequisite,
not because it is under the user home. The provisioner accepts only the npm
package layout physically contained by the resolved Node installation prefix
(including the common POSIX npm command symlink into that layout). A linked or
reparse-point npm package root is rejected rather than trusted relative to
itself. It invokes every npm operation as `node <npm-cli.js> ...`; shell and
batch shims are never executed.

Dry-run is read-only but necessarily contacts the npm registry through
`npm view @playwright/cli version --json`; failure to determine `latest` is a
structured failure rather than stale success. A missing or version-mismatched
package plans `npm install -g @playwright/cli@latest --prefix <prefix>`.
Apply runs it, re-queries the installed version, requires an exact match to the
previously observed registry version, and may report `playwright-cli.cmd` from
the Windows prefix root or `bin/playwright-cli` beneath a POSIX prefix when the
expected command resolves inside the selected prefix and home. Those command
shims are informational only and are never the trusted apply path.

The provisioner obtains the package root with
`npm root -g --prefix <prefix>`, validates the bundled
`@playwright/cli/playwright-cli.js` entry point within that root, validates the
`@playwright/cli` package's `skills` / `playwright-cli` directory and its
non-empty `SKILL.md`, then compares every regular file in the bundled and
registered trees by relative path and content hash.
Missing, extra, empty, unreadable, or byte-different files make registration
stale. Symlinks, junctions, and other Windows reparse points are rejected in
the managed `~/.agents` and `~/.playwright` path chains and in either skill
tree, including during dry-run. The bundled tree must remain physically inside
the validated package, npm root, selected prefix, and home. Stale registration,
and every package update, plans or runs the package entry point as
`node <playwright-cli.js> install --skills agents`; apply then requires the
complete trees to match. Skill traversal hashes files incrementally under
file-count, byte-count, and shared deadline bounds. Every subprocess, including apply, runs with the user
home as `cwd`; no project checkout content is registered or mutated. Commands
share one bounded provision deadline below the module runner's outer timeout.
Each command has a smaller cap and reserves cleanup time. Windows gates the
launcher into a kill-on-close Job Object before Node can start; POSIX uses a
new process group. Normal exit and every failure path verify that the complete
group/tree is empty, escalating to forced cleanup within a bounded wait. A
timeout only then returns partial output with exit code `124`.
Unexpected subprocess I/O failures use the same contained cleanup path before
returning structured exit code `126` evidence.

Missing prerequisites, failed commands, malformed package-query output, and
missing postconditions exit `2`. Human output includes bounded stdout/stderr
evidence for every command that ran; `--json` emits the same evidence in a
stable schema:

```json
{
  "schema_version": 1,
  "operation": "provision-playwright-cli",
  "ok": true,
  "mode": "dry-run",
  "home": "<home>",
  "changes_needed": false,
  "changed": false,
  "prerequisites": {
    "node": {"available": true, "path": "<node>"},
    "npm": {
      "available": true,
      "path": "<npm>",
      "cli_path": "<node-install>/node_modules/npm/bin/npm-cli.js"
    }
  },
  "npm": {
    "configured_prefix": "<configured-prefix>",
    "prefix": "<user-prefix>",
    "prefix_source": "configured",
    "root": "<user-prefix>/node_modules"
  },
  "package": {
    "name": "@playwright/cli",
    "installed": true,
    "version": "<version>",
    "latest_version": "<version>"
  },
  "cli": {
    "command": "playwright-cli",
    "available": true,
    "path": "<playwright-cli>",
    "entrypoint": "<user-prefix>/node_modules/@playwright/cli/playwright-cli.js"
  },
  "skill": {
    "path": "<home>/.agents/skills/playwright-cli",
    "bundle_path": "<user-prefix>/node_modules/@playwright/cli/skills/playwright-cli",
    "bundle_valid": true,
    "registered": true,
    "file_count": 11
  },
  "workspace_config": {
    "path": "<home>/.playwright/cli.config.json",
    "present": true
  },
  "actions": [],
  "commands": [
    {
      "argv": ["<node>", "<npm-cli.js>", "prefix", "-g"],
      "cwd": "<home>",
      "returncode": 0,
      "stdout_tail": "<configured-prefix>",
      "stderr_tail": ""
    },
    {
      "argv": ["<node>", "<npm-cli.js>", "view", "@playwright/cli", "version", "--json", "--prefix", "<user-prefix>"],
      "cwd": "<home>",
      "returncode": 0,
      "stdout_tail": "\"<version>\"",
      "stderr_tail": ""
    },
    {
      "argv": ["<node>", "<npm-cli.js>", "list", "-g", "@playwright/cli", "--depth=0", "--json", "--prefix", "<user-prefix>"],
      "cwd": "<home>",
      "returncode": 0,
      "stdout_tail": "<package-json>",
      "stderr_tail": ""
    },
    {
      "argv": ["<node>", "<npm-cli.js>", "root", "-g", "--prefix", "<user-prefix>"],
      "cwd": "<home>",
      "returncode": 0,
      "stdout_tail": "<user-prefix>/node_modules",
      "stderr_tail": ""
    }
  ],
  "error": null
}
```

Requirement packages consume the subcommand through agent-machines' existing
payload command. Invocation platforms use the engine's exact platform keys:
`windows`, `linux`, and `wsl`.

```yaml
modules:
  - name: playwright-cli
    invocation:
      plugin: agent-machines@copilot-extensions
      command: agent-machines
      platforms: [windows, linux, wsl]
      arguments: [provision-playwright-cli]
      dry_run_arguments: [--dry-run]
      apply_arguments: [--apply]
```

The provisioner does not manage browser profiles, credentials, application
navigation, or product-specific test policy. Those remain downstream.

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
.copilot-extensions/agent-machines/
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
`hostname` and `alias`; these remain accepted for package
gates, `per-machine` overlays, nested module/resource gates, and machine
directory selection. Actual identity-field conflicts fail before reconciliation.
`display_name` is metadata and may be duplicated, including between a host and
its guest. Unique legacy display-name selectors/gates remain compatible unless
they shadow an identity field; duplicated display names are not accepted as
gates and cannot select a machine. Use a key, hostname, or alias instead.
Native Windows and Linux retain the host identity. WSL defaults to `<host>-wsl`
(never double-suffixed), with guest topology resolved separately even when its
`hostname` is the Windows host name. Guest gates never accept the host's raw
hostname or host-only labels. Without a guest topology entry the qualified
identity remains usable, with a diagnostic instead of a host fallback.
`--machine` is an explicit target selector and is not reinterpreted as the
caller's local guest. SSH environment aliases remain independently configured
transport labels. Existing durable ownership references are not rewritten.

`discover.current_machine()` is the execution identity used by package discovery,
not physical hardware inventory. Default library discovery and the standalone
discovery module use the same resolver and accepted topology labels as the main
CLI; their platform path-key comes from that resolver too. A diagnostic
`raw: ...` label names the OS hostname only and does not grant it guest package
scope. Explicit discovery target selectors remain unchanged.

Machine identity uses the shared `machine_transport` owner model. Its read-only
logical projection expands legacy Windows/WSL topology into `<base>` and
`<base>-wsl`, preserving profile aliases and exposing `execution_platform` and
`physical_host` on `MachineIdentity` for display grouping. Explicit independent
entries retain their keys. Automatic local resolution knows its platform;
explicit physical-host selection requires
`resolve_machine(..., execution_platform="windows"|"wsl"|"linux")` and refuses
multiple eligible siblings. WSL gates accept its own logical key/profile aliases,
not the native key or shared physical hostname. No package, persisted registration,
or repository topology is rewritten; move any WSL-specific package gates to the
logical WSL key through normal repository maintenance.

Across adopted repositories, duplicate logical keys must agree on execution
platform and physical host. SSH connection details such as port, user and shell
do not define machine identity; profile aliases are combined and still checked
for cross-machine ambiguity. Legacy and explicit logical declarations can
therefore coexist without requiring identical transport configuration.

The canonical root is `.copilot-extensions/agent-machines/`. Legacy
`.agent-machines/` and `.github/machine-state/` remain bounded fallbacks only
when the canonical root is absent. An explicit marketplace-specific overlay may
live under `.copilot-extensions/agent-machines/marketplaces/<marketplace-id>/`.
Move a repo atomically: once the canonical root exists, legacy files in that
repo are ignored.

Use `agent-machines doctor` to find legacy, mixed, or malformed layouts across
adopted repos. `agent-machines migrate --repo <name-or-path>` previews a
behavior-preserving migration: legacy YAML files move byte-for-byte into
`.copilot-extensions/agent-machines/all/`, preserving gates, and a legacy
`README.md` moves to the canonical root. Re-run with `--apply` to perform it.
Migration refuses mixed
layouts, destination collisions, nested content, and unknown legacy entries
rather than guessing. Reorganizing a migrated package into `machines/<machine>/`
is a separate explicit edit.

A package under `.copilot-extensions/agent-machines/all/` has this shape. Schema v4 is required
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

Each `modules:` declaration may optionally set `maintenance_safe: true` to
include that individually trusted command in blanket `restore --maintenance-safe`
runs. The default is off: omitted or `false` modules are reported as skipped.
An explicitly declared value must be a boolean; strings, numbers, null, lists
and mappings are rejected during manifest loading. This additive field works
with supported requirement-package schema versions; no schema-version change
is needed because older runtimes continue to skip all modules during blanket
maintenance-safe restore.

This is consent to run selected trusted repo-local code unattended, not a
sandbox or a guarantee that arbitrary commands are safe. Review the module's
effects before opting it in. The flag changes no existing declarations,
machine/platform gates, source admission, dry-run requirements or failure
reporting. Plain restore runs applicable modules as before. Explicit
`--only <module>` remains operator-directed and can select a module without
the flag; unselected modules do not execute. During dry-run, opted-in modules
still require platform `dry_run_args` or invocation `dry_run_arguments`.

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

Plan JSON (also nested under `plan` in restore JSON) exposes `managed_keys`.
Its `copilot.settings` list contains the sorted, unique top-level settings keys
declared by applicable `ensure-present` and `enforce` contributions, including
grouped declarations and per-machine overrides. Nonempty `ensure-absent`
activation requests contribute `enabledPlugins`; other dispositions do not.
The list is empty when no settings operations apply. It contains key names only,
never desired values, and describes the selected repository/project scope, not
live state or an `--only` restore filter. Compare it with surface `applied_keys`
to check coverage; a removal-only key is reported as applied only when a live
entry was actually removed.

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


### Emergency pre-tool workflow guards

The default installation supports `agent-machines pretool-killswitch on
"<reason>"`, `status --json` and `off`. This explicitly pauses suite-owned
preToolUse workflow guards, not native Copilot permissions or other hook events.
For scope, hot-session delivery, installation-cell isolation and rollback, see
[`libs/bootstrap-killswitch/README.md`](../../libs/bootstrap-killswitch/README.md#emergency-pre-tool-workflow-guard-switch).
The control is inactive until explicitly enabled; a deployed release alone does
not activate it.
