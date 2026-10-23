# versioned-runtime

The **canonical source** of `versioned_runtime.py` — the stdlib-only,
cross-platform primitive that owns the immutable per-version runtime layout.
Each version installs into its own immutable
`<root>/versions/<version>/` directory and the active one is published by a
`<root>/current-version` **plain-text marker file**. Switching versions rewrites
that marker (atomic temp+rename) and re-points the **version-pinned binstubs**
(+ scheduled task / deploy manifest) straight at `versions/<version>/…`, never a
file rewrite. **On Windows there is no junction at all** — a reparse point was
blocked by RedirectionGuard (WinError 448) on managed devices, so the marker +
pinned binstubs replace it. **On POSIX** the marker is authoritative and a
`venv`/`.venv` **symlink** (not a reparse point) still publishes the active slot
as the stable runtime-facing path. See
[`docs/install-contract.md`](../../docs/install-contract.md) and
[`docs/patterns/README.md`](../../docs/patterns/README.md) §"Runtime installs are
immutable and versioned".

## Package releases and runtime generations

The [immutable development pattern](../../docs/patterns/mutable-dev-slot.md)
sets the target for frozen, non-editable, source-hashed venvs and bidirectional
current-generation handoff. Existing editable dev claims described below are
legacy compatibility, not proof of full adoption; #5472's
[successor effort](../../efforts/active/immutable-dev-slot/README.md) retains
the remaining gates. This documentation change does not change the API.

Selectors and retention leases name concrete slots. `package_version(slot_name)`
separates an installer-generated `+repair.<32-hex-id>` suffix (or `.repair.<id>`
after an existing local version) from its unchanged package version. Python and
both canonical native resolvers order supported package releases first, then
their slot identities. A repaired older package cannot win a newest-slot
fallback over a later release. Current and last-known-good selection still take
priority over that fallback.

The installer engine's opt-in `reuse_generations=True` ordinary admission
discovers matching completed generations only after freezing the caller's full
input. It acquires the existing retention custody and performs isolated installed
health before reuse. No marker, timestamp, or package file is rewritten; failed
health requires explicit repair, and a generation with different content cannot
be selected as a successful reuse.

## Why this is vendored, not a package

Unlike the other `libs/*` entries, this is **not** an installable package and has
**no `pyproject.toml`**. The primitive must run at *install time* via the
bootstrapping system python — **before any venv exists** — so it deliberately
stays out of runtime dependency resolution and ships as plain stdlib files:
`versioned_runtime.py` owns slot identity/admission and selectors;
`versioned_runtime_leases.py` owns kernel construction/retention/publication
ownership; `versioned_runtime_construction.py` owns the finite attached protocol
and shell adapters; `versioned_runtime_source.py` owns source receipts and exact
source-qualified recovery; `versioned_runtime_publication.py` owns created-file
proofs and caught-failure recovery. The protocol receives explicit typed runtime operations,
never reverse-imports a compatibility root. Each marketplace payload carries the
complete adjacent family and is independently usable before any venv exists.

Windows installers load `versioned_runtime_native.ps1` before source discovery.
Its shared `Invoke-Hidden` captures both native streams concurrently without
allocating a console, preserves exit status, and can return stderr separately
for precise refusal diagnostics. Its PowerShell 5.1 argument encoding preserves
empty/quoted arguments without unnecessarily quoting Python launcher selectors.
Source selection and deferred stamping support a host exposing only `py.exe`.

Because plugins are pulled **independently** from the marketplace, the file
cannot be a shared runtime import — it must physically exist in each plugin. So
this canonical copy is **vendored (synced) byte-identically** into every Python
runtime plugin's `scripts/` dir. The synchronizer and install-contract guard
verify each companion as well as the entry file.

