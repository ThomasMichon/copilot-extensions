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
  `__main__.py:997`. This path is **fetch-free by construction**:
  `behind`/`ahead` reflect the last fetch, never a fresh one. ~5 git calls/
  worktree, batched.
- Writes back: `list_cli._build_list_json_payload` calls
  `picker_support.data_local._stamp_from_raw` after a classify pass, which
  persists `git_state=raw.get("state")` onto the tracking record (so the
  *next* cache-only pass's baseline reflects this run's classify result).

### Path B — `worktree_status_compute.compute()` / `worktree_status_daemon` (agent-dispatch's Tasks-board bundle)

- Entry: `worktree_status_compute.compute(project, worktree_id)`, wrapped
  with a TTL cache by `worktree_status_daemon.py` (not read in this pass,
  wiring confirmed via `__main__.py`'s re-export of `_worktree_status_compute
  = worktree_status_compute.compute`).
- Calls `git_ops.classify_worktree(..., fetch=True, ...)` — this
  **requests** a fetch on every call (`fetch=True` passed unconditionally),
  but is not guaranteed to have *performed* one: `WorktreeStateInfo
  .fetch_requested`/`fetch_failed` (`git_ops.py:262-268`) distinguish a
  genuine fetch attempt from an early return (e.g. a missing/zombie
  checkout) or a failed fetch — so this path is **fetch-requesting**, with
  `git_confirmed = not (info.fetch_requested and info.fetch_failed)`
  gating the bundle's own `confirmed` flag, not an unconditional guarantee
  of fresh data. This is still the opposite freshness *intent* from Path
  A's fetch-free-by-construction contract, by design (a per-worktree
  bundle read, not a batch list render).
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
  `status_bar_cli.cmd_status_segment`'s `--json` branch, which calls
  **`_status_segment_json` directly** — not `_render_status_segment`
  (that function renders the live, every-`status-interval` mux bar; the
  JSON path is a deliberate sibling with its own, separate
  `git_ops.classify_worktree` call, per its own docstring: "kept as a
  deliberate sibling... rather than a shared refactor").
- Confirmed: `_status_segment_json` calls `git_ops.classify_worktree(...,
  fetch=bool(fetch), ..., active_paths=None)` **directly** — no daemon, no
  `_classify_records`, no coalescing. This is a third, fully independent
  inline compute, fetch-free by default (same as Path A) but with its own
  code path end to end.
- Crucially, it passes **`active_paths=None`**, and its own docstring says
  this renders "raw git disposition — never ACTIVE." This path is
  structurally immune to the live-override oscillation described below:
  it never promotes a row to `active` based on a live session at all.
- **Conclusion: Path C is not implicated in the reported oscillation**
  either — it is a different entry point (single-worktree status bar /
  Mux Companion), not the Worktrees-pivot row list, and it deliberately
  avoids the liveness-override mechanism that causes the symptom.

## The oscillation mechanism — what's confirmed, and what remains open

The reported symptom — a Worktrees-pivot row rendering `MERGED` → `WIP`/
`ACTIVE` → back to `MERGED`, with no real underlying change — is **not**
two compute paths disagreeing on git state (Paths B and C remain confirmed
uninvolved, as above). This section states only what is actually confirmed
by code, and names what is not yet confirmed (see the Journal for the
dated investigation history).

**Confirmed, by code:**

- The classify pass's own `active_paths` (`_build_active_paths`, unioning
  lock files, `session_ctx.active_sessions`, the batched mux-session list,
  the fresh `bound_live` hint, and bridge-lock) makes
  `git_ops.classify_worktree` return `ACTIVE` **before** any git status
  check (`git_ops.py:391-405`) whenever it agrees the worktree is live —
  so the classify pass does not generically "correct back" to stale state
  while a session is genuinely live by every signal it checks.
- `_worktree_to_dict`'s `_classify_records`/`_stamp_from_raw` write-back
  (`picker_support/data_local.py:379-394`) **attempts** to persist
  whatever `state` the classify pass computed (including `"active"`) onto
  `rec.git_state` via `tracking._STAMP_QUEUE` (async, off the render
  thread), and that queue's `flush()` is registered via `atexit`
  (`tracking.py:3763`) — including on the CLI's hard-exit path
  (`_shutdown_exit.run_and_exit` explicitly runs registered `atexit`
  handlers) — so every queued mutation is guaranteed to be *attempted*
  exactly once before the subprocess exits. **This does not mean each
  attempt succeeds**: `_apply_session_state_stamp` acquires its
  per-path `_RecordLock` **non-blocking** and silently returns `False`
  (write skipped, no retry) on contention — "best-effort background
  writer... skip on contention so a sweep never blocks a critical
  updater; the next populate re-stamps the (idempotent) cache"
  (`tracking.py:3708-3710`). So on an ordinary, clean process exit, the
  classify pass's correctly-computed `"active"` state can still fail to
  reach `rec.git_state` if anything else (another concurrent `list`
  invocation, a resume, a sweep) holds that record's lock at the moment
  the queued write is attempted — leaving `rec.git_state` at whatever it
  was *before* this classify pass, with no error surfaced anywhere.
- The cache-only (`list_cli.cmd_list`'s `--cache-only` branch) payload
  never carries `mux_session`/`mux_attached` (confirmed: it calls
  `_worktree_to_dict(rec, ...)` with no `mux_info`/`session_ctx` at all,
  and those fields are only set when those arguments are given —
  `__main__.py:1333-1335`). This is a real, confirmed field-coverage gap
  between the two payloads, and — combined with the contended-write skip
  above — is now a fully self-contained, code-confirmed mechanism on its
  own, independent of any mux-session timing question: a classify pass
  computes the correct state (e.g. `"active"`), its stamp write is
  skipped under contention, `rec.git_state` stays at its prior value
  (e.g. `"MERGED"`), the next cache-only paint renders that stale value
  (no mux marker to override it), the *next* classify pass recomputes
  `"active"` correctly again (git state itself never changed) and this
  time its write succeeds, the cache-only paint after that renders stale
  `"MERGED"` again only if *that* write was also skipped — i.e. the
  flap's exact cadence tracks write-lock contention, not a liveness
  signal disagreeing with itself.

**Not yet confirmed — pinning the frequency/cause of the lock contention,
not the mechanism itself, which is now code-confirmed above:**

The contended-write skip is a real, sufficient mechanism confirmed
directly from the code; what is not yet confirmed is how *often* it
actually fires in practice — i.e. how frequently concurrent `list --json`
invocations (or any other `_RecordLock` holder) genuinely contend for the
same worktree's record during a real Picker refresh cycle, which would
explain the reported recurrence cadence ("after a brief wait, swings back
again"). The separate, lower-confidence candidate from the prior revision
of this section — a timing lag in when a worktree's mux session is
created/destroyed (`_build_active_paths` tests session existence via
`sessions._list_mux_sessions()`, not attach/detach client-count changes)
or in the registered-lock transition `worktree_session_lock_state` checks
— remains a secondary, unconfirmed contributor, not the primary one.
Confirming either's actual contribution to the reported cadence requires
live reproduction (observing `rec.git_state` and `_apply_session_state_stamp`'s
own return value across repeated render cycles on a real worktree under
load), not more static tracing — but the contended-write skip alone is
already sufficient to explain the symptom without assuming any mux/session
timing disagreement at all. 6a's purpose was the dataflow trace and the
consolidation-shape decision (both done below); pinning the exact
contention frequency is scoped as a prerequisite check for the narrow
oscillation fix in 6b's recommendation, not asserted as already resolved.

## Recommendation for 6b (updated scope)

Two distinct work items are needed, and they should **not** be conflated
into a single change:

1. **The structural consolidation** the Phase 6 title names. Both Path A
   and Path B already call the same leaf (`git_ops.classify_worktree`,
   with different `fetch` values) — so sharing *only* that leaf is not a
   new seam at all, and a 6c delegation test asserting delegation to it
   would trivially pass today, before any real consolidation. The
   genuinely new, narrow seam worth building is one level up: factor
   `_classify_one_record`'s **full** wrapper (`active_paths` handling,
   the `classify_worktree` call itself, `_apply_tracking_override`'s
   FINAL/MERGED closure refinement, and `refine_state_with_session`'s
   `CONVO` refinement) into one function parametrized by `fetch: bool` —
   call it `_compute_worktree_git_facts(record, *, fetch, active_paths,
   session_ctx, repo) -> WorktreeStateInfo` — and have **both**
   `_classify_daemon_compute` (today's Path A caller, `fetch=False`) *and*
   `worktree_status_compute.compute()`'s own `facts["git_state"]`
   assembly (today's Path B caller, `fetch=True`) call it, instead of
   `compute()` calling `git_ops.classify_worktree` directly. This is
   genuinely narrower than routing all of Path A through Path B's full
   `compute()` bundle (which would wrongly force Path A's fast batch-list
   render to also pay for `compute()`'s lineage/liveness/claims/
   disposition assembly on every row) — only the richer, already-correct
   git-state wrapper is shared; `compute()` keeps assembling its other
   facts exactly as today, now just by calling the shared helper for one
   of them. **`compute()` must keep passing `active_paths=None`** to the
   shared helper: for a live worktree, `active_paths` short-circuits to a
   zero-valued `ACTIVE` *before* its requested fetch even runs, which
   would silently replace `compute()`'s actual git disposition fact with
   a placeholder — the bundle already has its own separate
   `facts["liveness"]` fact for this, so `active_paths` stays an opt-in
   parameter only Path A passes a real value for, not something both
   callers share. This also gives Path B's bundle the closure/
   `CONVO` refinement it currently lacks, which only ever adds a more
   complete answer for its own consumer (agent-dispatch's Tasks board),
   never changes Path A's existing contract.
   - **Neither existing `work_coalescing_singleton` server/rendezvous is
     retired by this shape.** `classify_daemon`'s `CoalescingServer`
     (`KIND = "classify"`) answers a whole-project **batch** request;
     `worktree_status_daemon`'s own `CoalescingServer`
     (`KIND = "worktree_status"`) answers a **single-worktree** bundle
     request. These remain genuinely different request granularities for
     genuinely different consumers — consolidating the shared helper does
     not collapse them into one server or protocol. Both daemons, both
     `KIND`s, and both sets of lock-file rendezvous fields remain live
     after 6b; only the one new `_compute_worktree_git_facts` helper is
     shared between their two `compute` callbacks.
   This closes 6b/6c/6d as scoped, and genuinely serves the effort's own
   stated purpose — but **does not by itself fix the reported
   oscillation**, since the oscillation's actual trigger (still open,
   above) was never in disagreement between Path A and Path B to begin
   with — both already compute from the identical leaf.
2. **The oscillation fix** (new, scoped here since 6a's own purpose is to
   establish the dataflow before consolidation starts): two independent,
   additive fixes, in priority order —
   - **Primary: make the classify pass's stamp write-back retry on
     contention, or block briefly, instead of silently skipping.**
     `_apply_session_state_stamp`'s non-blocking `_RecordLock` acquire
     returning `False` on contention (`tracking.py:3708-3710`) is a fully
     self-contained, code-confirmed mechanism on its own — the classify
     pass can compute the correct state and still never persist it on an
     ordinary clean exit, with no error surfaced anywhere, leaving the
     next cache-only paint reading stale data. A bounded retry (a short
     blocking wait, or a single re-attempt after a brief delay, inside
     `_StampWriteQueue._apply`) closes this without changing the
     non-blocking contract's original purpose (never blocking a
     *foreground* critical updater) — the stamp write is already off the
     render thread, so a short bounded wait there costs nothing a
     render-thread caller would notice.
   - **Secondary: surface the existing cached mux-liveness signal the
     tracking record already carries** — `WorktreeRecord.mux_live`/
     `mux_live_at`, refreshed by `reconcile_bound_live()` via its own
     `mux_status_many` call (a **separate** observation from
     `_build_active_paths`'s own direct `_list_mux_sessions()` call —
     `__main__.py:453-477` — not the same batched call; both ultimately
     read the same underlying mux primitive but as two distinct
     point-in-time observations) and already freshness-gated by
     `_fresh_mux_live_hint()` (`picker_support/data_local.py:112-123`,
     `__main__.py:524-548`) — into the cache-only branch's
     `_worktree_to_dict`/`_overlay_cached_state` call, rather than adding
     a second, new liveness source or probe. This closes the remaining
     field-coverage gap independent of the primary fix, for a worktree
     whose only liveness source is genuinely an attached mux session.

Both are narrow, independent fixes — neither requires or blocks on the
Path A/B consolidation, and per the effort's own prior note both should
land as their own change(s), not be read as satisfying the structural
consolidation claim on `#5555`.

Recommend sequencing: land the primary (contended-write retry) fix first
— it is already fully code-confirmed, with no further live reproduction
needed to justify it, unlike the prior revision of this section assumed.
Live reproduction remains useful afterward to confirm how much of the
reported recurrence it alone resolves, before deciding whether the
secondary (mux-visibility) fix is still needed. Proceed with 6b/6c/6d for
the structural consolidation in parallel, since it is independently
justified regardless of the oscillation fix's own scheduling. All of this
is required before this phase's closing claim on `#5555` is complete —
6d's own checklist item already requires not letting 6a-6c's completion
alone read as "the Worktrees-pivot half of #5555 is done" if a known gap
is left silently open; this oscillation fix is exactly such a gap and
must not be silently dropped once 6b/6c land.

## Enumerated facts: Path A (`WorktreeStateInfo`) vs. Path B (`compute()` bundle)

| Fact | Path A (`classify_daemon`) | Path B (`worktree_status_compute`) | Overlap? |
|---|---|---|---|
| `state` (git disposition) | yes (fetch-free) | yes, via `facts["git_state"]` (fetch-requesting) | **Same leaf call today; shared wrapper is 6b's actual target** |
| `ahead`/`behind`/`dirty`/`branch_drift`/`current_branch` | yes | yes (inside `facts["git_state"]`'s `WorktreeStateInfo` asdict) | **Same leaf call today; shared wrapper is 6b's actual target** |
| `active_paths`-forced `ACTIVE` | yes (Path A only) | no | Path-A-only; stays so after 6b (`compute()` must pass `active_paths=None`) |
| closure/`CONVO` refinement | yes | no | Path-A-only today; 6b's shared wrapper extends this to Path B |
| `title` | yes (session-summary refined) | no (session_length only has turn/session counts) | distinct |
| session turn/count | no (separate `_worktree_to_dict` field) | yes (`facts["session_length"]`) | distinct, Path-B-only |
| liveness (mux/bound) | no (separate overlay — see above) | yes (`facts["liveness"]`, `verify_worktree_active`) | distinct, Path-B-only |
| lineage | no | yes (`facts["lineage"]`) | distinct, Path-B-only |
| claims/owner_ref | no | yes (`facts["claims"]`) | distinct, Path-B-only |
| disposition (title/summary/follow_up/paused/history) | no | yes (`facts["disposition"]`) | distinct, Path-B-only |

Both paths already call the same leaf (`git_ops.classify_worktree`) today
— the git-classification fact row above is not a candidate for a *new*
seam on its own; 6b's actual target is the richer wrapper (active_paths/
closure/CONVO) one level up, which Path A already has and Path B does
not, per the Recommendation above.

## Status

**Cache-stamp follow-on:** the background worker now waits boundedly for the
exclusive record lock and warns on timeout/write failure; the synchronous
best-effort surface is unchanged. Cross-process regression and real
interpreter-exit tests prove recovery after short-lived contention and
preservation of concurrent lifecycle fields. This addresses the dropped-write
case described above, not the full live recurrence or daemon-authority gate.
The original recommendations remain the design record; shared computation
and snapshot/stream authority are still pending.

6a complete as scoped: all three consumer dataflows traced, the two
additional findings from before this session (`current_worktree_status`'s
non-daemon pass; `_overlay_cached_state`'s `live` override) investigated —
the first ruled out; the second resolved into a fully code-confirmed
primary mechanism (`_apply_session_state_stamp`'s silent, no-retry,
contended-write skip), with a secondary field-coverage gap and the
contention's actual real-world frequency left for live reproduction, not
asserted as resolved — and the consolidation shape decided (6b's actual
new seam is the `active_paths`/closure/CONVO-refining wrapper one level
above the shared leaf call, with `compute()` keeping `active_paths=None`
and neither `work_coalescing_singleton` server retired). This closes 6a's
own scope for 6b/6c (the shared seam's implementation target and test
surface); it does **not** close 6d — sharing a compute seam between Path A
and Path B does not touch 6d's own still-pending snapshot/stream decision
(`list --json --classify` remaining a polled call vs.
`pivot-streaming-transport`'s `stream`/`subscribe` contract), which stays
open and unaffected by anything in this trace. Not yet done: landing the
primary (contended-write retry) and secondary (mux-visibility) oscillation
fixes, confirming their real-world contribution via live reproduction,
6b implementation, 6c's delegation test, and 6d's own snapshot/stream
closing decision.

## Documentation impact

This change is effort-tracking documentation only: it updates this
effort's own `README.md` (Phase 6 heading and checklist status, a new
Journal entry) and adds this new `phase-6-audit.md` file under the
effort's own `efforts/active/` tree. No source code changes, no
user-facing behavior changes, and no authoritative architecture/behavior
documentation outside this effort's own tracking is affected.
