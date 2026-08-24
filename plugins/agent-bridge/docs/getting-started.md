# Agent Bridge -- Getting Started

Set up agent-bridge from scratch. Assumes only that Copilot CLI is installed.
agent-bridge works standalone: no repo has to be registered as a harness and
agent-worktrees is only used when you want its project/worktree conveniences.

## 1. Install the Plugin

If you haven't registered the marketplace yet:

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
```

Install agent-bridge itself:

```bash
copilot plugin install agent-bridge@copilot-extensions
```

Optional siblings are plug-and-play. For example, installing
`agent-codespaces` or `agent-containers` lets those plugins drop provider
manifests into `~/.agent-bridge/providers.d/`; the bridge then exposes
`codespace:` / `container:` agents and folds in their credential-relay profiles.
If a sibling is missing, only that namespace/relay feature is unavailable.

## 2. Bootstrap the Service

`copilot plugin install` only vendors the plugin **payload** into
`~/.copilot/installed-plugins/`. agent-bridge is a **Python package**
(`plugins/agent-bridge/src/agent_bridge` plus vendored `libs/`); the installer
below deploys its **runtime**. The current runtime layout is versioned:
`~/.agent-bridge/versions/<version>/` holds the venv, `venv` is the stable link,
and `current-version` selects the active slot. The installer builds the slot
with `uv venv` + `uv pip install`, writes the self-provisioning binstub, and
registers the always-on service.

`uv` is required for provisioning. The Linux/WSL installer can vendor a
standalone `uv` into `~/.agent-bridge/tool` when it is absent; the Windows
installer fails loudly with the install URL if `uv` is not on PATH.

The session-start hook (`hooks.json` -> `scripts/bootstrap-check.*`) also keeps
the runtime reconciled with the payload. On a fresh install it performs a cheap
`stamp` (snapshot + binstub) and lets the first `agent-bridge` invocation run
`provision` to build the venv. On later payload drift it launches the installer
in the background and records progress in `~/.agent-bridge/reconcile-status.json`
and `reconcile.log`.

Start a Copilot CLI session and say:

> *"set up agent-bridge"*

This invokes the `agent-worktrees:copilot-extensions-setup` skill, which runs the
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
  versions/<version>/      Versioned Python venv slots
  venv/                    Stable link/junction to the active slot
  current-version          Active version marker
  payload-dir              Snapshot used by first-use self-provisioning
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
  Discovered live port     Starts only when at least one provider contributes
                           credential sources. Provider profiles may request a
                           dynamic port (0) or a fixed fallback; the live port
                           is published for transport clients to discover.
```

The credential relay is part of agent-bridge startup, but provider plugins own
the target-specific credential policy. agent-bridge applies each provider's
`relay-profile` over a process boundary (falling back to an import only for
back-compat), then hosts one shared relay. With no provider sources, the relay is
disabled and the bridge service still works.

### Verify

```bash
agent-bridge version
agent-bridge status
```

If `agent-bridge` is not found, ensure `~/.local/bin` is on PATH.

## 3. Configure Machine Topology (optional)

You can use agent-bridge without a topology: provider namespaces discovered from
`providers.d` work on their own, and an explicit `agents_config` can define local
agents. Configure a topology when you want named machine/repo agents derived
from a repo's `machines.yaml`.

### Option A: Auto-adopt from a repo (recommended)

If your repo has a `machines.yaml`:

```bash
agent-bridge config adopt --repo /path/to/repo --profile my-project
```

This auto-discovers `machines.yaml` and creates a topology profile; the agent
roster is **derived** from it (+ `.agent-worktrees/related.yaml` and local repo
registry data when available). See
[Machine Configuration](machine-config.md) for the full guide on the
`machines.yaml` format and the derived roster.

Linked Git worktrees are canonicalized to their stable anchor before an
auto-discovered topology path is stored. Explicit config paths remain exact.

### Option B: Manual config

Edit `~/.agent-bridge/config.yaml` directly:

```yaml
port: 0               # dynamic by default: OS-assigned ephemeral, advertised via active.json (set a positive port only to pin)
bind: 127.0.0.1
log_level: info

topologies:
  my-project:
    machines_yaml: /path/to/machines.yaml
    # agents_config: /path/to/acp-agents.json   # explicit deprecated override
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
agent-bridge service start
```

`agent-bridge start` runs the daemon in the foreground and is mainly for
debugging or for the service manager itself.

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

### Normal update flow

After the marketplace payload updates, the session-start reconcile hook detects
version drift and runs the installer in the background. To force a runtime
repair/update from the plugin directory:

```bash
install.ps1 update    # Windows
install.sh update     # Linux/WSL
```

The `update` action builds the new versioned slot, verifies imports, updates the
binstub/service manifest, and if a daemon is already live performs the
installer-driven graceful cutover (falling back to drain/stop/start only on
failure).

## Migration from Old Installer

If the machine previously used a project binstub (e.g. `<project>
services agent-bridge install`), the plugin installer detects this
automatically: stops the old service, preserves config/auth/DB, and
replaces the service registration with plugin-owned versions.

## Next Steps

- [Machine Configuration](machine-config.md) -- detailed topology setup
- [Architecture](architecture.md) -- service internals and API
- [CLI skill](../skills/agent-bridge/SKILL.md) -- full CLI command reference
