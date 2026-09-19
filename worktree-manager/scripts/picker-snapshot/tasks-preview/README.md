# Tasks-pane UX-overhaul preview tooling

Renders **real** screenshots of the proposed agent-dispatch Tasks pane
redesign (see `efforts/active/agent-dispatch-tasks-pane-ux-overhaul/README.md`
and `visions/plugins/agent-dispatch/tasks-pane-ux/README.md`) for operator
review, iterating before any implementation PR. Not shipped runtime.

It drives the real Textual `PickerApp`/`TasksView`/`TaskMenuScreen`/
`PivotCardScreen` — the actual engine, not a mockup renderer — against:

- a hermetic demo Worktrees source (frozen clock, no git/SSH/subprocess;
  `_demo_worktrees_source()` in `render_tasks_preview.py`), and
- a **proposed** Tasks pivot manifest (`agent-dispatch.proposed.json`) whose
  `list` points at `fake_board.py`, a fixed stand-in for
  `agent-dispatch-board` (`plugins/agent-dispatch/src/agent_dispatch/
  board_cli.py`) that never talks to a live coordinator.

Nothing here mutates anything or requires a live agent-dispatch install.

## Prerequisites (one-time)

Build the two plugin venvs the picker engine imports from:

```powershell
cd plugins\agent-worktrees
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"

cd ..\..\worktree-manager
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"

cd scripts\picker-snapshot
npm install   # installs @resvg/resvg-js, the deterministic SVG->PNG rasterizer
```

## Run

```powershell
worktree-manager\.venv\Scripts\python.exe `
  worktree-manager\scripts\picker-snapshot\tasks-preview\render_tasks_preview.py `
  --out-dir worktree-manager\scripts\picker-snapshot\tasks-preview\out
```

Writes nine PNGs to `--out-dir`:

| File | What it shows |
|------|----------------|
| `tasks-list.png` | The redesigned Tasks table at the canonical 118-col capture width: phase colour (`task_phase` palette), REPO column, WT (claiming worktree id4), T (turns), LIVE, sectioned by phase. ARTIFACTS is gracefully **dropped** at this width by the column-fit algorithm (see `tasks-list-wide.png`) — no line-wrap anywhere. |
| `tasks-list-wide.png` | The same table at a realistic 160-col width — every column shown, including ARTIFACTS. |
| `task-menu-blocked.png` | The action menu for an embodied+blocked (LIVE headless agent) task: Steer, View charter, Worktree status, Pause, Force-stop, Reset to Proposed, Force-abandon. **No "Open into a CLI session"** — CLI and ACP can't co-drive a live session. |
| `task-menu-started.png` | The action menu for an embodied+started (LIVE) task — same CLI-open exclusion. |
| `task-menu-suspended.png` | A Suspended task (worktree exists, agent not live) — **"Open into a CLI session" is offered**, resuming the existing worktree. |
| `task-menu-queued.png` | A Queued task already claimed by a pool (`cli_openable: false`) — CLI-open excluded; the pool's own next worker should take it. |
| `task-menu-proposed.png` | A Proposed, unpooled task — CLI-open offered; would create the worktree interactively. |
| `task-charter.png` | The read-only charter card (`kind:"card"`, reused unmodified). |
| `task-worktree-status.png` | The new Worktree Status card: session lineage, turns/commits/claims, live state. |
| `task-steer.png` | The existing steer modal (`PivotFormScreen`), reusing this task's own review card — unchanged mechanism, shown for completeness. |

## Files

- `fake_board.py` — fixed JSON fixture standing in for `agent-dispatch-board`
  (7 tasks, one per phase, with/without an assigned worktree, with/without
  artifacts, with/without a pool assignment). Computes the proposed
  `cli_openable` field: true only for Proposed / Queued-with-no-`pool` /
  Suspended — never for an embodied task with a LIVE headless agent
  (Blocked/Started), since CLI and ACP cannot co-drive one session.
- `bin/agent-dispatch-board.cmd` — a PATH shim so the pivot's `list` argv
  (`["agent-dispatch-board", "--machine", "{machine}"]`, unmodified from the
  real manifest) resolves to `fake_board.py` instead of the real CLI.
- `agent-dispatch.proposed.json` — the proposed pivot manifest. **Not**
  named `agent-dispatch.json`: that filename is in `pivots.py`'s
  `_KNOWN_LEGACY_PIVOTS` and gets identity-checked against the real
  installed plugin's own template (dropped on any divergence) — a distinct
  filename is treated as an independent, unmanaged static pivot instead.
- `render_tasks_preview.py` — the driver: builds the sandbox
  (`AGENT_WORKTREES_PIVOTS_DIR` + `PATH`), then captures each screenshot via
  `picker_tui.capture`.

## Known nuance carried into the effort's Journal

A `kind:"card"` action's `status_from`/`link_from` default to
`card.status`/`card.link` when the action omits them (`pivots.py`'s action
parser) — harmless for the steer-adjacent case it was built for, but a
`charter`/`worktree-status` card rooted at a different dotted path must
declare `status_from`/`link_from` explicitly (as this manifest does) or it
silently borrows an unrelated `card.*` field.

## General engine fixes landed alongside this preview

Two real, generalizable `engine.py`/`pivots.py`/`plugin_contracts.py` fixes
(not preview-only scaffolding) came out of reviewing these screenshots, and
ship with whichever PR lands Phase 1 of the effort:

- **Column.priority + `TasksView._fitted_columns()`** — any registered
  pivot's declarative `columns` now run through the same `fit()` drop/shrink
  algorithm the Worktrees list uses, so a manifest whose columns sum wider
  than the render viewport shrinks/drops the lowest-priority column instead
  of silently line-wrapping. `Column.priority` is an optional new manifest
  field (defaults to declaration order).
- **Scroll-position preservation across a full rebuild** —
  `_PickerNativeData._rebuild()` now restores the OptionList's scroll offset
  after `clear_options()`/`add_options()` instead of leaving it at the top.
  This benefits any pivot's full rebuild; it was most visible on Tasks,
  whose background `list` poll (`_maybe_repoll_pivot`) rebuilds
  independently of operator input.
