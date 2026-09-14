# Cleanup TOCTOU Revalidation Hardening

- **Slug:** `cleanup-toctou-revalidation`
- **Repo:** copilot-extensions
- **Branch(es):** single working branch, one participant
- **Created:** 2026-09-14
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** [`visions/plugins/agent-worktrees/README.md`](../../../visions/plugins/agent-worktrees/README.md) §
  `contribution-aware-lifecycle` ("...prove content safe before cleanup") and
  [`docs/patterns/ephemeral-process-reaping.md`](../../../docs/patterns/ephemeral-process-reaping.md)
  ("corroborate before acting" — require an authoritative, fresh observation
  before tearing anything down). This effort is vision-closing: it makes the
  actual cleanup-reaper behavior match both standing statements, which today
  it only partially does.
- **Umbrella issue:** #2640
- **Sub-issues:** _to be filed once the Plan below is reviewed and phases are ready to claim_

## Guiding Intent

`agent-worktrees cleanup` deletes worktrees. Between the moment it decides a
worktree is safe to delete and the moment it actually deletes it, the world
can change — a dirty edit lands, a session attaches, a claim reopens the
record. The system must never act on a stale safety decision: whatever made a
worktree "safe to reap" must still be true **at the instant of deletion**, not
merely at scan time. PR #2635 established this principle for one path (batch
`cleanup --clean`, and only for two of its several safety signals); this
effort finishes it properly, as one deliberate design pass rather than another
round of reactive per-finding patches.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementer | Sole driver — investigation, design, implementation, tests | a copilot-extensions worktree |

## Coordination

- **Topology:** single participant, single working branch (no delegation needed at this scope).
- **Host (owns PRs):** Implementer.
- **Delegates:** none.
- **Handoff:** n/a.

## Context

