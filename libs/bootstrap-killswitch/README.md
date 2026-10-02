# bootstrap-killswitch

A single, repo-wide operator switch that pauses **every plugin's**
sessionStart bootstrap-check reconcile at once.

## Why

Each plugin's `scripts/bootstrap-check.sh` / `.ps1` hook runs on every new
session and, when it sees its own install drift from the deployed version,
re-runs its installer in the background (see each plugin's own
`bootstrap-check.*` header). That's the right default -- but it is **not**
safe to leave running while a human or agent is hand-diagnosing a venv or
install path directly (walking it step by step, re-running an installer
manually, comparing a broken path against a working one): a background
reconcile can silently redo or undo the exact thing being diagnosed,
mid-diagnosis, with no visible conflict -- it just looks like the system is
fighting the investigation.

This lib gives that moment one clearly-scoped, resettable switch instead of
either (a) nothing, so reconciles keep racing a live diagnosis, or (b)
disabling an individual plugin's hook by hand, which does not cover the
other plugins' hooks that fire on the exact same session starts.

## Shape

- **Canonical sources** (this directory): `bootstrap-killswitch-guard.sh` and
  `.ps1`. Vendored byte-identically into every plugin that ships a
  `scripts/bootstrap-check.sh` (the same opt-in-by-presence pattern
  `sync-versioned-runtime.py` uses for the canonical resolver) via
  `tools/sync-bootstrap-killswitch.py`. Never edit a vendored copy directly --
  edit here and re-run the sync tool.
- **Shared state**: one JSON file, not per-plugin --
  `~/.copilot-extensions/bootstrap-killswitch.json`
  (`{"active": true, "reason": "...", "set_by": "...", "set_at": "..."}`).
  One on/off affects every plugin's hook uniformly, because they all read
  the same file.
- **Fails open**: a missing, unreadable, or malformed state file is always
  treated as *inactive* (reconcile proceeds normally). A corrupt switch must
  never silently wedge every plugin's reconcile forever.
- **Scope: the default (non-namespaced) installation only.** This is a single
  machine-local file at the installation-home root, outside every
  marketplace installation cell's own ownership boundary (see
  [Marketplace Installation Cells](../../visions/plugin-services/installation-cells/README.md)).
  A namespaced cell reconciles through its own cell-scoped mechanism, which
  never reads this file -- every call site wraps its guard check in `if (no
  cell context)`, matching the exact same no-op `COPILOT_EXTENSIONS_CONTEXT`
  exit each `bootstrap-check.*` already takes for its real reconcile logic.
  Activating the switch from one marketplace's session can therefore never
  pause an independent, unrelated cell's reconcile.

## Usage

Every adopting plugin vendors an identical guard that doubles as a direct
CLI, so **no single plugin is a hard dependency** -- a machine with only one
adopter installed (per `docs/patterns/a-la-carte-independence.md`: users may
install any plugin subset, and plugins cannot assume a sibling exists) can
still toggle the switch through its own vendored copy:

```bash
bash ~/.copilot/installed-plugins/copilot-extensions/<any-adopter>/scripts/bootstrap-killswitch-guard.sh on "<reason>"
bash ~/.copilot/installed-plugins/copilot-extensions/<any-adopter>/scripts/bootstrap-killswitch-guard.sh status
bash ~/.copilot/installed-plugins/copilot-extensions/<any-adopter>/scripts/bootstrap-killswitch-guard.sh off
```

(PowerShell: the same verbs against the sibling `.ps1` copy.) All adopters
read/write the identical shared state file, so it does not matter which
one's copy you invoke.

**If `agent-machines` happens to be installed**, it additionally exposes
this as a friendlier, discoverable subcommand (optional, not required):

```bash
agent-machines bootstrap-killswitch on "hand-diagnosing intelligence-dampener-reviewer venv"
agent-machines bootstrap-killswitch status
agent-machines bootstrap-killswitch off
```

Remember to turn it back off when the hand diagnosis is done -- it has no
automatic expiry by design: a diagnosis of unknown duration has no safe
default timeout to guess, and an auto-expiring switch could silently re-arm
reconcile mid-diagnosis just as easily as never having the switch at all, with
no way for the operator to know it happened. `status` always shows who set
it, when, and why, so a forgotten switch is discoverable rather than silent.

## Known limitations

- **Does not stop an already-in-flight reconcile.** The switch only ever
  prevents a *new* session start from launching a background reconcile; it
  cannot retroactively stop one already running when `on` is called, since
  that process is detached and outlives the session-start hook that spawned
  it. `on` best-effort detects this: most adopters guard their own
  background reconcile with a `~/.<plugin>/reconcile.lock` single-flight
  file naming the live PID, and `on` scans every such lock under
  `$BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT` (defaults to the home
  directory; this override exists mainly for tests, since Windows
  PowerShell 5.1's `$HOME` automatic variable does not honor a reassigned
  `HOME` env var the way pwsh/bash do), warning by name if any are still
  alive. This is **not exhaustive** -- `agent-bridge`, `agent-machines`,
  and `agent-worktrees` don't use this exact lock convention and are never
  detected this way. If `on` reports no warning, a diagnosis can still
  theoretically race one of those three plugins' own in-flight reconcile;
  when in doubt, wait a few seconds after activating the switch (most
  reconciles are brief) before treating any plugin's venv as settled.