The standalone `agent-index-service` and thin `fleet-manager` distributions also
consume this exact primitive, without introducing a plugin or a second
maintained source mirror. The shared `standalone_build.py` setuptools hooks
copy the canonical Python family into wheel build output; each sdist carries the same
primitive and helper bytes so a subsequent wheel build needs no repository
checkout. The source bootstrap supports root-level and nested service project
directories, rejects ambiguous/linked helper selection, and refuses conflicting
included resources rather than preferring stale copies.
Required inputs are explicit: a detected canonical library directory requires
entry, companion and build-helper files, even when included copies exist. Those
copies are optional byte-equality comparisons in a checkout; outside it, the
complete sdist family is required.
Wheel/sdist byte-equality tests and wheel RECORD bind the generated resource to the package
artifact. Plugin bootstrap scripts remain self-contained.
The index staging adapter uses only its own installed resource, never a
repository/plugin/PATH fallback; editable/source-only installation is not an
installer runtime. Fleet's artifact delivery and client resource diagnostics
do not yet implement a managed installer or activate a runtime slot.

Slot resolution is completion-marker strict: `current-version`,
`last-known-good`, and newest-slot fallback candidates must carry a valid
`.install-complete.json` and an interpreter. The marker schema is exact: string
`version` matching the slot, string `completed_at`, and nonnegative integer
`pid` (booleans excluded), with only an optional string `payload_hash`; extra or
duplicate keys are invalid. Python readers accept valid fields in any order,
while shell resolvers recognize the canonical field order emitted by
`mark_complete`. Cleanup refuses an incomplete selected numbered slot with distinct-generation
health-repair guidance, even when its recorded owner has exited. Legacy `dev`
cleanup retains its process-evidence safeguards for compatibility, but new
explicit development refreshes build distinct `dev-<generation>` slots and
never rewrite the working environment. Tier-3
package-only fallback uses the same numeric-token ordering in Python, POSIX shell, and
PowerShell, so development versions such as `dev10` sort after `dev9` without
`packaging` or GNU `sort -V`. Both manifest `-devN` and normalized `.devN`
spellings remain prereleases before the matching final package release,
including repair generations. Source-qualified slots instead require an exact
current or last-known-good selection; hashes are not ordered recovery candidates.

Native resolution supports three durable-trace modes: normal (the unchanged
default), read-only (no durable trace), and ready (buffer records and append them
only when a completed interpreter resolves). PowerShell callers pass
`-TraceMode`; shell callers scope `AGENT_RT_RESOLVE_TRACE_MODE` around sourcing.
Opt-in stderr trace remains available in every mode. Cold first-use callers use
ready before publication admission, then normal only after admission succeeds.
Installed wrappers require the resolver's declared
`normal/read-only/ready-v1` contract before sourcing it; an older or missing
helper returns an explicit readiness error rather than assuming it suppresses
writes. These modes do not grant installation or publication permission.

## Construction ownership (Python API)

`construction_lease(root, version, *, timeout=60.0, poll_interval=0.05)` is a
stdlib-only context manager for installer-owned construction. It uses a
nonblocking kernel lease (`flock` on POSIX; `LockFileEx` on Windows) under
`<root>/.construction-leases/`, outside immutable slot content. Contenders wait
within a monotonic budget; `ConstructionTimeout` distinguishes exhausted
contention from filesystem or lock-backend failures, which propagate unchanged.
The persistent lock file is never deleted, and is not evidence of a live owner.
Version case aliases always share a lease, including on case-insensitive macOS
volumes; distinct case-only names on a case-sensitive volume conservatively
serialize too.
Normal return, exceptions, cancellation, and process death close the descriptor
and release ownership.

After acquisition, call `check_admission` again before writing: another builder
may have published while the caller waited. Matching input then reuses the winner;
different input refuses construction. A completed-slot fast path does not need
this exclusive construction lease. Hold it through construction, isolated health
gating, and publication only; release before unrelated reconciliation or
activation/cutover coordination. A repair's outer cutover guard remains required.

