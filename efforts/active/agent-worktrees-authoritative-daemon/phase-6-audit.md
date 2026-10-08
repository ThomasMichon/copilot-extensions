# Phase 6a audit — Worktrees-pivot status oscillation

Design trace for Phase 6 (`agent-worktrees-authoritative-daemon`'s README,
`### Phase 6`), item **6a**. Traces every compute path named in that
checklist against the actual repository code (this session, 2026-10-07/08),
to establish concretely whether/how they disagree, confirm or rule out the
two additional findings already logged in the effort, and decide the
consolidation shape for 6b.

## The three compute paths, as they actually exist in code

### Path A — `classify_daemon` / `_classify_records` (the Worktrees-pivot's own git classify)

- Entry: `agent_worktrees.__main__._classify_records(records, session_ctx,
  daemon_filters=...)`.
- `daemon_filters` is only ever passed by `list_cli.py`'s `--classify` branch
  (`_build_list_json_payload`, line ~337-344) — every other caller passes
  `daemon_filters=None` and skips the daemon entirely (unchanged legacy
  behavior for those callers).
- With `daemon_filters` set: tries the resident status-monitor's coalescing
  classify daemon (`classify_daemon.classify_with_boot`), booting one if
  needed; falls back to `_classify_records_lease_guarded` →
  `_classify_records_live` on any miss (no daemon, timeout, malformed
  response, or an id-set mismatch).
- **Either way** the actual git work is `_classify_one_record`, which calls
  `git_ops.classify_worktree(..., fetch=False, ...)` — confirmed at
  `__main__.py:997`. This path is **always fetch-free**: `behind`/`ahead`
  reflect the last fetch, never a fresh one. ~5 git calls/worktree,
  batched.
- Writes back: `list_cli._build_list_json_payload` calls
  `picker_support.data_local._stamp_from_raw` after a classify pass, which
  persists `git_state=raw.get("state")` onto the tracking record (so the
  *next* cache-only pass's baseline reflects this run's classify result).

### Path B — `worktree_status_compute.compute()` / `worktree_status_daemon` (agent-dispatch's Tasks-board bundle)

