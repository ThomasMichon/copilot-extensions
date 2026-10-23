# agent-vault

`agent-vault` is a standalone, machine-local secret store for Copilot CLI agents and local automation. It uses a user-selected KeePassXC `.kdbx` database, a small local daemon that caches the KeePass master password for a bounded time, and an `agent-vault` CLI that fetches API keys, SSH keys, credentials, and small sealed values on demand.

It does **not** require a repo to be registered with `agent-worktrees`, and it
does not require a broker, container, tunnel, or remote service. Agent-facing
skills receive an exact payload-local command from the session catalog.
Out-of-session automation, Git credential-helper registration, supervision,
and `vault-askpass` remain explicit compatibility/management boundaries until
they receive attributable installation context.

The released payload carries the shared installation driver's standalone
foundation and canonical installer helpers through the existing dependency
materializer. Cold legacy payload commands and generated primary binstubs submit
their fixed owning-source provision action to its durable queue, without holding
an outer provisioning lock or installing in the caller. They wait for the
recorded outcome, then dispatch the selected runtime. Healthy runtime invocation
does not start the foundation or submit work.
The default execution root is `~/.copilot-extensions/update-driver`; launch
diagnostics do not imply request acceptance. Missing/incompatible packaged
support and cold namespaced or unsupported-platform calls require explicit
repair/management rather than a foreground fallback. Managed-publication,
pinned-read and no-self-provision refusals remain in force.
The legacy session bootstrap uses that same owner for its first stamp and
version-drift provisioning, without a separate installer PID/reaping guard.
It returns only `{}` and a diagnostic launch acknowledgement within its hook
budget, not installation readiness or durable acceptance. The global binstub
appears after queued stamping; the payload-local catalog command is available
while that work is pending. Healthy, cell-scoped and killswitch-paused hooks
remain no-ops; no-self-provision and pinned reads explicitly defer publication.
Missing/older driver support refuses without an inline installer.
The POSIX hook retains its existing metadata-Python prerequisite and no-Python
no-op; the payload command still supports true Python-free genesis. Cold
bootstrap requires the qualified Windows or Linux native driver, not stock
legacy Bash/macOS. The independent session-guidance writer is unchanged.
Explicit installer entry points remain separate, unadopted callers.
Service-enabled provisioning remains the command and drift default; this does
not enroll maintenance or replace service supervision.
The native qualification portfolio is documented in `libs/update-driver/README.md`.

## Setup in one pass

1. **Enable the plugin payload** through Copilot's plugin mechanism (or work from a local checkout of this repo).
2. **Install or stamp the runtime.** The session-start bootstrap queues a publication-only `stamp` when the runtime is missing; the stamped binstub queues full provisioning on first use. Neither action runs its installer in the hook/calling command. To use the separate explicit management entry point from a checkout:

   ```powershell
   pwsh -File plugins\agent-vault\scripts\install.ps1 -Action install
   ```

   ```bash
   bash plugins/agent-vault/scripts/install.sh install
   ```

   Use `stamp` instead of `install` when you only need the binstub now and want the first real `agent-vault ...` call to build the runtime.
3. **Install KeePassXC** with `keepassxc-cli` on PATH (or at `C:\Program Files\KeePassXC\keepassxc-cli.exe` on Windows).
4. **Point the CLI at a database** with `KPDB`, a named vault, or a repo-local `.agent-vault.json`.

## Source-qualified runtimes

Both native installers accept canonically prepared `runtime-source.json`
payloads, pinning the complete identity across the install lock, construction,
completion and activation. These non-editable generations include the package
version, full source commit and assembly hash; package-only installs remain
separate. Deferred stamps retain the complete verified payload and vendor
closure, allowing provisioning after the publisher checkout is unavailable.

Identical inputs preserve snapshots and completion evidence; failed candidates
do not replace the healthy selected generation. Resolvers require an exact
current or last-known-good selector when source-qualified slots exist rather
than choosing an unordered source hash or downgrading to a plain package slot.
`agent-vault --version` exposes the selected generation while package metadata
is unchanged. Compatibility helpers continue through their existing command
routes; this source-adoption path does not change service-supervision policy.
Retained source slots pass the same isolated interpreter CLI `--help` probe as
fresh slots before activation; importing only the lazy initializer is insufficient.
Reselection refreshes source and venv manifest fields
without rewriting completion evidence.
Required resolver/binstub publication succeeds after isolated health gating and
before activation on both native installers. Retained source retries reconcile
these helpers before selecting the target or refreshing its manifest; publication
failures are not allowed to fall through into activation.
The POSIX stamp path refuses directory-preparation failures before changing the
payload pointer, including when invoked by a conditional caller.
Service-enabled POSIX install/update callers refuse failed directory preparation,
unit construction or publication before reconciling systemd or reporting success.
Unit contents are staged before replacement. Client-only and unavailable-systemd
skips, and the documented warning-only unsupported-manager behavior, remain
best-effort and never require a host service in the regression fixtures.
Custom askpass and service-unit publication also refuses directory and
symlink-to-directory destinations and invalid staging types; colliding
directories are preserved rather than treated as move containers.

