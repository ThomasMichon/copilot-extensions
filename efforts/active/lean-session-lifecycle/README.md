# Lean Session Lifecycle

- **Slug:** `lean-session-lifecycle`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-slice PRs targeting `dev`
- **Created:** 2026-10-08
- **Status:** Active
- **Owner:** `curious-bolt` for the Mux-launch continuation
- **Vision:** `visions/plugin-services/README.md` - process-count-scales-with-services-not-sessions and hooks-and-callbacks-are-transient; `visions/plugins/context-handoff/README.md` - host-agnostic trigger and durable recovery
- **Tracking issues:** #5579, #5664, #2619
- **Related, separately driven mitigation:** #5637

## Goal

Make affirmative lifecycle transitions do the work without retaining wait-only
Python forwarding processes or weakening recovery, process ownership, terminal
behavior or handoff mode gates. The current Mux-launch continuation raises the
source-declared compatible minimum and preferred PSMux version to 3.4.0, while
preserving 3.3.5 observation/coexistence for existing live sessions. No forced
upgrade or provider replacement under live sessions is authorized. The current
attach-path repair must recognize an existing live mux and attach through the
existing owner rather than refuse it because an unrelated native process
identity cannot be read; incomplete evidence must still refuse duplicate
creation. The reported live session is not an authorized diagnostic target.
The approved source scope also includes repairing the blocking validation
contracts, deduplicating other owners first: the changed-file strict typing
gate, legacy workflow and installer fixture contracts, and the legacy-plan
cold-start control. No deployment, deadline widening or live-session changes
are included.
The parent retains its broader shim/PowerShell depth outcome and the grouped
process diagnostic's delivered-or-tracked-deferral outcome. This source slice
does not close those gates or transfer #8669's separate custody and installed
acceptance obligations.
The operator subsequently directed this owner to identify the blocking PR's
agent through Bridge, have that owner drive its gates and notify this owner,
and not remain blocked on the PR if no Bridge owner can be identified. This
changes dependency handling, not contributor custody or review/CI authority.
Keep this Mux change independently qualified rather than incorporating or
superseding another contributor's unmerged fixture repair.

### Current-owner charter

| Field | Current assignment |
|-------|--------------------|
| Root goal | "Diagnose the launcher root cause and fix it in source (ThomasMichon/copilot-extensions) with tests and a PR driven through review/CI to merge." Reliable Mux launch remains the outcome, not completion of a validation detour. The broader lifecycle Goal above remains open. |
| Kind | Completion. |
| Acceptance | Qualify the launch and attach repairs through real consumer controls, required strict typing, review and CI, then verify the source PR merged. Preserve legacy-session coexistence, unknown-observation refusal, process custody, seed admission and unchanged deadlines. |
| Finalization criterion | Source merge verified, canonical outcome records published and this source worktree's claims settled through its native owner. Keep broader parent and separately owned installed-acceptance gates open unless completed or explicitly transferred to a tracked owner. |
| Now | The independently qualified Mux source repair merged in #12981 after current-head approval and green CI. Its outcome is recorded in the journal below; source-worktree settlement follows publication of this record. The contributor-owned #12880 repair was not incorporated or superseded. |
| Return | Keep LS5579-P1/P2/V1 and all other pre-existing parent validation, release and installed-acceptance gates open under their existing owners. Source merge grants no rollout, retained-session restoration or live diagnostic authority. |

This charter records existing authority only. It grants no deployment, installed
edits, service restart, deadline widening, retained-session restoration or
diagnostic access to the reported live session. The private continuation record
retains the original operator wording and all superseding limits.

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

The finite #5579 clarification retains the Lifecycle driver, the existing
installed-acceptance owner and #8669's separate installation-independent-session
scope. It grants no competing shared-module implementation or ownership
transfer; silence neither abandons #8669's scope nor accepts a transfer of its
custody. Existing
single-writer and installed lifecycle obligations keep their original scope.

### Mux-launch ownership continuation

