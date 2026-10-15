# Standardized configured-vault PR identity setup

Parent: [Private PR Source Attribution](README.md). Coordination: #6068.

## Request and boundary

Provide a discoverable flow that a local agent can drive on each machine when
the user already has an agent-vault KeePass database configured. Runtime
cache-only consumption exists; manual environment variables and adopter-specific
scripts are not a complete reusable setup interface.

The operator selected separately confirmed first-owner initialization for a
new user. Every subsequent machine reuses the shared entry. A cache miss, locked
database, missing synchronized file, failed read or malformed key is never
permission to generate or replace an owner key.

This is below-altitude completion of the reviewed common-key/cache contract,
composing the existing optional plugins rather than introducing a service,
broker, transport or fleet dependency. It follows the configuration ownership,
installation-cell provenance, optional composition and cross-platform patterns.
Private adoption and remote transport implementations remain outside this slice.

## Proposed contract

Agent-worktrees owns the PR identity setup/status/verification interface and
trigger-discoverable guidance. Agent-vault exclusively owns KeePass operations,
unlock prompting and protected-cache population. Cooperation uses attributable
payload-local CLI invocations, not imports into another plugin's runtime,
guessed PATH commands or direct database manipulation.

- Read-only status reports separately whether the selected vault invocation,
  configured database, shared entry cache and local consumer configuration are
  ready. It does not start a service, unlock, populate a cache, generate a key,
  read arbitrary entries, publish PRs or expose key bytes.
- Explicit setup accepts the owning vault invocation from its session catalog,
  retains its selected named database/group/installation context, and uses the
  already-configured database. It delegates population of the selected entry
  to the vault. Unlock prompting is separately opted in; unattended calls fail
  with an actionable reason rather than hanging or prompting invisibly.
- Persist only non-secret, validated source configuration in the worktrees
  installation's machine-local state. No repository configuration, shell
  startup edits, global environment mutation or private downstream package is
  required. Existing explicit environment/file-key overrides keep their
  compatibility precedence and are reported if they conflict with setup.
- Normal publication and decoding continue to use cache-only reads through
  the configured owning vault command. Cache enablement/selection must survive
  fresh processes without silently changing other vault installations or
  unrelated callers. Configured-source failures never select a legacy key.
- Verification exercises the actual normal key reader and portable encryption
  in a fresh process, returning a bounded secret-free readiness result. It does
  not lock a shared daemon as a side effect, print decrypted production
  identities, create a PR or claim that remote machines are ready.
- First-owner initialization is a distinct, explicit opt-in, not part of normal
  additional-machine setup. The vault owns an absence-checked, serialized,
  create-only operation that stores one base64-encoded 32-byte owner key in the
  selected database entry without putting it on a process command line or
  writing a plaintext key file. Existing valid values are reused; malformed
  existing values are refused, never replaced. Ambiguous writes are reconciled
  by an authoritative reread before any retry. An unavailable/locked database
  is not authoritative absence.
- Serialize same-database initialization and use the backend's create-only
  entry behavior. Independent unsynchronized copies of a database cannot
  establish global absence: guidance requires first-owner initialization once
  on the designated authoritative copy, synchronization confirmation, and only
  ordinary setup on subsequent machines. No cross-machine atomicity is claimed.
- Keep generic source-attribution opt-outs and optional encryption unchanged.
  Existing file-key adopters remain compatible. No key rotation, old-key
  deletion, remote key transfer or automatic vault installation is introduced.

## Plan

- [ ] Review and merge this amendment before implementing new behavior.
- [ ] Implement installation-scoped non-secret worktrees configuration and
  explicit local status/setup/verification using the vault tool boundary.
- [ ] Supply a vault-owned create-only first-owner operation with explicit
  consent, no secret-bearing argv/output and no overwrite on retry/race.
- [ ] Add trigger-discoverable agent guidance and reconcile the existing PR
  attribution, vault setup, CLI help and adopter documentation.
- [ ] Land implementation through current-head review and required CI, prove
  the promoted release, and distinguish authorized installed verification
  from source merge. Retire reliance on adopter-specific setup where the
  standard flow covers it without discarding existing protected caches.

## Validation

- [ ] Disposable configured KeePass database: existing common entry populates
  the normal protected cache and fresh-process publication/decoding succeed.
- [ ] Missing entry refuses ordinary setup; initialization is explicit,
  create-only and preserves existing key material under repeats/concurrency.
- [ ] Locked/unconfigured/missing/unreadable database and malformed keys produce
  distinct bounded action-required errors, not success or replacement keys.
- [ ] Read-only status has no file/service/cache/key mutation; failed setup
  leaves prior configuration and authoritative key intact.
- [ ] Named vault/group and installation-cell selection stay pinned across
  population and later reads; conflicting overrides are not silently ignored.
- [ ] No key/master password in argv, logs, JSON, temp plaintext files or
  diagnostics. Explicit interactive prompt behavior is covered independently
  of bounded unattended behavior.
- [ ] Existing environment/file-key precedence, optional encryption, generic
  opt-outs and legacy tokens remain compatible.
- [ ] Windows native ARM64 and POSIX unit/real-subprocess coverage; first-touch
  clean-room configured-database exercise and available live local proof.
  Record skipped lanes and reasons; remote Gateway availability is not a
  prerequisite for local-machine setup.

## Journal

- The existing implementation provides cache-only consumption, not a complete
  generic setup flow. This amendment defines the missing reusable boundary.
  No production database, common key, protected cache or installed payload is
  changed by this planning slice.
