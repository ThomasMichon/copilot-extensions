# Lean Session Lifecycle

- **Slug:** `lean-session-lifecycle`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-slice PRs targeting `dev`
- **Created:** 2026-10-08
- **Status:** Draft
- **Vision:** `visions/plugin-services/README.md` - process-count-scales-with-services-not-sessions and hooks-and-callbacks-are-transient; `visions/plugins/context-handoff/README.md` - host-agnostic trigger and durable recovery
- **Tracking issues:** #5579, #5664, #2619
- **Related, separately driven mitigation:** #5637

## Guiding Intent

Make affirmative lifecycle transitions do the work: register handoff requests
when they are triggered, and transfer launch responsibility without retaining
Python interpreters whose only remaining job is to wait. Minimize both resident
process count per session and recurring monitor work without weakening durable
recovery, process ownership, terminal behavior, or handoff mode gates.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Lifecycle driver | Plan, implementation, serial PR ownership, and validation | This effort and its public tracking issues |
| Existing log-cache driver | Independently owns #5637 append-heavy parsing mitigation | #5637; coordinate, do not duplicate |

## Coordination

- **Topology:** one writer, serial reviewable slices.
- **Host (owns PRs):** Lifecycle driver.
- **Delegates:** evidence-only research; no delegated edits.
- **Handoff:** the successor reads this README and resumes the first unchecked
  Plan and Validation Plan items, preserving any outstanding PR obligations.
- Do not take over #5637's active implementation or broaden this effort into
  all monitor scheduling, transcript archival, or a new process-host framework.

## Context

Representative nonblocking `py-spy` dumps established:

- `agent-mcp bridge` already enters `forward.run()` and waits in
  `sockio.pump`; an explicit `forward` invocation has the same live shape.
  Changing the command name is not itself a process-count optimization.
- Both `agent_worktrees` and `worktree_manager` retain Python workers on the
  Windows launch path after picker selection. Their main threads wait in
  `_exec_worktree_manager` and `_run_relocated_mux_launch`, respectively.
  Including interpreter trampolines, those layers retain four Python
  processes per sampled launch chain. This is not proof of a leaked picker UI.
- A 30-second, 25-Hz monitor profile attributed approximately 39% of
  main-thread observations to `activity.read_events` in handoff retirement
  discovery and 47% to worktree-list warming/classification. These include
  blocking observations and are not CPU percentages.
- A temporary old/new monitor overlap resolved through its own cutover;
  do not treat every observed passive generation as a rogue daemon.

The relevant existing seams are `context-handoff`'s `noteHandoff` /
`agent-worktrees note-handoff`, the durable pending-handoff record, and the
monitor's existing wake/IPC machinery. Reuse them rather than introducing
an arbitrary user-supplied shell callback or another resident helper.

This is vision-closing work. Follow the existing work-coalescing-singleton,
process-slot-ownership, uniform-runtime-resolution, and lifecycle-activity-logging
patterns. Logs remain diagnostic evidence; they must not become the automatic
request queue.

## Request

The operator approved the slug and two-slice scope.

> Ideally, we use the affirmative trigger-handoff to call some register command callback somehow, so we don't need to continuously poll for handoffs. We also should avoid spawning things "just to wait". Draft plans and build fixes for these issues

## Plan

### Phase 1 - Reviewed design and coordination

- [x] Map launch ancestry and sample representative Python roles before
  attributing costs to duplicate workers.
- [x] Deduplicate against #5579, #5664, #2619, and the independently driven #5637.
- [x] Publish the measured evidence and claim the bounded implementation slices.
- [ ] Land this plan through the repository's review gate before implementation.

### Phase 2 - Affirmative handoff registration

- [ ] Trace extension and CLI trigger, save, abort, consume, successor-ready,
  and predecessor-retirement transitions against the current host boundary.
- [ ] Persist an idempotent, identity-validated request through the owning
  worktree command, then wake the existing monitor with the affected key.
- [ ] Process only registered pending work and its bounded retries; remove
  handoff-discovery full-history scans from ordinary monitor ticks.
