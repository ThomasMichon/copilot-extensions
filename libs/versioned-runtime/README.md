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

Slot resolution is completion-marker strict: `current-version`,
`last-known-good`, and newest-slot fallback candidates must carry a valid
`.install-complete.json` and an interpreter. Cleanup may rebuild an incomplete
current slot only when no live process owns it; it atomically detaches stale
marker references before removal.

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
