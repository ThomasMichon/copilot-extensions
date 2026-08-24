# Agent Bridge -- Machine Configuration

This guide covers the optional machine topology that agent-bridge uses to
discover and connect to named machine/repo agents. The bridge itself is
standalone: provider namespaces from `~/.agent-bridge/providers.d/` and explicit
local agents can work without a `machines.yaml`; explicit local agents only need
a minimal profile that points at an `agents_config` file.

Agent-bridge and agent-worktrees both consume **the same `machines.yaml`
file** -- agent-worktrees uses it for terminal profiles and SSH sessions;
agent-bridge uses it for agent subprocess spawning and SSH transport.

## Overview

Agent-bridge topology is configured via **profiles** in
`~/.agent-bridge/config.yaml`. A derived-roster profile points to a
`machines.yaml` in your project repo, from which agent-bridge derives machine ×
repo × environment agents; a legacy local-only profile may instead point only at
an explicit `agents_config` file:

| File | Purpose |
|------|---------|
| `machines.yaml` | Machine inventory (SSH environments, readiness, roles) **plus** `control_plane.project`. agent-bridge derives one control-plane agent per (machine, SSH environment), and `<repo>@<machine>` agents from each repo's `.agent-worktrees/related.yaml`. |
| `acp-agents.json` | **Deprecated.** Hand-authored agent list. Still honored if a profile sets `agents_config` (explicit entries win over derived ones), but no longer required — the roster is derived from topology. |

## Quick Setup

### Auto-adopt from a repo

```bash
agent-bridge config adopt --repo /path/to/repo --profile my-project
```

This searches for `machines.yaml` at conventional paths and creates a topology
profile. The agent roster is derived from it (plus `.agent-worktrees/related.yaml`
and local repo registry data when available). A legacy `acp-agents.json` is **not
auto-discovered** anymore; pass `--agents-config` explicitly if you need that
deprecated override.

When `/path/to/repo` is a linked Git worktree, adoption resolves its common Git
directory and persists the stable anchor checkout's `machines.yaml`. Removing
the temporary worktree therefore does not strand the profile. An explicit
`--machines-yaml` or `--agents-config` path is never remapped.

### Verify

```bash
agent-bridge config show       # show config with resolved paths
agent-bridge config validate   # check file existence and structure
agent-bridge machines          # list machines for the cwd project
agent-bridge agents            # list agents for the cwd project
agent-bridge --project my-project agents
agent-bridge agents --all-projects
```

Inside an adopted project, `agents` and `machines` use that CWD project by
default. Top-level `--project` selects another project without changing
directories; `--all-projects` restores the fleet-wide catalog. From a neutral
directory where no project resolves, the fleet-wide catalog remains the
default. Bare `--json` listing is also fleet-wide for machine-consumer
compatibility; pair `--json` with explicit `--project` when structured output
must be filtered.

---

## machines.yaml Format

`machines.yaml` defines the machines in your infrastructure under a
top-level `machines:` key. Each machine has a unique key and nested
metadata including SSH environments.

```yaml
machines:
  my-workstation:
    display_name: My Workstation
    environment: "Windows 11 Pro"
    role: "Development, compilation"
    ssh:
      ready: true
      ip: "192.168.1.100"           # optional, for reference only
      environments:
        - name: windows
          alias: my-workstation      # must match ~/.ssh/config alias
          port: 2222
          user: jsmith
          shell: pwsh
        - name: wsl
          alias: my-workstation-wsl
          port: 22
          user: jsmith
          shell: bash

  build-server:
    display_name: Build Server
    environment: "Ubuntu 24.04"
    role: "CI/CD, builds"
    ssh:
      ready: true
      environments:
        - name: linux
          alias: build-srv           # must match ~/.ssh/config alias
          port: 22
          user: deploy
          shell: bash
```

### Machine Fields

| Field | Required | Description |
|-------|----------|-------------|
| `display_name` | No | Human-readable name (defaults to machine key) |
| `environment` | No | OS/platform description (e.g., "Windows 11 Pro") |
| `role` | No | Machine role description |
| `field_terminal` | No | Boolean -- marks roaming/field machines |
| `ssh.ready` | **Yes** | Whether SSH is configured and reachable |
| `ssh.ip` | No | IP address (reference only, not used for connections) |
| `ssh.environments` | **Yes** | List of SSH environments (see below) |

### SSH Environment Fields

Each machine has one or more SSH environments (e.g., Windows native +
WSL on the same host):

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Environment identifier (`windows`, `wsl`, `linux`) |
| `alias` | Yes | SSH alias from `~/.ssh/config` -- used for connections |
| `port` | No | SSH port (default: `22`) |
| `user` | No | SSH username |
| `shell` | No | Remote shell (`bash`, `pwsh`, `sh`, `zsh`) -- default: `bash` |

