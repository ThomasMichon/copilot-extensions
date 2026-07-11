---
name: agent-vault-setup
description: >
  One-time install and update of the agent-vault runtime -- the local vault
  service, the `~/.local/bin/agent-vault` binstub, and (on Linux/WSL) the
  `vault-askpass` SUDO_ASKPASS helper. Use this skill for first-time setup,
  refreshing the runtime after a plugin payload update, or uninstalling. For
  day-to-day use (get/add/search/SSH keys), see the `agent-vault` skill.
  Trigger phrases include:
  - 'install agent-vault'
  - 'set up agent-vault'
  - 'update the vault runtime'
  - 'deploy agent-vault'
  - 'agent-vault setup'
  - 'uninstall agent-vault'
---

# agent-vault Setup

`agent-vault` ships a **runtime** (a Python venv at `~/.agent-vault`, a
`~/.local/bin/agent-vault` binstub, and a background vault service). A
`copilot plugin update agent-vault` only refreshes the plugin **payload** --
it does **not** move the runtime. Deploying is always two steps: refresh the
payload, then run the plugin's installer from the source folder.

## Prerequisites

- **Python 3.10+**.
- **KeePassXC** with `keepassxc-cli` on PATH (Linux/WSL) or installed at the
  standard Windows path. The runtime installs without it, but cannot unlock a
  database until it is present.
- **Windows only -- a code-signed base Python** (python.org or the Microsoft
  Store build). The installer builds the venv from a signed interpreter via
  `--copies` so Smart App Control trusts it; without one, the venv is built
  unsigned and SAC may block it (a loud warning is printed).

## Install

Run the plugin's installer from its source directory (a local checkout of the
`copilot-extensions` repo, or the marketplace-vendored plugin dir):

```bash
# Linux / WSL
bash plugins/agent-vault/scripts/install.sh install
```
```powershell
# Windows
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action install
```

This creates the venv, installs the `agent_vault` package into it, writes the
`~/.local/bin/agent-vault` binstub, deploys a background service (systemd
**--user** unit on Linux; a windowless scheduled task on Windows), and -- on
Linux/WSL -- installs the `vault-askpass` helper. Add `--no-service`
(`-NoService` on Windows) for a client-only host that should not run the daemon.

Ensure `~/.local/bin` is on your PATH.

## Update (after a payload refresh)

```bash
copilot plugin update agent-vault           # refresh the payload
bash plugins/agent-vault/scripts/install.sh update
```

> **Windows caveat.** When agent-vault is loaded in the running session,
> `copilot plugin update` can fail with a busy-directory error. Run
> `install.ps1 -Action update` from a **local checkout** of the repo instead
> (its `source.kind` becomes `local`).

The installer is downgrade-guarded; force a deliberate rollback with `--force`
(`-Force`) or `AGENT_VAULT_ALLOW_DOWNGRADE=1`.

## First-run configuration

Point the vault at your database and start it:

```bash
export KPDB="$HOME/Secrets/vault.kdbx"
agent-vault ping        # cold-starts the service; prompts for the master password on first read
```

## SUDO_ASKPASS wiring (Linux / WSL)

```bash
export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass"
export VAULT_SUDO_ENTRY="Personal/sudo"     # your KeePass entry holding the sudo password
sudo -A true                                 # sourced from the vault
```

Set these from a profile your non-interactive shells read; a `~/.bashrc`
`sudo()` wrapper only covers interactive shells.

## Status / uninstall

```bash
bash plugins/agent-vault/scripts/install.sh status
bash plugins/agent-vault/scripts/install.sh uninstall            # keep config + DB
bash plugins/agent-vault/scripts/install.sh uninstall --purge    # also remove the runtime dir
```

The `.kdbx` database is yours and is never created, moved, or deleted by the
installer.
