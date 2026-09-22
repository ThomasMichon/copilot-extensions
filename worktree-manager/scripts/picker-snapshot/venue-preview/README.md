# Codespaces/Containers pivot preview tooling

Renders **real** before/after screenshots for the `picker-venue-pivots`
effort (`efforts/active/picker-venue-pivots/README.md` and
`visions/venue-pivots-ux/README.md`) for operator review, iterating before
any implementation PR. Not shipped runtime.

It drives the REAL Textual `PickerApp`/`WorktreesView`/generic column-pivot
renderer — the actual engine, not a mockup — against:

- a hermetic demo Worktrees source (frozen clock, no git/SSH/subprocess),
  matching `../tasks-preview`'s pattern, and
- **two manifest variants per pivot**: a byte-for-byte copy of the REAL
  installed manifest (`agent-codespaces.current.json`,
  `agent-containers.current.json`) proving today's actual shape, and this
  effort's **proposed** manifest (`agent-codespaces.proposed.json`,
  `agent-containers.proposed.json`), rendering the SAME underlying fixture
  rows (`fake_pool.py`/`fake_fleet.py`, shaped like a Phase 1/2
  `pool.py`/`_cmd_fleet` would compute them once implemented).

Nothing here talks to a live CodeSpace, Docker daemon, or agent-bridge, and
nothing mutates anything.

## Prerequisites (one-time)

Build the two plugin venvs the picker engine imports from (same as
`../tasks-preview/README.md`):

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
  worktree-manager\scripts\picker-snapshot\venue-preview\render_venue_preview.py `
  --out-dir worktree-manager\scripts\picker-snapshot\venue-preview\out
```

Writes eight PNGs to `--out-dir`:

| File | What it shows |
|------|----------------|
| `codespaces-before.png` | The REAL current CodeSpaces pivot: repo-grouped, columnar (health/use/safe/worktree/cores/task), but **no subtitle line at all** — `pool.picker_payload`'s computed subtitle is silently dropped today. |
| `codespaces-after.png` | The SAME fixture rows through the proposed manifest: a new compact `sess`/`claims` column pair (the `worktree` column drops for width — see the `+3` fit indicator, demonstrating the existing column-fit/priority algorithm working unmodified). `sess` reuses the **Worktrees pane's own column** (key/header/width unchanged) rather than a wide "driven" boolean — `LIVE`/`IDLE`/blank — since the existing `worktree` column already signals driving. Row-grammar line two: `"→ Reproduce #4021 on a clean box - fixing the relay reconnect backoff..."` (declared intent + live activity), `"→ Fix agent-mcp decorator ordering regression"` (no live activity — title only, graceful-absence), a bare venue-identity fallback with no mark (no driving worktree), and `"⚠ ... - holder worktree gone (orphaned lock)"` (the orphan mark). |
| `containers-before.png` | The REAL current Containers pivot: a flat, ungrouped badge list — `name`, `[state]`, `[fleet]` badges, and an `image`-only subtitle. No columns, no group, no worktree cross-link, no actions. |
| `containers-after.png` | The SAME fixture rows through the proposed manifest, now at CodeSpaces' fidelity: `container`/`fleet`/`state`/`sess`/`worktree`/`profile`/`task`/`claims` columns, fleet-based grouping, and the identical row-grammar line two. |
| `codespaces-menu-driven-live.png` | The action menu for a `sess: LIVE` CodeSpace row (`cs-a1c4-relay`): **Open into a CLI session**, **View driving worktree**, **Worktree status**, plus the existing Release — all new actions marked `PROPOSED — semantics not yet implemented (Phase N)`. |
| `containers-menu-driven-live.png` | The identical menu shape for a `sess: LIVE` fleet container row (`sample-repo-1`), proving the two pivots converge on one action vocabulary. |

## Files

- `fake_pool.py` — fixed JSON fixture standing in for
  `agent-codespaces pool --picker-json` (4 CodeSpaces: live, idle,
  undriven, orphaned-lock), with the row-grammar fields (`subtitle`,
  `sess`, `claims_summary`) a Phase 1 `pool.py` would compute.
- `fake_fleet.py` — the same shape standing in for
  `agent-containers fleet --json` (3 fleet containers, same four
  scenarios), matching `test_fleet_json.py`'s real field names
  (`name`/`state`/`fleet`/`lease`/`security_profile`/...).
- `bin/agent-codespaces.cmd`, `bin/agent-containers.cmd` — PATH shims so the
  pivots' unmodified `list` argv resolve to the fake CLIs.
- `agent-codespaces.current.json`, `agent-containers.current.json` —
  byte-for-byte copies of the real installed manifests.
- `agent-codespaces.proposed.json`, `agent-containers.proposed.json` — this
  effort's proposed manifests (new columns, `subtitle` wiring, new actions).
  **Not** named `agent-codespaces.json`/`agent-containers.json`: those
  filenames are in `pivots.py`'s `_KNOWN_LEGACY_PIVOTS` and get
  identity-checked against the real installed plugin's own template
  (dropped on any divergence) — the sandbox installer copies each variant
  under a distinct `*-preview.json` filename instead, exactly like
  `../tasks-preview`'s own precedent.
- `render_venue_preview.py` — the driver: builds an isolated sandbox pivot
  directory per manifest variant (so "before" and "after" never coexist in
  one capture), then captures each screenshot via `picker_tui.capture`.

## Known nuances

- The demo Worktrees source deliberately includes `a1c4` (title differs
  from its venue's declared checkout intent — demonstrates the "intent
  overrides worktree task title" fallback rung) and `88de` (title reused
  verbatim as the venue's durable title — demonstrates "no explicit intent
  -> falls back to the worktree's own task title"), and deliberately
  **omits** `c72e` (the orphaned-lock row's driving worktree is gone, so the
  `task` column and worktree-title fallback both come up empty, leaving
  only the fixture's own baked orphaned-lock subtitle text).
- `containers-menu-driven-live.png`'s background list shows a transient
  "loading containers..." state behind the modal — a capture-script
  cosmetic artifact (the modal opener selects the target row directly
  rather than waiting for the background list's own paint), not a real
  pivot bug; the modal's own content is fully loaded and correct.
- The claims values (`PR #2481`, `PR #2477 · bug #2410`, `issue #118`) are
  hand-authored **placeholders standing in for the shared claims-pecking-
  order module's eventual output** (Phase 1's own to-be-built
  infrastructure) — not a working implementation of that ranking.