### SSH Alias Convention

The `alias` field must match an entry in `~/.ssh/config`. Agent-bridge
uses `ssh <alias>` to connect, so the alias must resolve to the correct
host, user, port, and key. Common convention:

- **Bare name** (e.g., `my-workstation`) = Windows native SSH (port 2222)
- **`-wsl` suffix** (e.g., `my-workstation-wsl`) = WSL SSH (port 22)
- **Single-OS machines** use the bare name only

### Auth Hooks

Machines can also declare an optional `auth` block. `auth.hooks` is a
per-machine array of auth hook definitions shared by all agents that run
on that machine.

Each hook declares SSH connection wiring that agent-bridge applies when
spawning remote agents:

- **Reverse port forwards** from `local_port` to `remote_port`
- **Environment variables** exported from the `env` map

This is how shared auth services such as the credential relay are made
available to remote agents. The relay server runs automatically as part
of agent-bridge, so hooks only describe how to forward it over SSH.

> **The `git-credential-relay` port is sourced from the live relay.** For the
> hook named `git-credential-relay`, agent-bridge overrides the declared
> `local_port`/`remote_port` and `LC_GIT_CREDENTIAL_RELAY` with the port the
> daemon's credential relay is **actually bound to** at spawn time. The declared
> port is only a fallback (used when no live port is available — neither hosted
> nor discovered). So the port need not be hardcoded here — a machine may omit
> the hook entirely and the forward + env are synthesized from the live port
> automatically. A **sibling/elevated sub-daemon** that reuses the primary's
> relay (and so hosts none itself) discovers the primary's live port from a file
> the primary publishes to its config dir, so it too forwards the correct port.

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Hook identifier for diagnostics and future extensibility |
| `local_port` | Yes | Local listening port on the machine running agent-bridge |
| `remote_port` | Yes | Remote port exposed to the SSH session |
| `env` | No | Environment variables exported for the remote process |

Example:

```yaml
machines:
  my-workstation:
    ssh:
      ready: true
      environments:
        - name: windows
          alias: my-workstation
          shell: pwsh
    auth:
      hooks:
        - name: git-credential-relay
          local_port: 9857
          remote_port: 9857
          env:
            LC_GIT_CREDENTIAL_RELAY: "9857"
```

In this example, agent-bridge adds an SSH `-R` reverse port forward for
`127.0.0.1:<relay-port>` and exports `LC_GIT_CREDENTIAL_RELAY=<relay-port>` for
the remote agent process, where `<relay-port>` is the daemon's live relay port
(the declared `9857` is the fallback).

### File Locations

`config adopt` searches these paths (first match wins):

1. `{repo}/machines.yaml`
2. `{repo}/.agent-worktrees/machines.yaml`
3. `{repo}/config/machines.yaml`
4. `{repo}/.github/machines.yaml`

---

## Derived Roster (machines × repos × environments)

By default agent-bridge **derives** its agent roster from committed topology —
no hand-authored agent list. Two sources:

1. **`machines.yaml` `control_plane.project`** → one **control-plane agent per
   (machine, SSH environment)**, named by the machine's short `display_name`
   (windows → `dev6`, wsl → `dev6-wsl`, a second box → `cloud1`), all backed by
   the control-plane project's binstub. Local envs resolve to loopback; remote
   to SSH.

   ```yaml
   # machines.yaml
   control_plane:
     project: dotfiles
   machines:
     host-dev6:
       display_name: dev6
       ssh:
         environments:
           - { name: windows, alias: host-dev6, shell: pwsh }
           - { name: wsl, alias: host-dev6-wsl, shell: bash }
   ```

2. **Each repo's `.agent-worktrees/related.yaml`** → a `<repo>@<machine>` agent
   for every **remote** machine in a related entry's `locus.machines` whose
   `delegate.via == agent-bridge`. (Local related repos are already covered by
   projects.yaml auto-discovery.)

Precedence: explicit `acp-agents.json` (deprecated) > derived > projects.yaml
auto-discovered. Names collide-safely; explicit/derived win over auto-discovered.

## acp-agents.json Format (deprecated)

> **Deprecated.** Prefer the derived roster above. `acp-agents.json` is still
> honored if a profile's `agents_config` points at one (explicit entries win),
> but it is no longer required or auto-created.

`acp-agents.json` defines the agents available across your machines as a
**dict keyed by agent name**. Each agent maps to a machine from
`machines.yaml` (or runs locally with no `host`).

