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
`mark_complete`. Cleanup may rebuild an incomplete current slot only when no
live process owns it; it atomically detaches stale marker references before
removal. On hosts without reliable process enumeration (for example macOS
without `/proc`), current-slot cleanup fails conservatively unless an explicit
`running-version.json` ownership record proves its owner is gone. Tier-3
fallback uses the same numeric-token ordering in Python, POSIX shell, and
PowerShell, so development versions such as `dev10` sort after `dev9` without
`packaging` or GNU `sort -V`.

## Construction ownership (Python API)

`construction_lease(root, version, *, timeout=60.0, poll_interval=0.05)` is a
stdlib-only context manager for installer-owned construction. It uses a
nonblocking kernel lease (`flock` on POSIX; a CRT byte lock on Windows) under
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
Installer adoption must also implement candidate/rollback GC retention before
construction release; this primitive alone does not protect a ready candidate
or authorize rewriting a published slot. Existing installer lock schemes are
not interchangeable with this protocol until all contenders on that path adopt
the same lock.

Focused native-process tests:
`python tools/run-plugin-tests.py agent-bridge -k construction_lease`.

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