Installed first-use wrappers and payload dispatchers enforce the shared
[publication boundary](../../libs/installation-context/README.md) before cold
trace, status or provisioning-lock writes. Healthy managed runtimes still run.
Payload dispatchers retain the independently selected cell; absent or older
admission support returns explicit readiness instead of authorizing replacement.
Fresh Windows activation checks the actual historical `.venv` path before
attempting legacy daemon migration, not the newly built version slot.

The cross-plugin native source contracts are in
`plugins/agent-logger/tests/test_source_runtime_install.py`. Required smoke uses
small assembled payloads and the real native source/stamp boundaries. Full
release installation and checkout-removal proof is explicitly gated by
`COPILOT_EXTENSIONS_SOURCE_INSTALL_FULL=1` and `--allow-explicit-tiers`; select
the two Vault contracts with
`-k "(stamp_provision_reuse or failed_source_candidate) and agent-vault"` in
their own bounded invocation. They share one real healthy installation rather
than rebuilding it for each contract; existing item/group limits are unchanged.
The offline package fixture identifies retained projects
by their declared project names, not their compact retention directory names,
and still checks complete project metadata and Python source contents.

## Runtime removal

The installer's `uninstall` action serializes with install/update and keeps vault
state by default. Explicit `-Purge` / `--purge` removes runtime/config contents,
but retains the transaction-lock files and `.install-stage` coordination;
the install directory may remain. Normal self-staging reap removes dead-owner
stages on later invocations.
An uninstall preview neither takes the transaction lock nor removes contents;
normal transient payload self-staging still applies.

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
| SSH keys | `import-key`, `export-key`, `ssh-key prepare --entry SSH/node-a`, `ssh-key install --entry SSH/node-a --directory <absolute-path>` |
| Service | `ping`, `start`, `stop`, `lock`, `unlock` (`--terminal`/`--here`) |
| Config | `which`, `vault list`, `vault add`, `vault set-default`, `vault remove` |
| Git HTTPS credentials | `git-credential get|store|erase` (delegates allowlisted hosts to local Git Credential Manager) |
| Persistent cache | `cache-populate`, `cache-status`, `cache-clear`, `cache-verify` |
| Envelope KEK | `seal`, `unseal`, `kek-list` |
| Shared owner key | `identity-key context --json`, `identity-key setup --entry Identity/PR-attribution` |

## SSH key preparation and protected installation

For standalone SSH identity preparation, see
[Vault-owned SSH key preparation](docs/ssh-key-preparation.md). It validates and
reuses a whole pair, permits separately confirmed first creation only, and
returns public metadata without writing plaintext key files. This is not SSH
enrollment or automatic mesh setup; attachment import/export remains unchanged.
`ssh-key install` separately materializes an existing pair in an explicitly
selected owner-private local directory. It never initializes a missing record,
overwrites a different key or repairs existing permissions. Both commands support
`--require-running` to prohibit service activation and first-touch provisioning.

## Shared owner-key setup

Use the owning payload's session-catalog command, not a guessed PATH command:

```text
<agent-vault catalog argv[0]> identity-key context --json
<agent-vault catalog argv[0]> identity-key setup --entry Identity/PR-attribution
```

`context` strictly resolves an existing configured database and returns
`{"version":1,"env":{...}}`. It does not contact/start the service,
unlock, or read/write the protected cache. Its non-secret `env` mapping pins
absolute `KPDB`, cache directory, runtime home, config and rendezvous directory,
the selected vault/group (including empty selections), installation ID, port,
socket/pipe and explicit transport overrides. `AGENT_VAULT_PINNED_CONTEXT`
contains validated JSON with exactly `version:1`, an absolute `kpdb`, normalized
`group` and `vault_name` strings (including empty strings), and an integer
`port`. That JSON is authoritative even if legacy selection environment
variables conflict, and prevents repository, extension or global fallback.
Malformed pins fail closed rather than falling back. Pinned `get ... --cache-only`
also refuses legacy cache-entry fallback/migration to an unselected group.
Apply the complete mapping to later fresh-process readers; retain the
attributable owning command separately. `COPILOT_PLUGIN_ROOT` is not captured.

