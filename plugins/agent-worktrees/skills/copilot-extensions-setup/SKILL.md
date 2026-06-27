---
name: copilot-extensions-setup
description: >
  Install and adopt for the copilot-extensions plugins (agent-worktrees,
  agent-bridge, agent-codespaces, agent-containers, and agent-mcp) -- runtime
  bootstrap, repo adoption, topology wiring, and service registration. One
  skill for all setup flows. Trigger phrases include:
  - 'install agent-worktrees'
  - 'install agent-bridge'
  - 'install agent-codespaces'
  - 'install agent-containers'
  - 'install agent-mcp'
  - 'set up agent-worktrees'
  - 'set up agent-bridge'
  - 'set up copilot extensions'
  - 'bootstrap agent-worktrees'
  - 'bootstrap agent-bridge'
  - 'agent-worktrees not found'
  - 'agent-bridge not found'
  - 'agent-bridge not installed'
  - 'agent-containers not found'
  - 'agent-mcp not found'
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
---

# Copilot Extensions Setup

Install and adopt flows for all **five** copilot-extensions plugins:

| Plugin | Type | What It Does |
|--------|------|-------------|
| **agent-worktrees** | Session tool | Worktree isolation, launch sessions, finalize |
| **agent-bridge** | Persistent service | Inter-agent sessions, machine mesh (port 9280 Win / 9281 WSL) |
| **agent-codespaces** | CLI + relay | CodeSpace lifecycle, `codespace:` resolver, credential relay (port 9857) |
| **agent-containers** | CLI + fleet | Local Docker dev-container fleet, lease broker, `container:` resolver |
| **agent-mcp** | MCP bridge (standalone) | Wrap an upstream MCP server + inject host creds; invoked from an agent's `mcp-servers` config — **not** part of the bridge mesh |

All five ship from the same `copilot-extensions` repo. Install order for the
**mesh**: agent-worktrees first (prerequisite), then agent-codespaces and
agent-containers, then agent-bridge (the bridge installer imports
agent-codespaces and agent-containers for their `codespace:` / `container:`
resolvers, so install them before the bridge). agent-mcp is **standalone and
optional** — install it any time; it has no ordering constraint.

**End state:** every module installed from the marketplace and running from its
local install path (`~/.agent-worktrees`, `~/.agent-codespaces`,
`~/.agent-containers`, `~/.agent-bridge`, `~/.agent-mcp`) with binstubs in
`~/.local/bin`.

---

## 0. Install the plugins (marketplace)

Run once per machine. **Install the four mesh plugins** — installing
agent-worktrees alone is not enough for codespace/container support.
agent-mcp is optional; add it if you need to wrap an authenticated MCP.

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install agent-worktrees@copilot-extensions
copilot plugin install agent-codespaces@copilot-extensions
copilot plugin install agent-containers@copilot-extensions
copilot plugin install agent-bridge@copilot-extensions
copilot plugin install agent-mcp@copilot-extensions          # optional, standalone
```

Verify all vendored:

```powershell
Get-ChildItem "$env:USERPROFILE\.copilot\installed-plugins\copilot-extensions"
# expect: agent-worktrees, agent-bridge, agent-codespaces, agent-containers (+ agent-mcp if installed)
```

If agent-codespaces or agent-containers is missing here, the bridge installer
will WARN that the corresponding namespace resolver is unavailable.

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

$acDir = Get-ChildItem -Recurse -Path "$env:USERPROFILE\.copilot\installed-plugins" -Filter "plugin.json" |
    Where-Object { (Get-Content $_.FullName -Raw) -match '"agent-codespaces"' } |
    Select-Object -First 1 -ExpandProperty DirectoryName

$anDir = Get-ChildItem -Recurse -Path "$env:USERPROFILE\.copilot\installed-plugins" -Filter "plugin.json" |
    Where-Object { (Get-Content $_.FullName -Raw) -match '"agent-containers"' } |
    Select-Object -First 1 -ExpandProperty DirectoryName

$amDir = Get-ChildItem -Recurse -Path "$env:USERPROFILE\.copilot\installed-plugins" -Filter "plugin.json" |
    Where-Object { (Get-Content $_.FullName -Raw) -match '"agent-mcp"' } |
    Select-Object -First 1 -ExpandProperty DirectoryName
```

```bash
# Linux/macOS
aw_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-worktrees {} \; | head -1 | xargs dirname)
ab_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-bridge {} \; | head -1 | xargs dirname)
ac_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-codespaces {} \; | head -1 | xargs dirname)
an_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-containers {} \; | head -1 | xargs dirname)
am_dir=$(find ~/.copilot/installed-plugins -name plugin.json -exec grep -l agent-mcp {} \; | head -1 | xargs dirname)
```

