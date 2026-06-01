---
name: copilot-extensions-setup
description: >
  Install and adopt for both copilot-extensions plugins (agent-worktrees
  and agent-bridge) -- runtime bootstrap, repo adoption, topology wiring,
  and service registration. One skill for all setup flows. Trigger phrases
  include:
  - 'install agent-worktrees'
  - 'install agent-bridge'
  - 'set up agent-worktrees'
  - 'set up agent-bridge'
  - 'bootstrap agent-worktrees'
  - 'bootstrap agent-bridge'
  - 'agent-worktrees not found'
  - 'agent-bridge not found'
  - 'agent-bridge not installed'
  - 'runtime not installed'
  - 'adopt this repo'
  - 'adopt repo'
  - 'register project'
  - 'agent-worktrees adopt'
  - 'wire agent-bridge topology'
  - 'configure agent-bridge for this repo'
  - 'agent-bridge config adopt'
  - 'agent-bridge topology missing'
  - 'set up worktree sessions for this repo'
  - 'bootstrap this machine'
  - 'set up copilot extensions'
---

# Copilot Extensions Setup

Install and adopt flows for **both** copilot-extensions plugins:

| Plugin | Type | What It Does |
|--------|------|-------------|
| **agent-worktrees** | Session tool | Worktree isolation, launch sessions, finalize |
| **agent-bridge** | Persistent service | Inter-agent sessions, machine mesh, port 9280 |

Both ship from the same `copilot-extensions` repo. Install order:
agent-worktrees first (prerequisite), then agent-bridge.

---

## Finding Plugin Directories

Both install scripts live in their respective plugin directories:

```powershell
# Windows (PowerShell 5+ or pwsh)
$awDir = Get-ChildItem -Recurse -Path "$env:USERPROFILE\.copilot\installed-plugins" -Filter "plugin.json" |
    Where-Object { (Get-Content $_.FullName -Raw) -match '"agent-worktrees"' } |
    Select-Object -First 1 -ExpandProperty DirectoryName

$abDir = Get-ChildItem -Recurse -Path "$env:USERPROFILE\.copilot\installed-plugins" -Filter "plugin.json" |
    Where-Object { (Get-Content $_.FullName -Raw) -match '"agent-bridge"' } |
    Select-Object -First 1 -ExpandProperty DirectoryName
```

```bash
# Linux/macOS
aw_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-worktrees {} \; | head -1 | xargs dirname)
ab_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-bridge {} \; | head -1 | xargs dirname)
```

---

## 1. Agent-Worktrees Init

Install the worktree runtime. Run **once per machine**.

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File "$awDir\scripts\init.ps1"
```

```bash
# Linux
bash "$aw_dir/scripts/init.sh"
```

### What It Creates

```
~/.agent-worktrees/
  .venv/                    Python venv with pyyaml
  lib/agent_worktrees/      Python package (copied from plugin)
  bin/                      launch-session, bootstrap-check
  deploy-manifest.json

~/.local/bin/
  agent-worktrees[.cmd]     Binstub
