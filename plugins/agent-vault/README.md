# agent-vault

`agent-vault` is a local KeePassXC-backed secret store for Copilot CLI agents. It runs a small machine-local service that keeps the KeePass master password in memory for a limited time and exposes a CLI for common entry operations.

Secrets are fetched **on demand** and used in place -- never hardcoded, committed, or exported into the shell environment. The credential store is a **backend/driver**: KeePassXC is the only backend today (`KeePassXCBackend`), but the CLI verbs are store-neutral so other backends can be added later.

## Install

This plugin ships a runtime (a Python venv, a `~/.local/bin/agent-vault` binstub, and a background service), so `copilot plugin update` alone does not deploy it. Run the installer from the plugin source:

```bash
bash plugins/agent-vault/scripts/install.sh install       # Linux / WSL
```
```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action install   # Windows
```

See the `agent-vault-setup` skill for details (updates, `--no-service`, SUDO_ASKPASS wiring, the Windows signed-Python note).

## Prerequisites

- Python 3.10+
- KeePassXC with `keepassxc-cli` available on PATH, or installed at the standard Windows KeePassXC path
- A local `.kdbx` database file

## First-run configuration

Set `KPDB` to the full path of your `.kdbx` file before starting the service:

```powershell
$env:KPDB = "C:\Users\you\Secrets\vault.kdbx"
```

```bash
export KPDB="$HOME/Secrets/vault.kdbx"
```

Optional: set `VAULT_GROUP` to prefix bare entry names. For example, with `VAULT_GROUP=Personal`, `agent-vault get example` reads `Personal/example`; entries that already include a group path are left unchanged.

Optional: set `AGENT_VAULT_PORT` to override the localhost TCP port. The default is `19999`.

> **Config is read by the service at startup.** `KPDB`, `VAULT_GROUP`, and the
> port are resolved by the vault **service** when it starts (from the environment
> or the JSON config file at `$AGENT_VAULT_CONFIG` / the platform config dir), not
> by each CLI call. Set them **before** the service starts (or in the config
> file), and restart the service (`agent-vault stop` then a call re-starts it, or
> restart the systemd unit / scheduled task) after changing them. Setting
> `VAULT_GROUP` only in a client shell after the daemon is already running has no
> effect.

## Quickstart

```bash
agent-vault start
agent-vault ping
agent-vault add "Personal/example" --username alice
agent-vault get "Personal/example"
```

Useful commands:

- `agent-vault get ENTRY [field]`
- `agent-vault has ENTRY`
- `agent-vault search QUERY`
- `agent-vault lock` / `agent-vault unlock`
- `agent-vault set-password ENTRY`
- `agent-vault import-key ENTRY path/to/key`
- `agent-vault export-key ENTRY dest_dir key_name`

## The service

The vault service caches the master password in memory (default 1 h TTL) and
serves reads over a loopback endpoint (Unix socket + `127.0.0.1`). It is
**passively locked**: it prompts for the master password (GUI dialog or
terminal) only at the moment a secret is actually requested, and re-locks when
the TTL expires. The CLI cold-starts it on first use; the installer also deploys
it as a background service (systemd `--user` unit on Linux, a windowless
scheduled task on Windows) so `sudo -A` and other non-interactive callers work.

## SUDO_ASKPASS (Linux / WSL)

The installer ships `~/.local/bin/vault-askpass`, a sudo askpass provider that
sources the password from the vault:

```bash
export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass"
export VAULT_SUDO_ENTRY="Personal/sudo"    # your KeePass entry -- no default
sudo -A apt update
```

## Not in scope (v1)

A pluggable multi-backend driver interface, a native OS keychain / Secret
Service bridge, a Git credential-helper bridge, offline cache priming, and a
PowerShell module are intentionally out of scope for the first release.

## License

MIT.