Identity setup does not share the ordinary entry/field-only cache namespace.
An unpinned context selects
`<effective cache root>/identity/<sha256>` even when `AGENT_VAULT_CACHE_DIR`
explicitly selects that root. The stable digest covers the canonical absolute
database path, canonical runtime home and installation ID; distinct databases
or installation cells cannot collide on the same entry name. A valid persisted
pin reuses its exact absolute cache directory without nesting another scope.
Pinned context requires `AGENT_VAULT_CACHE=1` and an absolute
`AGENT_VAULT_CACHE_DIR`; it never falls back to the global cache.
Actual payload dispatchers preserve the identity cache pin only after shared
vault-owned validation confirms the database digest, selected runtime home
and installation ID, and exact `<owning runtime>/cache/identity/<digest>`
location. Both PowerShell and POSIX gates use the cell already resolved by the
existing installation-context primitive; they do not add another resolver.
Unpinned arbitrary cache overrides retain the ordinary gate behavior, while
invalid/foreign pins fail closed rather than silently reverting to a different
cache. Direct module callers may explicitly select another cache root; that
selection is not authorization to bypass a payload's cell-owned namespace.

Setup populates that selected directory from the existing authoritative vault
value. Migration into this flow is ordinary population, not copying a legacy
cache entry, generating another key, moving/deleting old caches, or changing
global cache semantics. If the vault is locked/unavailable, setup still fails
until access is restored. When deliberately selecting another database, clear
or replace inherited pin/selection variables in that subprocess before
resolving a fresh context; an existing pin is authoritative and is never
silently reinterpreted as another database.

The environment allowlist is `KPDB`, `VAULT_GROUP`, `AGENT_VAULT`,
`AGENT_VAULT_PINNED_CONTEXT`, `AGENT_VAULT_CACHE`, `AGENT_VAULT_CACHE_DIR`,
`AGENT_VAULT_HOME`, `AGENT_VAULT_INSTALLATION_ID`, `AGENT_VAULT_CONFIG`,
`AGENT_VAULT_RUN_DIR`, `AGENT_VAULT_ENDPOINT`, `AGENT_VAULT_HOST`,
`AGENT_VAULT_PORT`, `AGENT_VAULT_PIPE`, `AGENT_VAULT_SOCKET`,
`AGENT_VAULT_PID`, `AGENT_VAULT_LOG`, `AGENT_VAULT_SYSTEMD_UNIT`,
`AGENT_VAULT_TASK_NAME` and `AGENT_VAULT_WINDOWS_RUN_DIR`.
Every value is a string. Optional absent
transport overrides use empty strings so another caller cannot inject them
through an inherited environment.
PID/log paths are resolved absolutely, as is an explicit Windows-side
rendezvous directory. Absent systemd-unit, task-name and Windows-side directory
overrides remain empty. Clear all 20 known context keys from a child environment
before applying the complete saved mapping, so a refresh/cold start cannot
inherit another installation's lifecycle artifacts.

Setup requires an enabled, usable protected cache (`AGENT_VAULT_CACHE=1` or
an explicit cache directory), may start the configured service, and populates
only the selected entry using authoritative live reads and the existing cache
replacement protocol. It never collects extension bulk-cache entries. Ordinary
setup must reuse a canonical base64-encoded 32-byte key; locked, missing,
unreadable or malformed values fail without generating or replacing a key.
Unattended requests have a 90-second bound and never prompt; `--prompt`
explicitly permits unlocking and uses a 120-second request bound.
Delegated/contained callers must pass `--require-running`. It performs a bounded
check of the selected existing service and returns `service_unavailable` when
stopped, without activating a daemon. Payload gates also refuse first-use
provisioning for that option. Provision/start the owning vault independently
outside the contained consumer adapter before retrying. Standalone setup
without this flag retains its explicit activation behavior.

First-owner initialization requires **both** `--initialize-owner` and
`--confirm-first-owner`, only after the user separately confirms this is a new
owner with no existing common key. Initialize once on the designated
authoritative database copy, confirm synchronization, then use ordinary setup
on every other machine. Independent unsynchronized copies cannot establish
global absence, and no cross-machine atomicity is claimed. The service
serializes creation with credential operations, proves absence through a
successful recursive flattened root listing, and uses create-only KeePass
`add` with random key material on stdin. It refuses ambiguous/duplicate paths
and malformed keys; ambiguous add completion is authoritatively reread,
never retried or overwritten.

