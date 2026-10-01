---
name: agent-machines-setup
description: >
  Install, update, and author for the agent-machines runtime -- the portable
  restore-machinestate engine. Use this skill to enable or repair the
  self-provisioning binstub/venv, inspect runtime readiness, or change desired
  machine configuration by authoring requirement packages under a repo's
  .copilot-extensions/agent-machines/ namespace. Fleet-wide requests such as standardizing a
  setting, application, service, or default across machines are configuration
  changes and belong here; use restore-machinestate only to inspect or apply
  desired state that is already declared.
  Trigger phrases include:
  - 'install agent-machines'
  - 'update agent-machines'
  - 'set up agent-machines'
  - 'author a requirement package'
  - 'add a machine-state manifest'
  - 'change configuration across all machines'
  - 'ensure this setting on every machine'
  - 'make this the default on all machines'
  - 'agent-machines setup'
  - 'set up the machine maintenance task'
  - 'keep my machines healthy'
---

# agent-machines setup

> **Before you start — readiness (self-provisioning, no agent-worktrees required).**
> The runtime can run standalone. Discovery uses agent-worktrees registries when
> they exist, but missing registries just mean "no packages discovered." In an
> agent session, invoke the exact `argv` from the agent-machines session command
> catalog; the payload-local command provisions the runtime on first use. Do not
> search `PATH` or substitute a same-named command from another payload.
>
> Outside an agent session, stamp a management binstub from an explicitly chosen
> payload; the first command then builds the venv on demand.
>
> Windows:
> ```powershell
> $s = Join-Path '<explicit-payload-path>' 'scripts\init.ps1'
> & $s stamp
> ```
>
> POSIX:
> ```bash
> bash "<explicit-payload-path>/scripts/init.sh" stamp
> ```
>
> The first call may take ~30–120s. POSIX emits `::agent-provisioning::`; Windows
> prints a provisioning message. If provisioning fails, surface the exact message.

## Install / update the runtime

`agent-machines` is a runtime CLI (a venv plus a `~/.local/bin/agent-machines` <!-- marketplace-isolation: allow deployed-runtime-diagnostics -->
binstub; on Windows the executable shim is `agent-machines.cmd`). The
session-start hook reconciles the runtime only; it never runs machine-state
`restore`. To (re)deploy the runtime from the source folder after a payload
update:

```
# from the plugin's source dir (marketplace install path or a local checkout)
scripts\init.ps1         # Windows
scripts/init.sh          # Linux / WSL / macOS
```

Verify:

```
<catalog argv[0]> version                    # inside an agent session
<management-binstub-path> version            # outside a session
```

## Author a requirement package

