# Status-monitor singleton correctness

- **Slug:** `status-monitor-singleton-correctness`
- **Repo:** copilot-extensions
- **Branch(es):** _TBD per phase (one PR per phase)_
- **Created:** 2026-10-06
- **Status:** Draft
- **Umbrella issue:** _none yet — this README is the umbrella; see Sub-issues_
- **Sub-issues:** #5453 (promotion/cutover exclusivity) · #5512 (restart-seam exclusivity)

## Guiding Intent

Realize Vision
[`plugins/agent-worktrees/status-monitor-singleton`](../../../visions/plugins/agent-worktrees/status-monitor-singleton/README.md):
at every instant, including mid-transition, at most one resident
`status-monitor` process is actively serving per host, and no transition
ever leaves an unbounded zero-coverage window. PR #5412 (merged to `dev`
on 2026-10-06) closes the ordinary-cold-start case (the
originally reported bug: a burst of concurrent cold starts producing 100+
duplicate residents); this effort's Phase 1 work must not start until #5412
has actually merged, since it is the foundational guarantee the rest of the
lifecycle builds on. This effort closes the three remaining transitions:
replacement, the promotion/cutover handoff, and the restart (auto-update)
seam. Replacement and promotion/cutover share Phase 2 and issue #5453.

## Participants

Solo effort; no distinct execution venue beyond the normal `copilot-extensions`
worktree/PR flow.

## Context

PR #5412 ("status-monitor: close a TOCTOU race in the single-instance
guard") fixed the ordinary cold-start race with an OS-mediated exclusivity
lease. During that PR's review, more than ten rounds attempting to extend
the same exclusivity to two further transitions — the muxless/superseded-
runtime replacement path together with promotion/cutover, and separately the
restart/auto-update seam — each surfaced a new, genuinely real concurrency
bug, never a false positive. Both extensions were reverted (operator
decisions, captured in that PR's history) rather than continuing to patch
round by round, and the gaps were disclosed in the PR's final body and
tracked:

- **#5453** — replacement is not covered by the ordinary-cold-start lease:
  a replacement candidate loses that lease and backs off instead of
  promptly replacing a still-live muxless/superseded-runtime incumbent.
  The promotion/cutover transition is structurally unsafe to
  patch inside `status_monitor_cli.py` alone: the cutover orchestrator
  (`libs/zdd/src/zdd/cutover.py`, `status_monitor_cutover.py`) promotes the
  successor *before* requesting the predecessor's drain, so the predecessor
  can legitimately keep serving — and keep holding its own ownership claim —
  for 30+ seconds after promotion. A real fix changes the orchestrator's own
  transition protocol (confirm-then-promote or drain-then-promote), not
  `status_monitor_cli.py`'s lease guard.
- **#5512** — the restart seam (`_restart_status_monitor()` in
  `status_monitor_runtime.py`, invoked by `status-monitor-restart`) reaps
  the predecessor and spawns a successor with no exclusivity guarantee
  between the two steps. Three distinct races were found while iterating on
  a wait-for-exit fix: a terminate-failure skip, unconditional metadata
  deletion that can clobber a concurrent new owner's metadata, and a
  non-exit-aware (`os.kill(pid, 0)`-based) liveness check vulnerable to
  zombies/PID reuse.

These gaps are the **same kind of problem** — a transition point lacking the
same exclusivity standard the cold-start path now has — which is exactly
why the vision states one standard across all four transitions rather than
treating these as unrelated bugs.

## Request

Operator (2026-10-06), verbatim: "Revert, build out a vision, write an
effort stating the desired outcome (effective singleton process), and focus
on staying on the rails to avoid scope creep." The vision is
`visions/plugins/agent-worktrees/status-monitor-singleton/README.md`; this
effort is that follow-up, explicitly scoped to stay narrow: one phase
at a time, each phase its own reviewed PR, no attempt to redesign both
phases — or the cold-start/metadata layer that already works — in one
sweep.

## Plan

_(All items below are agent-recommended decomposition of the operator's
stated outcome, not separately operator-specified line items.)_

### Phase 0 — Prerequisite: PR #5412 merged (_agent-recommended_)
- [x] Confirm PR #5412 ("status-monitor: close a TOCTOU race in the
  single-instance guard") has merged to `dev` before starting Phase 1
  design work. Phase 1 builds on the ordinary-cold-start lease that PR
  introduces; starting Phase 1 before it merges risks building on code
  that could still change under review. Verified merged on 2026-10-06;
  this confirms the prerequisite only, not the start of Phase 1.

