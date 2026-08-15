# agent-vault architecture

This document describes the implementation as it exists today. It intentionally references the repo-wide patterns instead of restating their prescriptions: [service-lifecycle-supervision](../../../docs/patterns/service-lifecycle-supervision.md), [graceful-daemon-cutover](../../../docs/patterns/graceful-daemon-cutover.md), [local-endpoint-discovery](../../../docs/patterns/local-endpoint-discovery.md), and [service-transport](../../../docs/patterns/service-transport.md).

## Components

| Component | Source | Responsibility |
|-----------|--------|----------------|
| CLI | `src/agent_vault/cli.py` | Parses `agent-vault` commands, resolves the active vault on each call, cold-starts the service, performs persistent-cache tier-0 reads, and sends newline-framed JSON requests to the daemon. |
| Service | `src/agent_vault/service.py` | Owns the in-memory KeePass master-password cache and credential-value cache, verifies/unlocks databases, handles entry operations, GCM delegation, KEK operations, endpoint advertisement, and shutdown. |
| KeePassXC backend | `src/agent_vault/keepassxc.py` | Locates `keepassxc-cli` and maps service actions to `keepassxc-cli` commands. KeePassXC is the only secret-store backend today. |
| Config resolver | `src/agent_vault/config.py` | Resolves database, group, vault name, port, runtime paths, and config files from env, repo config, extension config, global named vaults, and defaults. |
| Installer/bootstrap | `scripts/install.ps1`, `scripts/install.sh`, `scripts/bootstrap-check.*` | Creates/stamps the runtime, deploys binstubs, records a deploy manifest, installs platform supervision, and reconciles version drift. |
| Extensions | `src/agent_vault/extensions.py`, `src/agent_vault/core_ext.py` | Provides opt-in hooks for unlock providers, daemon actions, transports, config/cache sources, CLI commands, startup hooks, and optional core delegation. |

## Runtime layout and install modes

Default runtime home is `~/.agent-vault` (`%USERPROFILE%\.agent-vault` on Windows). Binstubs are written under `~/.local/bin`:

- POSIX: `agent-vault` plus `vault-askpass`.
- Windows: `agent-vault.ps1` plus `agent-vault.cmd`.

The installer always uses a versioned runtime slot (`~/.agent-vault/versions/<version>`). `scripts/versioned_runtime.py` publishes the active version with `current-version`; POSIX also exposes a stable `.venv` symlink, while Windows deliberately has no junction and instead rewrites binstubs/tasks to the concrete version slot. It writes `deploy-manifest.json` with source kind, source path, plugin version, and runtime path.

There are two setup paths:

1. **Full install/update** (`install`, `update`, `provision`) builds the venv, installs the package, writes binstubs, writes the manifest, and installs supervision unless `--no-service` / `-NoService` is set.
2. **Stamp** writes a payload snapshot marker plus self-provisioning binstub without building the venv. The session-start hook uses this when no manifest exists so the command is available quickly; the first real command provisions the venv and then dispatches.

The runtime is standalone: neither installer nor CLI requires an `agent-worktrees` repo registration.

## Configuration model

`src/agent_vault/config.py` resolves the active context on every CLI call. Precedence is:

1. Environment: `AGENT_VAULT` (vault name), `KPDB`, `VAULT_GROUP`, `AGENT_VAULT_PORT`.
2. The nearest `.agent-vault.json`, discovered by walking upward from the current directory.
3. Extension config sources registered via `agent_vault.extensions`.
4. Global named vaults and `default_vault` in `$AGENT_VAULT_CONFIG` or the platform config file (`agent-vault/config.json` under `%APPDATA%` or `$XDG_CONFIG_HOME`).
5. Built-in defaults (`port=19999`, no database, no group).

The resolver also supports legacy flat global keys (`kpdb`, `group`/`vault_group`, `port`). `agent-vault which --json` prints the resolved values and their sources.

## Service transport and discovery

The daemon is local-only. It serves the same newline-framed JSON protocol over the transports that are available on the host:

