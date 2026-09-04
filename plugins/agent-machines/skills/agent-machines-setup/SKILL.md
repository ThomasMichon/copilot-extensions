---
name: agent-machines-setup
description: >
  Install, update, and author for the agent-machines runtime -- the portable
  restore-machinestate engine. Use this skill to enable or repair the
  self-provisioning binstub/venv, inspect runtime readiness, or change desired
  machine configuration by authoring requirement packages under a repo's
  .agent-machines/ namespace. Fleet-wide requests such as standardizing a
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

`agent-machines` is a runtime CLI (a venv plus a `~/.local/bin/agent-machines`
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

A **requirement package** is one complete YAML file under either:

- `.agent-machines/all/` for shared packages; or
- `.agent-machines/machines/<machine>/` for packages implicitly scoped to one
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
across folders. The legacy `.github/machine-state/` path is consulted only when
`.agent-machines/` is absent, so migrate all of a repo's packages atomically.

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
`.agent-machines/all/`; it moves a legacy `README.md` to the canonical root.
It refuses mixed layouts, collisions, nested content, and unknown entries.
Moving a package into `machines/<machine>/` remains a deliberate follow-up
because the engine does not infer machine scope from a gate.