Shell installers use `construction-shell --shell ps1|sh` to obtain the canonical
attached-pipe adapter. Its finite `construction-session` supervisor holds kernel
ownership while the caller installs dependencies. Completion rechecks the
payload fingerprint and transfers to retention before releasing construction;
the caller settles that retention after selection/reconciliation. Failure or
pipe EOF reclaims only the owned unfinished candidate. Cleanup refusals remain
errors, and an aborted constructor cannot report success. This is not a daemon
or an `acquire` command that exits before the build it supposedly protects.
The POSIX adapter supports stock macOS Bash 3.2 as well as newer Bash. Its
private channel preserves caller descriptors and chains outer exit cleanup only
after construction settlement. `INT` and `TERM` terminate through that same
settlement rather than releasing an outer install lock or returning to a builder.
The scoped native macOS CI lane exercises the stock `/bin/bash` lifecycle.

Focused native-process tests:
`python tools/run-plugin-tests.py agent-bridge -k construction_lease`.

## Candidate and rollback retention (Python API)

`retention_lease(root, version, *, payload_hash=None, timeout=60.0,
poll_interval=0.05)` holds shared kernel ownership outside the slot. It yields
the existing ordinary slot path; a missing or linked slot refuses admission.
When `payload_hash` is supplied, it rechecks exact completed reuse **after**
acquiring ownership; conflicting, unfinished, or repair-required content refuses.
Without a hash, it can protect an incomplete candidate or broken rollback
identity, but makes no health or content claim. Multiple reuse owners coexist.
`RetentionTimeout` distinguishes exhausted contention from storage failures.
Normal exit, exceptions, cancellation, and process death release the descriptor;
surviving `.retention-leases` files are never removed and do not prove ownership.

### Windows user-space permissions

Construction, retention and publication lease directories and files explicitly
name the process account's user SID as their owner and grant that user access,
alongside SYSTEM and Administrators. They never depend on an elevated token's
default owner or an Owner Rights-only DACL: an elevated SSH installation must
remain usable by the same account's ordinary, non-administrator session.

For an existing non-public root owned by the user or an enabled owner group,
with an active full-control grant for the account's actual user SID, the canonical helper can
repair only the exact administrator-owned private lease ACL previously generated
by `mkdir(0700)`. It validates ordinary, single-link lock entries, retains an
original security-descriptor/identity audit before changing permissions, and
changes ACLs in place without replacing files or releasing their kernel locks.
Already repaired files permit a caught partial repair to resume. Overlapping
repairs also accept the exact completed target descriptor after confirming the
same filesystem identity, rather than mistaking a valid winner for corruption.
Other descriptor or identity changes still refuse. Foreign,
public, linked or unknown entries are not migration authority. An elevated
installer's Administrators-owned root or publication parent is admissible only
with that private account grant; the helper does not change the parent's ACL or
owner. Administrators or Owner Rights access alone is insufficient. A caller lacking
the rights to repair the old administrator-owned state receives the real access
error; the library never elevates itself.

A builder acquires retention **before** releasing construction, for example:

```python
with ExitStack() as lifecycle:
    with construction_lease(root, version):
        # Recheck admission, build/health-gate only when needed, then publish.
        lifecycle.enter_context(retention_lease(root, version, payload_hash=digest))
    # Retain the candidate through cutover and drain, or rollback settlement.
```

Reuse takes retention before trusting its selected completed candidate. Keep
separate references to the candidate and previous generation until their actual
promotion/drain or rollback outcome settles. Neither an mtime grace period nor
a completion helper's PID is a retention pin. These context managers must run
in the owning installer process; a shell command that exits cannot retain them.