The released launch-lifecycle responsibility is accepted by `curious-bolt`.
The immediate slice is diagnosing native Windows mux creation that returns a
session assignment without reaching the pane command or Copilot invocation,
then qualifying a focused source repair through the existing launch owners.
The parent Plan and Validation Plan remain open; this is not installed
acceptance or completion of the effort.

The separate compatibility-first messaging implementation remains with its
own coordination issue, #12487. This continuation does not take over that WIP,
its failing checks, retained resources or another driver's delivery repair.
No deployment, service restart or retained-session relaunch is authorized.

The subsequent attach-path report describes a known live mux being refused
during native observation and then treated as unknown during attachment.
The source investigation and regression controls must preserve that live
session without probing it through effectful provider commands or modifying
it. The unabstracted operator report is retained privately; this public-safe
capture does not add operational authority.

The attach correction reuses an authenticated positive target observation in
the same interactive launch plan. Manager uses its typed boolean hint only for
the existing join path and does not resolve creation support first. Failed
attachment terminates; absent, malformed or false hints retain observation and
the duplicate-creation fence. This removes redundant helper startup from the
positive attach path without changing process custody or seed admission.
Legacy plans still invoke the selected runtime's canonical observation module,
now once per observation rather than starting a separate contract-description
process first. Its response retains contract/version/status validation and
unsupported-runtime refusal; the existing deadlines are unchanged.

The operator selected the following source-only compatibility amendment:

> raise minimum and preferred to 3.4.0 in source
>
> Keep 3.3.5 legacy observation/coexistence working for live sessions; no forced
> upgrade or provider replacement under live sessions.

The existing manifest command transport is retained. Isolated native startup
reproduced failures on 3.3.5, while the same transport and focused regressions
passed against a private 3.4.0 fixture. This is not qualification of an installed
upgrade, every provider operation or the parent effort's remaining acceptance.

The source candidate additionally detaches creation stdin and retains bounded
client failure diagnostics without replaying uncertain creation. Its installer
uses passive process absence, not legacy provider CLI reads, before replacement;
missing PATH entries do not bypass the live/unknown activity hold.
Focused validation passed 24 native/core cases, 12 compatibility/installer
guards and 41 current native release-gate source controls. Legacy observation
controls also passed; source lint, version consistency and the install contract
are green. The declaration was regenerated through its existing owner.

Publication remains pending reconciliation of broader validation failures.
The adjacent legacy workflow controls tracked by #9348 now use an inert,
bounded historical CI recipe for their old guard/refusal contracts while
current workflow assertions retain the actual unit-only declarations and
read-only qualification permissions. All 71 controls in that file pass; this
does not close the issue's other historical qualification fixtures.
The three Windows extended-prefix self-install failures are repaired
by canonicalizing ordinary file-root spellings in the launcher renderer, without
changing other device namespaces or root authority. Five focused controls,
including real CMD front doors at short and long owned roots, pass; the full
Manager self-install file passes 125 cases with two existing skips. Fresh
provider-install policy is covered with isolated fake-package controls, not a
real installed upgrade or a complete clean-room installation. Operational
deployment and retained-session restoration remain unapproved.
The canonical mux creation module, root renderer, observation adapter, launch
planner and independent regression fixtures now pass whole-file strict checks
for both declared analysis platforms. The observation and passive installer
packet passes 75 cases; creation admission passes 18; the final focused launch
consumer packet passes 43. The six opt-in native creation cases were collected
but skipped in the typing-only run, not requalified natively. The attach
consumer's 11 legacy-plan controls passed with the original deadlines after
retaining its masked failure diagnostics; one passing run is not proof of a
timing fix. The prior candidate's strict gate reported 205 errors in the
separately owned seed-delivery fixture; those were not grandfathered.
The seed receiving-fixture and typing correction tracked by #12879 has a
separate contributor owner. Do not publish a competing fixture repair or
incorporate its unmerged implementation. The Mux-specific producer assertions
now live in an independent typed test file; the seed-delivery test file is
unchanged from the comparison base. Both new join-hint/prompt-custody cases
pass, and the complete 16-file, five-project strict ratchet passes both declared
analysis platforms. This removes the source dependency on #12880 without
claiming that its historical fixture failures are repaired or waived here.
A wider resolve packet initially reported 106 passes and 19 failures: 17 adjacent
receiving-ledger fixture mismatches in preview/handoff controls, one stale
topology stub and one nonexistent-workspace policy fixture. Those observations
were coordinated on the contributor's #12880 without changing its open
implementation or weakening the production checks. The independent adjacent
repair was claimed on #12879 after its owner confirmed that it remained outside
the contributor's scope. Those four fixture files now pass whole-file strict
typing on both declared analysis platforms and lint. Seventy unaffected controls
passed in the complete packet; after the final assertion correction all 13
handoff controls also pass, including distinct foreign-platform and
same-platform foreign-namespace refusals. The repaired preview packet includes
its real CLI subprocess and unchanged read-only snapshots. The fixtures use
the actual receiving-ledger location, declared checkout namespace and reporter
context; no production admission check is mocked away or relaxed by this repair.
This resolves the adjacent 19-case blocker, not #12880's separate seed fixture.
The remaining gate is this candidate's normal review, required CI and merge.

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