```json
{
  "workstation-wsl": {
    "host": "my-workstation",
    "ssh_environment": "wsl",
    "copilot_args": ["--allow-all"],
    "project": "my-project",
    "display_name": "Workstation (WSL)",
    "description": "Dev workstation WSL agent"
  },
  "build-server": {
    "host": "build-server",
    "ssh_user": "deploy",
    "copilot_args": ["--allow-all"],
    "display_name": "Build Server",
    "description": "CI/CD build agent"
  }
}
```

### Agent Fields

| Field | Required | Description |
|-------|----------|-------------|
| `host` | No | Machine key from `machines.yaml`. **Omit for local agents.** |
| `ssh_user` | No | SSH username override (defaults to the SSH env's `user`) |
| `ssh_environment` | No | Which SSH environment to use (e.g., `wsl`, `windows`, `linux`) |
| `cwd` | No | Working directory on the remote machine |
| `copilot_path` | No | Path to the `copilot` binary (default: `copilot` on PATH) |
| `copilot_args` | No | Extra args for `copilot` (e.g., `["--allow-all"]`) |
| `managed` | No | If `true`, agent is non-spawnable (external lifecycle) |
| `description` | No | Human-readable description |
| `display_name` | No | Display name (defaults to the agent key) |
| `env` | No | Environment variables to set: `{"KEY": "value"}` |
| `project` | No | agent-worktrees project name (binstub) for remote spawning |

### Local vs SSH Agents

Agent-bridge decides transport based on the `host` field:

- **`host` omitted or `null`** -- **local agent**, spawned as a
  subprocess on the current machine. No SSH, no machine topology needed.
- **`host` set** -- **SSH agent**, spawned on the named machine via SSH.
  The machine must exist in `machines.yaml` with `ssh.ready: true`.

### Agent Names

Agent names (the dict keys) are used in CLI commands:

```bash
agent-bridge send workstation-wsl "Check disk space"
agent-bridge send build-server "Run the test suite"
```

### File Locations

`config adopt` no longer searches for `acp-agents.json`. To use this deprecated
override, pass it explicitly:

```bash
agent-bridge config adopt --repo /path/to/repo --profile my-project \
  --agents-config /path/to/acp-agents.json
```

---

## Local Agents -- Same-Machine Communication

The simplest and most useful agent-bridge capability requires **no SSH
at all**: communicating with another Copilot CLI session on the same
machine. This is the default setup for any machine running agent-bridge.

### Why Local Agents?

- **Cross-worktree collaboration** -- ask an agent in another worktree
  to run tests, check status, or make changes while you keep working
- **Parallel work** -- delegate a sub-task to a local agent that runs
  independently in its own session
- **No SSH config needed** -- local agents spawn directly as subprocesses

### Defining a Local Agent

Omit the `host` field in `acp-agents.json`:

```json
{
  "local": {
    "copilot_args": ["--allow-all"],
    "project": "my-project",
    "display_name": "Local Agent",
    "description": "Copilot CLI on this machine"
  }
}
```

With `project` set, agent-bridge uses the project's binstub to launch
the session, which automatically picks a worktree and applies the
project's setup script.

### Using Local Agents

```bash
# Start a local agent session
agent-bridge send local "Set up the test database and run migrations"

# Check on it
agent-bridge sessions

# Send a follow-up
agent-bridge send <session-id> "Now run the full test suite"

# End when done
agent-bridge end <session-id>
```

### Minimal Setup (Local-Only)

For a machine that only needs same-machine agents, `machines.yaml` can be absent.
Create an `acp-agents.json` and point a profile's `agents_config` at it:

```json
{
  "local": {
    "copilot_args": ["--allow-all"],
    "project": "my-project",
    "description": "Local Copilot CLI agent"
  }
}
```

No `machines.yaml` or SSH config is needed; the profile exists only to name the
explicit agent file.

```yaml
# ~/.agent-bridge/config.yaml
topologies:
  local-only:
    agents_config: /home/user/agents/acp-agents.json
```

---

## Topology Profiles

Profiles live in `~/.agent-bridge/config.yaml` under the `topologies` key:

```yaml
port: 0               # dynamic by default: OS-assigned ephemeral, advertised via active.json (set a positive port only to pin)
bind: 127.0.0.1
log_level: info

topologies:
  my-project:
    machines_yaml: /home/user/src/my-project/machines.yaml
    agents_config: /home/user/src/my-project/tools/mcp/acp-agents.json

  another-project:
    machines_yaml: /home/user/src/other/machines.yaml
    agents_config: /home/user/src/other/acp-agents.json
```

### Path Conventions

- Use **forward slashes** on all platforms (including Windows) for config
  portability
- Paths are resolved relative to the config file location
- Tilde (`~`) expansion is supported

### Managing Profiles

```bash
# Add/update a profile
agent-bridge config adopt --repo /path/to/repo --profile my-project

# With explicit file paths
agent-bridge config adopt \
  --repo /path/to/repo \
  --profile my-project \
  --machines-yaml /custom/machines.yaml \
  --agents-config /custom/agents.json

# Remove a profile
agent-bridge config remove my-project

# Show all profiles
agent-bridge config show

# Validate file paths and structure
agent-bridge config validate
```

---

## Relationship to Agent-Worktrees

Both plugins consume `machines.yaml` but for different purposes:

| Aspect | agent-worktrees | agent-bridge |
|--------|----------------|-------------|
| **Reads** | `machines.yaml` | `machines.yaml`; optional explicit deprecated `agents_config`; local repo registry/projects data when available |
| **Uses for** | Terminal profiles, SSH session targets | Agent spawning, SSH transport |
| **Config location** | `~/.{project}/config.yaml` | `~/.agent-bridge/config.yaml` |
| **When configured** | During repo adoption (`register`) | During topology adoption (`config adopt`) |
| **Schema strictness** | `machines:` key required (error if missing) | `machines:` key optional (empty if missing) |
| **Extra fields** | -- | `ssh.ip` (optional, reference only) |

The schema is identical -- both read the same `machines:` structure with
`display_name`, `environment`, `role`, `ssh.ready`, and
`ssh.environments[].{name, alias, port, user, shell}`. The only
differences are strictness (agent-worktrees errors on missing `machines:`
key; agent-bridge tolerates it) and one optional field (`ssh.ip`).

The `agent-worktrees:copilot-extensions-setup` skill can handle both when you want an
agent-worktrees-backed harness, but agent-bridge does not require that flow.

---

## Creating machines.yaml from Scratch

If your project doesn't have a `machines.yaml` yet, create one in your
repo root.

### Minimal (single Linux machine)

```yaml
machines:
  my-server:
    display_name: My Server
    ssh:
      ready: true
      environments:
        - name: linux
          alias: my-server
          shell: bash
```

### Windows + WSL dual-environment

```yaml
machines:
  dev-workstation:
    display_name: Dev Workstation
    environment: "Windows 11"
    role: "Development"
    ssh:
      ready: true
      environments:
        - name: windows
          alias: dev-ws
          port: 2222
          user: jsmith
          shell: pwsh
        - name: wsl
          alias: dev-ws-wsl
          port: 22
          user: jsmith
          shell: bash
```

If you need explicit legacy agents in addition to the derived roster, create an
`acp-agents.json` and pass it with `--agents-config` (note: dict format, not
array):

```json
{
  "local": {
    "copilot_args": ["--allow-all"],
    "project": "my-project",
    "description": "Local agent on this machine"
  },
  "dev-wsl": {
    "host": "dev-workstation",
    "ssh_environment": "wsl",
    "copilot_args": ["--allow-all"],
    "project": "my-project",
    "display_name": "Dev Workstation (WSL)",
    "description": "WSL agent on dev workstation"
  }
}
```

### Adopt into agent-bridge

```bash
agent-bridge config adopt --repo /path/to/repo --profile my-infra
# optional deprecated explicit override:
# agent-bridge config adopt --repo /path/to/repo --profile my-infra --agents-config /path/to/acp-agents.json
agent-bridge config validate
agent-bridge machines
agent-bridge agents
```

---

## Troubleshooting

### "No machines found"

- Check `agent-bridge config show` for the topology profile paths
- Verify the files exist at those paths
- Run `agent-bridge config validate` for detailed diagnostics

### "Agent not found"

- Run `agent-bridge agents` to see available agents
- Use `agent-bridge agents --all-projects` when comparing multiple profiles
- Invalid profiles are printed after any valid partial roster and make the
  listing command exit non-zero; run `agent-bridge config validate` for the
  stored path and re-adopt from a stable repo anchor
- For derived agents, check `control_plane.project`, related repo entries, and
  the local repo registry/projects data that feed the roster
- For a deprecated explicit override, check that the agent's `host` in
  `acp-agents.json` matches a machine key in `machines.yaml`
- Local agents (no `host`) don't need a `machines.yaml` entry

### SSH connection failures

- Verify SSH aliases work directly: `ssh <alias> echo ok`
- Check `agent-bridge machines` for SSH readiness status
- Ensure SSH keys are configured for passwordless auth
- Only machines with `ssh.ready: true` can host SSH agents

### Config changes not taking effect

Restart agent-bridge after modifying config:

```bash
# Any platform (delegates to the scheduled task / systemd unit)
agent-bridge service restart

# Linux/WSL equivalent
systemctl --user restart agent-bridge.service
```