- [ ] Recover outstanding requests after monitor restart or lost notification
  from durable current state, not replay of machine-global diagnostic history.
- [ ] Preserve `auto`, `manual-only`, and `off` behavior, cancellation,
  successor readiness, and safe predecessor identity/ownership checks.
- [ ] Land code, tests, documentation, and required changefiles; update this
  effort with the merged outcome.

### Phase 3 - Remove wait-only launch layers

- [ ] Establish the per-session launch contract for muxed, non-muxed,
  resumed, new-window, and remote paths before changing ownership.
- [ ] Transfer the ordinary launch to the final execution host without
  retaining `agent_worktrees` or `worktree_manager` Python workers merely
  to wait. Preserve the selected runtime, environment, stdio, and argument fidelity.
- [ ] Preserve exit status, Ctrl+C, launch receipt, post-exit bookkeeping,
  terminal attachment, and ownership/reaping semantics; do not replace
  the waiters with a detached unowned process.
- [ ] Land code, lifecycle regression tests, docs, and required changefiles.

### Phase 4 - Release and measured completion

- [ ] Update consumers through the normal release/deploy flow and projection sync.
- [ ] Repeat single-session and concurrent-session inventories, distinguishing
  interactive host processes, transient helpers, trampolines, and shared daemons.
- [ ] Compare measured CPU and process counts with the pre-change evidence.
- [ ] Resolve all validation items, journal results, and mark this effort Done.

## Validation Plan

- [ ] Duplicate registration for one token is coalesced; a different token,
  worktree, project, or installation cell cannot alias the request.
- [ ] An affirmative trigger wakes processing without waiting for a full
  monitor poll; an idle handoff queue performs zero global activity-log reads.
- [ ] Lost wake, unavailable monitor, restart, request-before-subscription,
  cancellation, and partial persistence cannot lose or spuriously launch work.
- [ ] Manual-only/off/save operations never arm automatic cutover.
- [ ] Crash and PID-reuse tests preserve the current fenced head/readiness and
  predecessor-retirement guarantees.
- [ ] An append-heavy diagnostic log does not scale handoff-discovery work
  with log size or number of unrelated worktrees.
- [ ] Ordinary Windows launches retain zero Python workers in the two
  forwarding roles solely to wait; compare actual process trees, not argv text.
- [ ] One and several concurrent sessions retain exactly one live holder of each
  shared daemon role after bounded generation cutover.
- [ ] Launch parity tests cover mux/no-mux, local/remote, resume/new-window,
  quoted arguments, Ctrl+C, exit codes, and post-exit bookkeeping.
- [ ] Bounded targeted tests and applicable clean-room launch scenarios pass.
- [ ] Public artifacts and documentation are synchronized and publication-safe.
- [ ] Released behavior is verified live, not inferred from merged PRs.

## Proposal

Persist first, notify second. The callback is an affirmative request to the
existing host authority, not a process-management implementation in
`context-handoff`. A dropped wake must cost latency only, never truth.

For launch reduction, a Python parent disappearing is not enough: the actual
execution host must still own attachment, exit reporting, and cleanup.
_(Agent-recommended validation detail: zero wait-only Python workers in the
two measured forwarding roles, with live process-tree evidence.)_

## Journal

### 2026-10-08 - Evidence and scope

- Operator selected affirmative handoff registration and elimination of
  wait-only spawns, and approved this canonical effort name and scope.
- Nonblocking profiles corrected the earlier bridge-versus-forward assumption.
- Existing public trackers cover both findings; avoid a duplicate cache fix
  while #5637's driver is active.
- Plan is drafted; no implementation or production process termination has
  been performed for this effort.
- Evidence and scope claims were published on #5579 and #5664. The first plan
  publication was blocked by existing module-size violations:
  `agent_worktrees/__main__.py` is 7,012 lines against a shrink-only ceiling
  of 7,006; `status_monitor_runtime.py` is 1,001 lines against the 1,000-line
  cap. #5637's existing driver has already claimed the latter repair. Resolve
  the publication gate without bypassing it or duplicating that active work,
  then land the reviewed plan before starting Phase 2.
