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
│  ◀ lambda-core · win │ borealis │ All machines ▶                  │   ← machine tabs
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
  profiles; never repo-managed) and other settings.

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

**Messages** (read-only) peeks the last few conversation turns of the worktree's
latest session in an overlay, so you can tell what a worktree was doing — and
whether it still needs follow-up — without opening it. This is the read-side
companion to the disposition summary: it derives recent context straight from the
session's `events.jsonl` even when the agent-asserted summary never accumulated.
`↑`/`↓` scroll; `Esc` closes. Local worktrees load in-process; a remote worktree's
messages are fetched over SSH. (Backed by the `recent-messages` CLI verb.)

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
