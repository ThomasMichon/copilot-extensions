# Agent Bridge -- Getting Started

Set up agent-bridge from scratch. Assumes Copilot CLI is installed and
agent-worktrees is already set up (it's a prerequisite).

## 1. Install the Plugin

If you haven't registered the marketplace yet:

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
```

The agent-bridge plugin installs alongside agent-worktrees automatically
when you install the marketplace. Both plugins ship from the same repo.

## 2. Bootstrap the Service

Start a Copilot CLI session and say:

> *"set up agent-bridge"*

This invokes the `copilot-extensions-setup` skill, which runs the
platform-specific installer.

### Manual install (alternative)

```powershell
# Windows
$abDir = Get-ChildItem -Recurse "$env:USERPROFILE\.copilot\installed-plugins" -Filter plugin.json |
    Where-Object { (Get-Content $_.FullName -Raw) -match '"agent-bridge"' } |
    Select-Object -First 1 -ExpandProperty DirectoryName
powershell -NoProfile -ExecutionPolicy Bypass -File "$abDir\scripts\install.ps1" install
```

```bash
# Linux/WSL
ab_dir=$(find ~/.copilot/installed-plugins -name plugin.json \
    -exec grep -l agent-bridge {} \; | head -1 | xargs dirname)
bash "$ab_dir/scripts/install.sh" install
```

### What this creates

```
~/.agent-bridge/
  venv/                    Python venv (fastapi, uvicorn, acp SDK)
  config.yaml              Runtime config (port, bind, topology)
  auth.yaml                Bearer auth token (generated on first run)
  sessions.db              SQLite database (created on first start)
  deploy-manifest.json     Install provenance

~/.local/bin/
  agent-bridge[.cmd]       Binstub

Platform service:
  Windows:   "Agent Bridge" scheduled task (at-logon, 15s delay)
  Linux/WSL: ~/.config/systemd/user/agent-bridge.service (enabled)
```

### Verify

```bash
agent-bridge version
agent-bridge status
```

If `agent-bridge` is not found, ensure `~/.local/bin` is on PATH.

## 3. Configure Machine Topology

Agent-bridge needs to know which machines and agents are available. This
is done via **topology profiles** in `config.yaml`.

### Option A: Auto-adopt from a repo (recommended)

If your repo has a `machines.yaml` and/or `acp-agents.json`:

```bash
agent-bridge config adopt --repo /path/to/repo --profile my-project
```

This auto-discovers config files and creates a topology profile. See
[Machine Configuration](machine-config.md) for the full guide on
`machines.yaml` and `acp-agents.json` formats.

### Option B: Manual config

Edit `~/.agent-bridge/config.yaml` directly:

```yaml
port: 9280
bind: 127.0.0.1
log_level: info

topologies:
  my-project:
    machines_yaml: /path/to/machines.yaml
    agents_config: /path/to/acp-agents.json
```

### Verify topology

```bash
agent-bridge config show
agent-bridge config validate
```

## 4. Start the Service

The installer registers a platform service that starts automatically.
To start manually:

```bash
agent-bridge start
```

### Verify it's running

```bash
agent-bridge status
curl http://localhost:9280/health
```

## 5. Test It

```bash
# List available machines
agent-bridge machines

# List available agents
agent-bridge agents

# Send a prompt to an agent
agent-bridge send my-agent "Hello, are you there?"
```

## Updating

### Via the installer

```bash
# From the plugin directory
install.ps1 update    # Windows
install.sh update     # Linux/WSL
```

### Via a project binstub (if configured)

```bash
# Project binstubs can dispatch to the installer
<project> services agent-bridge update
```

## Migration from Old Installer

If the machine previously used a project binstub (e.g. `<project>
services agent-bridge install`), the plugin installer detects this
automatically: stops the old service, preserves config/auth/DB, and
replaces the service registration with plugin-owned versions.

## Next Steps

- [Machine Configuration](machine-config.md) -- detailed topology setup
- [Architecture](architecture.md) -- service internals and API
- [CLI skill](../skills/agent-bridge/SKILL.md) -- full CLI command reference