The 2026-11-01 focused Picker request and mini-effort are captured verbatim in
[Picker Launch Handoff](picker-launch-handoff.md). That sub-plan elaborates
Phase 3 without replacing this effort or taking over adjacent ownership.

## Plan

### Phase 1 - Reviewed design and coordination

- [x] Map launch ancestry and sample representative Python roles before
  attributing costs to duplicate workers.
- [x] Deduplicate against #5579, #5664, #2619, and the independently driven #5637.
- [x] Publish the measured evidence and claim the bounded implementation slices.
- [x] Land this plan through the repository's review gate before implementation.

### Phase 2 - Affirmative handoff registration

- [x] Trace extension and CLI trigger, save, abort, consume, successor-ready,
  and predecessor-retirement transitions against the current host boundary.
- [x] Persist an idempotent, identity-validated request through the owning
  worktree command, then wake the existing monitor with the affected key.
- [x] Process only registered pending work and its bounded retries. (The
  "remove handoff-discovery full-history scans from ordinary monitor ticks"
  half of this item is already satisfied -- see Journal, #5742 -- by a
  separately-driven, more general fix. #5966 supplies bounded pre-spawn retries;
  #10167 completes idle eligibility before history acquisition. Installed
  observation remains in Phase 4.)
- [x] Recover outstanding requests after monitor restart or lost notification
  from durable current state, not replay of machine-global diagnostic history.
  (Already structurally satisfied -- see Journal: the wake is latency-only.)
- [x] Preserve `auto`, `manual-only`, and `off` behavior, cancellation,
  successor readiness, and safe predecessor identity/ownership checks.
  (Unaffected -- see Journal: the wake adds no new mode-gated behavior.)
- [x] Land code, tests, documentation, and required changefiles; update this
  effort with the merged outcome.

The Phase 2 continuation reuses #5966's existing bounded retry policy: only
an attempt that provably created no successor can retry, with the existing
three-attempt cap and claim-staleness interval. Unknown or possibly started
successors are not replayed. #10167 defers requested/attempted history until a
durable handoff is armed, unconfirmed and has a token. These are source
completion claims, not installed-monitor or complete handoff-validation proof.

### Phase 3 - Remove wait-only launch layers

Focused mini-effort: [Picker Launch Handoff](picker-launch-handoff.md).

- [x] Establish the per-session launch contract for muxed, non-muxed,
  resumed, new-window, and remote paths before changing ownership.
- [x] Transfer the ordinary launch to the final execution host without
  retaining `agent_worktrees` or `worktree_manager` Python workers merely
  to wait. Preserve the selected runtime, environment, stdio, and argument fidelity.