- POSIX: a Unix socket (`AGENT_VAULT_SOCKET`, default currently `/tmp/agent-vault-service.sock`) and a loopback TCP listener (`127.0.0.1:<resolved port>`, default `19999`). If TCP bind fails but the Unix socket is up, the daemon keeps serving over the socket.
- Windows: loopback TCP plus a best-effort named pipe (`AGENT_VAULT_PIPE`, default `\\.\pipe\agent-vault`). The named pipe is the preferred advertised endpoint when it binds; TCP is advertised as an alternate for WSL/host-boundary callers.

On startup the daemon writes a rendezvous file at `~/.agent-vault/run/endpoint.json` (`AGENT_VAULT_RUN_DIR` overrides) using `src/agent_vault/rendezvous.py`. Clients resolve in this order:

1. `AGENT_VAULT_ENDPOINT` explicit endpoint spec.
2. The local rendezvous file.
3. WSL-only: a Windows-side rendezvous file under `/mnt/c/Users/<user>/.agent-vault/run/endpoint.json` (or `AGENT_VAULT_WINDOWS_RUN_DIR`).
4. Legacy fixed Unix socket / TCP fallback.
5. Registered extension transports. `before_builtin=True` transports run before built-ins; default transports run after built-ins fail.

This matches the discovery/transport patterns while preserving the legacy fixed port for backward compatibility.

## Unlock, TTL, prompting, and failure mode

`src/agent_vault/service.py` caches KeePass master passwords per database path in the `KeePassXCBackend` instance. Defaults:

- `VAULT_PASSWORD_TTL=3600`: after this many seconds the master password and credential-value cache for that database are cleared.
- `VAULT_TIMEOUT=600`: service inactivity timeout, unless the daemon is launched with `--persistent` (the installer uses persistent mode for supervised services).
- `VAULT_PROMPT_DISMISS_COOLDOWN=120`: suppresses repeated interactive prompt popups after a dismissal/timeout.

Locked reads are fail-fast by default. For `get`, `has`, `search`, list/show, mutations, and key import/export, the daemon first runs unlock-source providers. If no provider unlocks the database and the request did not opt into prompting, the response is `ok=false`, `needs_unlock=true`, with an actionable error. Prompting happens through explicit surfaces:

- `agent-vault unlock` is provider-first, then prompts via the best reachable channel.
- `agent-vault unlock --terminal` / `--here` reads from the controlling terminal.
- `agent-vault get --prompt ...` allows a daemon-side prompt for that read.
- Mutating commands call the CLI's unlock helper before sending the mutation.

Prompt helpers are in `src/agent_vault/cli.py` and `src/agent_vault/prompt.py`. They prefer a controlling terminal when present, avoid popping an unseen GUI in non-interactive SSH, and return failure instead of stalling when no prompt path is available.

## Data operations

The daemon supports the actions registered in `VaultService.handle_request`:

- Entry reads and mutations: `get`, `has`, `search`, `list`/`ls`, `show`, `add`, `set-password`, `set-username`, `remove`/`rm`, `move`/`mv`.
- SSH key attachment import/export: `import-key`, `export-key`.
- Service state: `ping`, `lock`, `unlock`, `stop`.
- Git HTTPS credential helper: `git-credential`, delegated to local Git Credential Manager for `VAULT_GCM_HOSTS` (default GitHub and Azure DevOps hosts), independent of KeePass unlock state.
- Envelope KEK: `seal`, `unseal`, `kek-list`, independent of KeePass unlock state.
- Extension actions registered with `register_action`.

`remove` and `move` are scoped to the resolved vault group unless the caller passes `--force`.

## Persistent cache and KEK storage

The persistent cache (`src/agent_vault/cache.py`) is off unless `AGENT_VAULT_CACHE` is truthy or `AGENT_VAULT_CACHE_DIR` is set. When enabled and `cryptography` is installed, `agent-vault get` checks the encrypted file cache before contacting the daemon, unless `--refresh` is used. `--cache-only` never contacts the service. Cache commands are `cache-populate`, `cache-status`, `cache-clear`, and `cache-verify` (which exits `2` when required entries are missing).

