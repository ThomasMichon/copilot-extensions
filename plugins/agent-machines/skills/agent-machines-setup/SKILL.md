---
name: agent-machines-setup
description: >
  Install, update, and author for the agent-machines runtime -- the portable
  restore-machinestate engine. Use this skill to deploy the agent-machines
  binstub/venv after a payload update, or to author requirement packages in a
  repo's .github/machine-state/ directory.
  Trigger phrases include:
  - 'install agent-machines'
  - 'update agent-machines'
  - 'set up agent-machines'
  - 'author a requirement package'
  - 'add a machine-state manifest'
  - 'agent-machines setup'
---

# agent-machines setup

## Install / update the runtime

`agent-machines` is a runtime CLI (a venv plus a `~/.local/bin/agent-machines`
binstub). On its gated machines it is reconciled automatically at session launch.
To (re)deploy the runtime from the source folder after a payload update:

```
# from the plugin's source dir (marketplace install path or a local checkout)
scripts/init.sh          # Linux / WSL / macOS
scripts/init.ps1         # Windows
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
several repos compose by union. The plugin's `enabledPlugins` union must retain
the stack-critical set (`agent-worktrees`, `agent-machines`) or the validator
errors.

Run `agent-machines validate` after authoring to catch conflicts.
