# copilot-extensions Worktree Manager

The standalone, **out-of-plugin** harness control plane (installer, configurator
& worktree launcher) for the copilot-extensions harness. It is deliberately
**not** a Copilot plugin and is **not** delivered through the marketplace/plugin
pipe — it is its own payload, fetched and run directly, because the thing that
must guarantee the plugins' prerequisites cannot itself be one of those inert
plugins. It is the one piece that must work *before* the plugins do.

> **Status: active control-plane extraction.** The Manager bootstraps and updates
> itself, provisions the harness core, reads harness state, shells out to the
> worktree engine, and hosts the first independent Textual Picker. It now also
> owns the versioned contract for plugin-contributed pivots, actions, cards/forms,
> and configuration sections. The Picker now renders contributed pivot snapshots
> asynchronously from that contract; action/streaming parity, repo adoption/config
> editing, mux/profile relocation, and presets remain under umbrella issue
> [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352) and the
> contract/render extraction is tracked by
> [#1165](https://github.com/ThomasMichon/copilot-extensions/issues/1165) and
> [#1174](https://github.com/ThomasMichon/copilot-extensions/issues/1174).

## Set up the harness

A bare `worktree-manager` invocation reports its running and installed versions,
observed core/engine availability, baseline prerequisite gaps, and registered
project count. Next steps are derived from that snapshot: an installed setup
is not repeatedly presented as a first-time Phase 2 install. The summary is
read-only and does not claim a complete health or authentication verdict;
use `doctor` for deeper inspection.

```bash
uv run python -m worktree_manager doctor        # report prerequisites + the core install
uv run python -m worktree_manager setup         # plan provisioning + core install (dry-run)
uv run python -m worktree_manager setup --apply # actually provision + drive the real installer
```

`doctor` is read-only; `setup` is **dry-run by default** and only changes the
machine with `--apply`. Provisioning is restart-aware (it tells you to restart
your shell after a PATH change) and idempotent (re-running heals a partial
install). The core install is never reimplemented — the Worktree Manager locates and
calls agent-worktrees' own `install.{ps1,sh}`.

`doctor` also reports **plugin-catalog alignment**: whether the authored
catalog (`data/plugins.toml`) still matches the discovered plugin membership
(same report as `plugins --reconcile`, folded into one surface). A discovered
plugin with no authored entry (**uncovered**) is expected and non-blocking —
it just runs on inferred defaults. An authored entry no longer discovered
(**phantom/renamed**) or a plugin publishing a prerequisite the catalog
doesn't carry (**published-prereq gap**) is real drift. In **human-readable
mode**, that drift fails `doctor`'s exit status alongside the existing
prerequisite/core-install gate; `--json` mode always exits `0` regardless of
findings (including `plugin_alignment.ok: false`) — inspect the JSON payload's
own `ok` fields for the actual status.

`doctor` also reports **repo registration**: whether every `repos.yaml` entry
with a checkout path registered for this platform actually resolves to a real
git checkout on disk (working tree or bare, confirmed by probing git itself
rather than trusting filesystem markers alone). A repo with no path
registered for *this exact* platform at all is not flagged — it may
legitimately be reference-only here, and another platform's own entry is
never substituted for it. A *registered yet missing or non-git* path is real
drift, surfaced the same way plugin-catalog alignment is: it fails `doctor`'s
exit status in human-readable mode, and `--json` mode always exits `0`
regardless (inspect `repo_registration.ok`). A repo whose checkout couldn't
be verified at all (git itself unavailable or the probe timed out) is
reported separately under `repo_registration.unknown` — visible, but never
treated as confirmed drift.

## Launch preparation

Normal shell launch maintenance reports each reconciliation action's start,
process exit code and completed duration. Failures (including commands that
cannot start) are explicitly reported as continuing: maintenance remains
best-effort, not a launch-wide abort policy. PowerShell uses a Stopwatch and
native UTC formatting. Bash, including Bash 3.2, uses one launch-owned
`update_telemetry.shell` renderer during maintenance, not the wall-derived
`SECONDS` variable. It acknowledges each reconciliation scope before its
command starts, providing the shared formatter's UTC and monotonic clocks
without a Python or `date` process per line. These durations measure the
containing action, not individual child lines or a verified serving-state
postcondition. An in-process clock reader observes native completion records
independently of the presentation sink, so draining queued diagnostics does
not inflate a completed action's duration. Child output
continues streaming to the setup log; interactive action summaries use the
existing launch-status gate, leaving quiet/protocol stdout unchanged.
Each pass still obtains a fresh plan and re-resolves the runtime afterward;
an exit of zero does not replace the second reconciliation pass.

The native maintenance sinks share one physical-line envelope with the
Python updater: `[HH:mm:ss.fffZ] scope state message [elapsed Ns]` or a
completed action's `[duration Ns]`. Canonical nested envelopes are retained,
not wrapped in another timestamp. The Bash renderer reads a private temporary
spool using shell-built-in record writes; it has no update, service or process
retirement authority. Normal completion drains and waits for it, then removes
the spool before Copilot starts. Startup and shutdown waits are bounded;
parent loss ends the renderer. A missing/failed renderer warns explicitly and
uses unavailable timing labels rather than invented durations. Failure
diagnostics remain in the named private spool instead of being discarded.
Parent-loss termination is independent of terminal writes and admission
waits; the dedicated CLI exits nonzero and retains a `ready.failure` reason.
Shutdown resolves the renderer's still-owned Bash job and command before
signalling; a cached PID alone never authorizes termination. A final receipt
is written after output closes and the clock thread joins. Cleanup requires
both that receipt and the absence of a live owned job, even if Bash already
reaped its exit status. Job-table checks run only at lifecycle boundaries.
Early/late Bash launcher diagnostics outside maintenance, inherited bootstrap
child streams and detached installer output retain their existing behavior;
these are not evidence of standalone installer presentation convergence.

New and Resume wait for the engine to finish repository synchronization,
prompt staging and instruction projection rather than imposing a fixed
aggregate plan timeout. Preparation stages and elapsed-time heartbeats stream
to the launch terminal; the final plan remains JSON on stdout. Individual
engine operations keep their own failure bounds, and automation can explicitly
request a bounded plan call.
An explicitly bounded SSH prompt plan allows at least 240 seconds for the
target's 180-second admission call and host overhead; an unbounded preparation
call remains unbounded.
Captured preparation owns a kill-on-close Job on Windows and a dedicated
process group on POSIX. A POSIX group owner stays unreaped until the engine's
completion receipt has been read and the entire group has been cleaned up,
including when an inner render failure degrades and the engine exits normally.
Foreground projection inherits the latter rather than
detaching from its launch owner, so an explicit timeout or cancellation also
terminates renderer and Git descendants. The bounded asynchronous backup
worker keeps its independent lifetime and containment.

Instruction projection is shipped inside agent-worktrees itself. It does not
require customizing-copilot to be installed or globally enabled: promotion
vendors the shared canonical engine into the owning worktree payload.

## Resume prompt

The initial loading tab is labelled `Local` with its environment until the
machine roster resolves its canonical name. It does not present the OS hostname
as a configured machine or add roster/configuration I/O to first paint.

Windows and WSL tabs for `<host>` and `<host>-wsl` share one physical-host
heading. An explicit `physical_host` association takes precedence over suffix
inference. Grouping changes presentation only: environment selection, source
identities, SSH aliases and worktree routing remain distinct.
Physical siblings are presented together even when the roster separates them;
keyboard traversal uses that same stable first-host-appearance order.
The configured local key or alias need not match the OS hostname: source
construction and per-row locality checks resolve it in the running execution
environment. Its current tab loads in-process; sibling environments keep their
own SSH targets, and returned rows retain the selected source's logical identity.

Ordinary Worktrees loads require the engine's resident current-roster
publication, including its observed git/session/mux facts and bounded claim
aggregates. The local and machine-SSH loaders hold the CLI's
`list --current --stream --follow` feed by default; the Manager never connects
to daemon transport directly or recomputes/overlays those facts. Complete
snapshot/update barriers replace rows atomically. Planned cutover notices and
abrupt disconnects are distinct; reconnect retains explicitly stale last-good
rows until the replacement generation completes. Unsupported authority is an
actionable engine-upgrade error. Explicit `AGENT_WORKTREES_PICKER_STREAM=0`
selects current one-shot reads, not a direct computation fallback.

Held-feed startup uses the source's existing bounded process/SSH budget
(20 seconds by default). Once an initial snapshot or update starts, the
two-second atomic-publication deadline applies; committed feeds retain the
five-second heartbeat deadline. A transient transport watchdog expiry
reconnects through the existing cancellable backoff, showing failure or stale
last-good rows until a complete replacement arrives. Explicit peer timeout,
authentication, malformed-protocol and incompatible-capability refusals remain
terminal; none authorizes a polling fallback or a daemon restart.

Sessionless workspaces retained by another controller appear separately under
**Owned**, not as ordinary resumable conversations. Use **Go to controller**
when its exact target is loaded, or **Refresh** / **View details** to diagnose
an unresolved owner. Existing live-mux attachment and orphan reclamation remain
available; a genuinely empty, unowned worktree still offers **Open**.
A successful explicit task embodiment may attach its newly launched session
even while the Picker's pre-transaction workspace snapshot is still sessionless.

For a stopped worktree, **Actions > Resume prompt…** supplies the first
turn of the cold Copilot start alongside its prior `--resume=<session>` target.
It uses the full `--interactive` argument, including with **No Mux**. A live
session offers **Open**, not Resume with a prompt; Open neither queues nor
injects a turn, even if the live session has not received its first prompt.
If the target becomes live during launch, the prompted resume refuses visibly.
New and Resume prompts are stored with their distinct provenance in the
worktree's external state folder through the daemon's existing write
authority. Failed attempts keep the prompt for the next cold attempt.
Both Manager resolve hops carry the exact staged identity, not another copy
of its text. Successful Copilot handoff discards the prompt; a setup refusal
or backend-start failure leaves it available for retry. Open never submits a
staged Resume prompt. Uncertain handoff acknowledgement keeps the intent
visible but refuses automatic resubmission, avoiding a duplicate turn.
A later explicitly supplied prompt replaces the pending intent; a stale
handoff acknowledgement cannot discard that replacement.

For SSH machine sources, the host renders the same composer and sends a bounded
structured request to the target engine before opening its interactive SSH
launch. The target daemon admits the request, allocates at most one New
worktree, and stages the prompt in that target's worktree state. The launch then
carries only the target worktree and seed identity. Arbitrary prompt text is
encoded, not interpolated into the remote shell. Requests are limited to an
7 KiB UTF-8 envelope, including JSON metadata, to stay within remote command
limits after PowerShell's UTF-16LE/Base64 wrapping. Oversized envelopes or shell
commands are rejected before SSH admission.

An old target engine refuses the structured request before creating or staging
anything; seedless remote launches remain compatible. An uncertain response
reports the target and request identity. Do not repeat New: on the target,
`<project> resolve --json --launch-request-status <id>` reports the already
allocated worktree and staged intent without submitting another prompt.
Resume that existing worktree only when its state is `staged`; an incomplete
creation or unconfirmed handoff requires explicit inspection.

Prompted requests require `--stage-launch-seed` support; ordinary seedless
launches retain older-engine compatibility. Uninstrumented legacy/custom
prompt launchers refuse without consuming the seed; use normalized setup or
adopt the engine's documented launch-seed invocation boundary.
The `gh copilot` intermediary cannot acknowledge actual Copilot startup;
prompted requests using that fallback retain their seed and require a direct
Copilot executable. Ordinary seedless fallback launches are unchanged.

## One-line bootstrap

**Windows (PowerShell):**

```powershell
iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.ps1)
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.sh | bash
```

The bootstrap fetches this `worktree-manager/` payload and **version-installs** it
under the same convention as the harness's other installers — an immutable
`~/.worktree-manager/versions/<version>/` slot, a plain-text
`~/.worktree-manager/current-version` marker, and a `~/.local/bin/worktree-manager`
binstub (`.cmd`/`.ps1` on Windows) — then launches it. Re-running the one-liner is
**version-gated** (a no-op when already current). After the first run, invoke
`worktree-manager …` directly (ensure `~/.local/bin` is on `PATH`). The bootstrap
**auto-provisions its prerequisites**: `uv` is installed user-local (no admin) when
missing, and `git` is installed best-effort where a package manager exists —
otherwise the payload is fetched as a GitHub tarball, so a bare machine bootstraps
even without `git`. It amends the current session's `PATH` and prompts for a
restart when it can't. Set
`WORKTREE_MANAGER_ROOT` to relocate the install root. Inspect/repair the versioned
install with `worktree-manager self-install` (dry-run) / `--apply`, and see it in
`worktree-manager doctor`. A successful self-install also refreshes the
`agent-worktrees` front door registration at
`~/.agent-worktrees/control-plane-providers.d/worktree-manager.json`, so a bare
project binstub discovers this Manager through the generic control-plane-provider
registry rather than a literal `worktree-manager` PATH probe.

### Slot completeness and recovery

Each `versions/<version>/` slot is proven complete by its own
`.install-complete` marker, written only as the **last** step of a fully
successful copy + pointer materialization, and invalidated **before** any
mutation of that slot begins (never left to the copy/removal step that
follows, which can itself fail partway through on a locked file) -- the
marker is what proves the build itself completed. A version is reported
installed (`current-version` published) only when its slot carries that
marker **and** its key entrypoint files (`pyproject.toml`,
`src/worktree_manager/__init__.py`, `src/worktree_manager/__main__.py`) --
a cheap, independent check against *later* damage to an otherwise-complete
slot, not an exhaustive scan of every module `__main__.py` imports. This
closes a real failure mode: a Windows `PermissionError` (WinError 32) from
`shutil.rmtree`/`shutil.copytree` hitting a slot still held open by another
process (a stranded or still-live mux-daemon pinned there) could otherwise
leave an existing-but-empty slot on disk while `current-version` still
correctly named it — every subsequent run then failed with `No module named
worktree_manager`, indefinitely, since presence-only (`is_dir()`) checking
treated that broken slot as valid forever.

If you hit `No module named worktree_manager`, the `worktree-manager` binstub
itself is unusable (it runs `python -m worktree_manager` straight out of the
broken slot, so it fails identically) — **re-run the bootstrap one-liner
above**, which fetches a fresh payload and runs its own `self-install --apply`
from that valid copy, independent of the broken slot. Once a working install
is restored, `worktree-manager self-install --apply` is the right command for
any *other* incomplete-slot symptom (e.g. a stale provider-manifest or
binstub) where the binstub itself still runs — it is version-gated and
idempotent, and will detect and rebuild an incomplete slot rather than
skipping it as "already current."

Registered project binstubs enter the interactive front door as
`worktree-manager --project <name>`; that project-only form launches the Picker.
Truly argument-free `worktree-manager` retains the provider-free first-run
banner and onboarding roadmap.

## Choose the update source (fork / canary branch)

By default the self-updater pulls the Worktree Manager payload from the canonical
GitHub repo's `main`. To track a **fork** or a **canary / different source
branch** for future updates, set a **user-level source override** — a small config
file at `~/.worktree-manager/config.toml` (`[source]` table), managed with the
`source` command (there is **no** environment variable for this):

```bash
worktree-manager source                                   # show the effective repo + ref
worktree-manager source set --ref canary                  # track a different branch
worktree-manager source set --repo https://github.com/<fork>/copilot-extensions.git
worktree-manager source reset                             # back to the canonical default
```

The override is honored by both `worktree-manager update` (the self-update step)
and the bootstrap one-liner (it reads the same config file on re-run), and shows
up in `worktree-manager doctor`. Resolution is simply **config file → built-in
default**; the file is human-editable:

```toml
[source]
repo = "https://github.com/<fork>/copilot-extensions.git"
ref  = "canary"
```

When a live `mux-daemon` is already serving managed sessions, `worktree-manager
update` now cuts it over in place instead of stacking a stale resident beside
the new code: the successor starts on a fresh loopback port, status writers
follow the routed active endpoint first, and the predecessor drains accepted
status-apply work plus its current republish cycle before retiring.

`worktree-manager update` owns the update run's telemetry: it records the
self-update as the `manager_self_update` phase, exports the run identity to the
engine it drives, and writes `~/.copilot-extensions/update/update-status.json`.
A failed cutover fails the run (`command_failed`, exit 1); the engine's exit
code is mapped through the shared update exit-code table (0 success, 1
`command_failed`, 2 usage error, 3 configuration, 4 auth, 5 blocked by a lock,
6 timeout (never reported as success), 130 interrupted; see the
agent-worktrees CLI reference). The run exists before the engine parses the
forwarded arguments, so an engine usage error is recorded as the run's failure
(error code `usage`) and exits 2. Events go to
`~/.copilot-extensions/update/runs/<run_id>/events.jsonl`.

On Windows, ordinary and passive daemon starts resolve the real base
`python.exe`, preserve the calling venv's import context, and apply the shared
windowless daemon launch flags. Recurring PSMux probes and status writes also
apply the shared captured-child window-suppression flags; redirecting output
alone is not sufficient. Windows status writes and liveness probes own their
descendants through a kill-on-close Job, including on timeout or cancellation.
The launch-kind contract is documented in
[`windows-background-process-launch`](../docs/patterns/windows-background-process-launch.md).
This keeps the spawned process handle's PID equal to the runtime PID instead of
publishing a venv redirector's PID. If the venv's direct base interpreter cannot
be resolved, startup fails explicitly rather than retaining an ambiguous
launcher/child identity. Repair that Python installation before retrying.
Existing launcher/child pairs are not rewritten or stopped by this change;
their identity must be classified before automated retirement.

## Manage the harness (state views)

Once set up, the Worktree Manager provides state views read from the harness's
own files, plus explicit local integration commands:

```bash
uv run python -m worktree_manager projects        # harness repos (binstubs + profiles)
uv run python -m worktree_manager projects <name> # config dir, linked knowledge repo, profiles, enabled plugins
uv run python -m worktree_manager repos           # every known repo + indicators
uv run python -m worktree_manager repos <name>    # worktree mode · agent mode · pr model · ownership · remote
uv run python -m worktree_manager worktrees       # live worktree counts per project (via the engine)
uv run python -m worktree_manager worktrees <name> # one project's worktrees: state, sync tags, titles
uv run python -m worktree_manager plugins --status  # known plugins vs. what is enabled user-global
uv run python -m worktree_manager contracts --project <name> # contributed pivots/actions/cards/config
uv run python -m worktree_manager refresh-profiles <name> # Windows: reconcile local profiles + shortcuts
```

`refresh-profiles` is the narrow, JSON-producing local integration command.
It collects saved project selections once, applies the existing fragment and
Terminal state/settings reconciliation, and refreshes native/WSL shortcuts.
Unchanged fragment content and shortcut properties are not rewritten. It does
not stage a plugin payload, build a venv, or probe a remote host. A missing
destination, invalid selection or failed shortcut operation returns a blocked
result and nonzero exit rather than claiming synchronized profiles.
Publishing also refuses malformed or unreadable registries and resolved machine
rosters, invalid registry entries and dangling configuration links before
touching fragment/state/settings outputs. Actual publishing collection uses
strict registry reads too; preliminary validation never authorizes a later
tolerant reload. Genuinely absent optional files remain supported. Read-only
state views and previews retain their tolerant behavior.
Present registries require their explicit `projects`/`repos` mapping; an empty,
null or missing-key document is not permission to retire profiles. Shortcut
display names are validated before fragment publication, not after it. Engine
callers pass their resolved `--machine` key so global configuration and machine
detection do not depend on a project-local machine setting.
For a selected installation cell, the engine additionally sends
`--input-roots-stdin`: a version-1
`copilot-extensions.terminal-profile-input-roots` JSON object with `context`,
`payloadRoot`, `registryRoot`, and a `projectRoots` name-to-directory mapping.
The engine resolves project identities; Manager validates the owning
agent-worktrees installation receipt and cell containment, then uses those
same explicit roots for preflight and publishing collection. Missing project
mappings or invalid cell inputs block instead of reading legacy selections.
This does not redirect Manager discovery, Terminal output or shortcut paths:
those remain machine-global. Direct legacy invocations need no extra argument.
Publication also validates existing Terminal fragments, state and settings
before writing, retaining the input bytes through reconciliation and refusing
observed drift. Missing optional documents remain valid; malformed or denied
reads never become empty profile inventories. Terminal settings support JSONC
comments and trailing commas. When settings need cleanup, their original bytes
are backed up; unchanged settings are not rewritten.
Strict YAML publishing rejects duplicate mapping keys before migration or
defaulting. Fragment-directory enumeration errors are failures, never evidence
of an empty inventory.
Publishing rejects blank/non-string selection identifiers and unknown kinds,
including when inputs change during collection; it never silently converts a
shell typo into an agent launch.

The **worktrees** views are the first slice of the extracted Picker: they read
**live** worktrees by shelling out to the `agent-worktrees` engine
(`agent-worktrees --project <p> list --json --current`) — the **process
boundary** the Textual Picker will sit on. The Manager never imports the plugin;
the only coupling is the engine's stable `--json` verbs (the pinned
[engine ↔ Picker contract](../plugins/agent-worktrees/docs/engine-picker-contract.md)),
and the client ([`engine_client.py`](src/worktree_manager/engine_client.py))
requires the current-roster authority contract. Older or incompatible engines
produce an actionable upgrade error rather than silently dropping authority.
Unreachable residents permit only explicitly labeled, independently indexed
advisory rows or an unavailable error, never caller-side reconstruction.

Plugin-contributed interactive surfaces are discovered independently from each
enabled plugin's installed payload. `worktree-manager contracts` validates the
versioned manifest contract and reports disabled, malformed, duplicate, legacy,
or missing-command contributions without importing plugin code or requiring a
plugin installer to write into Manager-owned state. See
[`docs/plugin-contribution-contract.md`](docs/plugin-contribution-contract.md).
The contract also lets one available contribution designate the initial home
pivot and lets providers declare view-scoped actions alongside row actions, so
the Manager does not need a provider-specific home or "New" path.
Available pivot contributions appear beside Worktrees in the Picker. Their list
commands run in background workers, so the UI opens before cross-process reads
finish; each pivot keeps its own cached rows and loading/error/empty state, and
renders either its declared columns or the contract's id/title/badge fallback.
The current internal Worktrees surface now uses that same declarative pivot
model (`entity`, home, list envelope, columns, row actions, and view actions)
while retaining a typed cross-cutting `Worktree` model for launch/session
integration. This is the migration seam for moving the declaration into the
agent-worktrees payload without pretending the Manager cannot understand
worktrees.
Streaming, actions, cards/forms, and configuration sections landed through the
subsequent parity slices, and the bundled Picker was retired once that parity
work completed.

## Production Picker transplant

The established production Picker source now lives under
[`src/worktree_manager/production_picker/`](src/worktree_manager/production_picker/)
as a wholesale copy of the still-shipping agent-worktrees implementation.
`worktree-manager picker <project>` runs that transplanted UI.

The production validation and preview surfaces live with it:

```bash
uv run python -m worktree_manager picker mock <project> --local
uv run python -m worktree_manager picker screenshot <project> --format text
uv run python -m worktree_manager picker screenshot <project> --format svg --out picker.svg
pwsh -File scripts/preview-picker.ps1 -Project <project> -Format svg -Out picker.svg
cd scripts/picker-snapshot && python install.py
uv run python render.py picker.png
```

New Worktree options and the Open/Resume action menu expose independent,
default-off `AHP` and `No Mux` checkmarks. `AHP` asks the Manager to create or
verify a same-machine hosted session for the exact engine-resolved worktree;
`No Mux` controls only terminal presentation. Configuration is user-owned in
`~/.worktree-manager/config.toml`; see
[`docs/configuration.md`](docs/configuration.md). Missing or mismatched AHP
configuration, account, host, session, or working directory fails closed and
never falls back to a direct Copilot process.

On resume, an `active` or `unknown` persisted execution leg forces its provider
even when the AHP checkmark is off; only a disposed or absent binding may return
to direct launch. An AHP-owned row exposes **Dispose hosted session** as an
explicit action. It disposes and terminally marks the hosted binding so a later
normal Finalize can proceed; terminal exit never performs that destruction.

### Developing a pivot: render early, render often

Any change touching a pivot's columns, derived fields, row grouping, or
section ordering (Worktrees or a plugin-contributed pivot alike) should be
verified **visually**, not just by reading a diff or trusting a passing test
— column widths, truncation, and drop-priority under narrow terminals are
exactly the kind of regression a unit test's string assertion can miss while
still "passing". Produce a render **before and after** the change, and again
at each meaningful milestone while the change is in progress, not only once
at the end:

```bash
# Fast, no dependencies -- read directly in a terminal or paste into a PR:
uv run python -m worktree_manager picker screenshot --demo --format text

# Shareable PNG (once, to install the Node dependency + cache the font):
cd scripts/picker-snapshot && python install.py
uv run python -m worktree_manager picker screenshot --demo --format svg --out /tmp/pivot.svg
node svg2png.mjs /tmp/pivot.svg /tmp/pivot.png 3
```

`--demo` needs no live engine or real worktrees -- it renders the real
production Picker against the deterministic fixture in
[`src/worktree_manager/demo.py`](src/worktree_manager/demo.py) (see the
`--demo`/`--preview` paragraph below). If the fixture doesn't yet exercise
the field you're adding (a new derived column, a new state, a new claim
shape), extend `demo.py` with representative values first -- a render against
data that never populates the field you're changing verifies nothing. Compare
the before/after renders side by side (or via the `tests/production_picker/`
golden-screenshot harness, `AGENT_WORKTREES_UPDATE_GOLDENS=1` to
intentionally accept a diff) before considering the change done, and keep
each phase's render as evidence in the tracking effort/PR rather than only a
prose description of what changed.