KeePass CLI snapshot writes do not provide an atomic concurrent first-owner
guarantee: simultaneous create-only adds can both succeed for one absent
entry. The backend therefore uses the existing `single-instance-lease`
OS-lock primitive during owner creation. Its stable, non-secret sidecar lock
is beside the canonical database pathname, independent of daemon, cache root,
runtime home and installation ID. Absence is rechecked after acquiring it,
and parent-group creation plus the single add remain inside that lease.
Contenders reuse the winner instead of issuing another add. Acquisition is
bounded to five seconds within the existing operation deadline; contention
returns `service_busy`, and unavailable coordination returns
`vault_read_failed`, with no unlocked fallback.

Keep the sidecar file: deleting it while a participant holds it can split the
lock namespace. The OS releases ownership automatically when its process
dies; stale PID text is not ownership. This coordinates cooperating
agent-vault identity initializers on the same canonical database pathname
where the filesystem supports the existing OS lease. It does not fence
unrelated KeePass GUI/CLI writers, rotate keys, or establish atomicity between
independent synchronized copies. First-owner initialization still happens once
on the designated authoritative copy.

Setup always prints secret-free JSON (also accepts `--json`):
`{"version":1,"status":"reused|created","cached":true}`.
Failures exit 1 with `{"version":1,"error":"reason-code","message":"sanitized actionable text"}`;
key bytes and backend stderr are never printed.
Cache failure does not rotate/delete the authoritative key: repair and retry.

Pinned cache-only reads are strictly read-only: a miss creates no directory,
cache, lock or key file, and legacy plaintext wrapping keys are not rewritten.
Corrupt protected data fails explicitly rather than being reported as a miss.
Setup distinguishes `cache_unavailable` (disabled/unusable protection),
`cache_read_failed` (existing cache cannot be safely read) and
`cache_update_failed` (population/verification failed) from
`database_unconfigured`, `database_unavailable`, `context_invalid`,
`needs_unlock`, `entry_missing` and `malformed_key`.
The remaining setup reason codes are `confirmation_required`, `invalid_entry`,
`ambiguous_entry`, `vault_read_failed`, `group_create_failed`,
`operation_expired`, `creation_unconfirmed`, `service_busy`,
`service_unavailable` and `invalid_response`. Consumers should allowlist these
codes, not copy backend diagnostics. Raw pinned cache-only `get` failures print
only sanitized `cache_missing`, `cache_unavailable` or `cache_read_failed`
diagnostics; successful `get` still returns the requested secret on stdout.

### Disposable real KeePass proof

With the existing prepared test runtime and source/shared-library `PYTHONPATH`,
opt into the bounded native lane:

```powershell
$env:AGENT_VAULT_IDENTITY_REAL_KEEPASS = "1"
& "<prepared-runtime-python>" -m pytest plugins\agent-vault\tests\test_identity_key_keepass_live.py -q
```

The lane creates only fresh disposable KDBX files, uses the available KeePassXC
binary with passwords/keys exclusively on stdin, and starts a foreground
daemon with private home, configuration, PID/log, socket/pipe and rendezvous
paths plus a dynamic loopback port. It verifies reused-key CLI setup and a
fresh cache-only reader while that private daemon is locked, plus separately
confirmed create-only initialization and retry reuse. It never connects to,
locks or restarts an installed daemon. Fixtures retire only their PID-owned
child on exit; the Windows fixture launches the selected interpreter's base
executable with its existing dependency paths to avoid venv redirector PID
ambiguity.
The concurrent lane starts two independent service processes in different
installation cells, synchronizes both authoritative absence reads against the
same physical KDBX, and requires exactly one actual KeePass add. Both returned
and in-memory-cached key digests must match the final authoritative value and
fresh protected-cache reader; no fixture key or master password is printed.

For caller-owned combined worktrees integration, the test module exports
`disposable_vault(root, seed_owner=True)`. Inside that context, use its `env`,
`argv`, `entry` and CLI-emitted context environment; it removes inherited
identity/vault/worktrees selection variables only from child environments.
Keep comparisons in memory: fixture secrets must not become arguments,
diagnostics or plaintext artifacts.

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

Password replacement is journaled before the KeePass mutation. Pending
replacements are never served by cache-first reads and cannot be overwritten by
ordinary cache population. The daemon reconciles an ambiguous backend result
against KeePass, while CLI callers serialize and generation-order their cache
finalization. A crash or transport loss therefore leaves a recoverable pending
record instead of resurrecting the retired password.

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
