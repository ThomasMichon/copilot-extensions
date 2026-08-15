---
name: agent-machines-setup
description: >
  Install, update, and author for the agent-machines runtime -- the portable
  restore-machinestate engine. Use this skill to enable or repair the
  self-provisioning binstub/venv, inspect runtime readiness, or author
  requirement packages in a repo's .github/machine-state/ directory.
  Trigger phrases include:
  - 'install agent-machines'
  - 'update agent-machines'
  - 'set up agent-machines'
  - 'author a requirement package'
  - 'add a machine-state manifest'
  - 'agent-machines setup'
---

# agent-machines setup

> **Before you start — readiness (self-provisioning, no agent-worktrees required).**
> The runtime can run standalone. Discovery uses agent-worktrees registries when
> they exist, but missing registries just mean "no packages discovered." If the
> binstub is missing, stamp it from the installed payload; the first command then
> builds the venv on demand.
>
> Windows:
> ```powershell
> $s = Get-ChildItem "$env:USERPROFILE\.copilot\installed-plugins\*\agent-machines\scripts\init.ps1" | Select-Object -First 1
> & $s.FullName stamp
> ```
>
> POSIX:
> ```bash
> bash "$(ls ~/.copilot/installed-plugins/*/agent-machines/scripts/init.sh | head -1)" stamp
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
agent-machines version
```

## Author a requirement package

A **requirement package** is one YAML file under a repo's
`.github/machine-state/`. Minimal shape:

```yaml
schema_version: 1
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

**Value-shape guidance:** scalar singletons (`model`, `effortLevel`) are
`enforce`; maps/lists (`enabledPlugins`, `permissions`) are `ensure-present` so
several repos compose by union. Do not explicitly disable bootstrap-critical
plugins (`agent-worktrees`, `agent-machines`); if a package manages
`extraKnownMarketplaces`, include the bootstrap-critical `copilot-extensions`
marketplace or the validator errors.

Run `agent-machines validate` after authoring to catch conflicts.