```

### Verify

```bash
agent-worktrees --version
```

If not found, ensure `~/.local/bin` is on PATH. The init script adds
it to the user's persistent PATH, but the current shell may need:

```powershell
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"   # Windows
```
```bash
export PATH="$HOME/.local/bin:$PATH"                   # Linux
```

### Update

The plugin contributes a `sessionStart` hook that auto-detects stale
runtimes and re-deploys automatically. Manual updates: re-run init.

---

## 2. Agent-Worktrees Adopt

Register a repo for worktree-managed sessions. Run **from inside the repo**.

### Flow

1. **Detect repo** -- `git rev-parse --show-toplevel`, identify default branch
2. **Sweep for machines.yaml** -- check `{repo}/machines.yaml`,
   `{repo}/config/machines.yaml`, `{repo}/.github/machines.yaml`.
   If found, ask user which machine this is. If not, auto-detect from hostname.
3. **Sweep for services** -- look for `services/*/service.yaml`
4. **Detect launch command** -- check for `tools/setup/setup.ps1` or `.sh`
5. **Choose worktree root** -- default: `{parent}/.worktrees/{repo-name}/`
6. **Generate config** -- write `~/.{repo-name}/config.yaml`
7. **Create project binstub** -- `~/.local/bin/{repo-name}[.cmd]`

### Binstub Format

**Windows (`{repo-name}.cmd`):**
```bat
@echo off
set "WORKTREE_PROJECT={repo-name}"
"%USERPROFILE%\.agent-worktrees\bin\launch-session.cmd" %*
```

**Linux (`{repo-name}`):**
```bash
#!/usr/bin/env bash
export WORKTREE_PROJECT="{repo-name}"
exec "$HOME/.agent-worktrees/bin/launch-session.sh" "$@"
```

### WSL Support (Windows)

When adopting on Windows, ask about WSL support. If yes, record in
`projects.yaml`:

```yaml
wsl:
  state: adopted
  distro: Ubuntu
  path: ~/src/my-project
```

The next install/update generates the `(WSL)` terminal profile.

### Terminal Profiles (Optional)

If the repo has terminal templates (`terminal/{repo-name}.json`),
offer to deploy Windows Terminal fragments to
`%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\`.

### Verify

```bash
{repo-name}              # launches worktree picker
agent-worktrees status   # shows adopted repo
```

---

## 3. Agent-Bridge Init

Install the bridge service. Run **once per machine** (after agent-worktrees).

```powershell
# Windows
powershell -NoProfile -ExecutionPolicy Bypass -File "$abDir\scripts\install.ps1" install
```

```bash
# Linux
bash "$ab_dir/scripts/install.sh" install
```

### What It Creates

```
~/.agent-bridge/
  venv/                    Python venv (fastapi, uvicorn, etc.)
  config.yaml              Runtime config (port, bind, topology profiles)
  auth.yaml                Bearer auth token (generated on first run)
  sessions.db              SQLite session database (on first start)
  deploy-manifest.json

~/.local/bin/
  agent-bridge[.cmd]       Binstub

Windows: "Agent Bridge" scheduled task (at-logon, 15s delay)
Linux:   ~/.config/systemd/user/agent-bridge.service (enabled)
```

### Migration

If the machine previously used `aperture-labs services agent-bridge update`,
the plugin installer detects this automatically: stops the old service,
preserves config/auth/DB, replaces the scheduled task/systemd unit with
plugin-owned versions.

### Verify

```bash
agent-bridge version
agent-bridge status
```

### Other Actions

```bash
install.ps1 update       # reinstall package, restart if running
install.ps1 start        # start the service
install.ps1 stop         # stop the service
install.ps1 status       # show status
install.ps1 uninstall    # remove (preserves config by default)
```

---

## 4. Agent-Bridge Adopt (Topology Wiring)

Wire agent-bridge to a repo's machine mesh. This creates a **topology
profile** in `~/.agent-bridge/config.yaml` pointing to the same
`machines.yaml` used by Windows Terminal fragments.

```bash
# Auto-discovers machines.yaml and acp-agents.json
agent-bridge config adopt --repo /path/to/repo --profile facility

# Verify
agent-bridge config show
agent-bridge config validate
```

### Auto-Discovery Paths

| File | Locations checked |
|------|-------------------|
| machines.yaml | `{repo}/machines.yaml`, `{repo}/config/machines.yaml`, `{repo}/.github/machines.yaml` |
| acp-agents.json | `{repo}/tools/mcp/acp-agents.json`, `{repo}/acp-agents.json`, `{repo}/config/acp-agents.json` |

### Explicit Paths

```bash
agent-bridge config adopt \
  --repo /path/to/repo --profile facility \
  --machines-yaml /custom/machines.yaml \
  --agents-config /custom/agents.json
```

### Multiple Repos

```bash
agent-bridge config adopt --repo ~/src/aperture-labs --profile aperture-labs
agent-bridge config adopt --repo ~/src/dotfiles --profile dotfiles
```

### After Adopt

Restart agent-bridge to load new topology:

```bash
# Windows
install.ps1 stop; install.ps1 start

# Linux
systemctl --user restart agent-bridge.service

# Then verify
agent-bridge machines
agent-bridge agents
```

### Remove a Profile

```bash
agent-bridge config remove my-profile
```

---

## Full Machine Bootstrap

To set up a fresh machine with both plugins:

```bash
# 1. Install agent-worktrees runtime
#    (find plugin dir, run init script -- see section 1)

# 2. Adopt the repo for worktree sessions
#    (cd into repo, follow section 2 flow)

# 3. Install agent-bridge service
#    (find plugin dir, run install script -- see section 3)

# 4. Wire topology
agent-bridge config adopt --repo /path/to/repo --profile facility

# 5. Start the service
agent-bridge start  # or: install.ps1 start

# 6. Verify everything
agent-worktrees --version
agent-worktrees status
agent-bridge version
agent-bridge machines
agent-bridge agents
```