> **Author it in your own consuming repo, never in copilot-extensions
> itself.** This plugin ships the mechanism; a requirement package declares
> *a consuming repo's* desired machine state, sometimes gated to a literal
> machine name. copilot-extensions is public and has no machine of its own
> to converge -- a package committed here would publish real machine
> names/topology to the world. Put it in the repo that actually adopts the
> pattern (e.g. your facility's own private control repo) instead. This is
> a hard-enforced rule, not just guidance:
> `tools/check-no-agent-machines-packages.py` (wired into CI and the
> pre-push hook) fails the build if any of the paths below ever appear in
> this repo's own tree.

A **requirement package** is one complete YAML file under either:

- `.copilot-extensions/agent-machines/all/` for shared packages; or
- `.copilot-extensions/agent-machines/machines/<machine>/` for packages implicitly scoped to one
  machine.

Package names must be unique across the shared and selected machine folders.
`<machine>` is the canonical key from a matching adopted-repository
`machines.yaml` entry. The raw `platform.node()` host name (Windows
`%COMPUTERNAME%`) resolves through the entry key, `hostname`, `alias`, and
`display_name`; every accepted identity matches gates, overlays, and machine
directories case-insensitively. Ambiguous topology matches fail closed. Without
a matching topology entry, the raw host name remains the standalone fallback.
Multi-machine packages belong in `all/` with an explicit `gate`.
Use a package-local `per-machine` block for partial overrides; files do not merge
across folders. Legacy `.agent-machines/` and `.github/machine-state/` are
consulted only when `.copilot-extensions/agent-machines/` is absent, so migrate
all of a repo's packages atomically.

Minimal shape:

```yaml
schema_version: 3
package: <owner>/<name>            # e.g. myrepo/copilot-defaults
gate: [this-machine, other-machine]  # omit or ["*"] for all machines
aliases:
  HOME:      { kind: home }
  REPO:      { kind: repo, name: myrepo }
  WORKTREES: { kind: worktree-glob, repo: myrepo }
manage:
  copilot.settings:                # -> ~/.copilot/settings.json
    disposition: enforce           # scalars: model, effortLevel, ...
    values: { model: <model>, effortLevel: high }
  copilot.permissions:             # -> ~/.copilot/permissions-config.json
    disposition: ensure-present    # union floor; never clobbers live grants
per-machine:                       # default <- per-machine (null unsets)
  other-machine:
    manage:
      copilot.settings:
        values: { model: null }
exclude:                           # capture must never serialize these
  - "mcp-oauth-config/**"
```

Machine gates and `per-machine` overlay keys are case-insensitive. Do not
declare two overlay keys that normalize to the same case-insensitive identity,
are empty, or carry surrounding whitespace; validation rejects the ambiguous
package.

**Value-shape guidance:** scalar singletons (`model`, `effortLevel`) are
`enforce`; maps/lists (`enabledPlugins`, `permissions`) are `ensure-present` so
several repos compose by union. Within `enabledPlugins`, a declared `false` is
an authoritative per-plugin tombstone while `true` remains additive and
preserves an operator opt-out. Tombstones require `schema_version: 2`, so older
exact-v1 runtimes reject the package before applying it. Do not explicitly disable bootstrap-critical
plugins (`agent-worktrees`, `agent-machines`); if a package manages
`extraKnownMarketplaces`, include the bootstrap-critical `copilot-extensions`
marketplace or the validator errors.

When a migration must run correctly on existing schema-v1 runtimes, use the
supported false-only `copilot.settings.plugin-tombstones` enforce group instead.
It may contain only `enabledPlugins.<plugin>: false` entries and preserves every
undeclared operator plugin.

**Recommended baseline `copilot.settings` values.** Beyond the bare minimal
shape above, a few settings are worth enforcing from day one on any Windows
harness, because the Copilot CLI features and behaviors they gate are
relied on broadly across this marketplace's plugins:

```yaml
manage:
  copilot.settings:
    disposition: enforce
    values:
      model: <model>
      effortLevel: high
      contextTier: <tier>           # e.g. "long_context" for large repos
      experimental: true            # several plugins rely on experimental-gated
                                     # features (fleet mode, dynamic retrieval,
                                     # assisted-approval)
  copilot.settings.powershell:
    # EXCEPTION to the list-shape guidance above: powershellFlags is an
    # ORDERED argv sequence of flag/value PAIRS, not an unordered set like
    # enabledPlugins -- `ensure-present` unions one token at a time, so if
    # the live list already has -WindowStyle paired with a different value,
    # the union floor appends only the new value, separating a flag from
    # its value and producing invalid argv. Use `enforce` (atomic replace)
    # here instead, even though it's list-valued.
    disposition: enforce
    values:
      # Suppresses the headed console window a naive pwsh.exe spawn otherwise
      # pops when Copilot runs a hook. Windows-only; harmless elsewhere.
      # Each flag/value is its OWN array item -- Copilot passes the array
      # straight through as argv tokens, one per item. A combined
      # single-string item ("-WindowStyle Hidden") is NOT a valid pwsh flag
      # and breaks parsing; confirmed empirically that the split form is
      # what actually suppresses the window.
      powershellFlags: ["-NoProfile", "-NoLogo", "-WindowStyle", "Hidden"]
```

`model`/`effortLevel`/`contextTier` are also independently guaranteed at
**launch time**, not just in the persisted settings file: `agent-worktrees`
re-expresses whatever is persisted there as explicit `--model`/
`--reasoning-effort`/`--context` CLI flags on every worktree
create/resume/recovery launch (unconditional, upstream behavior -- see
`plugins/agent-worktrees/docs/config-reference.md` § "Persisted model/effort/
context preference at launch"), because Copilot CLI has been observed to
ignore the persisted values alone at startup. Declaring these three in your
own `copilot.settings` package is still what feeds that mechanism the
correct values. That guarantee is not unconditional, though: an explicit
`--model`/`--reasoning-effort`/`--context` already present anywhere in the
assembled launch command always wins (never duplicated or overridden), and
ACP sessions (`--acp`) receive none of these injected flags at all --
Copilot ignores them in ACP mode, and `agent-bridge`'s ACP client carries
model/effort through its own configuration path there instead (context
tier is not yet carried through ACP -- a separate, tracked gap).

To make selected installed plugins repository-only, use schema v3 desired
absence. This removes user-global activation keys without uninstalling plugin
inventory:

```yaml
schema_version: 3
package: example/plugin-activation
manage:
  copilot.settings.plugin-activation:
    disposition: ensure-absent
    keys:
      enabledPlugins:
        - optional-plugin@example-marketplace
```

`ensure-absent` is valid only for this exact source-qualified key list. Restore
is dry-run-first, reports exact removals, and preserves unrelated settings.
Never use `exclude` for this purpose (`exclude` prevents secret capture) or
`prune` (garbage collection). Validation rejects value/removal conflicts and
protects `agent-worktrees`, `agent-machines`, and every declared bootstrap-floor
plugin.

Run `<catalog argv[0]> validate` after authoring to catch conflicts.

## Enable a regular unattended maintenance schedule (self-update watchdog)

> **"Set up the machine maintenance task" / "keep my machines healthy" --
> resolve the destination first, never author here.** This plugin (and this
> repo generally) ships only the mechanism; the actual opt-in package always
> belongs to *your own* config, never to `copilot-extensions` (see *Author a
> requirement package* above -- the same hard-enforced rule applies). Resolve
> where that config lives, in order:
>
> 1. **A bound knowledge/control repo**, if one is registered (check
>    `~/.agent-worktrees/config.yaml` / a project's own `config.yaml` for
>    `knowledge_repo`, or ask the operator). Author the package there, under
>    its own `.copilot-extensions/agent-machines/all/` (fleet-wide) or
>    `machines/<machine>/` (this machine only) -- see *Author a requirement
>    package*.
> 2. **No knowledge repo bound or reachable** -- fall back to the **user-scoped**
>    root, which needs no repo, registry, or adoption at all:
>    `~/.agent-machines/config/all/` (or `machines/<machine>/` for a
>    single-machine override). `discover()` always scans this location
>    alongside every adopted repo's own packages, so it works standalone
>    (agent-worktrees entirely absent) and is exactly as authoritative as a
>    repo-sourced package -- just without a repo, a PR, or a commit. Same
>    package shape as anywhere else (below); create the directory if absent.
>
> Either way, the result is **only a declarative opt-in package** -- see the
> `self-update` resource below. Actually registering the OS-level schedule
> (`self-update install`) is a separate, one-time, interactive step that still
> must run on the target machine after the package exists.

A reachable, logged-in machine can converge on its own, on a schedule, without
a live interactive session -- two independently-scheduled, independently-locked
tiers: `watchdog` (hourly; dtssh launcher liveness, dtssh host repair, and a
dtssh mesh refresh) and `sweep` (daily;
fast-forward pulls of discovered adopted repos, each owning repo's
`update --no-manager` flow, and `agent-machines restore <!-- marketplace-isolation: allow self-update-watchdog-description -->
--apply --all-projects --maintenance-safe`). Maintenance-safe sweep restores
always include `manage:` Copilot settings/permissions, include only resources
that declare `maintenance_safe: true`, and also allow already-installed pinned
package version realignment by default. Sweep no longer blanket-defers for an
unrelated live worktree session; git fast-forward safety still stays per-repo.
Neither tier mutates anything when its resolved config is "not opted in."

Opt in by declaring a `self-update` resource in a requirement package (`all/`
for a fleet default, `machines/<machine>/` for one machine -- an explicit
local declaration overrides a shared default, matching every other resource's
authority precedence). This package can live in a knowledge/control repo, or
(per the resolution above) in the home-relative user-scoped root -- the schema
is identical either way:

```yaml
schema_version: 3
package: <owner>/self-update-defaults
resources:
  - type: self-update
    tier: watchdog          # or sweep
    state: present          # present (default) opts in; absent opts out
```

Then register the actual OS scheduling (one-time, interactive, may require
elevation):

```
<catalog argv[0]> self-update install    # registers Scheduled Tasks for opted-in tiers
<catalog argv[0]> self-update status     # shows opt-in + registration state
<catalog argv[0]> self-update run --tier watchdog   # manual/on-demand invocation
<catalog argv[0]> self-update uninstall  # removes registered tasks
```

`install` resolves the declared config first and only attempts registration
for tiers resolved as `present`; a not-opted-in tier is never registered and
never prompts for elevation. `restore --apply` also reconciles Scheduled Task
presence as ordinary drift (registers a newly opted-in tier, removes a newly
opted-out one) so opting out never requires a separate uninstall step. See
[`docs/resources.md`](docs/resources.md#self-update) for the full resource
schema and conflict-resolution rules.

## Diagnose and migrate package layout

Run `<catalog argv[0]> doctor` to inspect every adopted repo for canonical,
legacy, mixed, malformed, unavailable, or absent package layouts. Use
`--repo <name-or-path>` to scope it and `--json` for structured output.

For a legacy-only repo:

```
<catalog argv[0]> migrate --repo <name-or-path>          # dry-run
<catalog argv[0]> migrate --repo <name-or-path> --apply  # move files
```

Migration preserves package bytes and gates, placing YAML in
`.copilot-extensions/agent-machines/all/`; it moves a legacy `README.md` to the
canonical root.
It refuses mixed layouts, collisions, nested content, and unknown entries.
Moving a package into `machines/<machine>/` remains a deliberate follow-up
because the engine does not infer machine scope from a gate.
