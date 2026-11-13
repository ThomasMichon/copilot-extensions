# agent-vault

[Configuration reference](docs/configuration.md)

Enabling this plugin exposes its guidance and commands, not its runtime.
Automatic bootstrap, install/update participation and ordinary command
provisioning require `"enabled": true` in the owning configuration. Without
opt-in, readiness is `configuration-empty` and no runtime, service or KeePassXC
installation is needed. Invalid configuration is a failure, not inactivity.
Repository and qualified bound-knowledge `.agent-vault.json` files use the
existing configuration-layer owner; standalone users can opt in through native
machine configuration without Worktrees. See the [activation contract](docs/configuration.md#runtime-activation).

Native `uninstall` removes explicitly named runtime slots, snapshots, selectors,
generated resolvers and service launchers. Configuration, authentication,
`run/`, `logs/`, `core/`, `cache/` and unknown files are retained by default.
Explicit purge still retains transaction coordination, and dry runs remain
non-mutating. Linked roots, foreign manifests and unclassified consumers or
descendants refuse removal. Canonical batch-removal custody remains held
through deletion; released lease registries retain their inodes without
preventing idle removal. See the
[install contract](../../docs/install-contract.md).
The cross-adapter native fixtures are in
`tests/test_native_uninstall_adapters.py`; cleanup requires the packaged shared
finite-removal helper and selective native engine API.

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
no-op. Activation reads use the payload's bundled configuration SDK and the
selected completed runtime interpreter when available, retaining ambient
Python discovery for cold payloads. Cold activation qualifies an existing
Python 3.10+ reader rather than accepting the first unsupported PATH winner;
Windows reuses read-only canonical native discovery and headless capture.
This neither raises Vault's floor to the installer's 3.11 tooling floor nor
installs Python, changes PATH or invokes an installer to select the reader.
The canonical installation-context
bootstrap keeps its independent platform prerequisites (currently Python
on PATH on Windows). No Vault runtime or backend is installed for this read. Cold
bootstrap requires the qualified Windows or Linux native driver, not stock
legacy Bash/macOS. The independent session-guidance writer is unchanged.
Explicit installer entry points remain separate, unadopted callers.
Service-enabled provisioning remains the command and drift default; this does
not enroll maintenance or replace service supervision.
The native qualification portfolio is documented in `libs/update-driver/README.md`.

## Setup in one pass

1. **Enable the plugin payload** through Copilot's plugin mechanism (or work from a local checkout of this repo).
2. **Opt in, then install or stamp the runtime.** Add `{"enabled": true}` to
   `.agent-vault.json` in the repository or its qualified bound knowledge
   repository, or merge `"enabled": true` into the native machine configuration.
   The configured session-start bootstrap queues a publication-only `stamp` when
   the runtime is missing; the stamped binstub queues full provisioning on first
   use. Neither action runs its installer in the hook/calling command. To use
   the separate explicit management entry point from a checkout:

   ```powershell
   pwsh -File plugins\agent-vault\scripts\install.ps1 -Action install
   ```

   ```bash
   bash plugins/agent-vault/scripts/install.sh install
   ```

   Use `stamp` instead of `install` when you only need the binstub now and want the first real `agent-vault ...` call to build the runtime.
3. **For selected KeePass-backed use, install KeePassXC** with `keepassxc-cli` on PATH (or at `C:\Program Files\KeePassXC\keepassxc-cli.exe` on Windows). This is not a prerequisite for loading the plugin or inactive readiness.
4. **Point the CLI at a database** with `KPDB`, a named vault, or a repo-local `.agent-vault.json`.

## Dependency profiles

Native setup can explicitly select `base` or `ssh`. SSH key generation and pair
validation need the `ssh` profile; ordinary Vault use does not. It supplies
`pycryptodomex` in the isolated `Cryptodome` namespace, with native Windows ARM64
support, and also supports the existing Fernet cache and AES-GCM envelope formats.
Existing records, wrapped keys and DPAPI caches are not converted or rotated.
For example:

```powershell
pwsh -File plugins\agent-vault\scripts\install.ps1 -Action install -Profile ssh
```

```bash
bash plugins/agent-vault/scripts/install.sh install --profile ssh
```

The same selection works with `stamp`, `provision` and `update`. Stamping records
the desired profile without acquiring dependencies. With no active runtime,
first use constructs it; an existing runtime remains active after stamping.
Use `provision` or `update` to apply a changed profile before using its capabilities.
The installation-owned `runtime-profile.json` preserves selection across ordinary
updates. With no explicit or recorded selection, existing base setup is unchanged.
Use `base` explicitly to select a new base runtime without the optional crypto
dependency; existing encrypted-cache/KEK consumers may still need that dependency.
Profile construction requires an existing Python 3.11+ bootstrap interpreter.

Profiles use separate immutable version slots and the canonical retained-input
constructor. A failed SSH build never selects a base substitute or modifies a
completed runtime. Package metadata remains unchanged; `deploy-manifest.json`
records the actual `runtime_version` and `runtime_profile`. Prepared deployments
retain their original `runtime_source` receipt separately from the profile.
Selection does not opt into runtime activation, unlock a database, configure
credentials or change service policy. See [SSH key custody](docs/ssh-key-preparation.md)
and the [configuration reference](docs/configuration.md#dependency-selection).

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
The existing service `ping` response adds the running process's frozen package
`version` alongside its PID. This identifies the responding build, not a newly
selected slot; older responses without the field do not establish build identity.
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

On Windows, an owned service's venv redirector can have a physical-interpreter
child outside the runtime directory. Uninstall retires that child before its
waiting launcher only after checking the venv's configured base interpreter,
the exact native process identities and ancestry, the current user, and matching
service arguments. Unknown or additional descendants still refuse removal;
an executable name or parent PID alone does not establish ownership.
Native process handles retain the birth identity through termination. File
observations use the existing external uninstall Python and headless capture,
not the retired compiled daemon adapter. The configured base path is read as
UTF-8 under both stock Windows PowerShell and PowerShell Core.
The canonical same-process daemon can instead run its physical interpreter
directly. Its default persistent-service command is admitted only after exact
matching against the source-owned service bootstrap and optional daemon lease wrapper,
ordinary installation-slot paths, the configured physical base, cache scope,
current-user ownership and the retained native birth/file identities. A PID
receipt alone still cannot authorize termination; unknown commands and children
remain refusal conditions.
Native removal distinguishes its own source-bound driver ancestry from runtime
consumers using the live authenticated uninstall request, not parent PIDs alone.
Broker removal and finite code removal retain that proof; an unverified caller
or an additional consumer still blocks removal.

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

For API requests, let a supported authenticated CLI/MCP tool hold the credential.
For generic gaps, use [URL-bound authenticated curl](docs/authenticated-http.md)
with an owner-approved opt-in binding, never a manual token/header construction:

```bash
agent-vault curl -- -sS https://api.example.test/v1/items
```

Supported built-in command groups:

| Area | Commands |
|------|----------|
| Entries | `get`, `batch-get`, `has`, `search`, `list`/`ls`, `show`, `add`, `set-password`, `set-username`, `remove`/`rm`, `move`/`mv` |
| Authenticated HTTP | `curl -- <supported curl arguments>` (explicit URL binding, no caller-visible credential) |
| SSH keys | `import-key`, `export-key`, `ssh-key prepare --entry SSH/node-a`, `ssh-key install --entry SSH/node-a --directory <absolute-path>` |
| Service | `ping`, `start`, `stop`, `lock`, `unlock` (`--terminal`/`--here`) |
| Config | `which`, `vault list`, `vault add`, `vault set-default`, `vault remove` |
| Git HTTPS credentials | `git-credential get|store|erase` (delegates allowlisted hosts to local Git Credential Manager) |
| Persistent cache | `cache-populate`, `cache-status`, `cache-clear`, `cache-verify` |
| Envelope KEK | `seal`, `unseal`, `kek-list` |
| Shared owner key | `identity-key context --json`, `identity-key setup --entry Identity/PR-attribution` |

## Batch reads

`agent-vault batch-get` reads an array of entry/field objects from JSON stdin
in one CLI process:

```bash
printf '%s\n' '[{"entry":"Example/service","field":"username"},{"entry":"Example/service"}]' |
  agent-vault batch-get
```

`entry` must be a nonempty string; omitted `field` means `password`. Unknown
properties, duplicate JSON object keys, non-finite numbers and malformed
requests fail before any credential read. The output is `{"results":[...]}`,
with one record per request in the original order, including duplicates.
Successful records have exactly `entry`, `field`, `value`, `ok: true` and
`source`. Values remain strings, including empty or multiline values; JSON
escaping does not change them. `source` is `cache` or `service`. Failed records
have `ok: false`, `value: null`, an error string and the attempted source
(`cache`, `service` or `none`).
Protected-cache failures are sanitized per item without cache replacement or
key regeneration. The selected Windows logon's DPAPI decryption failure uses
the same narrowly scoped service-only fallback as ordinary `get`, with a fixed
stderr warning and `source: service`; other protection failures do not fall
back. Later requests are still represented.

Exit status is `0` when every request succeeds (also for an empty array), `2`
for a mixture of successes and failures, and `1` when every request fails.
Invalid input also returns `1`, with stderr diagnostics and no results output.
Callers must decide whether to accept any values from a partial result.
Credential values are written only to the requested results stream, not to
diagnostic output.

Reads reuse ordinary `get` context selection, cache and service transports.
`--refresh` bypasses cached values and retains authoritative cache
reconciliation; a reconciliation failure is not a successful result. Batch
reads never enable interactive prompts. Use the existing explicit unlock
command if required. One credential CLI process handles the batch without
per-entry credential CLI children or a new daemon protocol. Existing context
qualification and transport discovery may still invoke their ordinary peer
helpers; this is not a daemon-native batch or a claim of one total OS process.
Callers may impose one deadline on the whole invocation and its descendants.

The selected runtime advertises `batch-get-v1` through the existing
`agent-vault invocation-contract --json` contract. Check that runtime's
capability before migrating a caller; a newer payload or package version
does not prove support. A cold unprepared runtime reports no capabilities,
and older receivers may not support the metadata query itself. Missing
support blocks only the new operation: existing `get` syntax remains usable.
Provider rollout precedes consumer migration; rollback must restore a
supported baseline or report the unsupported batch requirement without
guessing, silently issuing separate reads or retrying effectful calls.

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
Selected setup consumers can require `ssh-key-install-pin-v1` from the owning
invocation contract and pass `--expected-fingerprint` to refuse a changed public
identity before local key publication; existing unpinned callers are unchanged.

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

On Windows, KeePassXC and credential-helper console children run without creating
terminal windows, including when their parent is a windowless service. Explicit
password dialogs remain visible; console suppression does not enable prompting,
detach children from their caller or change credential stdin/output handling.

- The daemon keeps KeePass master passwords in memory **per database**. `VAULT_PASSWORD_TTL` controls the password TTL (default `3600` seconds). `agent-vault lock` drops the cached password immediately.
- A Windows logon that cannot decrypt the selected DPAPI cache may read through
  the same selected service without modifying that cache. Successful ordinary
  reads emit a fixed `cache_decryption_unavailable` warning; service refusal is
  authoritative and never triggers a stale-cache fallback or implicit prompt.
  `--cache-only` still refuses without service access. Explicit authoritative
  refresh still requires successful cache reconciliation. Schema, selection,
  permissions and recovery-policy errors are not decryption fallback triggers.
- Password TTL is an absolute lifetime from unlock, not inactivity; requests
  and `--persistent` do not extend it. Idle housekeeping clears expired
  passwords and their in-memory credential caches. Opt-in protected credential
  records have a separate lifetime, as described below. `ping` labels daemon lifetime
  separately from the selected database's remaining password lifetime.
  Its additive `password_lifetime` object uses contract
  `agent-vault.password-lifetime`, version `1`, with `state` equal to `bounded`,
  `locked`, `unbounded` or `unknown`, and `remaining_seconds` (a nonnegative
  integer only for `bounded`, otherwise null). Missing database/timestamp
  context remains unknown, not an invented deadline. The legacy `ttl` field
  retains daemon inactivity semantics; additive `daemon_persistent` identifies
  disabled daemon inactivity expiry. Older CLIs ignore the added fields;
  newer CLIs show unknown password lifetime for older or unsupported services.
  No new request syntax, credential access, unlock or synchronized upgrade is
  required. Existing inherited handoff deadlines still cap password lifetime.
  Busy credential operations report unknown lifetime without blocking health
  probes; expiry waits for the complete operation, including mutation setup.
  Password mutations and identity/SSH-key requests retain their handler-owned
  queue deadlines rather than waiting on an unbounded outer dispatch lock.
  After acquiring that lock, they recheck password expiry before unlocking
  or performing backend work; a queued request cannot reuse an expired master.
  Independent GCM/KEK actions do not acquire a KeePass dispatch lock; other
  credential and extension handlers retain custody. Legacy owner-gated exports
  refuse busy custody and atomically capture complete, unexpired password/clock
  pairs; unknown clocks are not exported.
  Housekeeping uses one owned expiry worker, independent of the credential
  request executor, so expiry checks and their cancellation do not queue behind
  credential requests before lifecycle ticks.
- Credential reads fail fast by default when locked: they return an actionable error instead of hanging on a hidden prompt. Use `agent-vault unlock`, `agent-vault unlock --terminal`, or `agent-vault get --prompt ...` when you want an interactive prompt.
- Unlock-source extensions run before prompts, so a configured provider can satisfy locked reads without UI.
- The CLI cold-starts the daemon when needed. Windows scheduling is opt-in via the native [`windows_scheduled_task` policy](docs/configuration.md#windows-scheduling-is-separate-from-activation); clean installs leave tasks absent and existing registrations are preserved by default. Linux/WSL installs retain the systemd user unit named `agent-vault.service` when systemd is available.
- Windows updates start the incoming generation independently; the old build preferably settles work and retires itself. A bounded, ownership-checked central retirement handles stuck predecessors, but live credential children retain writer exclusion. Compatible private auth handoff preserves unexpired unlocked state without resetting its lifetime. See [runtime replacement](docs/runtime-replacement.md) for the contract, readiness distinction and remaining POSIX/systemd migration.
- There is **no** `agent-vault doctor` subcommand today. Use `agent-vault ping`, `agent-vault which`, `agent-vault cache-status`, and the installer `status` action for troubleshooting.

## Persistent cache and sealed values

The persistent cache is off by default. Enable it only when a locked, unattended process must reuse previously fetched values:

```bash
export AGENT_VAULT_CACHE=1
agent-vault cache-populate --entry API/OpenAI
agent-vault cache-verify --entry API/OpenAI
```

An enabled service reads and writes this same protected store in its own OS
protection context. Fresh backend reads attribute records to the canonical
database, runtime home, installation and group; entry, field and generation
remain bound to that record. A matching record can satisfy the existing `get`
request after the master password expires, without another unlock. Unattributed
legacy records and records for another context are never adopted by a read.
Authoritative refresh bypasses cached values; explicit service locking revokes
its attributed records, and mutations invalidate affected entries before backend
work. Pending password-replacement journals remain pending, including when
revocation removes a previous value's service attribution.

Value/generation snapshots are read together. A client's exact writeback of a
service-attributed result retains its original age and attribution rather than
renewing freshness. Other/older writers may remove attribution; the service then
needs a fresh backend read, not a guessed binding. Existing callers, cache
formats, DPAPI/key protection and direct `--cache-only` behavior are unchanged;
old services simply lack locked-service cache access. A backend read whose
cache write fails reports additive `cache_warning: "service_cache_write_failed"`,
not successful cache population. Unlock-triggered warming is separate from
these read-through semantics.

Fernet caches and `seal`/`unseal` use the optional `pycryptodomex` dependency
(`cache` / `kek` extras), also supplied by the native `ssh` profile above.
Select that profile through the installer rather than modifying a completed
runtime. Without it, POSIX caches are inert and KEK commands return a clear
error. New Windows caches can use native per-user DPAPI without a third-party
package. Existing Fernet caches require the backend; unreadable originals are
never overwritten. Existing DPAPI caches keep their format after installation.

`cache-populate` bounds old-cache adoption through a contained helper: ten
seconds for reading/decoding, with two seconds reserved separately for native
process cleanup. Store/replacement lock acquisition is bounded to two seconds.
A compatible cache remains in place. Otherwise, its exact encrypted file and
wrapped key are copied into an owner-private `preserved-cache` compartment.
If the original is busy, inaccessible or the reader times out, it is retained
in place instead. A separately protected, schema-1 cache in that compartment
then serves the selected Vault/database and installation. This never changes
the authoritative Vault, SSH keys, signing keys or envelope KEKs.

The helper uses isolated Python imports and the selected runtime's package
root, never packages or startup hooks supplied by the caller's directory or
`PYTHONPATH`. Replacement coordination stays at the original cache root across
selection changes. If an old transaction or inaccessible lock prevents that
coordination, or the original reader times out, cannot open the data, or lacks
its decoder backend, the fresh empty cache may exist, but credential
reads and writes remain pending. A later explicit population/setup retries the bounded
assessment under the same replacement lock and carries readable pending
journals into the current cache before permitting use. Unreadable originals
remain preserved. An unresolved journal is never a recovered credential.

Recovered compartments include the selected endpoint and host in their authority
binding. Once recovery is active, an authority without a published compartment
cannot fall back to the original cache. Explicit population/setup prepares a
separate empty compartment without importing another authority's original values
or journals. Reads alone never prepare it.

Recovery uses the same live `get` request as ordinary population. Only missing
current records are inserted: newer writes, existing values and pending
password replacements win over a recovery response. Repeating population or
restarting the CLI does not re-import preserved bytes or reset the new cache.
`cache-status` (also `--json`) reports whether originals were copied or retained
in place. It keeps recovery visible even after some entries are populated:
the cache cannot know that every credential has been recovered. If the Vault
is locked, unlock it explicitly or use the existing explicit `--prompt` option.
Owner-key setup recovers only after the owner confirms the existing key; a
failed/locked owner request does not start cache recovery or initialize a key.
After preparation, setup re-reads the existing owner under replacement
coordination before reconciliation; it never repeats first-owner creation.

Cache-only, batch/pinned read-only, status and verification operations never
initiate this recovery, migrate a key or unlock/start a service. They can read
a previously published current cache. Protected-cache failures remain visible;
an unknown schema or corrupt ciphertext is not an empty store.

The selected runtime advertises `preserved-cache-recovery-v1` through the
existing `invocation-contract --json` metadata. This is additive client-local
behavior, not a new daemon request: existing `get` and owner-setup protocols
are unchanged. Healthy old caches remain readable by prior clients. After
fallback, older clients do **not** understand the new compartment selection;
they see only the retained original, not recovered values. Do not roll back a
recovery-dependent consumer to such a client or copy stale original records
over current state. Preserve originals for deliberate inspection; recovery
does not authorize their deletion or an older writer's takeover.

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