---

## 1. Agent-Worktrees Init

Install the worktree runtime. Run **once per machine**.

```powershell
# Windows
pwsh -NoProfile -ExecutionPolicy Bypass -File "$awDir\scripts\init.ps1"
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
pwsh -NoProfile -ExecutionPolicy Bypass -File "$abDir\scripts\install.ps1" install
```

```bash
# Linux
bash "$ab_dir/scripts/install.sh" install
```

### Windows: run the daemon whether you are logged on or not (opt-in)

By default the Windows daemon runs from an **at-logon** scheduled task -- it
only runs while a user is **interactively signed in**. On an always-on
workstation that you reach over **SSH/RDP with no persistent interactive
session** (so the at-logon task never fires, and any SSH-spawned daemon dies
with the session), install it **non-interactively** instead:

```powershell
# Headless: a boot-triggered S4U task ("run whether the user is logged on or
# not", no stored password). Outbound SSH still works (it authenticates with
# key files, not the Windows token).
pwsh -NoProfile -ExecutionPolicy Bypass -File "$abDir\scripts\install.ps1" install -NonInteractive
# or, for an automated/over-SSH install:
$env:AGENT_BRIDGE_NONINTERACTIVE = '1'
pwsh -NoProfile -ExecutionPolicy Bypass -File "$abDir\scripts\install.ps1" install
```

This is **opt-in and never forced**: a plain `install` keeps the at-logon task,
a genuine interactive desktop install **prompts** for the choice, and an
existing non-interactive task is **preserved across updates**. `-NonInteractive`
is accepted on `install` and `update`. Linux/WSL is unaffected (the systemd
user unit is unrelated to interactive logon).

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

Windows: "Agent Bridge" scheduled task (at-logon, 15s delay; or boot-start
         S4U with `-NonInteractive`)
Linux:   ~/.config/systemd/user/agent-bridge.service (enabled)
```

### Migration

If the machine previously used a project binstub to install agent-bridge
(e.g. `<project> services agent-bridge update`),
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

> **Detailed machine config guide:** For `machines.yaml` format,
> `acp-agents.json` format, creating these files from scratch, and
> troubleshooting topology issues, read
> `plugins/agent-bridge/docs/machine-config.md` in the installed plugin
> directory before proceeding. That doc is the canonical reference for
> topology setup.

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

### If the repo has no machines.yaml

The user may need to create `machines.yaml` and `acp-agents.json` from
scratch. Read `plugins/agent-bridge/docs/machine-config.md` (section
"Creating machines.yaml from Scratch") for templates and examples, then
guide the user interactively through:

1. Identifying their machines (hostname, platform, SSH alias)
2. Defining agents (name, host, type)
3. Writing both files to the repo
4. Running `agent-bridge config adopt`

### Explicit Paths

```bash
agent-bridge config adopt \
  --repo /path/to/repo --profile facility \
  --machines-yaml /custom/machines.yaml \
  --agents-config /custom/agents.json
```

### Multiple Repos

```bash
agent-bridge config adopt --repo ~/src/my-project --profile my-project
agent-bridge config adopt --repo ~/src/dotfiles --profile dotfiles
```

### After Adopt

Restart agent-bridge to load new topology:

```bash
# Any platform
agent-bridge service restart

# Linux equivalent
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

## 5. Agent-Codespaces Init

Install the agent-codespaces runtime (CLI binstub + `~/.agent-codespaces`
home). The credential relay itself runs inside the agent-bridge service, but
this step gives you the standalone `agent-codespaces` CLI and is the canonical
owner of the `~/.local/bin/agent-codespaces` binstub.

```powershell
# Windows
pwsh -NoProfile -ExecutionPolicy Bypass -File "$acDir\scripts\init.ps1"
```

```bash
# Linux/WSL
bash "$ac_dir/scripts/init.sh"
```

### Verify

```bash
agent-codespaces version
agent-codespaces status      # shows runtime, gh CLI, ssh
```

`gh` must be authenticated (`gh auth login`) for CodeSpace operations.

---

## 6. Agent-Codespaces Adopt

Register the repo so agent-codespaces reads `codespaces.yaml` live (CodeSpace
defaults + credential-relay policy). Run **from inside the repo**.

```bash
cd /path/to/repo
agent-codespaces config adopt
agent-codespaces config validate
agent-codespaces config show
```

If the repo has no `codespaces.yaml`, create one first — see the
`codespaces-setup` skill for the format (defaults, credential sources, per-repo
overrides).

### Verify relay + bridge integration

