---
name: copilot-extensions-setup
description: >
  Install and adopt for the copilot-extensions plugins (agent-worktrees,
  agent-bridge, agent-codespaces, agent-containers, and agent-mcp) -- runtime
  bootstrap, repo adoption, topology wiring, and service registration. One
  skill for all setup flows. Trigger phrases include:
  - 'install agent-worktrees'
  - 'install agent-bridge'
  - 'set up copilot extensions'
  - 'set up agent-worktrees'
  - 'bootstrap agent-bridge'
  - 'agent-bridge not installed'
  - 'agent-mcp not found'
  - 'runtime not installed'
  - 'adopt this repo'
  - 'register project'
  - 'agent-bridge config adopt'
  - 'wire agent-bridge topology'
  - 'bootstrap this machine'
---

# Copilot Extensions Setup

Use the exact `argv[0]` from each plugin's session command catalog for
interactive checks and configuration below. Replace the
`<agent-worktrees catalog argv[0]>`, `<agent-bridge catalog argv[0]>`,
`<agent-codespaces catalog argv[0]>`, `<agent-containers catalog argv[0]>`,
and `<agent-mcp catalog argv[0]>` placeholders with their published paths.
Substitute each raw path, then quote it at its shell call site.
Installer, service, and unified-update commands explicitly labeled as
management boundaries remain literal global-wrapper invocations.

Install and adopt flows for the **core runtime** copilot-extensions plugins
(the bridge **mesh** plus standalone agent-mcp). The full suite is larger — see
the [README](../../../../README.md) for the canonical plugin list; the other
plugins (agent-logger, agent-dispatch, and the payload-only skill plugins) set up
via their own skills:

| Plugin | Type | What It Does |
|--------|------|-------------|
| **agent-worktrees** | Session tool | Worktree isolation, launch sessions, finalize |
| **agent-bridge** | Persistent service | Inter-agent sessions, machine mesh (port 9280 Win / 9281 WSL) |
| **agent-codespaces** | CLI + relay | CodeSpace lifecycle, `codespace:` resolver, credential relay (port 9857) |
| **agent-containers** | CLI + fleet | Local Docker dev-container fleet, lease broker, `container:` resolver |
| **agent-mcp** | MCP bridge (standalone) | Wrap an upstream MCP server + inject host creds; invoked from an agent's `mcp-servers` config — **not** part of the bridge mesh |

These ship from the same `copilot-extensions` repo. Install order for the
**mesh**: agent-worktrees first (prerequisite), then agent-codespaces and
agent-containers, then agent-bridge (the bridge installer imports
the Codespaces and container plugins for their `codespace:` / `container:`
resolvers, so install them before the bridge). agent-mcp is **standalone and
optional** — install it any time; it has no ordering constraint.

**End state:** every module installed from the marketplace and running from its
local install path (`~/.agent-worktrees`, `~/.agent-codespaces`,
`~/.agent-containers`, `~/.agent-bridge`, `~/.agent-mcp`) with binstubs in
`~/.local/bin`.

---

## 0. Install the plugins (marketplace)

Two ways to register the plugins. **Repo-scoped registration is preferred** --
it pins the plugin set to the control repo, keeps machines consistent, and lets
the launcher keep everything fresh automatically.

### Recommended: register at repo scope

1. Enable experimental mode once per machine -- the CLI gates **all** extension
   loading on it (`~/.copilot/settings.json`):

   ```json
   { "experimental": true }
   ```

2. Declare the marketplace + enable the plugins in the **control repo's**
   `.github/copilot/settings.json` (committed, shared across every machine):

   ```json
   {
     "extraKnownMarketplaces": {
       "copilot-extensions": {
         "source": { "source": "github", "repo": "ThomasMichon/copilot-extensions" }
       }
     },
     "enabledPlugins": {
       "agent-worktrees@copilot-extensions": true,
       "agent-bridge@copilot-extensions": true,
       "agent-codespaces@copilot-extensions": true,
       "agent-containers@copilot-extensions": true,
       "agent-mcp@copilot-extensions": true
     }
   }
   ```

   Copilot vendors the enabled plugin **payloads** when a session runs in that
   repo. **agent-worktrees may only take effect after restarting the active
   session** -- plugins are scanned at startup.

3. Deploy the **runtimes** (the `uv` venvs + binstubs) by running this setup
   skill once the payloads are vendored -- *"set up copilot extensions"*
   (sections 1-8 below install the uv/pip payloads).