PR [#2635](https://github.com/ThomasMichon/copilot-extensions/pull/2635) fixed
the core bug: a worktree finalized earlier and modified afterward carried a
stale `rec.status == "finalized"` tracking status, and both
`_apply_tracking_override` and `prune.cleanup_disposition` trusted that status
without checking `info.dirty` — so plain `cleanup --clean` could delete
genuinely unlanded, uncommitted content with no `--force` involved at all.
That PR, across five review rounds, fixed:

- the finalized-status override masking `DIRTY` (and `ORPHAN`-with-dirty, and
  a cache-reconstructed-state case with `state == DIRTY, dirty == 0`);
- the same independent bug inside `cleanup_disposition` itself;
- a first-pass **under-lock revalidation** (`_revalidate_before_reap`) for the
  **batch** `cleanup --clean` path only, re-checking **dirty and active**
  right before each reap.

During that PR's final review pass, `copilot-pull-request-reviewer` surfaced
three further gaps in the same family, deliberately deferred rather than
expanding an already five-round PR further:

1. **`reap_one` (single-worktree `cleanup --worktree-id <id>`) has no
   revalidation at all.** It classifies before taking the lifecycle lock,
   then reaps after checking only hosted-session metadata. The exact race
   `_revalidate_before_reap` closed for the batch path is wide open here.
2. **`_revalidate_before_reap` itself uses a stale `active_paths` snapshot.**
   That snapshot (built by `_build_active_paths`) is captured once, before the
   finalization lock. A mux/lock session that attaches to a worktree *after*
   the initial scan is invisible to the revalidation's fresh git
   classification, so a clean finalized record can still be reaped out from
   under a newly attached session.
3. **Only dirty/active are re-checked — not the complete safety decision.**
   Between the initial scan and the lock, a candidate can also gain a
   committed WIP change, finish a conversation (updating `session_turns`), or
   acquire a held claim/follow-up that reopens its record. All of those
   should block cleanup (the WIP gate, `--include-conversations` gate, the
   held-claims/follow-up/paired-sibling gates in `cleanup_disposition`) — but
   none of them are re-checked today, only dirty/active are.

This effort's own review (rounds 3–4, on the plan itself) surfaced four more
concrete findings that sharpen the above rather than replace it — folded in
as explicit Plan items rather than re-litigated in prose here:

- `cleanup_disposition`'s `rec.status == "finalized" or info.state ==
  COMPLETED` shortcut runs **before** its `WIP` and `conversation-only`
  branches — so even a full "re-run cleanup_disposition" does not, by
  itself, catch a finalized record that gained a committed WIP change or
  fresh conversation turns after the scan. This needs the same kind of
  ordering fix PR #2635 already made for `dirty` (moving that gate ahead of
  the finalized shortcut), extended to `WIP`/`conversation-only`.
- `_build_active_paths` itself has a stale-cache fallback: when the batched
  mux query is unavailable, it treats a cached `mux_live=False` as
  authoritative and skips the `has_mux_session` fallback check — so a
  session attached after that false stamp is still invisible, independent
  of when `active_paths` is (re)built.
- Other record mutators — session registration, claim/follow-up writes —
  use a **per-record lock**, not `FinalizeLock`. "Hold `FinalizeLock`
  across the final read and the reap" (Phase 2's Option 1) only fences
  against this tool's *other* finalization/cleanup instances; it does not
  fence against those writers, which can still change safety inputs after
  the final read. Closing that gap needs either those writers to also
  honor `FinalizeLock`, or a target-record lock/CAS with an explicitly
  defined lock order.
- There is a **third** ordinary reaper beyond the two originally scoped
  here: `sweep_finished_session_worktrees` builds its candidates before
  `FinalizeLock` and calls `_reap_worktree` under the lock with no fresh
  safety decision — the same stale check-to-delete race, in the
  no-daemon/picker/session-end auto-clean path. In scope alongside the
  batch and single-item paths below; "two call sites" throughout this
  document should be read as **three**.

Filed as umbrella issue
[#2640](https://github.com/ThomasMichon/copilot-extensions/issues/2640).
Originating report: a private downstream tracker (not canonical here).

### Why this earns a real design pass, not another quick patch

The pattern so far has been: fix the symptom the reviewer just found, in the
exact spot it was found. That produced a working batch-path fix, but it also
produced the very gaps this effort exists to close — `reap_one` (and, per the
findings above, `sweep_finished_session_worktrees`) never got the fix because
each is a *separate* call site that happens to reimplement the same "check
then act" shape. Patching gap-by-gap in-place risks a fourth and fifth gap
surfacing the same way — as this very plan's own review rounds just
demonstrated. The fix that actually closes the class of bug is **one
canonical, single-sourced safety recheck**, called from every site that reaps
a worktree — so there is no other call site left to drift out of sync.

## Request

> For the follow-up, please draft and effort and break down the problem so we
> can solve it properly

(Operator instruction, captured verbatim, following the merge of PR #2635 and
the decision to defer the three review findings above to a tracked follow-up
rather than expand that PR further.)

## Plan

### Phase 1 — Investigate the current liveness/safety-signal landscape

Before consolidating anything, map what already exists so the fix doesn't
duplicate or fight an existing mechanism:

- [x] Enumerate every signal `cleanup_disposition` currently consults
      (`info.state`/`info.dirty`, `rec.status`, `turn_count`, held resource
      claims via the injected `claimant_alive` probe, itemized follow-ups,
      paired-sibling finality, PR state via `assess`) and, for each, whether
      it can be **cheaply re-derived locally** (no network) at reap time, or
      requires a network/remote round-trip that should stay scan-time-only
      by design or needs an explicit bounded-wait policy under the lock.
      **Findings (see Journal):** all signals are cheap-local (a fresh
      `git_ops.classify_worktree(fetch=False, ...)` call, a fresh
      `tracking.load_record`, or a plain record-field read) **except**
      `claimant_alive`, which is cheap only for a same-machine owner and
      degrades to a bounded (8 s) SSH round-trip for a cross-machine owner.
      PR state (`rec.prs`/`assess`'s PR-aware branch) is the one signal that
      must **stay scan-time-only**: it is populated by `reconcile_and_persist_best_effort`
      calling out to the PR provider, and Phase 2 explicitly must not add a
      network reconciliation under the lock.
- [x] **`claimant_alive`** (`resolve_claimant_alive`) specifically: this probe
      can perform a **cross-machine SSH check** and intentionally returns
      *unknown* on failure — it is not a cheap local read like the others.
      Decide and document an explicit under-lock policy for it: a bounded
      timeout, and whether "unknown" fails closed (treat as still-live, don't
      reap) or is otherwise handled — the design must not silently drop this
      gate, and must not let the lifecycle lock block indefinitely on remote
      I/O. **Findings (see Journal):** `claimant.py` already bounds the
      remote path to `_REMOTE_TIMEOUT = 8.0` seconds and the tri-state
      contract (`True`/`False`/`None`) already fails closed — `assess()`
      only lets a resource fall through to its content verdict when
      `claimant_alive(...)` returns `False` (confirmed gone); `True` *or*
      `None` (unconfirmed) both spare it. So the policy exists today; the
      open design question Phase 2 must settle is narrower than "invent a
      policy" — it's "do we hold `FinalizeLock` for up to 8 s of SSH I/O per
      candidate," since today's `reap_one` non-forced path already calls
      `cleanup_disposition` (and therefore `resolve_claimant_alive`)
      pre-lock, so wiring the *consolidated* revalidation to call it again
      under the lock is what would newly introduce that cost.
- [x] Trace exactly how `_hosted_session_blocks_cleanup(latest)` (already
      called under the lock today, in both `cmd_cleanup` and presumably
      `reap_one`) relates to `active_paths`/mux-session liveness — are these
      the *same* liveness signal read two different ways, or genuinely
      independent? Document the answer; don't build a second, divergent
      liveness check if one already exists under the lock. **Findings (see
      Journal):** genuinely independent. `_hosted_session_blocks_cleanup`
      (`__main__.py:377`) only looks at record fields — `session_backend_opaque`,
      `execution_leg_opaque`, and `tracking.derive_execution_leg(record).state
      in {"active", "unknown"}` — none of which involve `active_paths`, mux,
      or lock files. It covers **hosted**/cloud-backend Copilot sessions that
      leave no local mux/lock trace. `active_paths` (`_build_active_paths`,
      `__main__.py:388`) instead covers local lock-file sessions, batched
      tmux/psmux mux sessions, cached `mux_live`/`bound_live` hints, and
      bridge-lock liveness. Both signals are already consulted (the former
      fresh, under the lock, in `reap_one`/`cmd_cleanup`; the latter only
      pre-lock today) — the consolidated function needs **both**, not a
      merged/replaced single check.
- [x] Confirm `reap_one`'s current shape precisely, **including its `force`
      path**: `reap_one` deliberately bypasses `cleanup_disposition` when
      `force=True`, while still rejecting an active session — the skill
      documents `--force` as an individually-verified exception, not
      something this effort should remove or weaken. Document the forced and
      non-forced contracts **separately** so Phase 2's design doesn't
      conflate them. **Findings (see Journal):** confirmed exactly as
      described, with one added precision — the *initial* ACTIVE check
      (`info.state == git_ops.WorktreeState.ACTIVE`, `__main__.py:13580`) is
      shared by **both** forced and non-forced paths and runs **before** the
      lock, against the pre-lock `active_paths` snapshot built at
      `__main__.py:13558`. **Non-forced:** additionally calls
      `prune.cleanup_disposition` pre-lock (`__main__.py:13612`); under the
      lock it only re-checks `_hosted_session_blocks_cleanup(latest)`
      (`__main__.py:13641`) — no fresh git reclassification, no fresh
      `active_paths`, no fresh `claimant_alive`/held-claims/follow-up/branch-merge
      recheck. This is gap #1: zero revalidation of the actual disposition.
      **Forced:** skips `cleanup_disposition` entirely (the `if not force:`
      guard at `__main__.py:13595`); still subject to the same pre-lock
      ACTIVE check and the same under-lock fresh `_hosted_session_blocks_cleanup`
      check as the non-forced path — so `--force` today bypasses only the
      *disposition* (dirty/WIP/claims/etc.), never the active-session
      rejection, matching the documented contract. The gap specific to
      forced mode: the *initial* ACTIVE check it does share is still
      evaluated against the stale pre-lock `active_paths`, so a session that
      attaches between the scan and the lock is invisible to `--force` too
      (Phase 2/4's dedicated forced-attach-after-scan case).

### Phase 2 — Design the consolidated revalidation function

- [x] Design one function (tentatively `_revalidate_cleanup_safety`,
      replacing `_revalidate_before_reap`) that, given a worktree id, does
      **all** of: reload the tracking record fresh, re-classify git state
      fresh (`classify_worktree`, no fetch), refresh the liveness signal(s)
      identified in Phase 1 scoped to just this one worktree (not a full
      fleet rescan), re-derive `turn_count` from current session state, and
      run the **complete** `cleanup_disposition` against all of that —
      returning the fresh record, classification, disposition, and reason
      rather than a narrowed dirty/active-only check.
      **Decision:** signature
      `_revalidate_cleanup_safety(wt_id: str, *, repo: cfg.RepoConfig,
      force: bool = False) -> RevalidationResult`, taking only the id (not a
      pre-lock record/info) so it cannot accidentally read anything but
      fresh state. Callers acquire `FinalizeLock` (and, for non-forced, the
      per-record lock — see the lock-order decision below) *before* calling
      it, and it is the **sole** place any of the three reapers touches
      `cleanup_disposition`/`classify_worktree`/`resolve_claimant_alive` once
      the lock is held. It builds a **single-worktree** `active_paths` via
      `_build_active_paths([rec], session_ctx)` scoped to just this record
      (not a fleet rescan), matching Phase 1's confirmation that this is
      already how `reap_one` computes it today — only the *timing* (under
      lock vs. before it) changes.
- [x] **"Re-run `cleanup_disposition`" is necessary but not yet sufficient
      by itself — its internal ordering has the same class of bug PR #2635
      fixed for `dirty`, but for `WIP`/`conversation-only`.**
      `cleanup_disposition`'s `rec.status == "finalized" or info.state ==
      COMPLETED` shortcut runs *before* its `WIP` and `conversation-only`
      branches, so a finalized record that gains a committed WIP change or
      fresh conversation turns after the scan is still returned cleanable —
      exactly the bug #2635 fixed for `dirty`/`info.dirty`, recurring for
      two more signals. This design must either reorder those gates ahead
      of the finalized shortcut (mirroring #2635's fix) or otherwise ensure
      the consolidated function checks them before trusting that shortcut —
      "call `cleanup_disposition` again" is not sufficient on its own.
      **Decision:** fix `cleanup_disposition` itself (`prune.py`), not just
      its caller — move the `info.state == S.WIP` and the `empty`/
      `conversation-only` category checks (currently reached only after the
      `rec.status == "finalized" or info.state == COMPLETED` shortcut) ahead
      of that shortcut, mirroring exactly how `#2635` already moved the
      `info.dirty` check ahead of it. This benefits every caller of
      `cleanup_disposition`, not only the revalidator, and keeps there being
      one ordering fix rather than a second one duplicated inside the new
      function.
- [x] `_build_active_paths` has its own stale-cache fallback, independent of
      *when* it's called: when the batched mux query is unavailable, it
      treats a cached `mux_live=False` as authoritative and skips the
      `has_mux_session` fallback check — so a session attached after that
      false cache stamp is invisible even to a freshly-called
      `_build_active_paths`. The liveness refresh this design adds must
      either fix that fallback or use a different, authoritative action-time
      probe instead of relying on `_build_active_paths` as-is.
      **Decision:** fix the fallback in `_build_active_paths` itself
      (`__main__.py:415`-`430`) — when the batched `mux_sessions` query is
      unavailable, always fall through to the authoritative
      `sessions.has_mux_session(rec.worktree_id)` probe regardless of
      whether `_fresh_mux_live_hint` returns `True`, `False`, or `None`; a
      fresh `True` hint can still short-circuit the OR (already correct),
      but a fresh `False` must no longer skip the fallback probe. This is a
      one-line change (drop the `hint is None` guard on the `elif`) that
      fixes the signal for every caller, including callers unrelated to
      this effort's under-lock revalidation — no separate probe needed in
      the new function.
- [x] **The function's return contract must include the fresh record and
      classification, not only the disposition/reason.** Today's callers
      still pass the *pre-lock* record/`info` objects into `_reap_worktree`
      after revalidating — if the revalidator's fresh state isn't threaded
      through as what actually gets reaped, the delete can act on stale
      path, branch, or lifecycle metadata even after a correct fresh
      decision. Make "the reap acts on the revalidated record and
      classification" part of the contract, not just "the decision was
      fresh."
      **Decision:** `RevalidationResult` is a dataclass
      `(cleanable: bool, reason: str, bucket: str, record:
      tracking.WorktreeRecord | None, info: git_ops.WorktreeStateInfo |
      None)` — `record`/`info` populated only when `cleanable`. Every call
      site's `_reap_worktree(rec, info, ...)` is updated to use
      `result.record`/`result.info`, never the pre-lock objects, closing
      the "fresh decision, stale delete" gap named in the plan.
- [x] Decide explicitly between "recompute everything fresh" (this function
      re-decides from scratch) vs. "fail closed on any detected drift"
      (compare a fingerprint of relevant inputs at scan vs. reap time, abort
      on any difference without re-deciding) for inputs that are cheap to
      re-derive locally — the reviewer's review comment left both as
      acceptable; Phase 1's findings on per-signal re-derivation cost should
      settle this per-signal, not necessarily uniformly.
      **Decision:** recompute everything fresh for every signal Phase 1
      classified as cheap-local (git state, record fields, turn count,
      paired-sibling, `active_paths`) — a fingerprint/diff approach adds a
      second code path (compute-then-compare) for no savings when the
      underlying read is already cheap and local. `claimant_alive` is the
      one signal that is *not* uniformly cheap (bounded 8 s SSH for a
      cross-machine owner); it is still recomputed fresh rather than
      fingerprinted, because a fingerprint of "was it alive at scan time"
      is exactly the stale read this effort exists to eliminate — the cost
      is accepted (see the Phase 1 finding above) rather than designed
      around, since it is bounded and only paid once per reap candidate,
      already under the lock in today's non-forced `reap_one` path.
- [x] Explicitly scope what stays out: this function must not perform a
      network PR reconciliation under the lock (that's `--reconcile-prs`'s
      job, at scan time) — confirm this constraint is testable, not just
      assumed.
      **Decision:** `_revalidate_cleanup_safety` never calls
      `prune.reconcile_and_persist_best_effort` or any PR-provider lookup;
      it reads `rec.prs` as already reconciled at scan time (or not
      reconciled at all, in which case a stale `open` fails safe toward
      "don't reap"). Testable via a unit test that patches the PR-provider
      lookup to raise/track-calls and asserts it is never invoked from
      inside the revalidator or from within the `FinalizeLock` critical
      section (Phase 4).
- [x] `prune.cleanup_disposition` deliberately excludes `GONE` — the
      branch-merged-content proof for a missing worktree directory is owned
      by the **caller** (today, both the batch and single-item paths perform
      that proof *before* the lock, and `_revalidate_before_reap` skips
      revalidation entirely for a missing path). A worktree's branch can
      become unmerged relative to the default branch *after* that pre-lock
      proof and before the reap — the consolidated function must fold this
      caller-owned gate into the under-lock decision too, not just the
      signals `cleanup_disposition` itself already covers.
      **Decision:** `_revalidate_cleanup_safety` re-checks
      `git_ops.is_branch_merged(rec.branch, upstream, cwd=repo.anchor)`
      itself, under the lock, whenever the fresh classification comes back
      `GONE` — this is a local git ref comparison (no fetch), not a network
      PR lookup, so it doesn't conflict with the "stays out" scope above.
- [x] **Define the forced-path contract explicitly and separately from the
      full revalidation.** `reap_one --force` is meant to remain an
      individually-verified escape hatch (per the `worktree` skill's
      documented `--force` semantics from #2635), not a second thing this
      consolidation silently removes or changes. Specify precisely what a
      forced reap still re-checks under the lock (at minimum: the existing
      active-session rejection) versus what only the non-forced path
      re-checks (the full consolidated disposition) — as two named
      contracts, not one blended description.
      **Decision — two named contracts:**
      **Non-forced contract:** `_revalidate_cleanup_safety(wt_id,
      force=False)` reloads the record, reclassifies git state, refreshes
      `active_paths`/`_hosted_session_blocks_cleanup`, re-derives
      `turn_count`, calls `claimant_alive`/`paired_sibling_final`, and runs
      the (ordering-fixed) `cleanup_disposition` in full; any non-cleanable
      verdict aborts the reap with `result.reason`.
      **Forced contract:** `_revalidate_cleanup_safety(wt_id, force=True)`
      skips `cleanup_disposition` entirely (dirty/WIP/claims/follow-ups/
      branch-merge are NOT re-checked — `--force` still means "override
      the disposition") but still refreshes liveness under the lock and
      rejects on `ACTIVE` state or a true `_hosted_session_blocks_cleanup`
      — the *one* check `--force` has never bypassed, per the `worktree`
      skill. This is the same shape `reap_one` has today, just with the
      liveness refresh moved under the lock (see next item) instead of
      reusing the pre-lock snapshot.
- [x] **The forced path's active-session check must itself be a fresh,
      under-lock liveness read — not the pre-lock `active_paths`
      snapshot.** `reap_one` computes `active_paths` *before* acquiring
      `FinalizeLock` and, when `force=True`, skips the non-forced
      revalidation entirely; if the active-session rejection it *does* keep
      still consults that stale pre-lock snapshot, a session attaching
      between the scan and the lock is invisible even in forced mode, and
      `--force` can terminate a session it was never meant to touch. Both
      the forced and non-forced contracts refresh liveness under the lock;
      only the *disposition* (dirty/WIP/claims/etc.) differs between them.
      **Decision:** confirmed by the contract above —
      `_revalidate_cleanup_safety` computes its own fresh, single-worktree
      `active_paths` and calls `classify_worktree` itself in **both**
      `force` branches, before the `if force` fork that decides whether to
      also run `cleanup_disposition`. There is exactly one liveness-read
      code path inside the function, shared by both contracts; only the
      disposition call is conditional on `force`.
- [x] **Acknowledge and design for the residual TOCTOU window explicitly —
      revalidating "right before" the reap does not make check-and-delete
      indivisible, and "hold `FinalizeLock` throughout" is necessary but not
      sufficient.** `FinalizeLock` serializes this tool's own
      finalization/cleanup instances against each other; it does **not**
      fence out an external edit, or the tool's **own other writers** —
      session registration and claim/follow-up mutations use a *per-record*
      lock (not `FinalizeLock`), so they can still change safety inputs
      after the final read and before `_reap_worktree`, even while
      `FinalizeLock` is held throughout the critical section. Two acceptable
      outcomes, to be decided here rather than left implicit:
      1. **Shrink the window to one held critical section, and make it
         actually exclusive against this tool's own writers** — perform the
         final classification/liveness read and the `_reap_worktree` call
         as a single, uninterrupted sequence while continuously holding
         `FinalizeLock`, **and** either (a) make session-registration and
         claim/follow-up writers also acquire `FinalizeLock` (or a
         compatible lock, with an explicitly defined lock order to avoid
         deadlock with existing per-record-lock callers), or (b) use a
         non-degrading target-record lock (`require_sidecar=True`, failing
         closed on timeout) or CAS (a generation token bumped by every
         writer, checked immediately before the reap) so a write racing the
         critical section is detected even without a shared lock. The
         default `_RecordLock` mode is insufficient because it may proceed
         on only its in-process lock after sidecar timeout. Whichever of
         (a)/(b) is chosen, **explicitly document that a true fence
         against arbitrary *external* actors (an editor, a directly-invoked
         git command outside this tool entirely) remains out of scope**
         unless a further mechanism (e.g., marking the worktree read-only
         for the duration of the critical section) is deliberately adopted
         as a stretch goal.
      2. **Or** adopt that further external-fencing mechanism if the
         residual risk from (1) is judged unacceptable even for in-tool
         writers, and specify exactly what it is, with its own cost/
         complexity tradeoff spelled out.
      Either way, the plan must say which was chosen and why, rather than
      implying "hold `FinalizeLock` across the final read and the reap"
      already closes the race against every writer to zero.
      **Decision: Option 1(b).** `_revalidate_cleanup_safety`'s
      non-forced contract, in addition to holding `FinalizeLock`
      throughout, opens `tracking._RecordLock(yaml_path,
      require_sidecar=True)` for the fresh-read-through-reap window and
      keeps it held until `_reap_worktree` returns — `require_sidecar=True`
      means it **raises `TimeoutError`** (fail closed, skip that
      worktree with a "revalidation lock contended" reason) rather than
      degrading to the in-process-only lock, unlike the default critical-
      writer mode. This works because `tracking.py`'s own docstring already
      establishes that every RMW writer this effort cares about (resource
      claims, follow-ups, session registration's `mark_resumed`, etc.) goes
      through the *same* `_RecordLock` sidecar for its own short RMW window
      — so holding it for the revalidate-then-reap window fences those
      writers out for free, with **no new lock primitive and no explicit
      lock-order table to maintain**, at the cost of an explicit, small
      **lock-order constraint**: `FinalizeLock` is acquired first (already
      true today), `_RecordLock` second, and no other code path may acquire
      them in the reverse order — verified by Phase 4's lock-ordering test.
      A true external fence (an editor, a bare `git` command run outside
      the tool) remains explicitly out of scope, same as before — this
      closes the race against the tool's own writers, not against arbitrary
      external actors, and the Journal/Phase 5 docs must say so plainly.
      **Forced mode does not take `_RecordLock`** (matching its narrower
      contract of "only the fresh liveness read, no disposition re-check"),
      since it isn't gated on the same set of writer-mutable fields.

### Phase 3 — Wire all three reaper call sites through it

- [x] Route `cmd_cleanup`'s batch loop through the consolidated function
      (replacing today's narrower `_revalidate_before_reap` call).
- [x] Route `reap_one` (`cleanup --worktree-id <id>`) through the *same*
      function for its **non-forced** path — this is the actual fix for gap
      #1; it must not gain its own parallel implementation. Its **forced**
      path keeps the narrower, separately-specified contract from Phase 2
      (active-session rejection only) — force stays force.
- [x] Route `sweep_finished_session_worktrees` (the no-daemon/picker/
      session-end auto-clean path) through the same consolidated function
      too — it builds candidates pre-lock and reaps under the lock with no
      fresh safety decision today, the identical race being fixed
      elsewhere. Recompute its idle-grace input at action time from fresh mux
      activity and tracking timestamps so activity after candidate selection
      postpones removal. If this reaper's constraints (e.g. no `force`
      concept, a narrower record shape) make full reuse awkward, that's a
      Phase 2 design input, not a reason to leave it out — resolve the shape
      during design, don't scope it out by default.
- [x] Remove the now-superseded `_revalidate_before_reap` (or fold it into
      the new function) so there is exactly one non-forced revalidation code
      path left, not two that can drift apart again.

**Implemented** in `_revalidate_cleanup_safety` (`__main__.py`), replacing
`_revalidate_before_reap` entirely — see the 2026-09-14 Phase 3 Journal
entry for the concrete shape and the two upstream bugs fixed alongside it.

### Phase 4 — Regression coverage

Each bullet below is **one named test per signal, per reaper, per mode** —
not a bundled scenario covering several transitions at once. The full matrix
crosses: **signal** (dirty, WIP, new conversation turn, held claim,
follow-up, active session, branch-merge/GONE, claimant-liveness-unknown)
× **reaper** (batch `cleanup --clean`, single `cleanup --worktree-id`,
automatic `sweep_finished_session_worktrees`)
× **mode** (non-forced; forced, for the single-item reaper only, per
Phase 2's forced-path contract). Concretely, at minimum:

- [ ] Batch, non-forced: dirty-after-scan refused (already covered by
      #2635 — confirm it still passes through the consolidated function).
- [ ] Batch, non-forced: active-session-after-scan refused (closes gap #2).
- [ ] Batch, non-forced: WIP-after-scan refused (closes part of gap #3).
- [ ] Batch, non-forced: a record already marked `finalized` that gains WIP
      after the scan is refused, proving the finalized shortcut cannot bypass
      the fresh WIP gate.
- [ ] Batch, non-forced: new-conversation-turn-after-scan refused, i.e. an
      `empty`/`unused` candidate that gained turns and would need
      `--include-conversations` (closes part of gap #3).
- [ ] Batch, non-forced: a record already marked `finalized` that gains a
      conversation turn after the scan is refused when conversations were not
      included, proving the finalized shortcut cannot bypass the fresh
      conversation gate.
- [ ] Batch, non-forced: held-claim-or-follow-up-reopened-after-scan
      refused (closes part of gap #3).
- [ ] Batch, non-forced: branch-becomes-unmerged-after-scan refused (the
      `GONE`/branch-merge caller-owned gate).
- [ ] Batch, non-forced: `claimant_alive` returns unknown under the lock —
      exercises the exact bounded-wait/fail-closed policy Phase 1/2 define
      (not just a happy-path alive/gone result).
- [ ] Batch, non-forced: when the batched mux query is unavailable and the
      record has a fresh cached `mux_live=False`, a mux session attached after
      that stamp is still found by the authoritative action-time probe (or the
      reap fails closed if the probe is unavailable).
- [ ] Single-item (`reap_one`), non-forced: dirty/active/WIP/conversation/
      claim/follow-up/branch-merge-after-scan each refused, mirroring the
      batch cases above — this is the direct fix for gap #1 (currently zero
      coverage), including the finalized-record WIP and conversation variants.
- [ ] Single-item (`reap_one`), non-forced: the unavailable-batch-query plus
      fresh cached `mux_live=False` race is resolved by the authoritative
      action-time probe or fails closed.
- [ ] Single-item (`reap_one`), **forced**: an active session is still
      rejected (the one check `--force` does not bypass); a merely dirty/WIP/
      claimed worktree **is** removed when forced (confirms `--force` still
      works as documented, i.e. this effort does not regress the escape
      hatch).
- [ ] Single-item (`reap_one`), **forced**, **session attaches *after* the
      pre-lock scan** (not merely already-active at scan time): still
      rejected. This is the specific reviewer-identified gap — `force`
      bypasses the disposition checks but must not bypass a *fresh*
      liveness read, since `active_paths` is otherwise only ever computed
      before `FinalizeLock` is acquired.
- [ ] Single-item (`reap_one`), **forced**: the same post-scan attachment is
      detected when the batched mux query is unavailable and the record has a
      fresh cached `mux_live=False`, or the action-time probe fails closed.
- [ ] Automatic finished-session sweep, non-forced: each dirty, active, WIP,
      new-conversation-turn, held-claim, follow-up, branch-merge/GONE, and
      claimant-liveness-unknown transition after candidate selection is
      refused in its own named test, including finalized-record WIP and
      conversation variants.
- [ ] Automatic finished-session sweep, non-forced: activity after candidate
      selection refreshes the idle-grace timestamp and postpones removal even
      when no session remains live at reap time.
- [ ] Automatic finished-session sweep, non-forced: the unavailable-batch-query
      plus fresh cached `mux_live=False` race is covered by an authoritative
      action-time probe/fail-closed test, matching both manual reapers.
- [ ] Tests that specifically exercise the Phase 2 critical section for both
      manual reapers and the automatic finished-session sweep: assert the final
      classification/liveness read and `_reap_worktree` happen while
      continuously holding `FinalizeLock`, and that a racing record mutation
      is fenced or detected before removal. Exercise record-lock contention
      from a separate process and prove sidecar-lock timeout fails closed
      rather than degrading to in-process-only exclusion. Assert every
      participant acquires locks in the declared order.
- [ ] Every existing regression test from PR #2635
      (`test_tracking_override.py`, `test_prune.py`) still passes unchanged
      or is updated to call through the new consolidated function without
      losing coverage.

### Phase 5 — Documentation

- [ ] Update the `worktree` skill's Cleanup Procedure / dirty-worktree
      section (added in #2635) to describe the **unified** guarantee — batch
      cleanup, single-item cleanup (non-forced), and automatic finished-session
      cleanup revalidate the complete safety decision through the cleanup
      handoff, while `--force` remains the documented, narrower,
      individually-verified exception — so a future reader doesn't have to
      reverse-engineer this from several PRs' diffs.
- [ ] Document the known residual gap
      ([#2649](https://github.com/ThomasMichon/copilot-extensions/issues/2649))
      in that same skill section: a `finalized`-status record that gains
      genuinely new WIP/conversation content after finalize is not yet
      caught by this effort's revalidation, because
      `_apply_tracking_override` masks it to `COMPLETED` before
      `cleanup_disposition` runs. State plainly that this is a known,
      tracked limitation, not an oversight the reader needs to
      rediscover.

## Validation Plan

- [ ] `test-supervisor -- python3 tools/run-plugin-tests.py agent-worktrees`
      (or this repo's equivalent bounded test runner) passes in full, with no
      pre-existing-failure caveats beyond ones independently confirmed
      unrelated (as PR #2635 did for `test_knowledge_plugins.py`).
- [ ] Every named test enumerated in Phase 4 exists, is named for the
      specific signal/reaper/mode it covers (no bundled test standing in for
      several transitions), includes the authoritative action-time mux
      fallback, automatic reaper, idle-grace refresh, cross-process fence, and
      lock ordering, fails on the pre-effort code, and passes after.
- [ ] `tools/check-version-consistency.py` and `tools/check-version-bump.py`
      pass on the implementation PR.
- [ ] Manual smoke check (documented in the Journal, not just asserted): a
      real worktree finalized, then edited, then fed through **both**
      `cleanup --clean` and `cleanup --worktree-id <id>` (non-forced) is
      preserved by both, with a clear skip reason printed; the same worktree
      fed through `cleanup --worktree-id <id> --force` is removed, confirming
      the forced escape hatch still works as documented.

## Proposal

_Pending — this README is the proposal; submitted as PR #2641 per the repo's
effort review-gate. This repo has no auto-merge label (`CONTRIBUTING.md`):
the submitter gives Copilot's advisory review a bounded ~5-minute window per
round, addresses what genuinely lands in that window, and then squash-merges
manually with `pr-merge ... --now` regardless of whether a further review has
posted — there is no required approval to wait for. Investigation work in
Phase 1 that is purely read-only (no code changes) may begin in parallel with
review if useful, but no consolidation/wiring changes land before the plan
clears review._

## Journal

### 2026-09-14 — Kickoff

- Effort created following the merge of PR #2635 and the operator's decision
  to defer three review findings (single-worktree revalidation gap, stale
  `active_paths` snapshot, partial dirty/active-only safety recheck) to a
  tracked follow-up rather than expand that PR further.
- Filed umbrella issue #2640 in this repo (this repo's own convention: GitHub
  issues are the discrete tracking token here — a private downstream tracker
  holds the originating cross-repo report, not canonical for this repo's
  work).
- Framed the fix as one consolidation (a single canonical revalidation
  function used by both `cmd_cleanup`'s batch loop and `reap_one`) rather
  than three independent patches, since the root cause of gap #1 is
  precisely that two call sites reimplement the same check-then-act shape
  and only one got fixed.

### 2026-09-14 — Review round 1

- Public-safe participant identity: replaced a private machine identity in
  Participants/Coordination and genericized the cross-repo tracker reference
  (Context + Journal) to avoid naming private infrastructure in this public
  effort.
- Added the `GONE`/branch-merge caller-owned gate (excluded by
  `cleanup_disposition` itself, currently proven pre-lock by both callers)
  as an explicit Phase 2 design requirement and Phase 4 regression case.

### 2026-09-14 — Review round 2

- Phase 2's return contract now requires the revalidator's fresh record and
  classification to be what `_reap_worktree` actually acts on, not just an
  informational disposition alongside the stale pre-lock objects.
- Split the forced (`reap_one --force`) and non-forced contracts explicitly
  throughout Phases 1/2/3/4 and the Validation Plan's manual smoke check —
  `--force` remains the documented, narrower, individually-verified
  exception (active-session rejection only); it is not silently absorbed
  into the full consolidated revalidation.
- Rewrote Phase 4 and the Validation Plan as a named signal × reaper × mode
  matrix instead of four bundled scenario bullets, so no implementation can
  claim completion by testing one representative per bundle.
- Added the `claimant_alive` cross-machine-probe signal to Phase 1's
  inventory and Phase 2's design (explicit bounded-timeout / fail-closed
  policy required) — flagged by the reviewer as a "previously missed"
  finding in code unchanged since the prior round, but a genuine gap in the
  plan's own signal inventory.

### 2026-09-14 — Review round 3

- Named the fundamental limit explicitly rather than leaving it implicit:
  "revalidate right before the reap" does not make check-and-delete
  indivisible. `FinalizeLock` only serializes this tool's own instances
  against each other; an external edit, session attach, or claim write can
  still land in the gap between the final fresh read and the delete
  syscall. Phase 2 now requires a stated decision — shrink the window to a
  single held critical section (documenting the residual OS-level gap as
  out of scope) or adopt further fencing (a lease/generation token, a
  transient read-only flip) — rather than implying the race closes to zero.
- The forced (`reap_one --force`) path's active-session rejection must
  itself refresh liveness under the lock, not reuse the pre-lock
  `active_paths` snapshot — otherwise `--force` can still terminate a
  session that attached *after* the scan, which is exactly the race the
  non-forced path is being fixed to avoid. Added as its own Phase 2 design
  point and Phase 4 test (distinct from "forced still rejects an
  already-active session," which was already covered).

### 2026-09-14 — Review round 4 (final planning round)

- A **third** ordinary reaper, `sweep_finished_session_worktrees`, was
  identified with the same stale check-to-delete shape as the two originally
  scoped — added to Phase 1/3's inventory and call-site list; "two call
  sites" throughout this document means three.
- `cleanup_disposition`'s finalized-shortcut ordering bug (fixed for `dirty`
  in #2635) still applies to `WIP`/`conversation-only` — "re-run
  `cleanup_disposition`" alone does not close those two signals; Phase 2 now
  requires the ordering fix (or an equivalent pre-gate) explicitly.
- `_build_active_paths` has its own stale-cache fallback (a cached
  `mux_live=False` is trusted without a `has_mux_session` cross-check) —
  independent of when the function is called. Phase 2 now requires fixing
  this fallback or using a different authoritative probe.
- Corrected Option 1's claim: holding `FinalizeLock` throughout the critical
  section only fences this tool's own finalization/cleanup instances, not
  its own session-registration/claim-mutation writers (which use a
  per-record lock). Phase 2 now requires either those writers to also honor
  a shared lock (with an explicit lock order) or a target-record lock/CAS,
  in addition to the previously-noted true-external-fencing scope decision.
- Linked the effort to the two standing statements it makes concrete —
  `visions/plugins/agent-worktrees/README.md`'s `contribution-aware-lifecycle`
  ("prove content safe before cleanup") and
  `docs/patterns/ephemeral-process-reaping.md`'s "corroborate before acting"
  — replacing the initial "Vision: none."
- Corrected the Proposal section: this repo's `CONTRIBUTING.md` documents a
  manual `pr-merge ... --now` flow with a bounded ~5-minute advisory-review
  window per round, not an auto-merge label. Per that policy, this effort
  plan stops chasing further review rounds here and proceeds to merge —
  remaining design refinements (if any) belong to Phase 1's investigation,
  which will re-derive and settle them against the real code rather than a
  plan document's prose.

### 2026-09-14 — Review round 4 follow-up

- Expanded Phase 4 so all three reapers explicitly cover the complete signal
  matrix, finalized-record WIP/conversation drift, and cached-negative mux
  fallback; the forced path keeps its separate liveness-only contract.
- Required non-degrading cross-process record exclusion: sidecar-lock timeout
  must fail closed (or an equivalent generation/CAS protocol must detect the
  write), with real cross-process contention coverage.
- Added action-time idle-grace revalidation for the automatic sweep so a
  post-selection resume cannot be hidden merely because no session remains
  live at the final check.

### 2026-09-14 — Phase 1: investigation findings

Read-only pass over the real code (no changes yet); confirms the plan's
findings against current line numbers and settles the open questions Phase 1
posed. Full detail folded into Phase 1's checkboxes above; summarized here:

- **Signal inventory settled.** Every `cleanup_disposition` input
  (`prune.py:253`) is cheap-local — `info.state`/`info.dirty` from a fresh
  `git_ops.classify_worktree(fetch=False, ...)`, `rec.status`/held
  claims/follow-ups from a fresh `tracking.load_record`, `turn_count` from
  `sessions.scan_sessions_fast`, and `default_paired_sibling_final`
  (`prune.py:672`, confirmed no I/O) — **except** `claimant_alive`
  (`claimant.py:216`), which is cheap only same-machine and is a bounded
  (`_REMOTE_TIMEOUT = 8.0`, `claimant.py:44`) SSH round-trip cross-machine.
  PR state must stay scan-time-only (network reconcile, not re-derivable
  under the lock by design).
- **`claimant_alive`'s bounded/fail-closed policy already exists** — the
  tri-state contract (`True`/`False`/`None`) already treats `None`
  (unconfirmed/timeout) as "spare, don't reap" (`prune.py:135`, `assess()`).
  Phase 2's real design question isn't inventing a policy; it's whether the
  consolidated revalidation accepts up to 8 s of held-lock SSH latency per
  candidate, since today only `reap_one`'s pre-lock `cleanup_disposition`
  call pays that cost, not any current under-lock check.
- **`_hosted_session_blocks_cleanup` vs. `active_paths` are independent
  signals, not the same one read twice.** The former (`__main__.py:377`)
  reads only record fields (`session_backend_opaque`,
  `execution_leg_opaque`, `tracking.derive_execution_leg(...).state`) for
  **hosted**/cloud-backend sessions; the latter (`_build_active_paths`,
  `__main__.py:388`) covers local lock-file sessions, batched mux/tmux,
  cached `mux_live`/`bound_live` hints, and bridge-lock liveness. The
  consolidated function needs both, not a merge.
- **`reap_one`'s exact shape confirmed** (`__main__.py:13506`): the initial
  ACTIVE check (`__main__.py:13580`) runs pre-lock against the pre-lock
  `active_paths` built at `__main__.py:13558`, and is shared by forced and
  non-forced. Non-forced additionally runs `cleanup_disposition` pre-lock
  (`__main__.py:13612`); under the lock (`__main__.py:13631`) it re-checks
  only `_hosted_session_blocks_cleanup(latest)` (`__main__.py:13641`) — no
  fresh git reclassification, `active_paths`, `claimant_alive`, held-claims,
  follow-up, or branch-merge recheck. This is gap #1 exactly as scoped: zero
  revalidation of the disposition itself. Forced mode skips
  `cleanup_disposition` (`__main__.py:13595`) but shares the same pre-lock
  ACTIVE check and the same fresh under-lock `_hosted_session_blocks_cleanup`
  check — so `--force` bypasses only the disposition, matching the
  documented contract, but its shared ACTIVE check is still stale
  pre-lock `active_paths`, confirming the forced-attach-after-scan gap
  Phase 2/4 already call out.
- **Additionally confirmed while tracing the above** (not new findings —
  cross-checking the plan's Context section against real line numbers):
  `_revalidate_before_reap` (`__main__.py:15400`, called from `cmd_cleanup`
  at `__main__.py:15646`) only re-checks ACTIVE/DIRTY, is handed the
  `active_paths` snapshot built pre-lock at `__main__.py:15483` (never
  rebuilt under the lock), and `cleanup_disposition`'s finalized-shortcut
  (`prune.py`, `rec.status == "finalized" or info.state == COMPLETED`) runs
  before its `WIP`/`empty`/`conversation-only` branches, reproducing the
  #2635-class ordering bug for those two signals. `sweep_finished_session_worktrees`
  (`__main__.py:14690`) builds its entire candidate set — including its
  `cleanup_disposition` call — in a read-only Pass 1 before ever acquiring
  `FinalizeLock` (`__main__.py:14819`), then reaps every candidate in Pass 2
  with **no revalidation at all**, the starkest instance of the same race.
  `_build_active_paths`'s stale-cache fallback (`__main__.py:415`-`430`) is
  confirmed: when the batched mux query is unavailable, a fresh
  (`_fresh_mux_live_hint` within its 600 s TTL) but cached `False` is
  trusted outright — the `has_mux_session` fallback probe only runs when
  the hint is `None` (absent/stale), not when it is a confirmed-fresh
  `False`.

Phase 1 complete. Proceeding to Phase 2's design in a follow-on session/PR
per the plan's own sequencing (no consolidation/wiring changes land before
the design is settled).

### 2026-09-14 — Phase 2: design settled

Design-only (no wiring changes yet — Phase 3 lands those). Concrete
decisions folded into Phase 2's checkboxes above; summarized here:

- New function `_revalidate_cleanup_safety(wt_id, *, repo, force=False) ->
  RevalidationResult`, called with only an id so it can't accidentally
  consume stale caller state; returns the fresh `record`/`info` alongside
  the disposition so every reaper acts on what was actually revalidated,
  not the pre-lock objects.
- Two bugs get fixed **in their owning function**, benefiting every caller,
  not just the new revalidator: (1) `cleanup_disposition`'s `WIP`/
  `empty`/`conversation-only` branches move ahead of the
  finalized/COMPLETED shortcut, mirroring #2635's `dirty` fix; (2)
  `_build_active_paths`'s stale-cache fallback always falls through to
  `has_mux_session` when the batched mux query is unavailable, regardless
  of a fresh cached `False` hint.
- `claimant_alive`'s existing bounded (8 s)/fail-closed tri-state policy is
  kept as-is and paid fresh under the lock for the non-forced path — no new
  policy needed, cost accepted rather than engineered around.
- Forced vs. non-forced are two named contracts sharing one liveness-read
  code path (fixing the forced-path's stale-`active_paths` gap) but
  diverging on whether `cleanup_disposition` runs at all — force never
  re-checks dirty/WIP/claims/follow-ups/branch-merge, only liveness.
- The residual-TOCTOU question resolves to **Option 1(b)**: the non-forced
  contract holds `tracking._RecordLock(yaml_path, require_sidecar=True)`
  (fail-closed on contention) across the fresh-read-through-reap window, in
  addition to `FinalizeLock` — reusing the sidecar lock every other RMW
  writer in this codebase already takes, rather than inventing a new
  primitive or a CAS/generation token. Lock order is `FinalizeLock` then
  `_RecordLock`, verified by a Phase 4 test. A true external fence (an
  editor, a bare `git` command outside the tool) stays explicitly
  out of scope, to be documented plainly in Phase 5.

Proceeding to Phase 3 (wiring) in a follow-on PR.

### 2026-09-14 — Phase 3: wiring implemented

Implemented exactly the Phase 2 design; no design changes. Summary:

- Added `_revalidate_cleanup_safety(wt_id, *, repo, tracking_path,
  force=False, include_unused=False, include_conversations=False, reap=None)`
  returning a `RevalidationResult(cleanable, reason, bucket, record, info,
  failures, warnings, reaped)`. It reloads the record fresh from disk,
  rebuilds a single-worktree `active_paths`, re-classifies git state, and —
  non-forced only — runs the full `cleanup_disposition` and re-proves the
  `GONE`/branch-merge gate itself. When a `reap` callback is supplied and
  the decision is cleanable, it is invoked while a non-forced call still
  holds `tracking._RecordLock(require_sidecar=True)` (fail-closed on
  contention), so the fresh read and the delete are one uninterrupted,
  lock-held sequence. Forced mode shares the same liveness-read path but
  skips `cleanup_disposition` and doesn't take `_RecordLock`.
- Wired all three call sites: `cmd_cleanup`'s batch loop, `reap_one`'s
  non-forced path (forced keeps its pre-lock fast-reject UX checks but the
  authoritative decision now goes through the same function too, with
  `force=True`), and `sweep_finished_session_worktrees`'s Pass 2 — which
  also re-derives its idle-grace timestamp at reap time from fresh mux
  activity/tracking timestamps before calling the revalidator, so a resume
  between Pass 1 (candidate selection) and Pass 2 (reap) postpones removal.
- Fixed `cleanup_disposition`'s WIP/conversation-only ordering bug and
  `_build_active_paths`'s stale-negative-cache fallback in their own
  functions, per the Phase 2 decision, so every caller benefits.
- Removed `_revalidate_before_reap` entirely — one non-forced revalidation
  path now exists.
- Updated `test_auto_clean.py`'s `_sweep` test helper to mock
  `tracking.load_record`/`tracking._RecordLock` (the revalidator reloads
  fresh from disk under the lock, which the old helper didn't anticipate).
- Full `agent-worktrees` suite: 583 passed, 1 pre-existing unrelated failure
  (`test_controller_relations.py`'s `_all_tracking_dirs` reference, confirmed
  to fail identically on an unmodified checkout — filed as #2647, not part
  of this effort).
- Bumped `agent-worktrees` to `1.5.5-dev104` (`plugin.json`/`pyproject.toml`/
  `marketplace.json`), verified via `check-version-consistency.py`.

Phase 3 complete. Phase 4 (the full named signal × reaper × mode regression
matrix) and Phase 5 (skill docs) remain — substantial enough in their own
right (≈30 named tests) to warrant their own dedicated pass/PR(s), continuing
this effort rather than closing it here.

### 2026-09-14 — Phase 3 follow-up: residual gap found while pinning tests

While replacing the (now-removed) `_revalidate_before_reap` tests with
`TestRevalidateCleanupSafety`, a genuine, previously-unrecognized gap
surfaced: `_apply_tracking_override` masks ANY non-dirty/GONE/ACTIVE fresh
git state to `COMPLETED` for a `finalized`-status record — including WIP
and UNUSED-with-turns — **before** `cleanup_disposition` ever runs. It
does this deliberately, to correct a real squash-merge artifact (a
finalized branch that still reads "ahead" of upstream even though its
content already landed), but it cannot distinguish that from genuinely
new commits/turns made *after* finalize, which present identically.

**Consequence:** the Phase 2/3 fix to `cleanup_disposition`'s WIP/
conversation-only ordering (mirroring #2635's `dirty` fix) is real and
correct for records whose git state isn't first collapsed by the
override — but for a `finalized`-status record specifically, the
override already converts WIP/conversation-only to COMPLETED
independently of, and prior to, that ordering fix, so the fix alone does
not close the finalized-record case the original Context section
described. This was not caught during Phase 1/2 because those phases
traced `cleanup_disposition`'s own internal ordering without also tracing
what `_apply_tracking_override` does to `info.state` immediately
beforehand.

Filed as [#2649](https://github.com/ThomasMichon/copilot-extensions/issues/2649),
with a pinning test (`test_finalized_status_currently_masks_new_wip_gap`)
documenting today's actual (unsafe) behavior. Not fixed in this effort's
Phase 3 PR — a real fix needs a way to distinguish "already-known-squashed
WIP at finalize time" from "new WIP since finalize" (e.g. a branch-tip
fingerprint recorded at finalize time, or relying on `classify_worktree`'s
own squash-merge-aware COMPLETED detection instead of blanket-masking by
status), which is a distinct design question from anything Phase 2
already decided. Left as a tracked follow-up rather than silently folded
in or dropped.