Canonical `gc`, `toss_incomplete`, and incomplete `slot` cleanup acquire
construction, exclusive retention, then selector-publication ownership and
recheck protection under those guards before deleting. Cleanup skips a busy
slot (`slot(clean_incomplete=True)` instead raises), without waiting for an
installer or deleting its lock file. Only the thread that already owns
construction can clean its own unfinished slot without reacquiring construction;
its GC/toss operations still preserve that build. Do not clean a slot while
holding its shared retention lease.
Activation takes shared target retention before selector publication, so it
cannot publish a target deleted after an earlier existence probe. Current and
last-known-good references are protected by GC/toss, including incomplete
rollback slots. A dev claim also preserves its recorded previous version;
claim/release serialize against cleanup's selector recheck. Transfer that
rollback identity to retention before releasing the claim. Cleanup and claim
mutations read dev ownership/rollback evidence strictly: only a missing file is
absent; unreadable or malformed evidence refuses the operation. The public
status reader retains its legacy malformed-as-absent behavior.
Claim publication validates owner and rollback-version input before creating
state, so rejected new/forced claims cannot wedge later strict cleanup/release.
Unreadable selectors/storage errors propagate rather than
authorizing reclamation. Incomplete selected numbered identities are refused
with distinct-generation repair guidance, not detached and rebuilt as failed
unpublished candidates. Mutable dev cleanup retains its process-evidence and
detach/restore safeguards inside these guards.

Repair's existing outer cutover/governance guards still precede construction;
ordinary builders release construction before joining cutover. Retention does
not grant installation-cell provenance or transaction authority. The cell
`release_runtime_slot` operation remains narrower: it can remove only an exact
receipt-only reservation, never an owned runtime candidate or rollback slot.
The Agent Index cell lifecycle holds construction across reservation,
dependency installation, isolated health and completion publication. It
transfers to candidate retention before releasing construction, then retains
candidate/current/rollback references through selection settlement. Dependency
installation goes through the canonical installer-engine UV wrapper; absence
of UV refuses rather than falling back to unrestricted pip.

`discard_failed_candidate(root, version, *, link_name="current", validate=None)` requires the
caller's live construction ownership and takes exclusive retention and
selector-publication ownership before deleting. It never recreates a slot.
Selected/in-use candidates and any completion evidence (including malformed
evidence) refuse. A receipt-validation callback runs inside those guards and
must raise for foreign or changed evidence. Storage/cleanup denial raises.
The cell adapter additionally holds receipt governance locks, validates the
snapshot/ownership digest and exact namespace/install generations, and refuses
cell completion evidence. This is distinct from receipt-only reservation
release. Exception cleanup preserves the original construction error and
reports a separate cleanup refusal; a retry revalidates and removes a prior
unpublished candidate before reserving a clean slot.

The independently shipped shell installers enroll the same attached construction
lifetime and skip dependency writes on exact completed reuse. Existing
installation-cell adapters retain receipt/governance validation in addition to
kernel ownership; a copied helper alone never grants receipt authority.

`remove_owned_candidate(root, version, *, remove, link_name="current")` supplies
the same canonical cleanup custody for an installation owner's explicit
completed-candidate removal. It refuses busy construction/retention,
current/last-good/dev references and live-process protection before calling
`remove(candidate_path)`. The mandatory callback owns first-delete completion
and installation validation, durable physical ownership outside the candidate,
partial-delete retry and exact object deletion. It must preserve changed/new
objects and raise on incomplete removal; the primitive checks root absence
afterward. Even absence reaches the callback for owned outcome validation.
There is no raw recursive-delete fallback, completion-marker downgrade, CLI
uninstaller or installation-retirement authority. Do not hold a shared
retention lease when entering this operation.

## Created-file publication custody (Python API)