- [x] Preserve exit status, Ctrl+C, launch receipt, post-exit bookkeeping,
  terminal attachment, and ownership/reaping semantics; do not replace
  the waiters with a detached unowned process.
- [x] Land code, lifecycle regression tests, docs, and required changefiles.
- [x] Qualify the independent Windows PSMux creation/attachment repair through
  current-head review and CI, then verify #12981 merged. See the
  [source outcome](journal/2026/11.17-mux-source-outcome.md); this closes no
  broader shim, diagnostic, release or installed-acceptance item.

Phase 3's source slice landed in #9936. The changed boundary is ordinary
Windows foreground preparation and terminal-owned attachment; unchanged
no-mux, remote, new-window and POSIX paths retain their prior behavior.
This does not satisfy Phase 4's authorized installed rollout, exhaustive
transient-process inventory or live shared-daemon singleton proof. The
mini-effort retains those distinct open gates. Phase 2's source continuation
landed separately in #10167; the remaining Validation Plan and tracking issues
are not closed by either source slice.

The acquisition prerequisite's source landed in #10198. The operator resumed
the charter after its winddown. Bootstrap and installed qualification remain
open in the mini-effort, including the flagged CMD resident-picker entry found
during live process observation; no retained session is migrated by this source
continuation.

- [ ] **LS5579-P1:** Retain the broader command-shim and nested-PowerShell
  depth-reduction outcome separately from the two credited Python forwarding
  roles. Define the representative supported Windows launch paths and measure
  persistent ancestry separately from transient helpers. Preserve literal
  arguments including `--`, the interactive-profile decision, terminal I/O,
  Ctrl+C, exit/receipt bookkeeping, Job containment and safe reaping. A warm
  broker or direct PSMux attachment is not a mandated architecture.

### Phase 4 - Release and measured completion

- [ ] Update consumers through the normal release/deploy flow and projection sync.
- [ ] Repeat single-session and concurrent-session inventories, distinguishing
  interactive host processes, transient helpers, trampolines, and shared daemons.
- [ ] Compare measured CPU and process counts with the pre-change evidence.
- [ ] **LS5579-P2:** Explicitly account for the retained grouped-process
  diagnostic wish: deliver it OR record a scoped deferral to a named tracked
  outcome under its existing owner. The suggested lightweight view of one's
  live processes grouped by purpose is not silently satisfied by internal
  qualification inventories, silently dropped, or made a mandatory new
  implementation architecture.
- [ ] Resolve all validation items, journal results, and mark this effort Done.

The diagnostic prerequisite in #10514 removes Windows daemon-status probe
subprocesses without changing cutover authority. The installed `0.6.291-dev1`
reader matches the reviewed modules and verifies the same selected daemon
across two refresh cycles with subprocess probes forbidden. This qualifies the
reader prerequisite, not the remaining launch or shared-daemon inventory.
The failed full-update outcome remains an independent open gate.
The blocking-reader correction in #10562 subsequently lands the ordinary JSON
readiness wait and its terminal-error controls. A promoted snapshot includes
that merge. The normal updater subsequently adopts `1.24.475-dev1`; loaded
client/feed identities, authenticated serving health, the ordinary JSON CLI,
isolated delayed-publication controls and caller-owned timeout cleanup qualify
the blocking-reader prerequisite. Together these restore the saved installed-set
continuation, not the remaining parent gates. The separately queued Worktrees
target completes successfully and verifies the same qualified reader in
`1.24.477-dev1`. The broader update finishes unsuccessfully with five component
failures; whole-set acceptance and the remaining parent gates stay open.
The routed-port observation follow-up in #10916 subsequently lands with
birth-qualified active-route discovery and an explicitly cold Companion
fixture. Exact-head Linux/Windows native qualification and required CI pass;
the installed reader and the remaining serving-generation/whole-update gate
are still separate. No Phase 4 or Validation item closes from that source merge.

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
- [ ] **LS5579-V1:** Qualify the remaining shim/PowerShell depth outcome on
  representative supported Windows paths, distinguishing persistent ancestry
  from transient helpers and preserving argument/profile, terminal, receipt,
  Job and reaping behavior. Record the diagnostic wish as delivered OR
  explicitly deferred to its named tracked outcome. Keep credited Python-role
  removal, source/release proof and authorized installed acceptance distinct;
  no mandatory warm broker, direct PSMux path or expanded platform gate follows.
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

