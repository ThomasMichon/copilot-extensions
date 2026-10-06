# Status-monitor singleton correctness

- **Slug:** `status-monitor-singleton-correctness`
- **Repo:** copilot-extensions
- **Branch(es):** _TBD per phase (one PR per phase)_
- **Created:** 2026-10-07
- **Status:** Draft
- **Umbrella issue:** _none yet — this README is the umbrella; see Sub-issues_
- **Sub-issues:** #5453 (promotion/cutover exclusivity) · #5512 (restart-seam exclusivity)

## Guiding Intent

Realize Vision
[`plugins/agent-worktrees/status-monitor-singleton`](../../../visions/plugins/agent-worktrees/status-monitor-singleton/README.md):
at every instant, including mid-transition, at most one resident
`status-monitor` process is actively serving per host, and no transition
ever leaves an unbounded zero-coverage window. PR #5412 already closed the
ordinary-cold-start case (the originally reported bug: a burst of concurrent
cold starts producing 100+ duplicate residents). This effort closes the two
remaining transitions the vision names as not yet meeting that standard:
the promotion/cutover handoff and the restart (auto-update) seam.

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

- **#5453** — the promotion/cutover transition is structurally unsafe to
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

Both gaps are the **same kind of problem** — a transition point lacking the
same exclusivity standard the cold-start path now has — which is exactly
why the vision states one standard across all four transitions rather than
treating these as unrelated bugs.

## Request

Operator (2026-10-07), verbatim: "Revert, build out a vision, write an
effort stating the desired outcome (effective singleton process), and focus
on staying on the rails to avoid scope creep." The vision is
`visions/plugins/agent-worktrees/status-monitor-singleton/README.md`; this
effort is that follow-up, explicitly scoped to stay narrow: one transition
at a time, each phase its own reviewed PR, no attempt to redesign both
transitions — or the cold-start/metadata layer that already works — in one
sweep.

## Plan

_(All items below are agent-recommended decomposition of the operator's
stated outcome, not separately operator-specified line items.)_

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

### Phase 2 — Promotion/cutover exclusivity (#5453)
- [ ] Design the cutover orchestrator's confirm-then-promote (or
  drain-then-promote) sequencing change in `libs/zdd/src/zdd/cutover.py` /
  `status_monitor_cutover.py`, per #5453's own scope section.
- [ ] Update `status_monitor_cli.py`'s lease-guard side to match the new
  sequencing.
- [ ] Add real (non-mocked where practical) activation-path regression
  tests driving an actual cutover transition, not only the existing
  fake-daemon control-server tests.
- [ ] Open a PR scoped to this phase only; drive it through review to merge.
- [ ] Close #5453 on merge; update this README's Journal.

Phases are intentionally sequential and independently shippable — do not
start Phase 2 design work until Phase 1 has merged, to keep each PR's
review surface (and this effort's own scope) bounded to one transition at a
time.

## Validation Plan

- [ ] Phase 1: new unit/regression tests reproduce each of the three named
  races against the OLD code (red), then pass against the fix (green); full
  `tests/test_status_monitor.py` suite stays green; lint
  (`ruff check --select F,E9`) and `tools/check-module-size.py` clean.
- [ ] Phase 2: new activation-path regression test(s) exercise an actual
  cutover transition (not only mocked control-server calls) and demonstrate
  the predecessor cannot continue serving once the successor is confirmed
  promoted; existing cutover/control-server test suite stays green.
- [ ] After both phases merge: re-read Vision
  `status-monitor-singleton`'s Behaviors section and confirm each one now
  holds for all four named transitions (cold start already did; replacement
  piggybacks on cold start's lease and is unchanged by this effort; restart
  and promotion/cutover are the two this effort closes) — record that
  confirmation in the Journal rather than assuming it.

## Proposal

_Pending — design work starts at Phase 1, above._

## Journal

### 2026-10-07 — Kickoff
- Effort created after reverting PR #5412's replacement-path and
  restart-wait exclusivity extensions back to the narrow ordinary-cold-start
  fix (operator-directed scope-downs), and after authoring Vision
  `plugins/agent-worktrees/status-monitor-singleton` to state the
  all-transitions standard those extensions were (correctly, per repeated
  review findings) failing to meet piecemeal.
- Filed #5512 (restart-seam races) alongside the pre-existing #5453
  (promotion/cutover) as this effort's two sub-issues/phases.