### Phase 1 — Restart-seam exclusivity (#5512)
- [ ] Design the restart seam's exit-aware wait + identity-scoped cleanup
  per the vision's `exit-awareness-not-existence-checking` and
  `identity-scoped-cleanup` behaviors — informed by, but not copying
  verbatim, the three reverted attempts from PR #5412 (bounded wait with
  `pid_alive()`, then a post-wait re-check): this phase must specifically
  address the terminate-failure-skip and concurrent-new-owner-metadata-
  deletion races those attempts left unresolved, not only re-add the same
  bounded wait.
- [ ] Add regression coverage for all three known races (terminate failure,
  concurrent-publish-during-wait, zombie/PID-reuse) before considering the
  phase done.
- [ ] Open a PR scoped to this phase only; drive it through review to merge
  per the usual `copilot-extensions` PR flow.
- [ ] Close #5512 on merge; update this README's Journal.

### Phase 2 — Replacement and promotion/cutover exclusivity (#5453)
- [ ] Design an ownership handoff for muxless/superseded-runtime
  replacement that permits a legitimate successor to replace the stale
  incumbent without overlapping active service or abandoning replacement
  simply because the incumbent still holds the cold-start lease.
- [ ] Design the cutover orchestrator's confirm-then-promote (or
  drain-then-promote) sequencing change in `libs/zdd/src/zdd/cutover.py` /
  `status_monitor_cutover.py`, per #5453's own scope section.
- [ ] Update `status_monitor_cli.py`'s lease-guard side to match the new
  sequencing.
- [ ] Add real (non-mocked where practical) activation-path regression
  tests driving an actual cutover transition, not only the existing
  fake-daemon control-server tests. Cover muxless and superseded-runtime
  replacement as well as promotion/cutover, including losing replacement
  candidates and identity-scoped metadata cleanup.
- [ ] Open a PR scoped to this phase only; drive it through review to merge.
- [ ] Close #5453 on merge; update this README's Journal.

Phases are intentionally sequential and independently shippable — do not
start Phase 2 design work until Phase 1 has merged, to keep each PR's
review surface (and this effort's own scope) bounded to one phase at a
time; Phase 2 covers the coupled replacement/cutover protocol.

## Validation Plan

- [ ] Phase 1: new unit/regression tests reproduce each of the three named
  races against the OLD code (red), then pass against the fix (green); full
  `tests/test_status_monitor.py` suite stays green; lint
  (`ruff check --select F,E9`) and `tools/check-module-size.py` clean.
- [ ] Phase 2: new activation-path regression tests exercise muxless and
  superseded-runtime replacement and an actual cutover transition (not only
  mocked control-server calls). Demonstrate that a legitimate replacement
  eventually serves within the defined transition deadline, losing
  candidates cannot publish or delete another owner's metadata, and
  the predecessor cannot continue serving once the successor is confirmed
  promoted; existing cutover/control-server test suite stays green.
- [ ] After both phases merge: re-read Vision
  `status-monitor-singleton`'s Behaviors section and confirm each one now
  holds for all four named transitions (ordinary cold start via merged
  #5412; restart via Phase 1; replacement and promotion/cutover via Phase 2) —
  record that confirmation in the Journal rather than assuming it.

## Proposal

_Pending — design work starts at Phase 1, above._

## Journal

### 2026-10-06 — Kickoff
- Effort created after reverting PR #5412's replacement-path and
  restart-wait exclusivity extensions back to the narrow ordinary-cold-start
  fix (operator-directed scope-downs), and after authoring Vision
  `plugins/agent-worktrees/status-monitor-singleton` to state the
  all-transitions standard those extensions were (correctly, per repeated
  review findings) failing to meet piecemeal.
- Filed #5512 (restart-seam races) alongside the pre-existing #5453
  (promotion/cutover) as this effort's two sub-issues/phases.

### 2026-10-10 — Planning review corrections
- Confirmed #5412 merged on 2026-10-06, satisfying Phase 0 only.
- Made the deferred replacement path an explicit Phase 2 deliverable and
  validation target under #5453; no implementation phase has started.
- Corrected inception dates to the PR's 2026-10-06 creation/review history.