### 2026-11-17 - Mux source repair merged

[Source outcome, validation limits and retained parent gates](journal/2026/11.17-mux-source-outcome.md).

### 2026-11-15 - Broader shim scope and diagnostic wish retained

Added three unchecked #5579 Plan/Validation clauses without reopening the
credited two-role Python handoff. The remaining command-shim/nested-PowerShell
outcome and persistent-versus-transient evidence are direct acceptance.
The operator-facing grouped diagnostic remains a suggested direction with
an explicit delivered-or-tracked-deferral outcome, not a silently discarded
wish or a mandated architecture. Existing driver, #8669, single-writer and
installed-acceptance boundaries remain; no implementation, rollout, source
retirement or resource effect is claimed.

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

### 2026-10-08 - Plan published and merged; Phase 2 slice 1 superseded

- The plan PR landed as #5725 (module-size blockers had already been
  resolved on `dev` in the interim by other work -- no separate blocker fix
  was needed). Phase 1 is complete.
- Traced the full note-handoff/trigger/consume/retire path and confirmed the
  precise Phase 2 hot-path root cause: `activity.read_events()` re-scanned
  and re-filtered the ENTIRE machine-global event history on every call --
  `jsonl_cache` already parses the underlying file incrementally, but
  `read_events()`'s own Python-level filtering was still O(total history),
  called up to 3x per worktree per monitor tick for every worktree with a
  pending/active handoff. Matches this effort's measured profiler evidence.
- Built and reviewed a fix (a per-`(worktree_id, event)` incremental index)
  as PR #5739, including two rounds of real Copilot-review-caught
  concurrency/correctness fixes (a cross-caller snapshot-bounding race, and
  an `id()`-based generation token CPython could coincidentally reuse).
  Before merge, discovered **#5742** ("Index lifecycle activity queries
  incrementally"), landed independently to `dev` during the same window by
  a different session under the same account -- solving the identical
  problem more generally (a generic multi-field `match=` index built inside
  `jsonl_cache`'s own single existing lock, which structurally avoids the
  need for a second lock or a separate generation token at all). Verified
  via an actual rebase that every hunk of #5739's diff was redundant once
  #5742's version was taken, and that #5742's own test coverage
  (`test_activity_index.py`, including a concurrent-access test) was at
  least as thorough. Operator confirmed: abandoned #5739 rather than land a
  no-op PR duplicating already-landed work.
- Net effect for this effort: the "remove handoff-discovery full-history
  scans from ordinary monitor ticks" half of Phase 2's third Plan item is
  now satisfied -- by #5742, not by a PR of this effort's own. The
  remaining Phase 2 work (persist-then-affirmatively-wake the resident
  monitor via its existing `hook_ipc` channel instead of relying on its
  periodic tick to discover a newly registered handoff; bounded-retry
  pending-work processing; restart/mode-preservation guarantees) is
  unaffected by #5742 and remains open.
- Lesson for future slices: check `git log --oneline HEAD..origin/dev`
  immediately before opening a review-ready PR (not just once at branch
  start) when a slice targets a shared, actively-touched module -- this
  cost one full review cycle that a fresher pre-push check would have
  caught before investing in the review itself.

### 2026-10-08 - Phase 2 slice 2: affirmative cross-process wake

- Traced the resident monitor's existing wake machinery end to end:
  `resident_push.notify()` is in-process only (a `threading.Event`); the
  ONLY existing cross-process channel into an already-running resident is
  `hook_ipc.HookIpcServer`, today reached only by a live Copilot session's
  own `postToolUse`/`sessionStart`/etc. hook callbacks (via the standalone,
  package-import-free `scripts/hook_client.py`, invoked as a subprocess by
  `hooks.json`) -- `note-handoff`'s CLI path never spoke to it at all, so a
  newly registered handoff sat durably persisted but undiscovered until the
  monitor's next periodic sweep tick.
