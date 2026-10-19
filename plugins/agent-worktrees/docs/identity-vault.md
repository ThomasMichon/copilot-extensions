# Shared vault-backed PR identity

The optional encrypted `enc=` PR attribution layer uses **one owner key across
machines**, held in a shared agent-vault KeePass database. Each machine keeps its
own protected cache. Neither publication nor decoding unlocks the vault or
creates a key. Codenames, generic attribution opt-outs, and file-key compatibility
are unchanged.

## Local agent-driven setup

The [setting-up-pr-identity skill](../skills/setting-up-pr-identity/SKILL.md)
drives these commands using the exact worktrees/vault session-catalog argv:

```text
<worktrees argv> identity-vault status --vault-argv '<vault argv JSON>' --json
<worktrees argv> identity-vault setup --vault-argv '<vault argv JSON>' --json
<worktrees argv> identity-vault verify --json
```

The vault database must already be configured and its service must run
independently of the contained worktrees adapter. If it is stopped, use the
selected vault's owning setup/start lifecycle first, then retry. Worktrees
passes `--require-running`; it never creates a resident daemon inside its
kill-on-close adapter Job. Routine reader containment remains unchanged.

Setup reuses
`Identity/PR-attribution` by default. `--entry` selects an explicit complete
group/entry path. Windows setup refuses shell-sensitive entry characters
(`&|<>^%!"`) rather than passing them through a batch launcher as command syntax.
`--prompt` separately permits an interactive local unlock; without it, an
unavailable/locked vault fails with an actionable error rather
than displaying an unexpected prompt. Unattended adapter calls are bounded;
setup allows up to 125 seconds around the vault's bounded operation;
prompt-enabled setup allows up to 180 seconds. Persisted-source cache reads
allow up to 45 seconds including the owning payload gate's native-shell
provenance validation; legacy explicit-argv reads retain their 15-second bound.
Both preserve bounded output, non-breakaway containment and child cleanup.

Worktrees delegates context selection and key/cache operations to agent-vault's
`identity-key context` and `identity-key setup` commands. It does not import vault
code or manipulate KeePass itself. Only the selected entry is populated.
Initial discovery removes a foreign worktrees receipt and lets the selected
vault payload resolve and validate its own installation. Discovery explicitly
disables first-use provisioning: an unprepared runtime refuses without invoking
an installer or activating a service, for both status and setup. For an explicitly
selected/non-default installation, `--vault-context <absolute receipt>` supplies
the owning receipt from its source-qualified catalog; the vault gate validates
it. Subsequent vault calls carry that validated vault context, not worktrees'
receipt. Worktrees keeps its own context for its readiness subprocess.
Discovery and cache-only calls direct the launcher's optional boot trace to the
OS null device so read-only probes do not create incidental runtime log files.

After a successful fresh cache-loader/encryption probe, setup atomically stores non-secret
`version`, exact `argv`, `entry` and pinned `env` in `identity-vault.json` beneath
the **selected worktrees installation's runtime/state root**. This is not a
repository file, shell startup change, machine-global environment setting,
private downstream package, or new service.

The selected database, group, named vault, cache directory and vault installation
context stay pinned for later subprocess reads, independent of the caller's
repository and ambient vault selection. The vault launcher remains the owning
payload's command; rerun explicit setup with its new catalog argv if that
installation is moved or replaced. Separate worktrees installation cells have
separate configuration.
Candidate metadata reaches the probe over bounded stdin, not by replacing the
probe's installation environment. `verify` separately exercises selection from
the persisted configuration through the normal key reader.

## First owner versus another machine

Normal setup **never generates a key**. For a genuinely new owner, separately
confirm the absence of an existing key and designate the authoritative database
copy before running:

```text
<worktrees argv> identity-vault setup --vault-argv '<vault argv JSON>' --initialize-owner --confirm-first-owner --json
```

Add `--prompt` only with permission. The vault serializes initialization,
checks authoritative database contents and creates an absent entry without
overwriting one. Existing valid keys are reused; malformed values and failed
reads are refused. An ambiguous write is reread before any retry.
Cooperating initializers in different services coordinate through the vault's
stable, non-secret database-sidecar OS lease. Keep that sidecar file; deleting
it while held can split the lock namespace. Unrelated GUI/raw CLI writers are
not fenced by this cooperative lease. See the
[vault-owned contract](../../agent-vault/README.md#shared-owner-key-setup).
The key never appears in command arguments, readiness output, or plaintext key
files. This is not key rotation.

Independent, unsynchronized database copies cannot prove global absence or
provide cross-machine atomicity. Initialize **once**, confirm synchronization,
then run ordinary setup on every additional machine. Never copy a protected cache
between machines: its wrapping is local even though the cached owner key is common.

## Readiness and failures

`status --json` reports `configured`, selected `invocation`, `database`, `cache`,
`overrides`, and overall `ready` independently. It reads cache-only and does not
start/unlock a service, populate a cache, generate keys, or persist configuration.
`verify --json` starts a fresh local process and exercises the normal key reader
and portable encryption/decryption using a synthetic identity; it returns
`ready`, `cache_only` and `roundtrip`, never the key or decrypted production
identities. Neither command proves another machine or cloud synchronization.

Setup and verification refuse conflicting explicit file/legacy argv overrides.
Resolve those deliberately, preserving the old configuration until the user
chooses the source. Existing explicit nonempty `AGENT_WORKTREES_IDENTITY_KEY`
still wins for normal reads; explicit `AGENT_WORKTREES_IDENTITY_VAULT_ARGV`
and its `AGENT_WORKTREES_IDENTITY_VAULT_ENTRY` retain legacy ambient-selection
semantics and take precedence over persisted setup. Without either explicit
override, the persisted source is used. A selected source's failure never falls
back to a different legacy key. With no selected source, legacy OneDrive file
discovery remains available.

A failed setup leaves previous source configuration intact. Cache and
authoritative vault failures do not authorize key replacement. Use the selected
vault's own `identity-key context --json` / `identity-key setup --json` diagnostics
for unlock, missing entry, unusable cache, malformed key or backend errors;
worktrees suppresses arbitrary adapter output because it may contain credentials.
