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

## The actual oscillation mechanism (traced, re-confirmed after review correction)

**Revision note (2026-10-08):** this section's first draft claimed the
classify pass unconditionally "corrects back" to the stale git state while
a session stays live, and that removing `_overlay_cached_state`'s `state`
override alone would fix the rendered display. This repo's own review
(PR #5659, round 1) correctly disproved both claims with specific line
references — see "What review round 1 disproved" below. This section is
rewritten with the actual, code-confirmed mechanism found on re-trace.

The reported symptom — a Worktrees-pivot row rendering `MERGED` → `WIP`/
`ACTIVE` → back to `MERGED`, with no real underlying change — is **not**
two compute paths disagreeing on git state (Paths B and C remain confirmed
uninvolved, as above). It is a **field-population gap between the two
render phases' payloads**, specifically around mux-session visibility:

1. **First paint (`classify=False`, cache-only).** The production call
   chain is `worktree_manager.production_picker.picker_tui.data_local.load()`
   → `engine_client.list_worktree_rows(cache_only=True)` → subprocess
   `agent-worktrees list --json --cache-only` → `list_cli.cmd_list`'s
   `--cache-only` branch (confirmed, `list_cli.py` ~452-468): it calls
   `_worktree_to_dict(rec, include_profile_assignment_history=...)` **with
   no `mux_info` and no `session_ctx` arguments at all**, then
   `picker_support.data_local._overlay_cached_state(raw, rec)`.
   - `_worktree_to_dict` (`__main__.py:1105`+) only sets `mux_session`/
     `mux_attached` when `mux_info is not None` (line 1333-1335), and only
     sets `session_lock_live`/`live_session_ids` from `session_ctx
     .active_sessions` when `session_ctx is not None` — **neither
     condition holds in the cache-only branch**, so this payload can
     *never* carry `mux_session`/`mux_attached`, regardless of the
     worktree's actual mux state.
   - The only liveness signals this branch *can* carry are
     `_overlay_cached_state`'s own direct `sessions
     .worktree_session_lock_state(rec)` check (a targeted
     `inuse.<pid>.lock` glob) and `_worktree_to_dict`'s always-computed,
     already-freshness-gated `_fresh_bound_live_hint(rec)` →
     `session_bound_live`.
2. **Authoritative populate (`classify=True`).** `list_worktree_rows
   (classify=True, mux_details=True, cache_only=False)` →
   `list_cli._build_list_json_payload`, which **does** pass
   `mux_map.get(rec.worktree_id)` (from a batched `sessions
   .mux_status_many` call) and `session_ctx` (from `sessions
   .scan_sessions_fast`) into `_worktree_to_dict` — so `mux_session`/
   `mux_attached`/`session_lock_live` (via `session_ctx`) are populated
   whenever genuinely true. Separately, `_classify_records`'s
   `active_paths` (`_build_active_paths`, which unions lock files,
   `session_ctx.active_sessions`, the batched mux-session list, the fresh
   `bound_live` hint, and bridge-lock) makes `git_ops.classify_worktree`
   return `ACTIVE` outright for any of these signals — confirmed via
   `active_paths is not None` short-circuiting *before* any git status
   check (`git_ops.py:391-405`).

**The confirmed divergence**: a worktree that is live **only** via an
attached mux session — no registered `inuse.<pid>.lock` in that worktree's
own session directory, no fresh cached `bound_live` hint (e.g. a plain
mux-attached terminal the bare-resume reconciler hasn't stamped, or one
whose lock file genuinely lives in a different session dir than the one
`worktree_session_lock_state` scans) — renders correctly on the classify
pass (`ACTIVE`, via either `active_paths` or the `mux_attached` marker
`derive.py`'s `_state()` checks), but on the **next** cache-only first
paint the mux signal is invisible to that payload entirely, so it falls
back to whatever `rec.git_state` was last classified+stamped as (e.g.
`MERGED`, if the branch is in fact merged) — then flips back to `ACTIVE`
on the next classify populate, and so on. This is a real, repeating,
code-confirmed cycle, matching the reported "after a brief wait, swings
back" recurrence (it recurs on every first-paint → populate → first-paint
render cycle, not once).

### What review round 1 disproved, and why it matters

- **"The classify pass corrects back to stale state while live" is false
  in general.** `_classify_daemon_compute`/`_classify_records_live` builds
  `active_paths` from `_build_active_paths` and passes it into
  `_classify_one_record` → `git_ops.classify_worktree`, which returns
  `ACTIVE` **before** doing any git status check when the worktree's path
  is in `active_paths` (`git_ops.py:391-405`). So whenever the classify
  pass's own (broader) liveness check agrees the worktree is live, it also
  renders `ACTIVE` — it does not revert to `MERGED` merely because the
  session continued. The genuine revert only happens when the classify
  pass's liveness check **misses** a signal the UI otherwise relies on —
  which is exactly the mux-visibility gap above, not a guaranteed
  "always corrects back while live" behavior.
- **"Stop overwriting `raw['state']`" would not change the display.**
  `picker_support/derive.py`'s `_state()` (the actual row-label function)
  checks `mux_session`/`mux_attached`/`session_lock_live`/
  `session_bound_live`/etc. **before** ever examining `state` — so for the
  lock-file and bound-live-hint liveness signals specifically, a live
  worktree already renders `ACTIVE` via these marker fields regardless of
  what `_overlay_cached_state` does to `state`. The originally-proposed
  "don't overwrite `state`" fix would do nothing for those two signals; it
  would only ever have mattered for a liveness source `_overlay_cached_state`
  detects but no marker field exists for — which is not the case here
  (it already sets `session_lock_live` for the lock case). The real,
  necessary fix is the mux-visibility gap identified above, not the
  `state`-field overwrite this draft originally targeted.

## Recommendation for 6b (updated scope)

Two distinct fixes are needed, and they should **not** be conflated into
a single change:

1. **The structural consolidation** the Phase 6 title names: merge Path A
   (`classify_daemon`/`_classify_records`, no-fetch) and Path B
   (`worktree_status_compute.compute`, fetch-confirmed) so they share one
   leaf computation, per the existing checklist's shape (i) — generalize
   `worktree_status_compute` with a `fetch: bool` parameter, and make
   `classify_daemon`'s fast, coalesced path a thin no-fetch view over it.
   Shape (i) is recommended over (ii): `worktree_status_compute.compute()`
   already assembles the richer, more complete fact set (lineage, claims,
   disposition, liveness — Path A/`WorktreeStateInfo` is a strict subset:
   state/ahead/behind/dirty/title/branch_drift), so generalizing the
   richer function to serve the narrower, faster call site is less
   duplication than the reverse.
   - **Neither existing `work_coalescing_singleton` server/rendezvous is
     retired by this shape.** `classify_daemon`'s `CoalescingServer`
     (`KIND = "classify"`) answers a whole-project **batch** request (all
     of a project's records, filtered by `status_filter`/
     `platform_filter`/`all`); `worktree_status_daemon`'s own
     `CoalescingServer` (`KIND = "worktree_status"`) answers a
     **single-worktree** bundle request (`compute(project, worktree_id)`).
     These are genuinely different request granularities serving
     genuinely different consumers (the Picker's batch list render vs.
     agent-dispatch's per-task board lookup) — consolidating the *leaf*
     git-classification call does not collapse these into one server or
     one rendezvous protocol. Both daemons, both `KIND`s, and both sets of
     lock-file rendezvous fields remain live after 6b; only
     `git_ops.classify_worktree` (and its `fetch` parameter) becomes the
     one shared implementation each daemon's own `compute` callback calls
     into.
   - **The shared seam must preserve Path A's own adapter-layer facts**,
     which Path B's `compute()` does not produce at all:
     `active_paths`-forced `ACTIVE` (`classify_worktree`'s own
     `active_paths` parameter, used only by Path A today),
     `_apply_tracking_override`'s FINAL/MERGED closure refinement
     (`__main__.py:603-608`), and `refine_state_with_session`'s `CONVO`
     refinement. These stay in Path A's own wrapper around the shared
     `classify_worktree` call — not inside the generalized function
     itself — so a naive "thin `fetch=False` view" does not silently drop
     any of them and change Picker-rendered states.
   This closes 6b/6c/6d as scoped, and genuinely serves the effort's own
   stated purpose — but **does not by itself fix the reported
   oscillation**, since neither Path A nor Path B was ever the disagreeing
   party for it (Path A already renders `ACTIVE` correctly whenever its
   own liveness check sees it; the gap is in which fields the cache-only
   *first paint* payload carries at all).
2. **The actual oscillation fix** (new, scoped here since 6a's own
   purpose is to confirm/rule out a direct mechanism before consolidation
   starts): give the `--cache-only` branch of `list_cli.cmd_list` a cached
   mux-liveness signal to pass into `_worktree_to_dict`/
   `_overlay_cached_state`, so a worktree whose only liveness source is an
   attached mux session doesn't fall back to a stale `state` on every
   first-paint render. The exact mechanism needs its own short design
   pass (options include: a cached `mux_attached` hint on the tracking
   record, reconciled off the hot path the same way `bound_live` already
   is via `reconcile_bound_live()`; or extending
   `_overlay_cached_state`'s own direct per-record check to include a
   cheap, single-record mux probe). This is a narrow, independent fix — it
   does not require or block on the Path A/B consolidation, and per the
   effort's own prior note should land as its own change, not be read as
   satisfying the structural consolidation claim on `#5555`.

Recommend sequencing: scope and land fix 2 first (small, isolated,
directly answers the operator's reported symptom) as its own PR; then
proceed with 6b/6c/6d for the structural consolidation already scoped.
Both are required before this phase's closing claim on `#5555` is
complete — 6d's own checklist item already requires not letting 6a-6c's
completion alone read as "the Worktrees-pivot half of #5555 is done" if a
known gap is left silently open; this oscillation fix is exactly such a
gap and must not be silently dropped once 6b/6c land.

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

6a complete as scoped: both consumer dataflows traced (plus the third,
`status-segment`, path), the two additional findings (`current_worktree_status`'s
non-daemon pass; `_overlay_cached_state`'s `live` override) investigated —
the first ruled out, the second revised after review correction into the
actual confirmed mechanism (the cache-only payload's mux-visibility gap)
— and the consolidation shape decided (shape (i), with neither
`work_coalescing_singleton` server retired). This closes 6a's own scope
for 6b/6c (the shared compute seam's implementation target and test
surface); it does **not** close 6d — sharing the polling compute seam
between Path A and Path B does not touch 6d's own still-pending
snapshot/stream decision (`list --json --classify` remaining a polled call
vs. `pivot-streaming-transport`'s `stream`/`subscribe` contract), which
stays open and unaffected by anything in this trace. Not yet done: 6b
implementation, the separately-scoped mux-visibility oscillation fix
above, 6c's delegation test, and 6d's own snapshot/stream closing
decision.

## Documentation impact

This change is effort-tracking documentation only: it updates this
effort's own `README.md` (Phase 6 heading and checklist status, a new
Journal entry) and adds this new `phase-6-audit.md` file under the
effort's own `efforts/active/` tree. No source code changes, no
user-facing behavior changes, and no authoritative architecture/behavior
documentation outside this effort's own tracking is affected.