- Added `hook_ipc.send_best_effort(kind, payload, *, lock_path=None,
  timeout=1.0)`: an in-package client counterpart to the existing server,
  reading the same `status-monitor.lock` rendezvous file and speaking the
  same wire protocol as `scripts/hook_client.py` (deliberately NOT shared
  code -- that script must stay import-free of this package for
  bootstrap-before-install reasons). Never raises; a missing/unreachable
  resident, a stale lock, a bad token, or an explicit fallback response all
  resolve to `False` -- a missed wake costs only latency, the periodic
  sweep interval remains the unconditional backstop (per
  `_wake_interruptible_wait`'s own existing docstring).
  `status_monitor_cli._kind_wakes_sweep` gained a new `"handoffWake"` kind
  that wakes the sweep loop unconditionally (no `targets` semantics of its
  own -- unlike `postToolUse`, it is never about invalidating a cached
  status segment). `cmd_note_handoff` now calls `send_best_effort` with the
  worktree id AFTER its existing durable persist (`tracking.open_handoff`/
  `_note_disposition_snapshot`) -- persist-then-notify, never the reverse.
  Deliberately did NOT touch `__main__.py`'s `_resident_hook_decision`
  dispatch: it already falls through to an unconditional `return {}` for
  any unrecognized `kind`, which is exactly the right no-op for this one,
  and `__main__.py` sits EXACTLY at its 6,979-line shrink-only ceiling (a
  first attempt to add an explicit branch there pushed it 2 lines over;
  reverted once the no-op fallthrough was recognized as sufficient).
- Phase 2's remaining restart-recovery and mode-preservation Plan items are
  satisfied by this design directly, not by new code: the wake is a pure
  latency optimization layered on top of the SAME already-durable
  `tracking.open_handoff` persistence and the SAME `auto`/`manual-only`/
  `off` mode gating `cmd_note_handoff`/the monitor's sweep already enforced
  before this slice -- nothing about restart recovery or mode semantics
  changed, so both items are checked off with that rationale rather than
  new code.
- Confirmed no overlapping work landed on `dev` before opening this slice's
  PR (applying the lesson above).
- Only Phase 2's third Plan item (bounded-retry/pending-work processing,
  independent of the already-resolved history-scan half) remains open.
