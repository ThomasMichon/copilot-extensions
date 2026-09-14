# Cleanup TOCTOU Revalidation Hardening

- **Slug:** `cleanup-toctou-revalidation`
- **Repo:** copilot-extensions
- **Branch(es):** single working branch, one participant
- **Created:** 2026-09-14
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** none — correctness/robustness hardening of an existing mechanism, not a new capability
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

Filed as umbrella issue
[#2640](https://github.com/ThomasMichon/copilot-extensions/issues/2640).
Originating report: a private downstream tracker (not canonical here).

### Why this earns a real design pass, not another quick patch

The pattern so far has been: fix the symptom the reviewer just found, in the
exact spot it was found. That produced a working batch-path fix, but it also
produced the very gaps this effort exists to close — `reap_one` never got the
fix because it's a *second, separate* call site that happens to reimplement
the same "check then act" shape. Patching gap-by-gap in-place risks a fourth
and fifth gap surfacing the same way. The fix that actually closes the class
of bug is **one canonical, single-sourced safety recheck**, called from every
site that reaps a worktree — so there is no second call site left to drift
out of sync.

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

- [ ] Enumerate every signal `cleanup_disposition` currently consults
      (`info.state`/`info.dirty`, `rec.status`, `turn_count`, held resource
      claims via the injected `claimant_alive` probe, itemized follow-ups,
      paired-sibling finality, PR state via `assess`) and, for each, whether
      it can be **cheaply re-derived locally** (no network) at reap time, or
      requires a network/remote round-trip that should stay scan-time-only
      by design or needs an explicit bounded-wait policy under the lock.
- [ ] **`claimant_alive`** (`resolve_claimant_alive`) specifically: this probe
      can perform a **cross-machine SSH check** and intentionally returns
      *unknown* on failure — it is not a cheap local read like the others.
      Decide and document an explicit under-lock policy for it: a bounded
      timeout, and whether "unknown" fails closed (treat as still-live, don't
      reap) or is otherwise handled — the design must not silently drop this
      gate, and must not let the lifecycle lock block indefinitely on remote
      I/O.
- [ ] Trace exactly how `_hosted_session_blocks_cleanup(latest)` (already
      called under the lock today, in both `cmd_cleanup` and presumably
      `reap_one`) relates to `active_paths`/mux-session liveness — are these
      the *same* liveness signal read two different ways, or genuinely
      independent? Document the answer; don't build a second, divergent
      liveness check if one already exists under the lock.
- [ ] Confirm `reap_one`'s current shape precisely, **including its `force`
      path**: `reap_one` deliberately bypasses `cleanup_disposition` when
      `force=True`, while still rejecting an active session — the skill
      documents `--force` as an individually-verified exception, not
      something this effort should remove or weaken. Document the forced and
      non-forced contracts **separately** so Phase 2's design doesn't
      conflate them.

### Phase 2 — Design the consolidated revalidation function

- [ ] Design one function (tentatively `_revalidate_cleanup_safety`,
      replacing `_revalidate_before_reap`) that, given a worktree id, does
      **all** of: reload the tracking record fresh, re-classify git state
      fresh (`classify_worktree`, no fetch), refresh the liveness signal(s)
      identified in Phase 1 scoped to just this one worktree (not a full
      fleet rescan), re-derive `turn_count` from current session state, and
      run the **complete** `cleanup_disposition` against all of that —
      returning `(disposition, reason)` rather than a narrowed
      dirty/active-only check.
- [ ] **The function's return contract must include the fresh record and
      classification, not only the disposition/reason.** Today's callers
      still pass the *pre-lock* record/`info` objects into `_reap_worktree`
      after revalidating — if the revalidator's fresh state isn't threaded
      through as what actually gets reaped, the delete can act on stale
      path, branch, or lifecycle metadata even after a correct fresh
      decision. Make "the reap acts on the revalidated record and
      classification" part of the contract, not just "the decision was
      fresh."
- [ ] Decide explicitly between "recompute everything fresh" (this function
      re-decides from scratch) vs. "fail closed on any detected drift"
      (compare a fingerprint of relevant inputs at scan vs. reap time, abort
      on any difference without re-deciding) for inputs that are cheap to
      re-derive locally — the reviewer's review comment left both as
      acceptable; Phase 1's findings on per-signal re-derivation cost should
      settle this per-signal, not necessarily uniformly.
- [ ] Explicitly scope what stays out: this function must not perform a
      network PR reconciliation under the lock (that's `--reconcile-prs`'s
      job, at scan time) — confirm this constraint is testable, not just
      assumed.
- [ ] `prune.cleanup_disposition` deliberately excludes `GONE` — the
      branch-merged-content proof for a missing worktree directory is owned
      by the **caller** (today, both the batch and single-item paths perform
      that proof *before* the lock, and `_revalidate_before_reap` skips
      revalidation entirely for a missing path). A worktree's branch can
      become unmerged relative to the default branch *after* that pre-lock
      proof and before the reap — the consolidated function must fold this
      caller-owned gate into the under-lock decision too, not just the
      signals `cleanup_disposition` itself already covers.
- [ ] **Define the forced-path contract explicitly and separately from the
      full revalidation.** `reap_one --force` is meant to remain an
      individually-verified escape hatch (per the `worktree` skill's
      documented `--force` semantics from #2635), not a second thing this
      consolidation silently removes or changes. Specify precisely what a
      forced reap still re-checks under the lock (at minimum: the existing
      active-session rejection) versus what only the non-forced path
      re-checks (the full consolidated disposition) — as two named
      contracts, not one blended description.

### Phase 3 — Wire both call sites through it

- [ ] Route `cmd_cleanup`'s batch loop through the consolidated function
      (replacing today's narrower `_revalidate_before_reap` call).
- [ ] Route `reap_one` (`cleanup --worktree-id <id>`) through the *same*
      function for its **non-forced** path — this is the actual fix for gap
      #1; it must not gain its own parallel implementation. Its **forced**
      path keeps the narrower, separately-specified contract from Phase 2
      (active-session rejection only) — force stays force.
- [ ] Remove the now-superseded `_revalidate_before_reap` (or fold it into
      the new function) so there is exactly one non-forced revalidation code
      path left, not two that can drift apart again.

### Phase 4 — Regression coverage

Each bullet below is **one named test per signal, per reaper, per mode** —
not a bundled scenario covering several transitions at once. The full matrix
crosses: **signal** (dirty, WIP, new conversation turn, held claim,
follow-up, active session, branch-merge/GONE, claimant-liveness-unknown)
× **reaper** (batch `cleanup --clean`, single `cleanup --worktree-id`)
× **mode** (non-forced; forced, for the single-item reaper only, per
Phase 2's forced-path contract). Concretely, at minimum:

- [ ] Batch, non-forced: dirty-after-scan refused (already covered by
      #2635 — confirm it still passes through the consolidated function).
- [ ] Batch, non-forced: active-session-after-scan refused (closes gap #2).
- [ ] Batch, non-forced: WIP-after-scan refused (closes part of gap #3).
- [ ] Batch, non-forced: new-conversation-turn-after-scan refused, i.e. an
      `empty`/`unused` candidate that gained turns and would need
      `--include-conversations` (closes part of gap #3).
- [ ] Batch, non-forced: held-claim-or-follow-up-reopened-after-scan
      refused (closes part of gap #3).
- [ ] Batch, non-forced: branch-becomes-unmerged-after-scan refused (the
      `GONE`/branch-merge caller-owned gate).
- [ ] Batch, non-forced: `claimant_alive` returns unknown under the lock —
      exercises the exact bounded-wait/fail-closed policy Phase 1/2 define
      (not just a happy-path alive/gone result).
- [ ] Single-item (`reap_one`), non-forced: dirty/active/WIP/conversation/
      claim/follow-up/branch-merge-after-scan each refused, mirroring the
      batch cases above — this is the direct fix for gap #1 (currently zero
      coverage).
- [ ] Single-item (`reap_one`), **forced**: an active session is still
      rejected (the one check `--force` does not bypass); a merely dirty/WIP/
      claimed worktree **is** removed when forced (confirms `--force` still
      works as documented, i.e. this effort does not regress the escape
      hatch).
- [ ] Every existing regression test from PR #2635
      (`test_tracking_override.py`, `test_prune.py`) still passes unchanged
      or is updated to call through the new consolidated function without
      losing coverage.

### Phase 5 — Documentation

- [ ] Update the `worktree` skill's Cleanup Procedure / dirty-worktree
      section (added in #2635) to describe the **unified** guarantee — both
      `cleanup --clean` and `cleanup --worktree-id` (non-forced) revalidate
      the complete safety decision under the lock, while `--force` remains
      the documented, narrower, individually-verified exception — so a
      future reader doesn't have to reverse-engineer this from two PRs'
      diffs.

## Validation Plan

- [ ] `test-supervisor -- python3 tools/run-plugin-tests.py agent-worktrees`
      (or this repo's equivalent bounded test runner) passes in full, with no
      pre-existing-failure caveats beyond ones independently confirmed
      unrelated (as PR #2635 did for `test_knowledge_plugins.py`).
- [ ] Every named test enumerated in Phase 4 exists, is named for the
      specific signal/reaper/mode it covers (no bundled test standing in for
      several transitions), fails on the pre-effort code, and passes after.
- [ ] `tools/check-version-consistency.py` and `tools/check-version-bump.py`
      pass on the implementation PR.
- [ ] Manual smoke check (documented in the Journal, not just asserted): a
      real worktree finalized, then edited, then fed through **both**
      `cleanup --clean` and `cleanup --worktree-id <id>` (non-forced) is
      preserved by both, with a clear skip reason printed; the same worktree
      fed through `cleanup --worktree-id <id> --force` is removed, confirming
      the forced escape hatch still works as documented.

## Proposal

_Pending — this README is the proposal; submit as a PR per the repo's effort
review-gate (auto-merge enabled) and await approval before starting Phase 1
implementation work. Investigation work in Phase 1 that is purely read-only
(no code changes) may begin in parallel with review if useful, but no
consolidation/wiring changes land before the plan clears review._

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