The publication companion's `CopyBinding(source, target, digest,
executable=False, normalized_noop=False)` declares one exact copy.
`CopyPublication(root, workspace, bindings, validate_inputs=None).publish()`
uses a caller-owned private workspace, captures every previous target before
replacement, and fsyncs each created-stage descriptor's proof before rename.
Explicit targets may include outside-root global commands; the lifecycle caller
still owns scope, source admission and installation authority.

The optional keyword-only `before_effects` callback runs once after the complete
context and receipt header exist, before any target stage or replacement.
`publish_selectors(..., before_effects=callback)` supplies the same boundary
after its receipt header. A lifecycle owner can sync that evidence and publish
its durable pending intent here rather than referencing an unfinished producer.
A callback failure prevents target effects; existing recovery and settlement
rules still apply.

`Receipt` writes and fsyncs its initial header in a unique private temporary,
then publishes its final name with an atomic no-replace native rename. It
reopens only the exact single-link producer-created object before appending.
Windows uses non-replacing rename; Linux and macOS use the same no-replace
native operation as installation-context's independently shipped slot publisher.
An unavailable native operation or storage failure refuses explicitly; there is
no check-then-replace or link-then-unlink fallback. Abandoned attempts remain
evidence and cannot block a fresh attempt. An already-existing final name is
never adopted or replaced, even when it contains identical bytes.

Matching target-writer leases remain held until `keep()` or `rollback()`.
`target_leases(root, targets, open_lock=None)` also accepts a pinned-directory
opener for callers with an admitted no-follow namespace. The opener receives
the lease-directory path and lock basename and returns an owned descriptor;
the lease closes it. The caller must retain its verified directory handles
through lease release. Default acquisition creates private lease storage,
including under a permissive umask; it does not repair existing permissions.
`verify()` checks the produced target states. A normalized no-op preserves the
existing file's physical identity and grants no created-object ownership.
The owner chooses the terminal operation from the actual runtime outcome:
keep candidate helpers after committed settlement, including an exception
re-raised after commit-forward; recover them only when the previous runtime
actually remains selected/serving.

Rollback restores proven uncontested outputs, preserves foreign replacements
and reports incomplete recovery with evidence retained. The separate
`recover_owned` API keeps its strict whole-set refusal semantics.
`keep()` verifies and removes owned backups before deleting proof metadata;
postcommit cleanup warnings never introduce a mutable-source recheck or undo a
live runtime. This Python API adds no bulk-copy CLI. It provides serialized
caught-failure recovery, not simultaneous multi-file visibility, process-crash
journaling or service-state rollback.

`read_receipt(path, root, allowed, recovery_only=True)` is an explicit read-only
exception for a lifecycle owner's withdrawal of an unsettled publication.
It validates the original physical producer file and every complete
newline-framed record, allowing a missing finish record or an incomplete final
tail. It returns only created-object states from valid complete records, does
not rewrite or complete the journal, and does not certify publication success.
The caller retains installation and target-writer custody and must refuse every
present target without a matching physical proof. Missing/invalid headers,
contexts or complete records remain refusals; a header alone proves no target.
Ordinary readers, registration reuse, `check_owned` and `recover_owned` retain
their complete-receipt requirement. This is not restoration authority for an
older runtime or a generic repair of missing evidence.

## Claim-owned development generations

An explicit development refresh uses a new `dev-<32 lowercase hex digits>`
generation, with editable source but immutable completed dependency content.
`construction-session`, `mark-complete`, and `activate` accept `--dev-owner`
and require that owner to match the current canonical `dev-claim.json`.
Losing ownership refuses completion/publication. Activation also requires valid
completion and records the selected generation in the claim under the selector
guard. Same-owner reclaims retain that generation and the original rollback
target; GC protects both while claimed.

`dev-release --owner <owner> --restore` pins the original predecessor, validates
its completion, restores selection and settles the claim under the publication
guard. A failed restore retains ownership and leaves the working generation
selected. Legacy claims and ordinary `release_dev(..., restore=False)` remain
compatible. New installers never refresh an existing `versions/dev` environment.
See [`claim-owned development generations`](../../docs/patterns/mutable-dev-slot.md).

Focused native-process tests:
`python tools/run-plugin-tests.py agent-bridge -k runtime_retention`.
Five smoke cases run in the existing changed-plugin guard lane; the broader
process, death, and ordering matrix runs in the full/manual promotion portfolio.

Failed-construction native-process checks:
`python tools/run-plugin-tests.py agent-bridge -k failed_construction`.
Cell receipt/reservation, native-shell failure, same-version retry and
release-before-settlement checks:
`python tools/run-plugin-tests.py agent-index -k cell_construction`.

## Merged-source runtime identities

`versioned_runtime_source.py` is the source identity adapter for
non-editable, merged-source payloads. It distinguishes original package
version, full Git commit/tree, materialized first-party assembly hash and
runtime generation. It is separate from editable development claims and does
not grant installation ownership or release attestation.

The adapter verifies the plugin/package identity, canonical assembly bytes
and any caller-pinned runtime selection. Malformed or changed metadata
refuses installation rather than falling back to the ordinary package slot.
The synchronizer ships the same helper to every runtime plugin without changing
ordinary package-only installation behavior; the source-preparation guide names
the native installer adopters. Independently deployed Python resolver bundles,
including standalone distributions, carry it for exact-selection recovery.
That does not introduce a managed installer into standalone consumers.

`publish_source_snapshot(payload, root, identity)` serializes source copying,
winner verification and publication with the existing construction lease scoped
to `root/snapshots`, separately from runtime-slot construction. A complete
candidate is renamed into place, never moved inside an existing winner. Identical
writers reuse the verified winner without rewriting it; conflicting receipts or
copies refuse publication. The adapter's `--snapshot-root` mode returns that
snapshot path, with `--expected-source-identity` retaining the caller's complete
initial identity across the process boundary.

Source preparation and the metadata contract are documented at
[`prepare_runtime_source`](../../tools/prepare_runtime_source.md). The
`runtime-source` bounded test target covers the producer and native consumers.

## Create-once completion publication

`mark_complete(root, version, *, payload_hash=None, pid=None)` publishes a
numbered marker only after the caller's isolated health gate. Exact reuse keeps
the original bytes, file identity, timestamp and PID; requesting a different
hash (including adding/removing a hash on legacy evidence) refuses. Existing
malformed or linked evidence requires explicit distinct-generation repair.
`read_marker` returns `None` only for absent/malformed evidence; actual storage
failures propagate, never masquerading as permission to rebuild.

Publication holds shared retention before selector-publication ownership.
A unique, flushed temporary file is linked atomically to the absent marker;
the numbered destination is never replaced. Unsupported hardlinks/storage
failures refuse publication without an overwrite fallback. Temporary files
are removed on normal/error paths. A crash before publication leaves no
completion assertion; a crash after linking leaves the complete marker intact.
Only an explicitly claimed `dev` slot permits atomic marker replacement.
Completion publication does not itself authorize package writers or retain a
candidate after the call returns: adopting installers still need admission
before construction and retention through their promotion/drain lifecycle.

Focused native-process tests:
`python tools/run-plugin-tests.py agent-bridge -k completion_publication`.
The executable install-contract probe also checks unchanged exact reuse and
conflicting-content refusal in the canonical helper, plus byte-identical fanout.

## Editing — one source of truth

**Do not hand-edit `plugins/*/scripts/versioned_runtime.py`.** Edit
`libs/versioned-runtime/versioned_runtime.py` here, then fan it out:

```
python tools/sync-versioned-runtime.py          # copy canonical -> every plugin
python tools/sync-versioned-runtime.py --check   # verify in sync (CI / pre-push)
```

`tools/check-install-contract.py` (run in CI) enforces that every Python runtime
plugin's `scripts/versioned_runtime.py` is **byte-identical to this canonical
copy** — a drifted or hand-edited copy fails the build with a nudge to run the
sync. This mirrors how the byte-identical `Get-SourceKind`/`_source_kind`
source-kind resolver blocks are kept in lockstep across installers.
