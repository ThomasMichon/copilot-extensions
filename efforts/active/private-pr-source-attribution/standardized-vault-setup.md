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

- [x] Review and merge this amendment before implementing new behavior (#6744).
- [x] Implement installation-scoped non-secret worktrees configuration and
  explicit local status/setup/verification using the vault tool boundary.
- [x] Supply a vault-owned create-only first-owner operation with explicit
  consent, no secret-bearing argv/output and no overwrite on retry/race.
- [x] Add trigger-discoverable agent guidance and reconcile the existing PR
  attribution, vault setup, CLI help and adopter documentation.
- [ ] Land implementation through current-head review and required CI, prove
  the promoted release, and distinguish authorized installed verification
  from source merge. Retire reliance on adopter-specific setup where the
  standard flow covers it without discarding existing protected caches.

## Validation

- [ ] Disposable configured KeePass database: existing common entry populates
  the normal protected cache and fresh-process publication/decoding succeed.
- [x] Missing entry refuses ordinary setup; initialization is explicit,
  create-only and preserves existing key material under repeats/concurrency.
- [x] Locked/unconfigured/missing/unreadable database and malformed keys produce
  distinct bounded action-required errors, not success or replacement keys.
- [x] Read-only status has no file/service/cache/key mutation; failed setup
  leaves prior configuration and authoritative key intact.
- [ ] Named vault/group and installation-cell selection stay pinned across
  population and later reads; conflicting overrides are not silently ignored.
- [x] No key/master password in argv, logs, JSON, temp plaintext files or
  diagnostics. Explicit interactive prompt behavior is covered independently
  of bounded unattended behavior.
- [x] Existing environment/file-key precedence, optional encryption, generic
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
- The amendment merged before implementation. Worktrees now supplies
  installation-scoped setup/status/verification and trigger-discoverable guidance;
  vault-owned provisioning and combined configured-database integration are
  implemented. These are source results, not a released or installed readiness
  claim; current-head review, required CI and promotion remain separate gates.
- Focused worktrees identity/compatibility/native-crypto tests and validated
  installation-cell path selection pass (88 items). Native Windows ARM64 runs
  without `cryptography` pass 50 items; only the optional comparison against that
  absent portable backend is skipped. POSIX source/fresh-process contracts pass
  37 items; nine Windows-only contracts are skipped there.
- The Docker clean-room engine is unavailable on this host. No daemon startup
  or repair was attempted to manufacture that lane. Fresh disposable homes,
  source settings, protected caches and real configured KeePass databases
  directly exercise first touch on both platforms; no whole-machine Docker
  clean-room pass is claimed.
- The Python-runtime combined live contract passes eight items on native Windows ARM64
  and eight on POSIX. It proves existing-owner reuse, immutable read-only status,
  locked-daemon fresh-process lookup, explicit first-owner creation and repeat
  reuse. POSIX execution uses a byte-matched native-filesystem source artifact;
  slow cross-filesystem cold startup and a TCP-only test-fixture assumption
  were diagnosed, not hidden by production timeout/transport changes.
- A bounded real KeePass probe demonstrated that concurrent create-only CLI adds
  can both succeed for the same absent entry. The implementation therefore
  coordinates cooperating initializers with the existing database-sidecar
  OS-lease primitive and rechecks absence under it. A real regression starts
  two independent service processes in different installation contexts against
  one database: exactly one add occurs and both cached results match the final
  authoritative key. Unrelated GUI/raw CLI writers and unsynchronized copies
  remain outside that cooperative guarantee.
- The initial review of #6782 identified missing payload-launcher coverage:
  runtime gates replaced the identity cache selection, a worktrees receipt
  crossed the vault boundary, and Windows cold activation inside a contained
  adapter could lose its resident. Those findings block merge. Python-runtime
  integration results do not discharge the corresponding launcher obligations;
  their validation items are reopened while the actual entrypoints are fixed.
  Worktrees now preserves its own probe context, passes candidate metadata over
  bounded stdin, and requires independent vault-service activation rather than
  weakening cache-reader containment. Actual launcher/cold-service regression
  proof and a subsequent passing review are still required.
- The review correction now passes real Windows payload-entry tests in legacy
  and namespaced modes, including a distinct worktrees receipt, scoped vault
  cache, persisted fresh reader and an independently running resident. The full
  native vault suite passes 368 items; the focused worktrees reader/CLI suite
  passes 99. A headless-launch guard failure and registration-introspection
  omission are corrected rather than bypassed.
- Actual native gate validation can exceed the old Python-adapter-only
  15-second reader budget. Persisted sources now explicitly allow 45 seconds
  including that provenance validation; legacy explicit argv retains 15 seconds.
  Output limits, non-breakaway process containment and cleanup stay enforced.
  This timing change is intentional and documented, not a claim that the
  earlier Python-only proof covered payload startup. Subsequent review remains
  a separate gate; Linux actual-entry validation is still in progress.
