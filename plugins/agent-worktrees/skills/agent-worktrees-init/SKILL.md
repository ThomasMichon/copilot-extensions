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

## What It Creates

```
~/.agent-worktrees/
├── .venv/              ← Python venv with pyyaml
├── lib/
│   └── agent_worktrees/  ← Python package
└── bin/
    ├── launch-session.ps1
    ├── launch-session.sh
    └── launch-session.cmd

~/.local/bin/
├── agent-worktrees.cmd    (Windows)
└── agent-worktrees        (Linux/macOS)
```

## Prerequisites

- Python 3.10+ on PATH
- Git 2.15+ (worktree support)
- PowerShell 7+ (Windows) or bash (Linux)

## Installation Steps

### 1. Locate plugin source

The plugin bundles the full Python package and shell wrappers. Find the
plugin install directory:

```powershell
# Windows — find the plugin's installed location
$pluginDir = Get-ChildItem -Recurse -Path "$env:USERPROFILE\.copilot\installed-plugins" -Filter "pyproject.toml" |
    Where-Object { $_.FullName -like "*agent-worktrees*" } |
    Select-Object -First 1 -ExpandProperty DirectoryName
```

```bash
# Linux/macOS
plugin_dir=$(find ~/.copilot/installed-plugins -path "*/agent-worktrees/pyproject.toml" -exec dirname {} \; | head -1)
```

### 2. Create runtime directory

```powershell
# Windows
$installDir = Join-Path $env:USERPROFILE ".agent-worktrees"
New-Item -ItemType Directory -Path $installDir -Force
New-Item -ItemType Directory -Path "$installDir\lib" -Force
New-Item -ItemType Directory -Path "$installDir\bin" -Force
```

```bash
# Linux
install_dir="$HOME/.agent-worktrees"
mkdir -p "$install_dir"/{lib,bin}
```

### 3. Create venv and install package

```powershell
# Windows
python -m venv "$installDir\.venv"
& "$installDir\.venv\Scripts\pip" install --quiet "$pluginDir"
```

```bash
# Linux
python3 -m venv "$install_dir/.venv"
"$install_dir/.venv/bin/pip" install --quiet "$plugin_dir"
```

### 4. Copy shell wrappers

Copy from the plugin's `bin/` directory to `~/.agent-worktrees/bin/`:

```powershell
# Windows
Copy-Item "$pluginDir\bin\launch-session.*" "$installDir\bin\" -Force
```

```bash
# Linux
cp "$plugin_dir"/bin/launch-session.* "$install_dir/bin/"
chmod +x "$install_dir"/bin/*.sh
```

### 5. Deploy binstubs

Create thin wrappers in `~/.local/bin/` so `agent-worktrees` is on PATH:

**Windows (`agent-worktrees.cmd`):**
```bat
@echo off
"%USERPROFILE%\.agent-worktrees\.venv\Scripts\python.exe" -m agent_worktrees %*
```

**Linux (`agent-worktrees`):**
```bash
#!/usr/bin/env bash
exec "$HOME/.agent-worktrees/.venv/bin/python" -m agent_worktrees "$@"
```

### 6. Copy terminal multiplexer config

```powershell
Copy-Item "$pluginDir\terminal\psmux.conf" "$env:USERPROFILE\.psmux.conf" -Force
```

### 7. Verify

```
agent-worktrees --help
```

If `agent-worktrees` is not found, ensure `~/.local/bin` is on PATH.

## Update Flow

When the plugin is updated (`copilot plugin update agent-worktrees`),
re-run init to pick up new source:

```powershell
& "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\pip" install --upgrade "$pluginDir"
```

## Next Step

After init completes, `cd` into a repo and run the `agent-worktrees-adopt`
skill to register it as a managed project.
