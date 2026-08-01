# Agent Bridge -- Getting Started

Set up agent-bridge from scratch. Assumes Copilot CLI is installed and
agent-worktrees is already set up (it's a prerequisite).

## 1. Install the Plugin

If you haven't registered the marketplace yet:

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
```

Each plugin installs individually. For full functionality (including the
`codespace:` resolver + credential relay) install all three — the bridge
imports agent-codespaces at startup:

```bash
copilot plugin install agent-worktrees@copilot-extensions
copilot plugin install agent-codespaces@copilot-extensions
copilot plugin install agent-bridge@copilot-extensions
```

All three ship from the same repo.

## 2. Bootstrap the Service

> **Prerequisite:** The `agent-codespaces` plugin must be installed in
> the agent-bridge venv for the integrated credential relay to start.
> When installed as a sibling plugin through the marketplace, no
> separate relay setup is required.

`copilot plugin install` only vendors the plugin **payload** into
`~/.copilot/installed-plugins/`. agent-bridge is a **Python package**
(`plugins/agent-bridge/src/agent_bridge` plus vendored `libs/`); the installer
below deploys its **runtime** — it builds a venv with `uv venv` and installs the
package (and the `agent_codespaces`/`agent_containers` resolver packages) with
`uv pip install` under `~/.agent-bridge/venv`, then registers the always-on
service. (`uv` is bootstrapped automatically if missing; nothing here uses
`uvx`/`pipx`.) A full update is two steps: `copilot plugin update` (payload)
**then** `scripts/install.* update` (runtime).

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
pwsh -NoProfile -ExecutionPolicy Bypass -File "$abDir\scripts\install.ps1" install
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

Credential relay:
  Ephemeral (discovered)   Starts with agent-bridge; binds an OS-assigned
                           loopback port and publishes it for clients. No
                           separate relay setup needed when agent-codespaces
                           is installed as a sibling plugin
```

The credential relay is part of agent-bridge startup. If the
`agent-codespaces` plugin is installed into the same venv, agent-bridge
starts the relay automatically on an OS-assigned ephemeral loopback port
(published for SSH-tunnel clients to discover).

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

If your repo has a `machines.yaml`:

```bash
agent-bridge config adopt --repo /path/to/repo --profile my-project
```

This auto-discovers `machines.yaml` and creates a topology profile; the agent
roster is **derived** from it (+ `.agent-worktrees/related.yaml`). See
[Machine Configuration](machine-config.md) for the full guide on the
`machines.yaml` format and the derived roster.

### Option B: Manual config

Edit `~/.agent-bridge/config.yaml` directly:

```yaml
port: 0               # dynamic by default: OS-assigned ephemeral, advertised via active.json (set a positive port only to pin)
bind: 127.0.0.1
log_level: info

topologies:
  my-project:
    machines_yaml: /path/to/machines.yaml
    # agents_config: /path/to/acp-agents.json   # deprecated override (optional)
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
agent-bridge status                 # prints the live loopback URL (dynamic port)
# then health-check that URL, e.g.:
# curl http://127.0.0.1:<port>/health
```

> **Port note:** the bridge binds an **OS-assigned ephemeral** loopback port by
> default (dotfiles #694) and advertises the actual port via its routing table
> (`active.json`), so nothing well-known (9280/9281) is reserved and there is no
> Windows/WSL collision to design around. `agent-bridge status` prints the live
> port; always use it (never a hardcoded number) when probing health. Pin a
> fixed port only for debugging via `--port` or a positive `port:` in config.

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