`picker mock` renders real-shaped state but simulates mutations. The screenshot
command exports the production character grid, ANSI grid, or SVG through the
same compositor used by the live app. `scripts/picker-shot.py` adds
identity-obscured/shareable captures and validates the PNG signature and
dimensions after browser rasterization. `picker [screenshot] --demo`/
`--preview` renders the same real production Picker against deterministic
mock data instead of live engine state -- see `preview.py`'s module
docstring for the two injections this composes (a fake-engine worktree data
source, and a manifest-injected mock pivot exercising the same cross-plugin
pivot registry a real contributed pivot uses). The older minimal `picker_app`
(`demo_source`/`capture_svg`/`run_picker`) is no longer used by `--demo`; it
remains only as the still-live `LaunchRequest`/launch-compose machinery
shared with other commands.

During the migration, the copied presentation modules reach their existing
engine/data operations through a private compatibility boundary to the active
agent-worktrees runtime. That boundary is intentionally temporary: it preserves
the proven UX first, while later slices replace each dependency with the
Manager-owned CLI/service contracts. The full legacy UX, golden, cache, pivot, streaming, steering, profile, SSH, and
selection corpus now runs from `tests/production_picker/` against the
Manager-owned package. Bare project invocation remains on the bundled
production Picker until remote/base-repo decisions and final live validation
are complete.

