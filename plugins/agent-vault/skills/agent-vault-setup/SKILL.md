---
name: agent-vault-setup
description: >
  Install, stamp, self-provision, update, inspect, troubleshoot, or uninstall
  the standalone agent-vault runtime -- the Python venv under `~/.agent-vault`,
  self-provisioning `~/.local/bin/agent-vault` binstub(s), optional background
  vault service, and Linux/WSL `vault-askpass` SUDO_ASKPASS helper. Use this
  skill for first-time setup, runtime refresh after a payload update, checking
  status in lieu of a doctor command, client-only installs, or cleanup. For
  day-to-day secret reads/writes, use the `agent-vault` skill.
  Trigger phrases include:
  - 'install agent-vault'
  - 'set up agent-vault'
  - 'update the vault runtime'
  - 'deploy agent-vault'
  - 'agent-vault setup'
  - 'check agent-vault runtime status'
  - 'diagnose agent-vault'
  - 'troubleshoot agent-vault'
  - 'uninstall agent-vault'
---

# agent-vault Setup

Use installer paths below for install, update, supervision, status, and
uninstall: those are explicit management boundaries that run outside session
command context. For runtime CLI checks, use the exact `argv[0]` from the
plugin's session command catalog. Replace
`<agent-vault catalog argv[0]>` with that path; in PowerShell invoke it as
`& "<agent-vault catalog argv[0]>" <args>`.

`agent-vault` is a runtime plugin: a Python package/venv, binstub(s), and an
optional always-on local daemon. It is also **standalone**: setup does not depend
on registering the current repo with `agent-worktrees`.

Runtime defaults:

| Host | Runtime | Binstub(s) | Service |
|------|---------|------------|---------|
| Windows | `%USERPROFILE%\.agent-vault` | `%USERPROFILE%\.local\bin\agent-vault.ps1` + `.cmd` | Scheduled Task `AgentVault` |
| Linux / WSL | `~/.agent-vault` | `~/.local/bin/agent-vault` + `vault-askpass` | systemd user unit `agent-vault.service` when systemd is available |

## Prerequisites

- Python 3.10+.
- KeePassXC with `keepassxc-cli` on PATH (or `C:\Program Files\KeePassXC\keepassxc-cli.exe` on Windows). The runtime can install without it, but unlocks fail until it is present.
- Windows: a code-signed base Python is preferred. The installer warns if it must build from an unsigned interpreter, because Smart App Control may block the venv.
- Ensure `~/.local/bin` (or `%USERPROFILE%\.local\bin`) is on PATH.

## Minimal bootstrap (stamp + self-provision)

The plugin's session-start hook runs `scripts/bootstrap-check.*`. When no deploy
manifest exists, it performs a cheap `stamp`: copy/record the payload and write a
self-provisioning binstub. The first payload-local runtime command then builds
the venv and prints `::agent-provisioning::` (~30-120s). Do not kill that
first run.

Manual stamp from a checkout:

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action stamp
```

```bash
bash plugins/agent-vault/scripts/install.sh stamp
```

Disable first-use provisioning only for diagnostics:

```bash
export AGENT_VAULT_NO_SELFPROVISION=1
```

```powershell
$env:AGENT_VAULT_NO_SELFPROVISION = '1'
```

## Full install

From a local checkout or installed plugin payload:

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action install
```

```bash
bash plugins/agent-vault/scripts/install.sh install
```

This builds the versioned runtime slot, installs the `agent-vault` package,
writes the binstub(s), writes `deploy-manifest.json`, verifies the module
imports, warns if KeePassXC is missing, and registers/starts the background
service unless disabled.

Client-only host:

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action install -NoService
```

```bash
bash plugins/agent-vault/scripts/install.sh install --no-service
```

Even without a supervised service, the CLI can cold-start the daemon on demand.

## Update after a payload refresh

If the plugin payload version changes, `bootstrap-check.*` detects drift on a
later session start and runs the installer in the background. To refresh
immediately, run the installer update action from the fresh payload or a checkout:

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action update
```

```bash
bash plugins/agent-vault/scripts/install.sh update
```

The installer is downgrade-guarded. For an intentional rollback, pass `-Force` /
`--force` or set `AGENT_VAULT_ALLOW_DOWNGRADE=1`.

Windows update path: after activating the new version slot, `install.ps1` sends
a cooperative stop to the old daemon and waits briefly for the endpoint to close
before starting the scheduled task again. This is not a hard process kill; short
in-flight requests can finish. The in-memory master password is released, so the
new daemon reconnects through the optional persistent cache or a single re-unlock
on first use.

POSIX update path: the installer refreshes the systemd user unit and restarts it
when systemd is available; without systemd, the next CLI call cold-starts the
updated daemon.

## Start / stop / status

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action status
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action start
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action stop
```

```bash
bash plugins/agent-vault/scripts/install.sh status
bash plugins/agent-vault/scripts/install.sh start
bash plugins/agent-vault/scripts/install.sh stop
```

There is no `doctor` subcommand today. Use installer status plus these runtime
checks:

```bash
<agent-vault catalog argv[0]> which --json
<agent-vault catalog argv[0]> ping
<agent-vault catalog argv[0]> cache-status --json
```

Common findings:

| Symptom | Check / fix |
|---------|-------------|
| Runtime command unavailable | Invoke the sole installed payload's `bin/agent-vault` / `bin\agent-vault.cmd` directly, or start a new session to refresh the catalog. |
| First command appears slow | It is probably self-provisioning; wait for the `::agent-provisioning::` run to finish. |
| `KeePass database path is not configured` | Set `KPDB`, add a named vault, or create `.agent-vault.json`; inspect with the payload-local `which`. |
| `keepassxc-cli` missing | Install KeePassXC or add `keepassxc-cli` to PATH. |
| Locked read fails fast | Run the payload-local `unlock`, `unlock --terminal`, or retry `get` with `--prompt`. |
| Cache commands are disabled | Set `AGENT_VAULT_CACHE=1` or `AGENT_VAULT_CACHE_DIR`; install `cryptography` into the runtime venv. |

## First-run database config

After install/stamp, configure the database before reading entries:

```powershell
$env:KPDB = "C:\Users\you\Secrets\vault.kdbx"
& "<agent-vault catalog argv[0]>" which
& "<agent-vault catalog argv[0]>" unlock
```

```bash
export KPDB="$HOME/Secrets/vault.kdbx"
<agent-vault catalog argv[0]> which
<agent-vault catalog argv[0]> unlock
```

For multi-vault setup, use the payload-local `vault add` and
`vault set-default` operations plus a repo-local `.agent-vault.json`; see the
`agent-vault` skill.

## SUDO_ASKPASS wiring (Linux / WSL)

`install.sh install` writes `~/.local/bin/vault-askpass`:

```bash
export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass"
export VAULT_SUDO_ENTRY="Personal/sudo"
sudo -A true
```

Set these from a profile read by non-interactive shells. A shell function or
`.bashrc` wrapper only affects interactive shells; scripts must call `sudo -A`.

## Uninstall

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action uninstall
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action uninstall -Purge
```

```bash
bash plugins/agent-vault/scripts/install.sh uninstall
bash plugins/agent-vault/scripts/install.sh uninstall --purge
```

Uninstall removes supervision and binstubs. Without purge, runtime state/config
is kept. The `.kdbx` database is user-owned and is never created, moved, or
deleted by the installer.
