---
name: agent-vault
description: >
  Store, fetch, and minimally manage secrets from a standalone local
  KeePassXC-backed vault -- API keys, SSH keys, tokens, account credentials,
  Git HTTPS credentials, persistent-cache entries, and envelope-KEK sealed
  values -- without hardcoding them, committing them, or exporting them into
  the environment. Use this skill for day-to-day `agent-vault` CLI usage
  (`get`/`has`/`search`/`list`/`show`/`add`/`set-password`/`set-username`,
  `import-key`/`export-key`, `git-credential`, `cache-*`, `seal`/`unseal`,
  `which`, `vault *`, lock/unlock behavior, and SUDO_ASKPASS wiring). For
  runtime install/update/status/uninstall, use `agent-vault-setup`.
  Trigger phrases include:
  - 'agent-vault'
  - 'get a secret'
  - 'fetch a credential'
  - 'store an API key'
  - 'local vault'
  - 'keepassxc'
  - 'vault get'
  - 'sudo askpass'
  - 'import an ssh key into the vault'
  - 'seal a secret with agent-vault'
  - 'agent-vault cache'
---

# agent-vault -- Local Secret Store

`agent-vault` is a **standalone, machine-scoped credential tap**. It uses a
local KeePassXC `.kdbx` database, a machine-local service that caches the
KeePass master password for a bounded time, and a CLI that fetches one entry at
the moment a tool needs it. It does **not** require an `agent-worktrees` repo
registration, a broker, a tunnel, a container, or a remote core.

## Readiness

If `agent-vault` is already on PATH, use it directly; the binstub may
self-provision the runtime on first use and print `::agent-provisioning::`
(~30-120s). Let that finish.

If the command is missing but the plugin payload is installed, stamp the binstub
without building the full runtime:

```bash
bash "$(ls ~/.copilot/installed-plugins/*/agent-vault/scripts/install.sh | head -1)" stamp
```

```powershell
$script = Get-ChildItem "$env:USERPROFILE\.copilot\installed-plugins\*\agent-vault\scripts\install.ps1" | Select-Object -First 1
pwsh -File $script.FullName -Action stamp
```

For full install/update/status/uninstall, switch to the `agent-vault-setup`
skill.

## First-run configuration

Point the vault at a KeePassXC database with either `KPDB`, named vaults, or a
repo-local `.agent-vault.json`.

Single database:

```bash
export KPDB="$HOME/Secrets/vault.kdbx"
export VAULT_GROUP="Personal"      # optional prefix for bare entry names
```

```powershell
$env:KPDB = "C:\Users\you\Secrets\vault.kdbx"
$env:VAULT_GROUP = "Personal"      # optional
```

Multiple named vaults:

```bash
agent-vault vault add Personal  --kpdb ~/Personal.kdbx --group Personal
agent-vault vault add Work      --kpdb ~/Work.kdbx     --group Work
agent-vault vault set-default Personal
agent-vault vault list
```

Repo-local selector (`.agent-vault.json` at or above the repo root):

```json
{ "vault": "Work" }
```

Precedence per call: env vars (`AGENT_VAULT`, `KPDB`, `VAULT_GROUP`,
`AGENT_VAULT_PORT`) > repo config > extension config > global named vault >
defaults. Check the resolved values with:

```bash
agent-vault which
agent-vault which --json
```

Prerequisite: KeePassXC with `keepassxc-cli` on PATH, or the standard Windows
install path (`C:\Program Files\KeePassXC\keepassxc-cli.exe`).

## Fetch-on-demand discipline

Fetch secrets in place at the point of use. Do **not** export them into a long-
lived shell environment.

```bash
# Good: command substitution limits lifetime to this command.
curl -H "Authorization: Bearer $(agent-vault get 'API/OpenAI')" https://example.invalid/

# Avoid: exported values linger and leak to children.
export OPENAI_KEY="$(agent-vault get 'API/OpenAI')"
```

## Common CLI verbs

```bash
agent-vault ping
agent-vault unlock                  # provider-first, then prompts if possible
agent-vault unlock --terminal       # force prompt on this terminal
agent-vault get "API/OpenAI"        # default field: password
agent-vault get "API/OpenAI" username
agent-vault has "API/OpenAI"
agent-vault search OpenAI
agent-vault list Personal -R -f
agent-vault show "API/OpenAI" -s
agent-vault add "API/OpenAI" --username alice
agent-vault set-password "API/OpenAI"
agent-vault set-username "API/OpenAI" alice
agent-vault remove "API/OpenAI" -f
agent-vault move "API/OpenAI" Archive -f
agent-vault lock
```

### SSH keys

```bash
agent-vault import-key "SSH/deploy" ~/.ssh/id_ed25519
agent-vault export-key "SSH/deploy" ~/.ssh id_ed25519
```

`import-key` requires the public key beside the private key (`.pub`).
`export-key` writes the private/public pair and sets POSIX file modes when not on
Windows.

### Git HTTPS credentials

`agent-vault git-credential get|store|erase` is a git credential-helper surface.
Only `get` resolves a credential; `store` and `erase` intentionally no-op. The
daemon delegates allowlisted hosts to local Git Credential Manager (`VAULT_GCM_HOSTS`,
default GitHub + Azure DevOps hosts). This path is independent of KeePassXC and
does not unlock the vault.

```bash
git config --global credential.helper '!agent-vault git-credential'
```

### Persistent cache

The encrypted persistent cache is off by default. Enable it only when an
unattended job needs previously fetched values while the vault is locked.

```bash
export AGENT_VAULT_CACHE=1
agent-vault cache-populate --entry API/OpenAI --prompt
agent-vault cache-status
agent-vault cache-verify --entry API/OpenAI
agent-vault get API/OpenAI --cache-only
agent-vault cache-clear
```

`cache-verify` exits `2` if any requested entry is missing. The cache requires
`cryptography`; without it, cache operations are a safe no-op.

### Envelope KEK (`seal` / `unseal`)

Use this when a consumer owns its own on-disk cache and needs a local encryption
key without hardcoding one. KEKs are stored beside the agent-vault config (DPAPI
per user on Windows; `0600` file on POSIX) and are independent of the KeePass
master password, so `seal`/`unseal` work while locked.

```bash
printf '%s' "$TOKEN" | agent-vault seal spark > token.sealed
agent-vault unseal spark --in token.sealed
agent-vault kek-list
```

Requires `cryptography`; otherwise the command returns a clear error.

## Locking and prompting

- Master passwords are cached **per database** by the daemon.
- `VAULT_PASSWORD_TTL` controls password lifetime (default `3600` seconds).
- `agent-vault lock` clears cached master passwords and in-memory credential
  values.
- Locked `get`/`has`/`search`/`list`/`show` reads fail fast by default after
  unlock-source providers miss. Use `agent-vault unlock`, `unlock --terminal`, or
  `get --prompt` when a prompt is appropriate.
- Non-interactive SSH sessions fail fast instead of popping a GUI the operator
  cannot see.

## SUDO_ASKPASS (Linux / WSL)

The POSIX installer writes `~/.local/bin/vault-askpass`:

```bash
export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass"
export VAULT_SUDO_ENTRY="Personal/sudo"
sudo -A true
```

There is no default sudo entry. Set `VAULT_SUDO_ENTRY` to the KeePass entry that
holds your sudo password.

## Troubleshooting quick checks

There is no `agent-vault doctor` command today. Use:

```bash
agent-vault which --json
agent-vault ping
agent-vault cache-status --json
bash plugins/agent-vault/scripts/install.sh status
```

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action status
```

For architecture details, see `plugins/agent-vault/docs/architecture.md`.