No registration step is needed: when agent-codespaces is installed, the
agent-bridge service imports it as a sibling and **auto-registers the live
`codespace:` namespace resolver** at startup, so CodeSpaces are addressable as
`codespace:<name>` (raw or friendly) on demand.

```bash
# CodeSpaces should already appear here -- no `bridge register` required.
agent-bridge agents          # look for codespace:<name> entries
```

If `agent-bridge agents` shows no codespace entries and the bridge install
WARNED about a missing sibling, re-run the agent-bridge installer **after** the
agent-codespaces plugin is installed (section 0) so the service venv picks up
the `agent_codespaces` package. (`agent-codespaces bridge register` exists but
only POSTs a static `cs-*` snapshot with a TTL — it is optional and superseded
by the resolver; see the `codespaces-lifecycle` skill.)

---

## 7. Agent-Containers Init

Install the agent-containers runtime (CLI binstub + `~/.agent-containers`
home). The `container:` namespace resolver runs inside the agent-bridge
service (installed as a sibling import); this step gives you the standalone
`agent-containers` CLI for fleet/lease management and owns the
`~/.local/bin/agent-containers` binstub.

```powershell
# Windows
pwsh -NoProfile -ExecutionPolicy Bypass -File "$anDir\scripts\init.ps1"
```

```bash
# Linux/WSL
bash "$an_dir/scripts/init.sh"
```

### What It Creates

```
~/.agent-containers/
  .venv/                   Python venv with the agent_containers package
  deploy-manifest.json

~/.local/bin/
  agent-containers[.cmd]   Binstub
```

### Verify

```bash
agent-containers version
agent-containers fleet       # lists local dev containers + lease status
```

Docker (Docker Desktop WSL2 backend) must be running for fleet operations.
The `container:` resolver in agent-bridge forwards the host `gh auth token`
into containers, so `gh` must be authenticated for dispatched agents to work.

---

## 8. Agent-MCP Init (optional, standalone)

Install the agent-mcp runtime (CLI binstub + `~/.agent-mcp` home). agent-mcp is
**not** part of the bridge mesh — it has no `codespace:` / `container:`-style
resolver and the bridge does not import it. An agent wraps an upstream MCP by
pointing an `mcp-servers` entry at the `agent-mcp` binstub. Install it only if
you need to bridge an authenticated MCP server.

```powershell
# Windows
pwsh -NoProfile -ExecutionPolicy Bypass -File "$amDir\scripts\init.ps1"
```

```bash
# Linux/WSL
bash "$am_dir/scripts/init.sh"
```

### What It Creates

```
~/.agent-mcp/
  .venv/                   Python venv with the agent_mcp package
  deploy-manifest.json

~/.local/bin/
  agent-mcp[.cmd]          Binstub
```

You create `~/.agent-mcp/bridges/<name>.yaml` config files yourself (or pass
`--config <path>`); init does not create the `bridges/` directory.

### Verify

```bash
agent-mcp status            # prerequisites + available bridges
```

Define a bridge under `~/.agent-mcp/bridges/<name>.yaml` (or pass `--config`),
then validate it with `agent-mcp validate <name>`. See the `agent-mcp` skill for
the config format and how to wire it into an agent's `mcp-servers`.

---

## Full Machine Bootstrap

To set up a fresh machine with the four **mesh** plugins (add agent-mcp
separately if needed — see section 8):

```bash
# 0. Install the mesh plugins from the marketplace (see section 0)
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install agent-worktrees@copilot-extensions
copilot plugin install agent-codespaces@copilot-extensions
copilot plugin install agent-containers@copilot-extensions
copilot plugin install agent-bridge@copilot-extensions
# optional: copilot plugin install agent-mcp@copilot-extensions

# 1. Install agent-worktrees runtime      (section 1)
# 2. Adopt the repo for worktree sessions  (section 2)
# 3. Install agent-bridge service          (section 3)
#    -> pulls in agent-codespaces + agent-containers for the
#       codespace: / container: resolvers
# 4. Wire topology
agent-bridge config adopt --repo /path/to/repo --profile my-control-harness

# 5. Install agent-codespaces runtime      (section 5)
# 6. Adopt the repo for codespaces         (section 6)
cd /path/to/repo && agent-codespaces config adopt

# 7. Install agent-containers runtime      (section 7)
# 8. (optional) Install agent-mcp runtime  (section 8)

# 9. Start the service
agent-bridge start  # or: install.ps1 start

# 10. Verify everything
agent-worktrees --version && agent-worktrees status
agent-bridge version && agent-bridge machines && agent-bridge agents
agent-codespaces version && agent-codespaces status
agent-containers version && agent-containers fleet
# agent-mcp status   # if installed
```
