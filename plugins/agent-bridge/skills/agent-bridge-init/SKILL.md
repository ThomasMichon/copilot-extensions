---
name: agent-bridge-init
description: >
  Install the agent-bridge service from this plugin -- create Python venv,
  install the package, deploy binstub, register scheduled task (Windows) or
  systemd unit (Linux), and generate default config. Run once per machine.
  Trigger phrases include:
  - 'install agent-bridge'
  - 'set up agent-bridge'
  - 'bootstrap agent-bridge'
  - 'agent-bridge not installed'
  - 'agent-bridge not found'
  - 'init agent-bridge'
---

# Agent-Bridge Init

Install the agent-bridge service from this plugin's bundled source. Run
this **once per machine** -- it creates the persistent service runtime.

The install script is idempotent -- safe to re-run for repairs or upgrades.
It also detects and migrates from the old aperture-labs service installer
if present, preserving config, auth tokens, and session DB.

## What It Creates

```
~/.agent-bridge/
  venv/                    Python venv with fastapi, uvicorn, etc.
  config.yaml              Runtime config (port, bind, topology profiles)
  auth.yaml                Bearer auth token (generated on first run)
  sessions.db              SQLite session database (created on first start)
  agent-bridge.pid         PID file (when running)
  deploy-manifest.json     Install provenance

~/.local/bin/
  agent-bridge[.cmd]       Binstub

Windows: "Agent Bridge" scheduled task (at-logon, 15s delay)
Linux:   ~/.config/systemd/user/agent-bridge.service (enabled)
```

## Prerequisites

- Python 3.10+ on PATH
- `uv` recommended (falls back to pip)

## How to Run

### Step 1 -- Locate the plugin directory

The install script lives inside the installed Copilot CLI plugin:

```powershell
# Windows (PowerShell 5+ or pwsh)
$pluginDir = Get-ChildItem -Recurse -Path "$env:USERPROFILE\.copilot\installed-plugins" -Filter "plugin.json" |
    Where-Object { (Get-Content $_.FullName -Raw) -match '"agent-bridge"' } |
    Select-Object -First 1 -ExpandProperty DirectoryName
```

```bash
# Linux/macOS
plugin_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-bridge {} \; | head -1 | xargs dirname)
```

### Step 2 -- Run install

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File "$pluginDir\scripts\install.ps1" install
```

```bash
# Linux/macOS
bash "$plugin_dir/scripts/install.sh" install
```

### Step 3 -- Verify

```bash
agent-bridge version
agent-bridge status
```

If `agent-bridge` is not found, ensure `~/.local/bin` is on PATH:

```powershell
# Windows -- update current session PATH
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
```

```bash
# Linux -- update current session PATH
export PATH="$HOME/.local/bin:$PATH"
```

## Update Flow

To update an existing installation:

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File "$pluginDir\scripts\install.ps1" update
```

```bash
# Linux
bash "$plugin_dir/scripts/install.sh" update
```

This reinstalls the package from the plugin source, updates the binstub,
scheduled task/systemd unit, and deploy manifest. If the service was
running, it restarts automatically.

## Other Actions

```bash
# Check status (running, version, config, scheduled task/systemd)
install.ps1 status   # or install.sh status

# Start the service
install.ps1 start    # or install.sh start

# Stop the service
install.ps1 stop     # or install.sh stop

# Uninstall (preserves config by default)
install.ps1 uninstall            # or install.sh uninstall
install.ps1 uninstall -Purge     # or install.sh uninstall --purge
```

## Migration from aperture-labs Installer

If the machine previously used `aperture-labs services agent-bridge update`,
the plugin installer detects this automatically and:

1. Stops the old service instance
2. Preserves config.yaml, auth.yaml, and sessions.db
3. Replaces the scheduled task/systemd unit with plugin-owned versions
4. Writes a new deploy manifest (schema v2, plugin-sourced)

No manual migration steps needed. The old `services/agent-bridge/install.ps1`
in aperture-labs is retired -- do not use it after migrating.

## Next Step

After install, wire up topology for your repo using the
`agent-bridge-adopt` skill or the CLI:

```bash
agent-bridge config adopt --repo /path/to/my-repo --profile facility
```
