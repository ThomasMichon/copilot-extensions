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

Optional: set `VAULT_GROUP` to prefix bare entry names. For example, with `VAULT_GROUP=Personal`, `agent-vault get example` reads `Personal/example`; entries that already include a group path are left unchanged. Missing groups are created automatically on `add`.

Optional: set `AGENT_VAULT_PORT` to override the localhost TCP port. The default is `19999`.

The CLI resolves the effective database, group, and port on **each call** (from the environment, a per-repo `.agent-vault.json`, and the global config — see below) and passes them to the service, so you don't have to restart the daemon to switch vaults.

## Named vaults & per-repo config

For a machine that needs more than one database (e.g. a personal vault and a work vault), give each a **nickname** in the global config and let each repository pick the one it needs.

**1. Register named vaults** (writes the global config at `$AGENT_VAULT_CONFIG` / the platform config dir):

```bash
agent-vault vault add Personal  --kpdb ~/Personal.kdbx  --group Personal
agent-vault vault add Microsoft --kpdb ~/work/MS.kdbx   --group Work
agent-vault vault set-default Personal
agent-vault vault list
```

The global config looks like:

```json
{
  "vaults": {
    "Personal":  { "kpdb": "~/Personal.kdbx", "group": "Personal" },
    "Microsoft": { "kpdb": "~/work/MS.kdbx",  "group": "Work" }
  },
  "default_vault": "Personal"
}
```

**2. Point a repo at a vault** with an `.agent-vault.json` at (or above) the repo root — discovered by walking up from the current directory, git-style:

```json
{ "vault": "Microsoft" }
```

You can also override individual fields inline (`{ "vault": "Microsoft", "group": "SpecialProject" }`) or skip names entirely (`{ "kpdb": "./repo.kdbx", "group": "X" }`).

**3. Resolution precedence** (per field): explicit **env var** › per-repo **`.agent-vault.json`** › **global** named vault › built-in defaults. The global `default_vault` is the backstop when a repo names none.

```bash
agent-vault which           # show the resolved vault, kpdb, group, port + where each came from
```

**One service, many vaults.** A single daemon caches master passwords **per database**, so your personal and work vaults can both be unlocked and in use at the same time — each prompts for its own master password the first time it's touched, and the prompt names the vault. Everything stays backward-compatible: with just `KPDB` set (no registry, no repo file) it behaves as a single default vault.

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
- `agent-vault list [GROUP] [-R] [-f]` (alias `ls`) / `agent-vault show ENTRY [-s]`
- `agent-vault lock` / `agent-vault unlock`
- `agent-vault add ENTRY [-u USER] [--url URL] [-g|--password PW]`
- `agent-vault set-password ENTRY` / `agent-vault set-username ENTRY USERNAME`
- `agent-vault remove ENTRY [-f]` (alias `rm`) / `agent-vault move ENTRY DEST [-f]` (alias `mv`)
- `agent-vault import-key ENTRY path/to/key`
- `agent-vault export-key ENTRY dest_dir key_name`
- `agent-vault git-credential get|store|erase` (git credential helper)
- `agent-vault get ENTRY [--refresh] [--cache-only]` (persistent-cache aware)
- `agent-vault cache-populate [--entry P[:FIELD]] [--manifest F] [--machine M]` (pre-warm the cache)
- `agent-vault cache-status [--json]` / `agent-vault cache-clear`
- `agent-vault cache-verify [--entry P[:FIELD]] [--manifest F] [--machine M] [--json]` (exit 2 if any missing)

`remove` and `move` are **scoped to the configured vault group**: an entry
outside that group is refused unless you pass `-f`/`--force`.

Configure `git-credential` as a helper so allowlisted HTTPS hosts (see
`VAULT_GCM_HOSTS`, default GitHub / Azure DevOps) resolve through Git Credential
Manager via the vault:

```sh
git config --global credential.helper '!agent-vault git-credential'
```

## The service

The vault service caches the master password in memory (default 1 h TTL) and
serves reads over a loopback endpoint (Unix socket + `127.0.0.1`). It is
**passively locked**: a secret is served from the in-memory master password, and
it re-locks when the TTL expires.

**Locked reads fail fast (opt into prompting).** When a credential is requested
and the vault is locked, the service **does not block on an interactive prompt by
default** — it returns promptly with an actionable `needs_unlock` result
("run `agent-vault unlock` … then retry") so a caller (e.g. an agent) can surface
it rather than stalling on a dialog. Unlock-source *providers* (an extension seam
hook) still run first, so inline resolution is never skipped — only the blocking
prompt is gated. To prompt: run `agent-vault unlock` (the explicit unlock), or
pass `--prompt` to `agent-vault get`. The CLI cold-starts the service on first
use; the installer also deploys it as a background service (systemd `--user` unit
on Linux, a windowless scheduled task on Windows) so `sudo -A` and other
non-interactive callers work.

