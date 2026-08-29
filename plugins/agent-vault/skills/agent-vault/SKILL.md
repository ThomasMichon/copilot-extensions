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
  - 'vault persistent cache'
---

# agent-vault -- Local Secret Store

`agent-vault` is a **standalone, machine-scoped credential tap**. It uses a
local KeePassXC `.kdbx` database, a machine-local service that caches the
KeePass master password for a bounded time, and a CLI that fetches one entry at
the moment a tool needs it. It does **not** require an `agent-worktrees` repo
registration, a broker, a tunnel, a container, or a remote core.

## Readiness

Use the exact `argv` prefix from the agent-vault session command catalog for every
shell operation in this skill. Replace `<agent-vault catalog argv prefix>` with
its shell-ready rendering: quote each prefix element separately and prepend `&`
in PowerShell. In
PowerShell invoke it as
`& <agent-vault catalog argv prefix> <args>`. Never search `PATH` for a
same-named command. The payload shim may self-provision on first use and print
`::agent-provisioning::` (~30-120s); let that finish.

If session-start hooks did not publish the catalog, enumerate installed
payloads for this plugin and fail unless exactly one exists. Invoke that
payload's `bin/agent-vault` on POSIX or `bin\agent-vault.cmd` on Windows
directly; do not stamp a global wrapper just to recover an in-session command.
Never choose the first match from multiple marketplaces.

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
<agent-vault catalog argv prefix> vault add Personal --kpdb ~/Personal.kdbx --group Personal
<agent-vault catalog argv prefix> vault add Work --kpdb ~/Work.kdbx --group Work
<agent-vault catalog argv prefix> vault set-default Personal
<agent-vault catalog argv prefix> vault list
```

Repo-local selector (`.agent-vault.json` at or above the repo root):

```json
{ "vault": "Work" }
```

Precedence per call: env vars (`AGENT_VAULT`, `KPDB`, `VAULT_GROUP`,
`AGENT_VAULT_PORT`) > repo config > extension config > global named vault >
defaults. Check the resolved values with:

```bash
<agent-vault catalog argv prefix> which
<agent-vault catalog argv prefix> which --json
```

Prerequisite: KeePassXC with `keepassxc-cli` on PATH, or the standard Windows
install path (`C:\Program Files\KeePassXC\keepassxc-cli.exe`).

## Fetch-on-demand discipline

Fetch secrets in place at the point of use. Do **not** export them into a long-
lived shell environment.

```bash
# Good: command substitution limits lifetime to this command.
curl -H "Authorization: Bearer $(<agent-vault catalog argv prefix> get 'API/Example')" https://example.invalid/

# Avoid: exported values linger and leak to children.
export EXAMPLE_KEY="$(<agent-vault catalog argv prefix> get 'API/Example')"
```

## Common CLI verbs

```bash
<agent-vault catalog argv prefix> ping
<agent-vault catalog argv prefix> unlock
<agent-vault catalog argv prefix> unlock --terminal
<agent-vault catalog argv prefix> get "API/OpenAI"
<agent-vault catalog argv prefix> get "API/OpenAI" username
<agent-vault catalog argv prefix> has "API/OpenAI"
<agent-vault catalog argv prefix> search OpenAI
<agent-vault catalog argv prefix> list Personal -R -f
<agent-vault catalog argv prefix> show "API/OpenAI" -s
<agent-vault catalog argv prefix> add "API/OpenAI" --username alice
<agent-vault catalog argv prefix> set-password "API/OpenAI"
<agent-vault catalog argv prefix> set-username "API/OpenAI" alice
<agent-vault catalog argv prefix> remove "API/OpenAI" -f
<agent-vault catalog argv prefix> move "API/OpenAI" Archive -f
<agent-vault catalog argv prefix> lock
```

### SSH keys

```bash
<agent-vault catalog argv prefix> import-key "SSH/deploy" ~/.ssh/id_ed25519
<agent-vault catalog argv prefix> export-key "SSH/deploy" ~/.ssh id_ed25519
```

`import-key` requires the public key beside the private key (`.pub`).
`export-key` writes the private/public pair and sets POSIX file modes when not on
Windows.

### Git HTTPS credentials

The `git-credential get|store|erase` subcommands form a Git credential-helper
surface.
Only `get` resolves a credential; `store` and `erase` intentionally no-op. The
daemon delegates allowlisted hosts to local Git Credential Manager (`VAULT_GCM_HOSTS`,
default GitHub + Azure DevOps hosts). This path is independent of KeePassXC and
does not unlock the vault.

```bash
git config --global credential.helper '!agent-vault git-credential' # marketplace-isolation: allow git-credential-management
```

This registration is an explicit out-of-session management boundary: Git
invokes the helper later, when no session command catalog exists.

### Persistent cache

The encrypted persistent cache is off by default. Enable it only when an
unattended job needs previously fetched values while the vault is locked.

```bash
export AGENT_VAULT_CACHE=1
<agent-vault catalog argv prefix> cache-populate --entry API/OpenAI --prompt
<agent-vault catalog argv prefix> cache-status
<agent-vault catalog argv prefix> cache-verify --entry API/OpenAI
<agent-vault catalog argv prefix> get API/OpenAI --cache-only
<agent-vault catalog argv prefix> cache-clear
```

`cache-verify` exits `2` if any requested entry is missing. The cache requires
`cryptography`; without it, cache operations are a safe no-op.

### Envelope KEK (`seal` / `unseal`)

Use this when a consumer owns its own on-disk cache and needs a local encryption
key without hardcoding one. KEKs are stored beside the agent-vault config (DPAPI
per user on Windows; `0600` file on POSIX) and are independent of the KeePass
master password, so `seal`/`unseal` work while locked.

```bash
printf '%s' "$TOKEN" | <agent-vault catalog argv prefix> seal spark > token.sealed
<agent-vault catalog argv prefix> unseal spark --in token.sealed
<agent-vault catalog argv prefix> kek-list
```

On Windows the catalog uses an absolute PowerShell 7.3+ host prefix ending in
the payload `.ps1`, preserving arguments and stdin for `seal`.

Requires `cryptography`; otherwise the command returns a clear error.

## Locking and prompting

- Master passwords are cached **per database** by the daemon.
- `VAULT_PASSWORD_TTL` controls password lifetime (default `3600` seconds).
- The payload-local command's `lock` operation clears cached master passwords
  and in-memory credential values.
- Locked `get`/`has`/`search`/`list`/`show` reads fail fast by default after
  unlock-source providers miss. Use the payload-local `unlock`,
  `unlock --terminal`, or `get --prompt` operation when a prompt is appropriate.
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

There is no `doctor` command today. Use:

```bash
<agent-vault catalog argv prefix> which --json
<agent-vault catalog argv prefix> ping
<agent-vault catalog argv prefix> cache-status --json
bash plugins/agent-vault/scripts/install.sh status
```

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action status
```

For architecture details, see `plugins/agent-vault/docs/architecture.md`.
