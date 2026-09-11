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

### Explicit current-payload convergence

Ordinary payload-local invocation resolves an existing complete runtime; it
does not synchronously upgrade that runtime after every payload refresh.
Bootstrap callers that have just refreshed the official marketplace payload
and require its implementation can explicitly converge it:

```bash
"$BRIDGE_PAYLOAD/bin/agent-bridge" provision --current-payload --json
```

```powershell
& "$BridgePayload\bin\agent-bridge.cmd" provision --current-payload --json
```

`BRIDGE_PAYLOAD` / `BridgePayload` denotes the exact attributed plugin payload
already selected by the caller, not a `PATH` search or a wildcard first match.
The payload gate handles this command **before dispatching to installed
Python code**, so an older runtime does not need to know this new operation.
Do not invoke it as `python -m agent_bridge provision`.

The gate reuses its ordinary installation-context resolver and runtime
selection. Callers must not reproduce that resolver or assume
`~/.agent-bridge`; an active context remains authoritative.

| Selected installation | Result |
|---|---|
| Positively authorized legacy, already current and complete | Read-only `unchanged` receipt; no installer/service call |
| Positively authorized legacy, stale | Existing owner installer `update --install-dir <resolved-root>` (Windows: `update -InstallDir <resolved-root>`), followed by strict verification |
| Positively authorized legacy, missing runtime | Existing owner `provision` at the resolved root, followed by strict verification |
| Active namespaced, already current and complete | Read-only validation of context generation, slot ownership/completion and payload content; `unchanged` receipt |
| Namespaced stale/incomplete, deactivating, ambiguous, mismatched or otherwise unsupported | Nonzero exit before installer, lock/status creation, marker writes or legacy fallback |

Legacy mutation requires the canonical `probe-legacy` decision to allow
mutation with `probeReason: legacy-active`, checked again inside the existing
provisioning lock. A migration-required decision is not authorization for this
operation. `AGENT_BRIDGE_NO_SELFPROVISION` still forbids provisioning/updating.
The owner installer's existing downgrade, dependency, locking and service
cutover policies remain in effect; convergence never forces a downgrade.
An update may cut over the **selected legacy service**. This is an explicit
management operation, not a side-effect-free status query.

On exit zero, stdout contains exactly one JSON object:

```json
{
  "schema": "copilot-extensions.agent-bridge.runtime-convergence",
  "version": 1,
  "pluginId": "agent-bridge",
  "status": "ready",
  "mode": "legacy",
  "action": "updated",
  "payloadRoot": "/path/to/payload",
  "payloadVersion": "0.4.0-dev480",
  "runtimeRoot": "/path/to/selected-runtime",
  "runtimeVersion": "0.4.0-dev480",
  "python": "/path/to/selected-runtime/versions/0.4.0-dev480/bin/python",
  "complete": true,
  "currentPayload": true,
  "serviceChecked": false
}
```

`action` is `unchanged`, `updated`, or `provisioned`. Readiness requires the
current marker, complete slot, installed distribution version and package
origin to agree with the selected payload. Legacy slots must also match the
owner installer's existing platform-specific content fingerprint. Namespace
slots require the canonical immutable completion chain and matching snapshot
content. Installer exit zero without those postconditions is an error, not a
success-shaped receipt. Diagnostics and installer output go to stderr.

This is **runtime readiness only**, not daemon readiness, native capability,
provider availability or session representation. After convergence, callers
still run their normal payload-local `service start`, readiness and capability
checks. There is no general namespaced bridge service-update transaction in
this compatibility operation; a stale namespace is deliberately refused.

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

This projects already-published repository topology into user-level bridge
configuration; it does not create or edit `machines.yaml`. Make repository
changes in a worktree, merge them, and sync the canonical checkout first.

This auto-discovers `machines.yaml` and creates a topology profile; the agent
roster is **derived** from it (+ `.agent-worktrees/related.yaml` and local repo
registry data when available). See
[Machine Configuration](machine-config.md) for the full guide on the
`machines.yaml` format and the derived roster.

Linked Git worktrees are canonicalized to their stable anchor before an
in-repo auto-discovered topology path is stored. A stateless harness may inherit
topology from its bound knowledge/state root, while explicit config paths remain
exact. Any external path into a disposable worktree will fail validation after
that worktree is removed. Before repairing it with re-adoption, preserve the
profile stanza from `~/.agent-bridge/config.yaml`, including
`default_copilot_args` and `default_env`; adoption replaces the named profile
rather than merging those defaults.

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
