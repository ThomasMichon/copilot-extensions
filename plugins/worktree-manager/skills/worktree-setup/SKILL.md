---
name: worktree-setup
description: >
  Bootstrap and manage the worktree-manager runtime — install the Python
  package, create venv, generate binstubs, verify installation, and update.
  Use this skill when the runtime is not installed, when the bootstrap check
  fires, or when the user asks to set up or update worktree-manager.
  Trigger phrases include:
  - 'set up worktree-manager'
  - 'install worktree-manager'
  - 'bootstrap worktree'
  - 'worktree-manager not found'
  - 'runtime not installed'
  - 'update worktree-manager runtime'
---

# Worktree Manager Setup

Bootstrap the worktree-manager Python runtime from this plugin's bundled
source. The plugin delivers skills and hooks automatically; this skill
handles the **runtime** — the Python package, venv, and binstubs that
power the CLI.

## Prerequisites

- Python 3.10+ (`python3` or `python` on PATH)
- `pip` (bundled with Python)
- Git

## Install Flow

### 1. Locate the plugin source

The Python package is bundled in this plugin's install directory. Find it:

```powershell
# Windows
$pluginDir = Join-Path $env:USERPROFILE ".copilot\installed-plugins" | Get-ChildItem -Recurse -Filter "pyproject.toml" | Where-Object { $_.FullName -like "*worktree-manager*" } | Select-Object -First 1 -ExpandProperty DirectoryName
```

```bash
# Linux/macOS
plugin_dir=$(find ~/.copilot/installed-plugins -path "*/worktree-manager/pyproject.toml" -exec dirname {} \; | head -1)
```

### 2. Create venv and install

```powershell
# Windows
$venvDir = Join-Path $env:USERPROFILE ".worktree-manager\.venv"
python -m venv $venvDir
& "$venvDir\Scripts\pip" install $pluginDir
```

```bash
# Linux/macOS
venv_dir="$HOME/.worktree-manager/.venv"
python3 -m venv "$venv_dir"
"$venv_dir/bin/pip" install "$plugin_dir"
```

### 3. Generate binstubs

Create a `worktree-manager` binstub in `~/.local/bin/`:

**Windows (`worktree-manager.cmd`):**
```bat
@echo off
"%USERPROFILE%\.worktree-manager\.venv\Scripts\python.exe" -m worktree_manager %*
```

**Linux (`worktree-manager`):**
```bash
#!/usr/bin/env bash
exec "$HOME/.worktree-manager/.venv/bin/python" -m worktree_manager "$@"
```

### 4. Verify

```
worktree-manager --help
```

## Update Flow

When the plugin is updated (`copilot plugin update worktree-manager`),
re-run the install step to pick up new source:

```powershell
& "$env:USERPROFILE\.worktree-manager\.venv\Scripts\pip" install --upgrade $pluginDir
```

## Uninstall / Cleanup

The plugin uninstall (`copilot plugin uninstall worktree-manager`) removes
skills and hooks but leaves the runtime intact. To fully clean up:

```powershell
# Windows
Remove-Item -Recurse -Force "$env:USERPROFILE\.worktree-manager"
Remove-Item "$env:USERPROFILE\.local\bin\worktree-manager.cmd" -ErrorAction SilentlyContinue
```

```bash
# Linux
rm -rf ~/.worktree-manager
rm -f ~/.local/bin/worktree-manager
```

## Project Registration

After the runtime is installed, register a project to use worktree
isolation:

```
worktree-manager install --machine <machine-name>
```

This creates `~/.{project}/config.yaml` and a project-specific binstub.
See the `worktree` skill for lifecycle details.
