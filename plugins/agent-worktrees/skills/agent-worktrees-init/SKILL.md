---
name: agent-worktrees-init
description: >
  Install the agent-worktrees runtime — create Python venv, install the
  package, deploy shell wrappers and binstubs. Run this once per machine
  before adopting any repos. Trigger phrases include:
  - 'set up agent-worktrees'
  - 'install agent-worktrees'
  - 'bootstrap agent-worktrees'
  - 'agent-worktrees not found'
  - 'runtime not installed'
  - 'init worktree'
---

# Agent Worktrees Init

Install the agent-worktrees runtime from this plugin's bundled source.
Run this **once per machine** — it creates the shared runtime that all
adopted projects use.

The init script is idempotent — safe to re-run for repairs or upgrades.

## What It Creates

```
~/.agent-worktrees/
├── .venv/                ← Python venv with pyyaml
├── lib/
│   └── agent_worktrees/  ← Python package (file copy from plugin source)
├── bin/
│   ├── launch-session.ps1 / .sh / .cmd
│   ├── bootstrap-check.ps1  ← session-start auto-update hook
│   └── bootstrap-check.sh
└── deploy-manifest.json

~/.local/bin/
├── agent-worktrees.cmd    (Windows)
└── agent-worktrees        (Linux/macOS)
```

## Prerequisites

- Python 3.10+ on PATH
- Git 2.15+ (worktree support)

## How to Run

### Step 1 — Locate the plugin directory

The init script lives inside the installed plugin. Discover the plugin
install location:

```powershell
# Windows (PowerShell 5+ or pwsh)
$pluginDir = Get-ChildItem -Recurse -Path "$env:USERPROFILE\.copilot\installed-plugins" -Filter "plugin.json" |
    Where-Object { (Get-Content $_.FullName -Raw) -match '"agent-worktrees"' } |
    Select-Object -First 1 -ExpandProperty DirectoryName
```

```bash
# Linux/macOS
plugin_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-worktrees {} \; | head -1 | xargs dirname)
```

### Step 2 — Run init

```powershell
# Windows — works with both powershell.exe (5.1) and pwsh (7+)
powershell -NoProfile -ExecutionPolicy Bypass -File "$pluginDir\scripts\init.ps1"
```

```bash
# Linux/macOS
bash "$plugin_dir/scripts/init.sh"
```

The script handles everything: venv creation (prefers `uv`, falls back
to `python -m venv`), package deployment, binstub generation, and
verification.

### Step 3 — Verify

```
agent-worktrees --help
```

If `agent-worktrees` is not found, ensure `~/.local/bin` is on PATH.

## Update Flow

The plugin contributes a `sessionStart` hook that auto-detects when the
deployed runtime is stale (commit mismatch) and re-deploys the package
automatically. Manual updates are rarely needed.

To force a manual re-deploy, re-run the init script.

## Next Step

After init completes, `cd` into a repo and run:

```
agent-worktrees register <project-name> --repo-dir <path>
```

Or ask Copilot to adopt a repo with the `agent-worktrees-adopt` skill.