- Entry: `worktree_status_compute.compute(project, worktree_id)`, wrapped
  with a TTL cache by `worktree_status_daemon.py` (not read in this pass,
  wiring confirmed via `__main__.py`'s re-export of `_worktree_status_compute
  = worktree_status_compute.compute`).
- Calls `git_ops.classify_worktree(..., fetch=True, ...)` — confirmed:
  **always fetches**, unconditionally. This is the opposite freshness
  contract from Path A, by design (a per-worktree bundle read, not a
  batch list render).
- Consumed by `agent_dispatch.worktree_status_relay` — a SQLite relay table
  for agent-dispatch's own Tasks board. Confirmed via that module: it only
  stores/serves bundle rows this compute already produced; it never
  independently computes anything and never feeds the Worktrees-pivot
  Picker/Worktree-Manager row render at all.
- **Conclusion: Path B is not implicated in the reported oscillation.** It
  is a different consumer (agent-dispatch's board), reading a different
  project/worktree-id shape, with its own cache. The two paths' *freshness
  contracts* (no-fetch vs. always-fetch) do genuinely differ, which is
  exactly the divergence 6c's own test-design note already anticipates and
  explicitly tells 6c not to treat as a bug — confirmed real here, not
  hypothetical.

### Path C — `current_worktree_status()` / `status-segment` (the status bar / Mux Companion)

- `worktree_manager.engine_client.current_worktree_status()` shells out to
  `agent-worktrees status-segment --json`, which resolves to
  `status_bar_cli.cmd_status_segment` → `_status_segment_json` →
  `_render_status_segment`.
- Confirmed: `_render_status_segment` calls `git_ops.classify_worktree(...,
  fetch=bool(fetch), ..., active_paths=None)` **directly** — no daemon, no
  `_classify_records`, no coalescing. Its own docstring's "reuses the status
  bar's own non-daemon classify pass" is accurate: this is a third, fully
  independent inline compute, fetch-free by default (same as Path A) but
  with its own code path end to end.
- Crucially, it passes **`active_paths=None`**, and its own docstring says
  this renders "raw git disposition — never ACTIVE." This path is
  structurally immune to the live-override oscillation described below:
  it never promotes a row to `active` based on a live session at all.
- **Conclusion: Path C is not implicated in the reported oscillation**
  either — it is a different entry point (single-worktree status bar /
  Mux Companion), not the Worktrees-pivot row list, and it deliberately
  avoids the liveness-override mechanism that causes the symptom.

## The actual oscillation mechanism (traced, not assumed)

The reported symptom — a Worktrees-pivot row rendering `MERGED` → `WIP`/
`ACTIVE` → back to `MERGED`, with no real underlying change — is **not**
two compute paths disagreeing (Paths B and C are confirmed uninvolved). It
is a single render protocol's own two intentional phases interacting with
the two additional findings already logged in the effort:

1. **First paint (`classify=False`, cache-only).** The production call
   chain is `worktree_manager.production_picker.picker_tui.data_local.load()`
   → `engine_client.list_worktree_rows(cache_only=True)` → subprocess
   `agent-worktrees list --json --cache-only` → `list_cli.cmd_list`'s
   `--cache-only` branch, which calls
   **`agent_worktrees.picker_support.data_local._overlay_cached_state()`**
   directly (not via that module's own `load()`, which is never reached
   from this call chain — confirmed by reading `list_cli.py`'s cache-only
   branch body, which imports and calls `_overlay_cached_state` inline
   per-record).
   - This starts from `rec.git_state` — the **last Path-A-classified**,
     cache-stamped value (e.g. `MERGED` from a previous classify pass).
   - It then computes `live = lock_live or (raw.get("session_bound_live")
     is True)` — a **local liveness probe** (a registered session lock file,
     or the cached bound-Copilot hint), independent of git state.
   - If `live`, it **unconditionally overwrites** `raw["state"] = "active"`
     — regardless of what `rec.git_state` said. This is the exact
     mechanism the effort's prior finding already named (the "intentional
     fast-pass precedence" docstring), now confirmed as the **first**
     swing: `MERGED` → `active`-rendered-as-`WIP`/`ACTIVE`, purely because
     a session is live on that worktree, with zero actual git change.
2. **Authoritative populate (`classify=True`).** The same `load()` next
   (or on the Picker's own subsequent populate cycle) calls
   `list_worktree_rows(classify=True, mux_details=True, cache_only=False)`
   → `agent-worktrees list --json --classify --mux-details` → Path A
   above. This **recomputes git state fresh** (fetch-free, ~5 git calls)
   via `_classify_one_record`, independent of the liveness override, and
   if the worktree is genuinely merged, correctly re-renders `MERGED` —
   the **second** swing, back to the true state. This also re-stamps
   `rec.git_state = "MERGED"` via `_stamp_from_raw`, so the *next*
   cache-only first paint starts from `MERGED` again — and will swing to
   `active` again on the next render cycle if the session is still live.

**This is a real, confirmed, repeatable 3-state cycle for any worktree
that is simultaneously (a) git-merged/completed and (b) has a currently
live bound session** (e.g. an operator still chatting in a worktree whose
branch already landed) — it recurs on every first-paint→populate cycle,
exactly matching the operator's report of the swing recurring "after a
brief wait."

### Why the "daemon vs. non-daemon" framing in the operator's report and the Phase 6 title doesn't quite match the root cause

The operator's framing — "Worktree Manager does its own calculation using
Git rather than trusting the status daemon" — is **directionally correct
for the first swing only**: the cache-only first-paint phase genuinely
does not ask any daemon; it combines a cached (previously daemon-sourced)
`git_state` with a **local, non-daemon liveness check**, and lets that
liveness check unconditionally win. But there is no second, competing
*daemon* computing a different git answer — the second swing is the
*same* classify machinery (Path A) correcting the state back, not a
different authority overruling the first. The oscillation's two numbers
come from one state source (the cached/classified `git_state`) and one
override (`live`), not from two disagreeing classifiers.

This matters for 6b's design: consolidating `classify_daemon` and
`worktree_status_compute` into one shared compute seam (the literal
Phase 6 title) will **not**, by itself, fix this — Path A and Path C were
never the source of the disagreement, and Path B is a different
consumer entirely. The operator's own policy ("if the daemon is alive at
all, we want the data solely from the daemon") does apply here, but the
fix it implies is narrower and different: **the cache-only first-paint
phase's liveness override must not unilaterally set the row's git `state`
field** (it already has its own distinct `session_lock_live` /
`session_bound_live` signal fields it could set instead, without
mutating `state`), or it must defer to a live daemon's already-fresh
cached value instead of the local liveness heuristic when one is
reachable.

## Recommendation for 6b (updated scope)

Two distinct fixes are needed, and they should **not** be conflated into
a single change:

1. **The structural consolidation** the Phase 6 title names: merge Path A
   (`classify_daemon`/`_classify_records`, no-fetch) and Path B
   (`worktree_status_compute.compute`, fetch-confirmed) into one shared
   compute seam, per the existing checklist's shape (i) — generalize
   `worktree_status_compute` with a `fetch: bool` parameter, and make
   `classify_daemon`'s fast, coalesced path a thin no-fetch view over it.
   Shape (i) is recommended over (ii): `worktree_status_compute.compute()`
   already assembles the richer, more complete fact set (lineage, claims,
   disposition, liveness — Path A/`WorktreeStateInfo` is a strict subset:
   state/ahead/behind/dirty/title/branch_drift), so generalizing the
   richer function to serve the narrower, faster call site is less
   duplication than the reverse. This closes 6b/6c/6d as scoped, and
   genuinely serves the effort's own stated purpose — but **does not by
   itself fix the reported oscillation**, since Path A was never the
   disagreeing party.
2. **The actual oscillation fix** (new, scoped here since 6a's own
   purpose is to confirm/rule out a direct mechanism before consolidation
   starts): change `agent_worktrees.picker_support.data_local
   ._overlay_cached_state()` so a live session sets a distinct liveness
   marker (it already partially does — `session_lock_live` — for the lock
   case) **without overwriting `raw["state"]`**, letting the row's
   renderer display "MERGED (active)" or similar rather than clobbering
   `state` itself with `"active"` outright. This is a narrow, independent
   fix to one function — it does not require or block on the Path A/B
   consolidation, and per the effort's own prior note should land as its
   own change, not be read as satisfying the structural consolidation
   claim on `#5555`.

Recommend sequencing: land fix 2 first (small, isolated, directly answers
the operator's reported symptom) as its own PR; then proceed with 6b/6c/6d
for the structural consolidation already scoped. Both are required before
this phase's closing claim on `#5555` is complete — 6d's own checklist
item already requires not letting 6a-6c's completion alone read as "the
Worktrees-pivot half of #5555 is done" if a known gap is left silently
open; this oscillation fix is exactly such a gap and must not be silently
dropped once 6b/6c land.

## Enumerated facts: Path A (`WorktreeStateInfo`) vs. Path B (`compute()` bundle)

| Fact | Path A (`classify_daemon`) | Path B (`worktree_status_compute`) | Overlap? |
|---|---|---|---|
| `state` (git disposition) | yes (no-fetch) | yes, via `facts["git_state"]` (fetch-confirmed) | **Same fact, different freshness — shape (i) target** |
| `ahead`/`behind`/`dirty`/`branch_drift`/`current_branch` | yes | yes (inside `facts["git_state"]`'s `WorktreeStateInfo` asdict) | **Same fact, different freshness — shape (i) target** |
| `title` | yes (session-summary refined) | no (session_length only has turn/session counts) | distinct |
| session turn/count | no (separate `_worktree_to_dict` field) | yes (`facts["session_length"]`) | distinct, Path-B-only |
| liveness (mux/bound) | no (separate overlay — see above) | yes (`facts["liveness"]`, `verify_worktree_active`) | distinct, Path-B-only |
| lineage | no | yes (`facts["lineage"]`) | distinct, Path-B-only |
| claims/owner_ref | no | yes (`facts["claims"]`) | distinct, Path-B-only |
| disposition (title/summary/follow_up/paused/history) | no | yes (`facts["disposition"]`) | distinct, Path-B-only |

Only the git-classification fact (`state` + its `ahead`/`behind`/`dirty`/
`branch_drift`/`current_branch` companions) is actually duplicated between
Path A and Path B — confirming shape (i)'s premise that the two can share
exactly that one sub-computation (`git_ops.classify_worktree`, parametrized
by `fetch: bool`) while each keeps its other, non-overlapping facts
entirely its own.

## Status

6a complete as scoped: both consumer dataflows traced, the two additional
findings (third compute path at `current_worktree_status`; the unconditional
`live` override) confirmed/ruled out as direct oscillation mechanisms, and
the consolidation shape decided (shape (i)). Not yet done: 6b
implementation, the separately-scoped oscillation fix above, 6c's
delegation test, 6d's snapshot/stream closing decision.
