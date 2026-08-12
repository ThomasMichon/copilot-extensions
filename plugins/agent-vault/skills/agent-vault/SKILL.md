---
name: agent-vault
description: >
  Store and fetch secrets from a local KeePassXC-backed vault -- API keys, SSH
  keys, tokens, and account credentials -- on demand, without hardcoding them,
  committing them, or exporting them into the environment. Covers the CLI verbs
  (get/has/search/add/set-password/import-key/export-key/seal/unseal), the on-demand fetch
  discipline, first-run database configuration, and wiring the vault as a
  SUDO_ASKPASS provider for `sudo -A` on Linux/WSL.
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
---

# agent-vault -- Local Secret Store

> **Before you start — readiness (self-provisioning, no agent-worktrees required).**
> agent-vault provisions its own runtime on first use and works standalone in any
> host (CLI, Copilot app, cloud agent). If `command -v agent-vault` fails, deploy
> its binstub first (it then self-provisions on first call):
> `bash "$(ls ~/.copilot/installed-plugins/*/agent-vault/scripts/install.sh | head -1)" stamp`
> The first call may take ~30–120s to provision (watch for `::agent-provisioning::`);
> let it finish. If it reports a provisioning failure (e.g. missing uv / network),
> surface the exact message — don't improvise a toolchain install.

`agent-vault` is a **local, machine-scoped credential tap** backed by a
KeePassXC database. A small background **service** holds the KeePass master
password in memory for a bounded time (passive lock + just-in-time unlock), and
the **CLI** fetches a specific named entry exactly when a tool or agent needs
it. Secrets are never written into the shell environment or committed to a repo.

The credential store is a **backend/driver**: today the only backend is
KeePassXC (`KeePassXCBackend`), but the CLI verbs are store-neutral by design so
other backends can be added later. Speak of "the vault" / "the credential
store," not "KeePassXC," at the user surface.

## First-run configuration

Point `agent-vault` at your `.kdbx` file via the `KPDB` environment variable (or
a JSON config at `$AGENT_VAULT_CONFIG` / the platform config dir):

```bash
export KPDB="$HOME/Secrets/vault.kdbx"      # Linux / WSL
```
```powershell
$env:KPDB = "C:\Users\you\Secrets\vault.kdbx"   # Windows
```

Optional knobs:
- `VAULT_GROUP` -- prefix bare entry names with a group (e.g. `VAULT_GROUP=Personal`
  makes `get example` read `Personal/example`; full paths are left as-is). Missing
  groups are created automatically on `add`.
- `VAULT_PASSWORD_TTL` -- seconds the master password stays cached (default 3600).
- `AGENT_VAULT_PORT` -- localhost TCP port (default 19999).

The CLI resolves the effective database/group/port on **each call** and passes
them to the service — no daemon restart needed to switch vaults.

## Named vaults, per-repo config, global backstop

For a machine with more than one database (personal + work), give each a nickname
in the global config and let each repo pick one:

```bash
agent-vault vault add Personal  --kpdb ~/Personal.kdbx  --group Personal
agent-vault vault add Microsoft --kpdb ~/work/MS.kdbx   --group Work
agent-vault vault set-default Personal        # the global backstop
agent-vault vault list
```

Point a repo at a vault with an `.agent-vault.json` at/above its root (found by
walking up from the CWD, git-style): `{ "vault": "Microsoft" }` (or inline
`kpdb`/`group` overrides). **Precedence, per field:** env var › per-repo
`.agent-vault.json` › global named vault › defaults. Inspect resolution with
`agent-vault which`. One service caches master passwords **per database**, so
personal and work vaults are both usable at once (each prompts once, the prompt
names the vault). Back-compatible: just `KPDB` set = a single default vault.

Prerequisite: **KeePassXC** with `keepassxc-cli` on PATH (or the standard
Windows install path).

## Fetch-on-demand discipline

The whole point is **least exposure**. When a tool needs a secret, resolve it
in place at the moment of use -- do not stage it in the environment:

```bash
# Good: fetch on demand, use in place
curl -H "Authorization: Bearer $(agent-vault get 'API/OpenAI' password)" ...

# Avoid: exporting the secret into the session environment
export OPENAI_KEY="$(agent-vault get 'API/OpenAI' password)"   # lingers, leaks
```

## CLI verbs

```bash
agent-vault ping                       # service status
agent-vault add "API/OpenAI" -u alice  # create an entry (prompts/generates pw)
agent-vault set-password "API/OpenAI"  # update an entry's password
agent-vault get "API/OpenAI" [field]   # read a field (default: password)
agent-vault has "API/OpenAI"           # existence check
agent-vault search "OpenAI"            # find entries
agent-vault lock                       # drop the cached master password now
agent-vault unlock                     # pre-warm the cache
agent-vault start | stop               # service lifecycle (auto-starts on demand)
```

### SSH keys

```bash
agent-vault import-key "SSH/deploy" ~/.ssh/id_ed25519   # stores key + .pub as attachments
agent-vault export-key "SSH/deploy" ~/.ssh key_name     # restores the pair to a directory
```

### Sealing secrets at rest (envelope KEK)

For a consumer that keeps its **own** on-disk cache (e.g. a short-lived token) and
just needs to encrypt it at rest without hardcoding a key, the vault provides an
**envelope key-encryption-key (KEK)**. `seal`/`unseal` run inside the service, so the
raw KEK never leaves it -- only ciphertext/plaintext cross the wire. The KEK is
generated once per name and wrapped at rest: **DPAPI** (per-OS-user) on Windows, a
`0600` file on POSIX. Because it's independent of the KeePass master password,
seal/unseal work on a **locked** vault (no unlock needed).

```bash
# Seal a token (stdin) under a named KEK (auto-created on first use) -> base64
printf '%s' "$TOKEN" | agent-vault seal spark > token.sealed

# Unseal it back to stdout later (any process, same OS user)
agent-vault unseal spark --in token.sealed        # -> the original token

agent-vault kek-list                              # list KEK names
```

Tamper/wrong-key detection is built in (AES-256-GCM); `unseal` fails cleanly if the
KEK is missing or the blob was altered. The KEK name is bound as authenticated data,
so a blob sealed under one name won't unseal under another.

> **Requires `cryptography`** (the AES-256-GCM backend) -- the same optional dep as
> the persistent cache. Install it via the `kek` (or `cache`) extra, e.g.
> `uv pip install cryptography`; without it `seal`/`unseal` return a clear error.
> The KEK *wrapping* (DPAPI on Windows) needs no extra dependency.

## SUDO_ASKPASS (Linux / WSL)

Wire the vault as sudo's askpass provider so `sudo -A` sources the password from
KeePassXC instead of prompting inline. The installer ships `vault-askpass` to
`~/.local/bin`:

```bash
export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass"
export VAULT_SUDO_ENTRY="Personal/sudo"    # the KeePass entry holding your sudo password
sudo -A apt update                          # sourced from the vault
```

There is **no** default entry -- set `VAULT_SUDO_ENTRY` to your own entry path.
Note: a `~/.bashrc` `sudo()` wrapper only applies in interactive shells; scripts
and non-interactive sessions must call `sudo -A` explicitly (or export
`SUDO_ASKPASS` from a profile that non-interactive shells read).

## Installing / updating the runtime

See the **`agent-vault-setup`** skill -- the plugin ships a runtime (venv +
`~/.local/bin` binstub + a background service), so a `copilot plugin update`
alone does not refresh it.