- Landed as PR #5849 after 6 review rounds. Rounds 1-5 each caught a
  genuine, distinct bug (never a restated/duplicate finding), consistent
  with this effort's earlier #5739 experience: a cross-caller
  snapshot-bounding-style class of issue recurred in miniature (a
  stale/mismatched installation-context lock accepted as reachable), plus
  a test that passed for the wrong reason (a double-started real server
  silently swallowed by production's own startup guard, masked because the
  test's dial happened to race ahead of that failure), a
  persist-before-notify test that only checked the call happened rather
  than the ordering, a test that only covered the legacy-mode branch of a
  new validation function, and two narrow never-raises gaps (a port outside
  1-65535 and an overflowing timeout each raising `OverflowError`, not
  `OSError`). Round 6 added no new finding -- it repeated the
  already-addressed oversized-port comment's wording in its summary while
  listing only the persistent cutover-statement thread below, confirming
  the fixes had converged. One review thread (a required **Graceful
  cutover impact** PR-description statement) stayed marked "active" across
  every round despite the statement being verified present
  in the live PR body each time (`gh pr view --json body`) -- treated as a
  non-blocking tooling artifact per the repo's own commented-verdict
  fallback policy, not a genuine gap, after independently confirming
  compliance with `CONTRIBUTING.md`'s actual requirement text. Merged via
  `pr-merge --now`; worktree reconciled via `pr-complete`.
- Also filed #5856 (tracked, not fixed -- out of this effort's scope): an
  unrelated, confirmed-pre-existing `dev` test failure
  (`resolve_active_plugins()` lost its `include_projects` kwarg, breaking
  `local_cache_refresh.py`), discovered incidentally while validating this
  slice and verified via `git stash` against plain `dev` before filing.

### 2026-11-01 - Picker launch handoff mini-effort

- Added [Picker Launch Handoff](picker-launch-handoff.md) as the bounded Phase 3
  proposal under #5579, preserving the original Request and existing ownership.
- The operator expects Picker Python to exit after launch/resume. The proposed
  first target is a shell-owned handoff, with direct PSMux attachment a preferred
  follow-on simplification only if lifecycle duties remain covered.
- Planning only: no runtime implementation, retained-session migration, process
  termination or installed rollout. The parent Plan and Validation Plan remain
  open; creating this sub-plan is not completion of the launch-reduction work.

### 2026-11-03 - Bounded pending-work continuation

- Reconciled the remaining Phase 2 item with #5966: productive pre-spawn
  retries already have a three-attempt cap, and ambiguous or possibly started
  successors remain non-replayable. No new retry interval or policy is needed.
- The monitor still acquired requested/attempted history before checking its
  durable record for eligible work. The bounded correction defers both reads
  until the first armed, unconfirmed, nonempty token, retaining newest-first
  selection and the existing retry/claim owners.
- The focused pending-request and bounded-retry packet passes 16 cases,
  including six idle eligibility cases that forbid history, request-file and
  claim access. Three older attempt fixtures now explicitly declare their
  armed state so they continue to exercise attempt suppression rather than
  passing merely because they are ineligible.
- Python lint, the complete module-size check and the 13-plugin installation
  contract pass. Review, required CI, source merge and installed observation
  remain separate gates. The authorized rollout blockers and selected-runtime
  query evidence are journaled in the linked Picker mini-effort.

### 2026-11-03 - Idle-path correction merged

- #10167 merged at `776a70b9ed289d6fbfec1827467cb497633c9c9f` after
  exact-head Copilot approval, the resolved journal-placement correction and
  green required CI `41577893454`. The managed merge used the normal
  repository-authorized review exception, not a CI bypass.
- Re-read the parent Plan/Validation Plan and linked mini-effort after merge.
  Phase 2's remaining source item now has explicit credit to #5966 and #10167.
  The combined handoff-validation items, release/adoption, exhaustive transient
  inventory and live singleton measurements remain open.
- Pull-forward encountered the already-squashed local commits and aborted
  without altering the branch. After verifying the provider's merge and exact
  equality of all four affected source/effort files, retained a backup and
  advanced the branch without replaying the merged commits. No content was
  discarded to guess a conflict resolution.
- The next bounded prerequisite is the opaque native payload-copy progress
  contract in #10159; the existing Manager preparation owner retains #10126.
  Coordinate readiness with the installed-acceptance owner before any new
  update. A source merge does not release those operational gates or complete
  this effort.

### 2026-11-07 - Ordered credential-read prerequisite merged

- #11075 merged to `dev` as `04de3c62cf9a50476239d2e8f5d8d2d315ab6a3f`.
  The additive `batch-get` operation reuses the ordinary credential-read path
  and advertises `batch-get-v1` through the selected runtime's invocation
  contract. Legacy `get` remains supported.
- The accepted source head received Copilot approval, resolved its review
  thread and passed required CI `42316292840`. Exact-head Linux and Windows
  qualification `42315656780` passed; both actual native batch cases ran
  without skips and completed their normal fixture cleanup.
- Source controls preserve ordered results, protected cache/key bytes and
  pending journals across expected failures, including legacy aliases.
  One credential CLI handles the batch; existing peer qualification and
  transport helpers retain their ordinary behavior.
- Re-read the parent Plan and Validation Plan after merge. No Phase 4 or
  Validation item closes from this prerequisite. Promotion, actual selected
  receiver support, configured-consumer qualification and whole-update
  acceptance remain separate gates; no live deployment or session transition
  was part of this source acceptance.