The cache file lives under the configured cache dir, defaulting to a `cache` directory beside the global config. Its Fernet key is wrapped with `src/agent_vault/kek.py`: DPAPI per-user on Windows, `0600` raw wrapping on POSIX. This is a convenience layer for locked unattended reads, not a substitute for host security and disk encryption.

`seal`/`unseal` use named 32-byte KEKs stored beside the config (`AGENT_VAULT_KEK_DIR` overrides). They require `cryptography` for AES-256-GCM. KEKs are independent of KeePass master passwords, so these commands work while the vault is locked.

## Supervision and updates

The POSIX installer writes a systemd user unit (`agent-vault.service`) when systemd is available. It runs:

```text
<runtime>/.venv/bin/python -m agent_vault.service --foreground --persistent
```

The Windows installer registers a Scheduled Task named `AgentVault`, triggered at logon with a 15-second delay, running the resolved version-slot Python through `conhost.exe --headless ... -m agent_vault.service --foreground --persistent`.

`--no-service` / `-NoService` installs a client-only runtime. Even without supervision, the CLI can cold-start the daemon on demand.

Windows `update` includes `Stop-VaultDaemonGraceful` in `scripts/install.ps1`: after building/activating the new slot, it pings the old daemon, sends the cooperative `--stop` action, waits briefly for the endpoint to be released, then starts/registers the scheduled task. This is the plugin's light connection-owner cutover: short in-flight requests finish, but the in-memory master password is intentionally released; reconnect is via the opt-in persistent cache or a single re-unlock. POSIX `update` reinstalls and restarts the systemd user service.

## SUDO_ASKPASS

`install.sh` writes `~/.local/bin/vault-askpass`. The helper sets `VAULT_NONINTERACTIVE=1` and executes:

```bash
agent-vault get "${VAULT_SUDO_ENTRY:?set VAULT_SUDO_ENTRY to your sudo KeePass entry path}" password
```

It is Linux/WSL-only and has no default entry.

## Extensions and optional core delegation

Extensions are discovered from Python entry points in the `agent_vault.extensions` group and from `AGENT_VAULT_EXTENSIONS` (`module` or `module:callable`, comma-separated). Loading is idempotent and fail-open.

Hook categories implemented in `src/agent_vault/extensions.py`:

| Hook | Register method | Consulted |
|------|-----------------|-----------|
| Unlock-source provider | `register_unlock_provider` | Before interactive unlock prompting. |
| Protocol action | `register_action` | Before the unknown-action fallback. |
| Client transport | `register_transport` | Before or after built-in transports, depending on `before_builtin`. |
| Config source | `register_config_source` | Below repo config and above named-vault base. |
| Cache source | `register_cache_source` | During `cache-populate` / `cache-verify`. |
| CLI command | `register_cli_command` | After built-in argparse verbs. |
| Startup hook | `register_startup` | Once after listeners bind and endpoint discovery is advertised. |

`src/agent_vault/core_ext.py` registers the built-in optional core-delegation transport. `AGENT_VAULT_CORE_ENDPOINT` or a core rendezvous file under `~/.agent-vault/core` selects a remote/containerized daemon speaking the same protocol; `AGENT_VAULT_CORE_TOKEN` attaches an optional bearer token; `AGENT_VAULT_CORE_TIMEOUT` bounds the round trip (default 30 seconds). It is a fallback transport, so a local daemon wins and an absent/unreachable core degrades to the local path or a normal error.

## Troubleshooting surfaces

There is no `agent-vault doctor` command in the CLI today. Use:

- `agent-vault which --json` for config resolution.
- `agent-vault ping` for daemon PID, TTL, cache count, status, and transport.
- `agent-vault cache-status --json` for persistent-cache enablement and location.
- `scripts/install.ps1 -Action status` or `scripts/install.sh status` for deployed version, binstub, KeePassXC prerequisite, and supervised service state.
- Service logs from `AGENT_VAULT_LOG` or the platform default (`agent-vault-service.log` under `%TEMP%` on Windows, `/tmp` on POSIX by default).
