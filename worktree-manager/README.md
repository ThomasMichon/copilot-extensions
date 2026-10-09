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

## Resume prompt

For a local, stopped worktree, **Actions > Resume prompt…** supplies the first
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

On Windows, ordinary and passive daemon starts use the shared direct,
windowless interpreter resolver and preserve the calling venv's import context.
This keeps the spawned process handle's PID equal to the runtime PID instead of
publishing a venv redirector's PID. If the venv's real base `pythonw.exe` cannot
be resolved, startup fails explicitly rather than retaining an ambiguous
launcher/child identity. Repair that Python installation before retrying.
Existing launcher/child pairs are not rewritten or stopped by this change;
their identity must be classified before automated retirement.

## Manage the harness (state views)

Once set up, the Worktree Manager is also the ongoing **Manager** — a read-only
window (today) onto the real config state, read from the files the harness
already writes:

```bash
uv run python -m worktree_manager projects        # harness repos (binstubs + profiles)
uv run python -m worktree_manager projects <name> # config dir, linked knowledge repo, profiles, enabled plugins
uv run python -m worktree_manager repos           # every known repo + indicators
uv run python -m worktree_manager repos <name>    # worktree mode · agent mode · pr model · ownership · remote
uv run python -m worktree_manager worktrees       # live worktree counts per project (via the engine)
uv run python -m worktree_manager worktrees <name> # one project's worktrees: state, sync tags, titles
uv run python -m worktree_manager plugins --status  # known plugins vs. what is enabled user-global
uv run python -m worktree_manager contracts --project <name> # contributed pivots/actions/cards/config
```

The **worktrees** views are the first slice of the extracted Picker: they read
**live** worktrees by shelling out to the `agent-worktrees` engine
(`agent-worktrees --project <p> list --json --classify`) — the **process
boundary** the Textual Picker will sit on. The Manager never imports the plugin;
the only coupling is the engine's stable `--json` verbs (the pinned
[engine ↔ Picker contract](../plugins/agent-worktrees/docs/engine-picker-contract.md)),
and the client ([`engine_client.py`](src/worktree_manager/engine_client.py))
tolerates an older engine by degrading a request rather than failing.

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
cd scripts/picker-snapshot && npm install
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
cd scripts/picker-snapshot && npm install
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
uv run pytest                          # tests
```

Python + [uv](https://docs.astral.sh/uv/); the Phase 4 visual worktree-manager uses
[Textual](https://textual.textualize.io/).