## Persistent cache (opt-in)

The service's in-memory cache dies on restart and cannot serve reads while the
vault is locked. The **persistent cache** is a separate, opt-in tier: a
Fernet-encrypted file on disk that survives restarts and answers reads *without*
unlocking the vault — so an unattended job on a locked box can still fetch a
previously-cached secret.

It is **off by default** (storing secrets on disk is a deliberate tradeoff).
Switch it on with either env var:

- `AGENT_VAULT_CACHE=1` — enable at the default location
  (`<config-dir>/agent-vault/cache/`).
- `AGENT_VAULT_CACHE_DIR=/path` — enable and store the cache there.

Encryption needs the optional `cryptography` dependency
(`pip install agent-vault[cache]`); without it the cache degrades to a safe
no-op. The Fernet key sits beside the cache file at `0600` — this is hygiene, not
a security boundary (physical-access control and full-disk encryption are the
real barriers).

Once enabled:

- `agent-vault get ENTRY` reads the cache first (tier 0), then the service,
  writing any live fetch back through to the cache.
- `--cache-only` reads *only* the cache and never contacts the service (returns
  non-zero on a miss); `--refresh` bypasses the cache and re-fetches live.
- `agent-vault cache-populate …` warms both the service and the persistent cache.
- `agent-vault cache-status` / `cache-clear` inspect and wipe it; `cache-verify`
  checks a set of entries are present (no unlock) and exits `2` if any are
  missing — a launch-time gate for "is this box primed while still locked?".

## SUDO_ASKPASS (Linux / WSL)

The installer ships `~/.local/bin/vault-askpass`, a sudo askpass provider that
sources the password from the vault:

```bash
export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass"
export VAULT_SUDO_ENTRY="Personal/sudo"    # your KeePass entry -- no default
sudo -A apt update
```

## Extensions

The core daemon and CLI ship a fixed feature set. A downstream harness that needs
extra behavior can register **extensions** against a small seam instead of forking
the core. Six generic hook categories are exposed (all in `agent_vault.extensions`):

| Hook | Signature | Consulted |
|------|-----------|-----------|
| **Unlock-source provider** | `provider(ctx) -> str \| None` | in the daemon *before* the interactive prompt — return a candidate master password (verified before use), or `None` to fall through |
| **Protocol action** | `handler(service, request, ctx) -> dict` | in the daemon *before* the `Unknown action` fallback — add a request action keyed by name |
| **Client transport** | `transport(request, timeout, ctx) -> dict \| None` | in the CLI *after* the built-in unix-socket + TCP transports both fail — reach a daemon they can't (e.g. over a tunnel). Register with `before_builtin=True` to be consulted *ahead* of the built-ins (for a transport that must take precedence over the local daemon; return `None` when it does not apply) |
| **Config source** | `source(cwd) -> dict` | in the resolver, at a tier below per-repo config and above the named-vault base — contribute `kpdb`/`group`/`port`/`vault` |
| **Cache source** | `source(machine) -> iterable` | in `cache-populate` — yield entries to pre-warm, each a `"path"` string or an `(entry, field)` pair (e.g. entries derived from installed services) |
| **CLI command** | `builder(subparsers) -> None` | in `cli.main()` *after* the built-in verbs — call `subparsers.add_parser(...)` then `set_defaults(func=handler)` to add a subcommand (e.g. facility-only verbs) instead of forking `cli.py` |

An extension is a module exposing `register(registry)` that calls
`registry.register_unlock_provider(...)`, `register_action(...)`,
`register_transport(...)`, `register_config_source(...)`,
`register_cache_source(...)`, or `register_cli_command(...)`. Extensions are
discovered via the `agent_vault.extensions` **entry-point group** or the
`AGENT_VAULT_EXTENSIONS` env var (comma-separated `module` / `module:callable`
paths). Loading is idempotent and **fail-open**: a broken extension is logged and
skipped, never crashing the daemon or CLI.

## Not in scope (v1)

A pluggable multi-backend driver interface, a native OS keychain / Secret
Service bridge, a Git credential-helper bridge, offline cache priming, and a
PowerShell module are intentionally out of scope for the core's first release.
Several of these (keychain bridge, git-credential helper, cache priming, an
alternate transport) can now be layered as **extensions** (see above) without
modifying the core.

## License

MIT.
