# agent-vault

`agent-vault` is a standalone, machine-local secret store for Copilot CLI agents and local automation. It uses a user-selected KeePassXC `.kdbx` database, a small local daemon that caches the KeePass master password for a bounded time, and an `agent-vault` CLI that fetches API keys, SSH keys, credentials, and small sealed values on demand.

It does **not** require a repo to be registered with `agent-worktrees`, and it
does not require a broker, container, tunnel, or remote service. Agent-facing
skills receive an exact payload-local command from the session catalog.
Out-of-session automation, Git credential-helper registration, supervision,
and `vault-askpass` remain explicit compatibility/management boundaries until
they receive attributable installation context.

## Setup in one pass

1. **Enable the plugin payload** through Copilot's plugin mechanism (or work from a local checkout of this repo).
2. **Install or stamp the runtime.** The plugin has a session-start bootstrap hook that performs a cheap `stamp` when the runtime is missing; the stamped binstub self-provisions the full venv on first use. To do it explicitly from a checkout:

   ```powershell
   pwsh -File plugins\agent-vault\scripts\install.ps1 -Action install
   ```

   ```bash
   bash plugins/agent-vault/scripts/install.sh install
   ```

   Use `stamp` instead of `install` when you only need the binstub now and want the first real `agent-vault ...` call to build the runtime.
3. **Install KeePassXC** with `keepassxc-cli` on PATH (or at `C:\Program Files\KeePassXC\keepassxc-cli.exe` on Windows).
4. **Point the CLI at a database** with `KPDB`, a named vault, or a repo-local `.agent-vault.json`.

## Configuration

Fast single-vault setup:

```powershell
$env:KPDB = "C:\Users\you\Secrets\vault.kdbx"
$env:VAULT_GROUP = "Personal"   # optional prefix for bare entry names
```

```bash
export KPDB="$HOME/Secrets/vault.kdbx"
export VAULT_GROUP="Personal"   # optional
```

Named vault setup for machines with multiple databases:

```bash
agent-vault vault add Personal  --kpdb ~/Personal.kdbx --group Personal
agent-vault vault add Work      --kpdb ~/Work.kdbx     --group Work
agent-vault vault set-default Personal
agent-vault vault list
```

Point a repo at a named vault with an `.agent-vault.json` at or above the repo root:

```json
{ "vault": "Work" }
```

Resolution is per call: environment (`AGENT_VAULT`, `KPDB`, `VAULT_GROUP`, `AGENT_VAULT_PORT`) beats repo config, which beats extension config, which beats the global named-vault registry/default. Inspect the result with:

```bash
agent-vault which
agent-vault which --json
```

## Daily use

```bash
agent-vault ping
agent-vault unlock                 # provider-first, then prompts where possible
agent-vault get "API/OpenAI"       # default field: password
agent-vault get "API/OpenAI" username
agent-vault add "API/OpenAI" --username alice
agent-vault set-password "API/OpenAI"
agent-vault search OpenAI
agent-vault list Personal -R -f
agent-vault show "API/OpenAI" -s
agent-vault lock
```

Fetch secrets **at the point of use** instead of exporting them into the shell:

```bash
curl -H "Authorization: Bearer $(agent-vault get 'API/OpenAI')" https://example.invalid/
```

Supported built-in command groups:

| Area | Commands |
|------|----------|
| Entries | `get`, `has`, `search`, `list`/`ls`, `show`, `add`, `set-password`, `set-username`, `remove`/`rm`, `move`/`mv` |
| SSH keys | `import-key`, `export-key` |
| Service | `ping`, `start`, `stop`, `lock`, `unlock` (`--terminal`/`--here`) |
| Config | `which`, `vault list`, `vault add`, `vault set-default`, `vault remove` |
| Git HTTPS credentials | `git-credential get|store|erase` (delegates allowlisted hosts to local Git Credential Manager) |
| Persistent cache | `cache-populate`, `cache-status`, `cache-clear`, `cache-verify` |
| Envelope KEK | `seal`, `unseal`, `kek-list` |

## Locking, prompting, and robustness

- The daemon keeps KeePass master passwords in memory **per database**. `VAULT_PASSWORD_TTL` controls the password TTL (default `3600` seconds). `agent-vault lock` drops the cached password immediately.
- Credential reads fail fast by default when locked: they return an actionable error instead of hanging on a hidden prompt. Use `agent-vault unlock`, `agent-vault unlock --terminal`, or `agent-vault get --prompt ...` when you want an interactive prompt.
- Unlock-source extensions run before prompts, so a configured provider can satisfy locked reads without UI.
- The CLI cold-starts the daemon when needed. The installer also deploys platform supervision: a Windows Scheduled Task named `AgentVault`, or a Linux/WSL systemd user unit named `agent-vault.service` when systemd is available.
- Windows updates cooperatively stop the old daemon before starting the new runtime so an in-flight request can finish; auth state reconnects through the opt-in persistent cache or by one re-unlock. POSIX updates rely on the systemd user service restart path.
- There is **no** `agent-vault doctor` subcommand today. Use `agent-vault ping`, `agent-vault which`, `agent-vault cache-status`, and the installer `status` action for troubleshooting.

## Persistent cache and sealed values

The persistent cache is off by default. Enable it only when a locked, unattended process must reuse previously fetched values:

```bash
export AGENT_VAULT_CACHE=1
agent-vault cache-populate --entry API/OpenAI
agent-vault cache-verify --entry API/OpenAI
```

The cache and `seal`/`unseal` require the optional `cryptography` dependency. Without it, the cache is a safe no-op and KEK commands return a clear error. Install it into the active runtime slot with the environment's package manager. On POSIX, the stable target is `~/.agent-vault/.venv/bin/python`; on Windows, read `%USERPROFILE%\.agent-vault\current-version` and target `%USERPROFILE%\.agent-vault\versions\<version>\Scripts\python.exe`.

`seal`/`unseal` use a named envelope KEK stored beside the agent-vault config (DPAPI-wrapped per user on Windows; `0600` file on POSIX). The KEK is independent of the KeePass master password, so these commands work while the vault is locked.

## SUDO_ASKPASS (Linux / WSL)

The POSIX installer writes `~/.local/bin/vault-askpass`:

```bash
export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass"
export VAULT_SUDO_ENTRY="Personal/sudo"
sudo -A apt update
```

Set `VAULT_SUDO_ENTRY`; there is no default.

## Architecture and references

- Plugin architecture: [`docs/architecture.md`](docs/architecture.md)
- Usage skill: [`skills/agent-vault/SKILL.md`](skills/agent-vault/SKILL.md)
- Setup skill: [`skills/agent-vault-setup/SKILL.md`](skills/agent-vault-setup/SKILL.md)
- Patterns referenced by the implementation: [`service-lifecycle-supervision`](../../docs/patterns/service-lifecycle-supervision.md), [`graceful-daemon-cutover`](../../docs/patterns/graceful-daemon-cutover.md), [`local-endpoint-discovery`](../../docs/patterns/local-endpoint-discovery.md), and [`service-transport`](../../docs/patterns/service-transport.md)

## Not in scope

The core currently has one secret-store backend: KeePassXC via `keepassxc-cli`. Native OS keychain/Secret Service backends and additional store drivers are future work. Cross-machine or containerized cores are optional extension transports, not requirements for local use.

## License

MIT.
