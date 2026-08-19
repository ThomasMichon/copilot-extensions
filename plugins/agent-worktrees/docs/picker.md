# The Worktree Picker

The **Picker** is the interactive terminal UI you get when you run a project
binstub with no arguments (`my-project`). It's the front door to the whole
worktree lifecycle: it lists every worktree, lets you **resume** or **create**
one, runs the setup script, and launches the Copilot session — keeping worktrees
fresh and services deployed along the way. This is the operator walkthrough; for
the pivot-registry internals see
[architecture.md § Picker Pivot Registry](architecture.md#picker-pivot-registry-cross-plugin),
and for the states and landing flow see
[worktree-lifecycle.md](worktree-lifecycle.md).

## What happens when you launch a binstub

```
my-project                         # bare project binstub (no subcommand)
   │
   ▼
launch-session.{ps1,sh}            # ① pre-flight freshness (see below)
   │
   ▼
agent-worktrees resolve            # ② the Picker — you select or create a worktree
   │                                  emits a JSON launch plan, then exits
   ▼
setup script                       # ③ tools/setup/setup.{ps1,sh} (or config launch:)
   │                                  install deps, set env, print status
   ▼
Copilot CLI session                # ④ your work happens here (often in a mux pane)
   │
   ▼
post-exit checks                   # ⑤ detect completion; finalize if pushed
```

Running the bare binstub always opens the Picker. To **skip** it and drive
worktrees programmatically, use `agent-worktrees create [--json]` (no launch) or
`agent-worktrees resolve --new` (create + launch a muxed session) — see
[cli-reference.md](cli-reference.md). Why sessions run in a multiplexer at all
(and when to skip it) is covered in [Multiplexed Sessions](mux.md).

> **The bare-invocation seam (Phase 6).** A bare, no-args `<project>` is the
> *human-facing* path, and it resolves through a **seam**: if the out-of-plugin
> **Worktree Manager** (`worktree-manager`) is on PATH, the engine hands off to
> it; otherwise it falls back to this bundled Picker. PATH presence of
> `worktree-manager` is the whole signal — no registration file. Any *args* route
> programmatically to the tool CLI and never touch the seam, so an agent running
> `<project> <verb>` never loads a Picker. The Manager reaches the engine only via
> its machine-readable verbs — see the
> [engine ↔ Picker `--json` contract](engine-picker-contract.md). (The seam lives
> in the CLI so bare invocation always resolves sanely even through a stale
> binstub; see the Phase 6 effort for the never-break 6a→6b→6c sequence.)
>
> **When the bundled Picker is retired (6c).** The fallback then becomes a
> **trustworthy install trigger**: with no Manager on PATH, bare `<project>`
> prints the Manager's verifiable source
> (`https://github.com/ThomasMichon/copilot-extensions`) and the platform install
> one-liner (`worktree-manager/bootstrap.{sh,ps1}`) so a user who was on the full
> version is guided to install it — never a silent break, and never an
> auto-executed remote script. This is capability-gated on the `picker_tui`
> package, so it stays dormant (no nag) while the Picker still ships and activates
> automatically once 6c removes it.

### ① Freshness done for you at launch

Before the Picker paints, the launcher brings the environment up to date so you
never work against a stale tree or runtime:

- **Pre-flight auto-update** — if the anchor repo has new commits affecting the
  worktree manager, the launcher re-runs the installer. Skip with `--no-update`
  or `WORKTREE_NO_UPDATE=1`.
- **Repo-adopted plugin reconciliation** — for each `<name>@copilot-extensions`
  in the anchor's `.github/copilot/settings.json`, the launcher ensures the
  payload is installed and its runtime matches (version-keyed, so an unchanged
  relaunch does ~no work). Opt out with `WORKTREE_NO_RECONCILE=1`. See
  [install-contract.md § Automatic reconciliation at launch](../../../docs/install-contract.md#automatic-reconciliation-at-launch-runtimescope).
- **Auto-fast-forward** — resuming a *clean, strictly-behind* worktree
  fast-forwards it first (never a worktree with local commits). Disable with
  `--no-fast-forward` or `auto_fast_forward: false`.

## The Picker screen

The default (Textual TUI) picker is organized into **regions** you move between
with `Tab`:

```
┌ my-project ───────────────────────────────────── ⚙ Configuration ┐   ← title + Config menu
│  ◀ Worktrees │ Tasks ▶                                            │   ← view pivots
│  ◀ anomalous-potato · win │ emancipation-cube │ All machines ▶                  │   ← machine tabs
├───────────────────────────────────────────────────────────────────┤
│  ▸ feat-abc  win  copilot-extensions:8e45   WIP ↑2                 │   ← worktree rows
│    fix-xyz   win  copilot-extensions:1c07   DIRTY                  │     (state + sync tags)
│    ...                                                             │
├───────────────────────────────────────────────────────────────────┤
│  [ New worktree ]   [ Cleanup ]   [ Sync ]                        │   ← Worktrees-row actions
└───────────────────────────────────────────────────────────────────┘
   Space: select · Enter: sub-menu for worktree 8e45 · Tab region · ^◀▶ machine   ← live footer
```

- **View pivots** — `Worktrees` is the home view. Other plugins can contribute
  pivots (e.g. a `Tasks` pivot from a task-queue plugin); they appear here
  automatically via a filesystem manifest, with no agent-worktrees code change
  (mechanism: [architecture.md](architecture.md#picker-pivot-registry-cross-plugin)).
- **Machine tabs** — one tab per registered machine plus **All machines**. The
  local host git-classifies its own worktrees; remote machines report their state
  over SSH.
- **Worktree rows** — each shows machine · environment · `repo:id4` and a **state
  block** (`WIP`, `DIRTY`, `UNUSED`, `CONVO 💬N`, `FINAL`, `ORPHAN`) with an
  `↑ahead`/`↓behind` sync tag. Same vocabulary as the status bar and
  [worktree-lifecycle.md § states](worktree-lifecycle.md#worktree-states).
- **⚙ Configuration** menu — hosts **Profiles** (user-local Copilot backend
  profiles; never repo-managed) and other settings. Installed plugins can
  **contribute their own sections** here (an SSH layer an "SSH" home, an MCP
  layer an "MCP" home) via a `config_sections` entry in their pivot manifest.

### Navigating — read the footer

Keys are **contextual**, and the footer always spells out exactly what `Enter`
and `Space` do for the current focus — read it rather than memorizing. The
constants:

| Key | Does |
|-----|------|
| `Tab` | Move to the next region (view tabs → machine tabs → list → buttons → …) |
| `↑` / `↓` | Move within the list / grid |
| `◀` / `▶` | Switch the focused tab set (view, or machine, or a button pair) |
| `Ctrl+◀` / `Ctrl+▶` | Switch machine tab from anywhere |
| `[` / `]` | Cycle the view pivot |
| `Enter` | Context action for the focus — focus a region, open a worktree's action sub-menu, press a button, apply staged changes |
| `Space` | Select / deselect the focused worktree row (multi-select set) |

> On **Windows over SSH** the TUI auto-falls back to a simpler legacy picker
> (a ConPTY keyboard limitation). You can force either one for a single run with
> `AGENT_WORKTREES_LEGACY_PICKER=1` (the rollback switch) or
> `AGENT_WORKTREES_NEW_PICKER=1`, or persist a machine default with
> `agent-worktrees picker disable` / `enable` (writes `new_picker`). See
> [config-reference.md](config-reference.md).

## Core actions

### Resume a worktree
Focus a row and press `Enter` for its action sub-menu; **resume** runs the setup
script and launches the Copilot session in that worktree (fast-forwarding it
first if it's clean and behind).

### Create a worktree
Focus the **New worktree** button and press `Enter`. It branches a fresh
worktree from the up-to-date default branch **on the selected machine tab's
machine/environment**, then launches into it. (Programmatic equivalent:
`agent-worktrees create` — no launch.)

### Per-worktree actions
`Enter` on a row opens its sub-menu — resume, plus context actions such as
**Jump to host** for a bridge/system row (navigates to the owning machine tab and
highlights the worktree by its stable id).

Installed plugins can **contribute their own actions** onto this sub-menu (e.g. a
bridge's "Send message", a dispatcher's "Dispatch task here") via a
`worktree_actions` entry in their pivot manifest — so the more of the fabric you
adopt, the more a worktree row can do. See
[architecture.md § Picker Pivot Registry](architecture.md#picker-pivot-registry-cross-plugin).

### Steering a blocked task — card + form (the DISPATCH pivot)
A registered pivot's action can declare a native **card** or **form** kind so an
operator can answer a worker that blocked on input (the agent-dispatch
card/steer seam). On the **Tasks** pivot, a task that is **awaiting steer** gains
two verbs (gated `when: {awaiting_steer: true}`):

- **View card** (`kind: "card"`) — a read-only, scrollable detail overlay of the
  brief the worker posted (title · status · link · body). Purely informational.
- **Steer** (`kind: "form"`) — a **docked card + elicitation** modal (a
  Copilot-CLI-style layout): the card's prose fills the top; a docked section at
  the bottom presents the worker's questions as **tabs** (one per question, read
  from the card's `request_input`), each a single-select (`choice`), multi-select
  (`multichoice`), or free-form **auto-expanding** text box (up to 10 lines). A
  choice/multichoice can declare an **"Other…"** option (a trailing `*` in the
  spec, e.g. `severity:choice[low,high,*]`) that reveals a free-text box. A
  single-line button row sits at the bottom: **Confirm** (submit), **Save**
  (persist a resumable draft and close — survives Escape or a kill), **Cancel**
  (discard). On Confirm the answers substitute into the action's `run`
  (`{field.<name>}` per name, or `{fields}` to submit every question) and call
  `agent-dispatch steer submit …`, which wakes the worker. `Tab` moves between
  regions, `←/→` move on the button row, `Ctrl+S` saves, `Esc` saves and closes.

Saved drafts live under `~/.agent-worktrees/steer-drafts/<task-id>.json`
(overridable via `AGENT_WORKTREES_STEER_DRAFTS`), so a half-answered card can be
resumed later.

This is a **general** steering surface (any blocked dispatched agent can use it),
and it is **never a verdict path** — it only carries the operator's answer back
to the worker.

**Messages** (read-only) peeks the last few conversation turns of the worktree's
latest session in an overlay, so you can tell what a worktree was doing — and
whether it still needs follow-up — without opening it. This is the read-side
companion to the disposition summary: it derives recent context straight from the
session's `events.jsonl` even when the agent-asserted summary never accumulated.
`↑`/`↓` scroll; `Esc` closes. Local worktrees load in-process; a remote worktree's
messages are fetched over SSH. (Backed by the `recent-messages` CLI verb.)

### Worktree types & visibility (origin × interface)

Every worktree carries two orthogonal marks (see
[architecture.md § The Worktree Record](architecture.md#the-worktree-record----single-writer-contract-invariant)):

- **Interface** — how it's *driven now*: an interactive terminal Copilot
  (**CLI**) or a programmatically driven session (**ACP** — a Neuron Forge /
  agent-bridge session). Derived from `kind` (a `bridge` worktree is ACP) unless
  explicitly stamped.
- **Origin** — *who kicked it off*: the operator via the Picker or Neuron Forge
  (**User**), a background/scheduled process (**System**), or one agent spawning
  another (**Delegate**). Derived from `kind` + the caller heuristic (a bridge
  worktree with no `caller_worktree` is the operator's — User; with one, a
  Delegate) unless explicitly stamped.

Rows are prefixed with the type in brackets — `[acp]`, `[system]`, `[delegate]`
— so an operator-owned Neuron Forge session reads distinctly from the machine's
own automation. A plain interactive CLI session carries no prefix (the common
case stays uncluttered).

**Visibility keys on _origin_, not kind.** The Picker foregrounds the operator's
own work — **User** on *either* interface, so an ACP session you started in
Neuron Forge is shown here by default, symmetric with the NF cockpit. **System +
Delegate** worktrees are tucked behind the **Toggle-hidden** button (they stay
synced, recoverable, and cleanup-exempt — visibility is decoupled from
lifecycle). This is why a bridge/ACP worktree is *shown* yet still lifecycle-
managed: `is_picker_hidden` (origin ∈ {system, delegate}) drives the row's
`hidden` flag, independent of the kind-based cleanup exemption.

### Two-step restore (Bare resume + Reclaim)
A workaround for a Copilot-CLI outage in which starting Copilot **inside a
repo/worktree directory** fails, and the only way in is to launch from the home
directory and `/resume <id>` manually. While that outage is live, a worktree
row's sub-menu cooperates so the manual restore keeps the correct mux identity:

- **The session id is shown** in the sub-menu header (with a *bound (lock live)*
  flag when a Copilot process currently holds the session's `inuse.<pid>.lock`),
  so you can copy it for the `/resume`.
- **Bare resume** creates the worktree's `wt-<id>` mux (correct identity + status
  bar) but launches Copilot in the **home** directory with **no `--resume`** —
  dodging the cwd start bug. It prints the `/resume <id>` line to run inside.
  (CLI: `resolve --worktree-id <id> --bare-resume`.)
- **Reclaim** appears whenever a live `inuse.<pid>.lock` binds a Copilot process —
  including a **bare** Copilot with no mux session, which **Stop** cannot reach.
  It kills the exact bound process (bare orphans only; a healthy muxed sibling is
  left to Stop) so the session can be re-Opened or Bare-resumed cleanly. (Backed
  by the `reclaim` CLI verb.)

Typical flow during the outage: **Reclaim** the wedged/bare process, then **Bare
resume** and `/resume <id>` inside.

### Bulk Cleanup and Sync
The **Cleanup** and **Sync** buttons on the Worktrees row open dialogs that act
across worktrees:
- **Cleanup** removes `completed` and `gone` worktrees (a commit-less
  `unused`/`convo` worktree is preserved unless you opt in — it may hold planning
  or conversation).
- **Sync** fast-forwards clean, strictly-behind worktrees to the default branch
  (never rebases or discards local commits).

`Space` multi-selects rows first, so Cleanup/Sync (and other batch actions) apply
to an exact chosen set.

### Backend profiles
Open **⚙ Configuration → Profiles** to Tab-cycle the Copilot backend profiles
declared in `copilot_profiles`, toggle a host→target mapping, and **Apply** (or
**Reset**) the grid. These are user-local settings, never repo-managed.

A host that has never been curated (no persisted `terminal_profiles` column) is
shown with the **default column**: minimal per-agent + bare cross-machine — the
host's own launcher (self·agent diagonal) plus a plain `ssh <machine>` shell for
every other machine. No remote agent-launch combos and no local shells are
emitted by default; the operator adds those explicitly in the grid. (This
replaces the retired "an uncurated host emits every possible profile" default.)

### Contributed Configuration sections
Beyond Profiles, installed plugins can add their own entries to the **⚙
Configuration** menu via a `config_sections` entry in their pivot manifest —
giving a settings-oriented layer a home (an SSH layer an "SSH" entry, an MCP
layer an "MCP" entry) without owning a whole pivot. Profiles is always listed
first; contributed sections follow. Selecting one runs the plugin's own
configuration command (scoped to the current machine); selecting Profiles still
opens the profiles grid. See
[architecture.md § Picker Pivot Registry](architecture.md#picker-pivot-registry-cross-plugin).

## Auditing & testing the render (headless)

The Picker is a **deterministic renderer**: `PickerScreen.render()` composes the
whole screen (topbar, pivots, machine tabs, borders, body, footer, and any modal
overlay) into a single styled Rich `Text`, and the app takes an injected data
`source`. Given the same data, the same grid comes out — so a state can be
captured with no live terminal and no human watching:

- **Screenshot for auditing** — `<project> picker screenshot` renders the current
  picker headlessly and writes it out for review. `--format svg` (default) is a
  standalone screenshot with colours preserved; `--format text` is the plain
  character grid; `--format ansi` is the colour-aware grid. `--out FILE` writes a
  file (else stdout), `--live` uses the multi-machine SSH source. Resolves the
  project from the cwd like every other verb (or pass `--project`).
- **Character-grid tests** — `picker_tui.capture` (`screen_to_text` /
  `screen_to_ansi` / `screen_to_svg`, and `capture()` to spin the app headlessly
  over a fixture fleet) lets tests assert *what the operator would see* — focus,
  selection, state blocks, colour-as-semantics — as a golden character grid.
  `tests/test_picker_capture.py` snapshots representative states
  (`tests/goldens/picker/`); regenerate goldens with
  `AGENT_WORKTREES_UPDATE_GOLDENS=1`.

### Shareable & animated demo captures

The same capture seam produces **safe-to-publish** imagery from real fleet data:

- **Obscured render** — `picker_tui.obscure.obscured_source()` turns real
  `list --json` dumps into a synthetic source with identifying particulars
  scrubbed (machine names -> codenames, repo/branch -> generic, titles -> a demo
  pool, PR url/number/sha and paths/summaries removed) while preserving the
  *shape* (states, sync tags, ages, dispositions) that makes it look authentic.
- **Animated walkthrough** — `capture_frames_async()` drives the picker through a
  scripted keyboard tour (switch pivot -> move selection -> open/close a menu)
  and returns a frame per step.
- **The tool** — `tools/picker-shot.py` packages the whole pipeline: gather (a
  directory of dumps, or live across the roster over SSH) -> obscure -> render ->
  rasterize to **PNG** via a headless Chromium-family browser, or `--animate` to
  a **GIF**. Example:
  `python tools/picker-shot.py --from-dir ./dumps --view all --out hero.png`.
  It's a maintainer/demo tool (needs a browser, plus Pillow for GIF), not shipped
  runtime.

## Keeping the list honest

The Picker reflects **live** state, not a snapshot: rows carry git-derived
state + sync tags, a staged runtime update surfaces as an "apply staged update +
restart the picker" row, and `r` refreshes (re-scanning contributed pivots).
Merged worktrees show as `FINAL`/completed and are cleared by Cleanup, not left
lying as open work.

## Related config

| Key / env | Effect |
|-----------|--------|
| `new_picker` (config; default `true`) | Textual TUI vs legacy picker. `picker disable`/`enable` persists it. |
| `AGENT_WORKTREES_LEGACY_PICKER` / `AGENT_WORKTREES_NEW_PICKER` | Force one picker for a single invocation (legacy wins). |
| `auto_fast_forward` (config; default `true`) | Auto-FF a clean, stale worktree on resume. |
| `copilot_profiles` (config) | The backend profiles offered in the Configuration → Profiles grid. |
| `WORKTREE_NO_UPDATE=1` / `WORKTREE_NO_RECONCILE=1` | Skip pre-flight auto-update / repo-plugin reconciliation at launch. |

Full key reference: [config-reference.md](config-reference.md).

## See also

- [Getting Started](getting-started.md) — install, register, first launch.
- [Worktree Lifecycle & Change Management](worktree-lifecycle.md) — states and
  the landing flow the Picker feeds into.
- [Multiplexed Sessions](mux.md) — why the launched session runs in a mux, and
  detach/rejoin.
- [CLI Reference](cli-reference.md) — `resolve` / `create` / `--new` and the
  non-interactive verbs.
- [Architecture § Picker Pivot Registry](architecture.md#picker-pivot-registry-cross-plugin)
  — how cross-plugin pivots and actions work.
