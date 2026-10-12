# versioned-runtime

The **canonical source** of `versioned_runtime.py` — the stdlib-only,
cross-platform primitive that owns the immutable per-version runtime layout
(dotfiles #581). Each version installs into its own immutable
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

## Why this is vendored, not a package

Unlike the other `libs/*` entries, this is **not** an installable package and has
**no `pyproject.toml`**. The primitive must run at *install time* via the
bootstrapping system python — **before any venv exists** — so it deliberately
stays out of every runtime venv (no vendored-lib fan-out) and ships as a plain
`scripts/versioned_runtime.py` file inside each Python-runtime plugin, so a
marketplace-installed plugin is self-contained.

Because plugins are pulled **independently** from the marketplace, the file
cannot be a shared runtime import — it must physically exist in each plugin. So
this canonical copy is **vendored (synced) byte-identically** into every Python
runtime plugin's `scripts/` dir.

The standalone `agent-index-service` distribution also consumes this exact
primitive for its explicit installer, without introducing a plugin or a second
maintained source mirror. Its standard setuptools build copies the canonical
file into wheel build output; its sdist carries the same bytes so a subsequent
wheel build needs no repository checkout. Wheel/sdist byte-equality tests and
wheel RECORD bind that generated resource to the package artifact. Existing
plugin bootstrap scripts remain self-contained and unchanged. The standalone
installer uses only its own installed resource, never a repository/plugin/PATH
fallback; editable/source-only installation is not an installer runtime.

Slot resolution is completion-marker strict: `current-version`,
`last-known-good`, and newest-slot fallback candidates must carry a valid
`.install-complete.json` and an interpreter. The marker schema is exact: string
`version` matching the slot, string `completed_at`, and nonnegative integer
`pid` (booleans excluded), with only an optional string `payload_hash`; extra or
duplicate keys are invalid. Python readers accept valid fields in any order,
while shell resolvers recognize the canonical field order emitted by
`mark_complete`. Cleanup refuses an incomplete selected numbered slot with distinct-generation
health-repair guidance, even when its recorded owner has exited. Only the mutable
dev exception may rebuild its incomplete current slot when no live process owns
it, atomically detaching stale references before removal. On hosts without
reliable process enumeration (for example macOS without `/proc`), current dev
cleanup fails conservatively unless an explicit
`running-version.json` ownership record proves its owner is gone. Tier-3
fallback uses the same numeric-token ordering in Python, POSIX shell, and
PowerShell, so development versions such as `dev10` sort after `dev9` without
`packaging` or GNU `sort -V`.

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

This API is not yet wired into shell installers. A separate `acquire` command
that exits could not retain kernel ownership for a subsequent shell build.
Installer adoption must also acquire candidate/rollback retention before
construction release; construction ownership alone does not protect a ready
candidate or authorize rewriting a published slot. Existing installer lock schemes are
not interchangeable with this protocol until all contenders on that path adopt
the same lock.

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
Shell-installer adoption and publication create-once behavior remain separate
Phase 3 work; old installers that do not hold construction or retention do not
gain protection merely because this primitive ships.

Focused native-process tests:
`python tools/run-plugin-tests.py agent-bridge -k runtime_retention`.
Five smoke cases run in the existing changed-plugin guard lane; the broader
process, death, and ordering matrix runs in the full/manual promotion portfolio.

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