4. From then on, **boot via the binstub or terminal profile**. Each interactive
   launch runs the payload-local `reconcile-plugins` operation, which keeps the repo's
   enabled payloads installed and their runtimes matched to the payload version
   -- so the plugin set stays fresh automatically (see
   [`docs/install-contract.md`](../../../../docs/install-contract.md)).

### Alternative: global install

Install into the user profile instead (handy for a machine with no single
control repo). **Install the four mesh plugins** -- agent-worktrees alone is not
enough for codespace/container support. agent-mcp is optional; add it if you
need to wrap an authenticated MCP.

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

Install the worktree runtime. Run **once per machine**. This is a standalone
runtime; the optional mesh plugins below are not required for local worktree
isolation.

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
  versions/<version>/       immutable Python venv slot with agent_worktrees
  current-version           marker naming the active slot
  payload-dir               installed payload pointer used for self-provisioning
  bin/                      launchers, hook shims, guard scripts
  pivots/                   cross-plugin picker pivot manifests
  deploy-manifest.json

~/.local/bin/
  agent-worktrees[.cmd]     Binstub
```

### Verify

```bash
agent-worktrees --version # marketplace-isolation: allow install-verification
```

If not found, ensure `~/.local/bin` is on PATH. The init script adds
it to the user's persistent PATH, but the current shell may need:

```powershell
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"   # Windows
```
```bash
export PATH="$HOME/.local/bin:$PATH"                   # Linux
```

### Update / self-provisioning

The global `agent-worktrees` binstub resolves the runtime through the
`current-version` marker (falling back to the newest installed slot). If no slot
exists but the payload is discoverable, it runs the lean `install.{ps1,sh}
provision` path on first use. For normal updates, run
`agent-worktrees update`: <!-- marketplace-isolation: allow deployment-management -->
it refreshes payloads, reconciles runtimes, and fast-forwards managed anchors.

---

## 2. Agent-Worktrees Adopt

Register a repo for worktree-managed sessions. Run **from inside the repo**.

### Flow

1. **Detect repo** -- `git rev-parse --show-toplevel`, identify default branch
2. **Resolve machines.yaml if present** -- prefer
   `{repo}/.agent-worktrees/machines.yaml`, falling back to legacy
   `{repo}/machines.yaml`.
   If found, ask user which machine this is. If not, auto-detect from hostname.
3. **Sweep for services** -- look for `services/*/service.yaml`
4. **Detect launch command** -- check for `tools/setup/setup.ps1` or `.sh`
5. **Choose worktree root** -- default: `<anchor>.worktrees` (a sibling folder,
   matching the Copilot CLI `/worktree` layout)
6. **Generate config** -- write `~/.{repo-name}/config.yaml`
7. **Create project binstub** -- `~/.local/bin/{repo-name}[.cmd]`

### Binstub Format

The binstub names its project via `--project` (context otherwise resolves from
CWD, git-like) and routes through the Python CLI, which dispatches subcommands
and launches sessions. It falls back to `launch-session` only when the runtime is
missing (recovery), passing the project via `WORKTREE_PROJECT` on that degraded
path.

The generated project binstub routes to the versioned runtime slot named by
`~/.agent-worktrees/current-version` and invokes:

```text
python -m agent_worktrees --project {repo-name} ...
```

On Windows both `{repo}.ps1` and `{repo}.cmd` are emitted; on POSIX a bare
`{repo}` shell stub is emitted. The fallback path is only for a missing runtime:
it sets `WORKTREE_PROJECT` and hands off to `launch-session.{cmd,sh}` so the
launcher can still recover.

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

Windows Terminal profiles are generated from the project registry, machine
rosters, and `terminal_profiles` selection in config. They are written to
`%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\AgentWorktrees\`.

### Verify

```bash
{repo-name}              # launches worktree picker
<agent-worktrees catalog argv[0]> status   # shows adopted repo
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
<agent-bridge catalog argv[0]> version
<agent-bridge catalog argv[0]> status
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

> **Detailed machine config guide:** For `machines.yaml` format, the derived
> agent roster (machines × repos × environments), creating these files from
> scratch, and troubleshooting topology issues, read
> `plugins/agent-bridge/docs/machine-config.md` in the installed plugin
> directory before proceeding. That doc is the canonical reference for
> topology setup.

```bash
# Auto-discovers machines.yaml (the roster is derived from it)
<agent-bridge catalog argv[0]> config adopt --repo /path/to/repo --profile multi-machine system

# Verify
<agent-bridge catalog argv[0]> config show
<agent-bridge catalog argv[0]> config validate
```

### Auto-Discovery Paths

| File | Locations checked |
|------|-------------------|
| machines.yaml | canonical `{repo}/.agent-worktrees/machines.yaml`, with legacy `{repo}/machines.yaml` fallback where supported |

> `acp-agents.json` is **retired** — the roster is derived from `machines.yaml`
> (+ `.agent-worktrees/related.yaml`). An explicit `--agents-config` is still
> honored as a deprecated override but is no longer auto-discovered.

### If the repo has no machines.yaml

The user may need to create `machines.yaml` from
scratch. Read `plugins/agent-bridge/docs/machine-config.md` (section
"Creating machines.yaml from Scratch") for templates and examples, then
guide the user interactively through:

1. Identifying their machines (hostname, platform, SSH alias)
2. Defining agents (name, host, type)
3. Writing both files to the repo
4. Running the payload-local `config adopt` operation

### Explicit Paths

```bash
<agent-bridge catalog argv[0]> config adopt \
  --repo /path/to/repo --profile multi-machine system \
  --machines-yaml /custom/machines.yaml \
  --agents-config /custom/agents.json
```

### Multiple Repos

```bash
<agent-bridge catalog argv[0]> config adopt --repo ~/src/my-project --profile my-project
<agent-bridge catalog argv[0]> config adopt --repo ~/src/dotfiles --profile dotfiles
```

### After Adopt

Restart agent-bridge to load new topology:

```bash
# Any platform
agent-bridge service restart # marketplace-isolation: allow service-management

# Linux equivalent
systemctl --user restart agent-bridge.service

# Then verify
<agent-bridge catalog argv[0]> machines
<agent-bridge catalog argv[0]> agents
```

### Remove a Profile

```bash
<agent-bridge catalog argv[0]> config remove my-profile
```

---

## 5-8. Optional plugins -- Codespaces, Containers, MCP

Setup for the optional / standalone plugins lives in
[references/optional-plugins-setup.md](references/optional-plugins-setup.md):

- **agent-codespaces** -- init (`~/.agent-codespaces` + binstub), adopt, credential relay + bridge integration
- **agent-containers** -- init (`~/.agent-containers` + binstub), fleet / lease config, `container:` resolver
- **agent-mcp** -- standalone init (optional; not part of the mesh)

---

## Full Machine Bootstrap

To set up a fresh machine with the four **mesh** plugins (add agent-mcp
separately if needed — see [references/optional-plugins-setup.md](references/optional-plugins-setup.md)):

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
<agent-bridge catalog argv[0]> config adopt --repo /path/to/repo --profile my-control-harness

# 5. Install agent-codespaces runtime      (optional-plugins-setup.md §5)
# 6. Adopt the repo for codespaces         (optional-plugins-setup.md §6)
cs='<agent-codespaces catalog argv[0]>'
cd /path/to/repo && "$cs" config adopt

# 7. Install agent-containers runtime      (optional-plugins-setup.md §7)
# 8. (optional) Install agent-mcp runtime  (optional-plugins-setup.md §8)

# 9. Start the service
agent-bridge start  # marketplace-isolation: allow service-management

# 10. Verify everything
<agent-worktrees catalog argv[0]> --version && <agent-worktrees catalog argv[0]> status
<agent-bridge catalog argv[0]> version && <agent-bridge catalog argv[0]> machines && <agent-bridge catalog argv[0]> agents
<agent-codespaces catalog argv[0]> version && <agent-codespaces catalog argv[0]> status
<agent-containers catalog argv[0]> version && <agent-containers catalog argv[0]> fleet
# agent-mcp status   # if installed
```

### WSL notes

The bootstrap above works unchanged **from inside a WSL distro** — run the
`init.sh` installers and the `config adopt` / `update` steps in the Linux shell.
Two WSL-specific facts:

- **`~/.local/bin` is already on `PATH`** via the stock `~/.profile` snippet
  (+ `~/.local/bin/env` from uv), so the binstubs the installers deploy there are
  picked up without editing PATH.
- **Don't install (or symlink) the Copilot CLI.** The Windows Copilot CLI's WSL
  stub auto-installs the Linux binary on first run, and agent-worktrees
  *resolves* it from known locations even when it isn't on `PATH` — so
  `<repo> update` and session-start provisioning just work. See the **`wsl-setup`**
  plugin's `wsl-setup:setting-up-wsl` skill (§ "Copilot CLI in WSL") for the details, and
  its `wsl-setup:setting-up-wsl` / `wsl-setup:troubleshooting-wsl-networking` skills for the distro,
  networking, and keepalive setup that this bootstrap assumes is already done.