**Projects** are the repos promoted to first-class harness projects (worthy of
binstubs + profiles, in `projects.yaml`); **Repos** are everything else in the
registry. The views expose worktree mode, agent mode, PR model, ownership, the
linked knowledge repo, and per-project enabled plugins. Config *editing* (linking
a knowledge repo, per-plugin config, adoption) is being built out under Phase 3/4
([#356](https://github.com/ThomasMichon/copilot-extensions/issues/356) /
[#357](https://github.com/ThomasMichon/copilot-extensions/issues/357)).

## Plugin-knowledge model (Phase 1)

The Worktree Manager learns **which** plugins exist dynamically from the marketplace
— a nearby checkout when present, otherwise the remote published marketplace ref
(the same ref the bootstrap fetches; set with `worktree-manager source set --ref`). Its
installer-owned catalog
([`src/worktree_manager/data/plugins.toml`](src/worktree_manager/data/plugins.toml), read
by [`catalog.py`](src/worktree_manager/catalog.py) + composed in
[`model.py`](src/worktree_manager/model.py)) is a **knowledge overlay** on that
membership: `kind`, the ordered "what to do" steps, and any prereqs beyond what a
plugin publishes. A discovered plugin with no catalog entry is kept with
**inferred defaults**, so the installer never breaks on a newly-added plugin.

```bash
uv run python -m worktree_manager plugins              # effective model (discovered + overlay)
uv run python -m worktree_manager plugins agent-worktrees   # one plugin's prereqs/config/steps
uv run python -m worktree_manager plugins --prereqs    # de-duplicated union of prerequisites
uv run python -m worktree_manager plugins --reconcile  # coverage: uncovered / phantom / prereq drift
```

This resolves **DQ2** in favor of an installer-side catalog **over dynamic
membership**: it imports no plugin code and requires no plugin to publish
anything installer-specific, yet it reads metadata plugins already publish for
their own reasons (the marketplace entry, `scripts/service.yaml` prereqs). Because
membership is discovered there is no frozen list to police — `--reconcile` is a
*coverage* report (uncovered = discovered but not authored, running on inference;
phantom = authored but not discovered), not a hard gate. A contract test asserts
the one-way boundary holds in both directions.

## Boundary: one-way, dependency-free

The Worktree Manager *knows about* the plugins and their configuration and "what to
do" to make each ready — but **neither depends on the other**. No plugin
requires the Worktree Manager, and the Worktree Manager never joins a plugin's
dependency graph. A plugin installed and run with the Worktree Manager never present
behaves exactly the same.

## Develop

```bash
cd worktree-manager
uv run python -m worktree_manager          # run the app
uv run python -m worktree_manager --version
uv run --extra dev pytest -q              # tests
```

Default pytest collection owns `tests/`, including `tests/production_picker/`,
in both development and materialized release layouts. Releases embed complete
shared libraries under `libs/`, including their test sources; those suites
remain owned by their canonical library lanes, not by Manager's default run.
`tools/test_worktree_manager_collection.py` checks real materialization with a
bounded selection of actual Manager test modules, preserves their collected
IDs, and verifies that embedded library code and tests remain intact. It
reconstructs development references from either checkout layout and compares
canonical bytes using the materializer's exclusions for generated build metadata.

Python + [uv](https://docs.astral.sh/uv/); the Phase 4 visual worktree-manager uses
[Textual](https://textual.textualize.io/).
