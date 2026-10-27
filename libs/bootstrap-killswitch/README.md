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
  `~/.copilot-extensions/killSwitches/bootstrap-killswitch.json`
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


## Emergency pre-tool workflow-guard switch

`agent-machines pretool-killswitch` is a separate, default-inactive control:

```console
agent-machines pretool-killswitch on "temporary latency diagnosis"
agent-machines pretool-killswitch status --json
agent-machines pretool-killswitch off
```

It pauses the suite-owned **preToolUse workflow guards**, not Copilot's native
permissions or all hooks: agent-worktrees' resident policy request and local
statelessness, cross-repository, anchor-write and PR-supersession guards, plus
copilot-extensions-harness' Copilot-mention guard. Operators temporarily lose
those automatic workflow checks and must continue following their repository
policy. No process is killed. postToolUse, prompt hooks, session lifecycle and
bootstrap reconciliation remain unchanged. The bootstrap switch above remains
independent and does not stop these pre-tool guards.

The state is `~/.copilot-extensions/killSwitches/pretool-killswitch.json` with `active`,
`reason`, `set_by` and `set_at`. There is no automatic expiry. `off` removes
only this state file and restores normal guard evaluation on the next call.
Missing, corrupt or non-boolean state leaves guards enabled; corrupt state is
reported on stderr and `status` fails visibly. `PRETOOL_KILLSWITCH_STATE_FILE`
selects an explicit **absolute** alternative state path, principally for isolated
testing. Relative overrides are refused by the CLI without effects; hooks report
the invalid configuration and continue normal guard evaluation. Thus CLI and
hook working-directory differences cannot silently select different state files.
Namespaced `COPILOT_EXTENSIONS_CONTEXT` cells never consult this default-install
flag, even with an override; the management command refuses in a cell.

The canonical stdlib-only `pretool_killswitch.py` is vendored byte-identically
into each adopting payload (and the agent-machines package). Run the existing
`tools/sync-bootstrap-killswitch.py` / `--check` to synchronize/audit both
controls. Agent-worktrees' installers publish the helper before its importing
hook. No sibling plugin's runtime is required to evaluate the flag.

**Delivery is not activation.** First merge, promote and deploy the release
through the normal owner update on each intended machine. Updating hooks.json
alone cannot relieve already-running sessions: hosts cache its command text.
The existing commands invoke the deployed hook script, which reads this flag
on **every invocation before stdin, IPC or fallback evaluation**, so current
sessions do not need a restart once those invoked files are updated. An
already-running hook invocation is not canceled. Activate separately on each
intended default installation and retain status/rollback evidence; unavailable
machines are not covered by another machine's switch.

A native per-hook `disabledHooks` entry is a distinct emergency escape hatch,
not this switch's backing store. If one was used while awaiting deployment,
restore only that exact entry through the native host's hook reload after
verifying this control on the installed entrypoint; preserve unrelated disabled
hooks and never set `disableAllHooks`. Clearing the native entry while leaving
this switch active keeps the workflow guards paused. Clearing this switch while
leaving the native entry disabled does **not** restore those guards.


### Contract and mixed-version operation

The selected executable's read-only `agent-machines pretool-killswitch status
--json` advertises contract `copilot-extensions.pretool-killswitch`, version `1`,
and capability `pretool-stop-v1`. Probe that actual selected executable, not a
newer payload/version stamp. A legacy Machines parser rejects the unknown
command before effects; do not treat it as supported or replay an effectful
command through another form. Existing bootstrap commands and state are unchanged.

New state writes include the same `contract` declaration. Readers also accept
the minimal version-1 baseline `{ "active": true }` (or `false`) and ignore
additive advisory fields. A declared foreign or unsupported contract leaves
guards enabled with an explicit diagnostic. State and command versions are
independent of package versions; no existing form is being retired.

Expand by deploying the new helper and hook entrypoints first (the installer
copies the dependency before its importer), then use the capable Machines
controller and migrate operators to explicit activation. A newer controller
with an older Worktrees/harness consumer can set a file but **cannot pause that
older consumer**. Controller status proves its state, not fleet coverage or
actual hook support. Validate each installed invocation before claiming relief.
Older controllers with newer hooks retain normal behavior while the file is
absent. For rollback, clear the switch using the capable selected controller
before rolling it back; keep the state inactive while rolling consumers back.
A retained flag cannot constrain an old consumer, and must not silently
reactivate on a later upgrade. Native per-hook disables remain separate as
explained above.


## Central namespace migration

Suite switch state belongs under `~/.copilot-extensions/killSwitches/`, not a
project folder. Bootstrap and pre-tool state retain their distinct file names
and semantics. Before updating consumers, inventory each intended installation
and copy any existing legacy bootstrap flag byte-for-byte to the central path;
preserve its active value, reason, actor and timestamp. Refuse conflicting
existing destination state rather than overwriting it. Retain active legacy
copies while older readers still need them, so migration never accidentally
resumes automatic reconciliation. Unreachable installations remain explicit
pending migration obligations, not evidence that their state is absent.

New bootstrap readers use the central file when it exists and otherwise honor
`~/.copilot-extensions/bootstrap-killswitch.json`. An existing central file is
authoritative, including an explicit inactive value; malformed central state
keeps the existing fail-open behavior rather than reviving a stale shadow.
New `on` writes only the central path. Default-installation `off` clears both
central and legacy bootstrap copies so a retained shadow cannot reactivate it.
An explicit `BOOTSTRAP_KILLSWITCH_STATE_FILE` remains isolated: reads/writes/off
touch only that override, never either default file.

Upgrade the selected controller and actual consumers before using the migrated
control. An old controller does not know the new directory; do not use its
`off` or status as proof that a migrated flag is cleared. Remove legacy copies
only after the owning readers are qualified on the new namespace. For a
controller/consumer rollback, clear both paths with the capable controller
first, or explicitly restore the retained original flag at the legacy path
before selecting the old reader. Do not lose a paused state across rollback.
The pre-tool control is new in this release; its canonical default starts in
the central directory and remains inactive until explicitly activated.
