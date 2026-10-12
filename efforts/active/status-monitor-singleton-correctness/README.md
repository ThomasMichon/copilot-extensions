# Status-monitor singleton correctness

- **Slug:** `status-monitor-singleton-correctness`
- **Repo:** copilot-extensions
- **Branch(es):** Sequential phase branches, one reviewed PR per phase
- **Created:** 2026-10-06
- **Status:** Draft
- **Vision:** [Status-monitor singleton correctness](../../../visions/plugins/agent-worktrees/status-monitor-singleton/README.md)
- **Umbrella issue:** _none — this README is the umbrella; see Sub-issues_
- **Sub-issues:** #5453 (replacement and promotion/cutover exclusivity) · #5512 (restart-seam exclusivity)

## Guiding Intent

Realize Vision
[`plugins/agent-worktrees/status-monitor-singleton`](../../../visions/plugins/agent-worktrees/status-monitor-singleton/README.md):
at every instant, including mid-transition, at most one resident
`status-monitor` process is actively serving per host, no transition
leaves an unbounded zero-coverage window, and exactly one resident serves
at healthy steady state while service is demanded. Intentional idle shutdown
with no remaining demand may leave zero residents. PR #5412 establishes the ordinary-cold-start
foundation only; it does not cover all lifecycle transitions. This effort
closes replacement, the promotion/cutover handoff, and the restart
(auto-update) seam. Restart is Phase 1 / #5512; muxless/superseded-runtime
replacement and promotion/cutover share Phase 2 / #5453.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Effort owner | Sole planning and phase-PR owner; owns restart, then replacement/cutover | This effort and its public issue/PR discussions |

## Coordination

- **Topology:** Sequential, independent per-phase branches and PRs based on
  current `dev`. The planning PR precedes implementation. Phase 1 follows
  the verified #5412 prerequisite; Phase 2 design starts only after Phase 1
  merges.
- **Host (owns PRs):** The effort owner is the sole writer and PR owner for
  each phase, using the normal `copilot-extensions` worktree/PR flow.
- **Delegates:** None; no parallel phase design or overlapping writers.
- **Handoff:** A successor explicitly takes sole ownership in the public
  coordination discussion before continuing. The outgoing owner records
  the current phase, branch/PR, validated head, outstanding findings and
  next action in the Journal. A handoff preserves the same effort, scope
  and phase PR rather than creating competing planning or implementation.

## Context

Ordinary-cold-start serialization with an OS-mediated exclusivity lease
does not by itself establish safe lifecycle ownership transfer:

- **#5453** — a replacement candidate that loses the cold-start lease
  must not abandon prompt replacement of a still-live muxless or
  superseded-runtime incumbent. The promotion/cutover transition also
  requires orchestrator-level sequencing: promoting the successor before
  requesting the predecessor's drain permits both to serve, while the
  predecessor's self-retire interval can exceed a short bounded wait.
  A real fix coordinates reversible ownership transfer at a safe drain
  boundary in `libs/zdd/src/zdd/cutover.py` and
  `status_monitor_cutover.py`, with `status_monitor_cli.py` matching that
  protocol; changing the CLI lease guard alone cannot establish it.
- **#5512** — the restart seam (`_restart_status_monitor()` in
  `status_monitor_runtime.py`, invoked by `status-monitor-restart`) needs
  exclusivity between predecessor retirement and successor activation.
  Its design must handle termination failure without skipping the wait,
  preserve a concurrent new owner's published metadata, and use actual
  exit/identity evidence rather than `os.kill(pid, 0)`-based existence
  checks that confuse a terminated, unreaped zombie record with a serving
  process or a recycled PID with the predecessor.

These are the same class of problem — transition points requiring the
same exclusivity standard as cold start — which is why the vision states
one standard across all four transitions rather than treating them as
unrelated bugs. The Journal records the original scope-down decisions and
review history; they are not implementation claims.

## Request

Operator request, verbatim: "Revert, build out a vision, write an
effort stating the desired outcome (effective singleton process), and focus
on staying on the rails to avoid scope creep." The vision is
`visions/plugins/agent-worktrees/status-monitor-singleton/README.md`; this
effort is the follow-up, explicitly scoped to stay narrow: one phase
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
  introduces; the prerequisite establishes that foundation only, not the
  start or completion of either implementation phase.

### Phase 1 — Restart-seam exclusivity (#5512)
- [ ] Design the restart seam's exit-aware wait + identity-scoped cleanup
  per the vision's `exit-awareness-not-existence-checking` and
  `identity-scoped-cleanup` behaviors. Specifically address the
  terminate-failure-skip and concurrent-new-owner-metadata-deletion races,
  not only a bounded wait followed by a PID-existence re-check.
- [ ] Define the process-exit and PID-identity contract separately for
  Windows, Linux and macOS, including the native evidence each uses,
  termination failure, bounded uncertainty and cleanup. Linux `/proc` or
  `pidfd` facilities are not generic POSIX or macOS proof. A terminated
  zombie is non-serving and must not block admission merely because its
  record has not been reaped.
- [ ] Add regression coverage for all three known races (terminate failure,
  concurrent-publish-during-wait, zombie/PID-reuse) before considering the
  phase done.
- [ ] Define bounded recovery when successor spawn fails or the successor
  exits before metadata publication after predecessor retirement. Specify the
  deadline, retry/recovery owner and visible failed/deferred outcome; a restart
  cannot be declared complete with an unbounded zero-serving interval.
- [ ] If PID-based termination remains, require both an identity-bound process
  token and independent proof that the process is the intended resident owner
  before signaling. Refuse stale identity, wrong ownership or uncertainty;
  otherwise replace PID-based termination with an ownership-safe primitive.
- [ ] Define and implement the reader-side ownership observation required by
  `a-reader-never-observes-a-false-resident`: distinguish no owner from an
  acquired lease whose resident metadata is not yet published, and distinguish
  both from a validated serving resident. Readers must report bounded starting
  or uncertain state rather than infer absence or healthy service from metadata
  presence alone. Phase 2 must preserve this contract during ownership handoff.
- [ ] Open a PR scoped to this phase only; drive it through review to merge
  per the usual `copilot-extensions` PR flow.
- [ ] Close #5512 on merge; update this README's Journal.

### Phase 2 — Replacement and promotion/cutover exclusivity (#5453)
- [ ] Design an ownership handoff for muxless/superseded-runtime
  replacement that permits a legitimate successor to replace the stale
  incumbent without overlapping active service or abandoning replacement
  simply because the incumbent still holds the cold-start lease.
- [ ] Design the cutover orchestrator's reversible confirm-then-promote
  (or drain-then-promote) ownership transfer at the safe drain boundary in
  `libs/zdd/src/zdd/cutover.py` / `status_monitor_cutover.py`,
  per #5453's own scope section.
- [ ] Inventory the shared `zdd.cutover` consumers and preserve their existing
  active/passive contract through an opt-in seam, or validate every changed
  shared behavior across all affected consumers before publication. Synchronize
  any real vendored copies, including agent-worktrees, and add every affected
  consumer's required changefile using the shared-library release rules in
  `docs/pipelines.md`; canonical editable references do not replace release
  materialization or compatibility proof.
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

_(Agent-recommended evidence for the requested outcome; implementation
and replacement/cutover proofs belong to the later phase PRs.)_

- [ ] Phase 1: new unit/regression tests reproduce each of the three named
  races against the old code (red), then pass against the fix (green); full
  `tests/test_status_monitor.py` suite stays green; lint
  (`ruff check --select F,E9`) and `tools/check-module-size.py` clean.
- [ ] Restart activation-failure proof: inject spawn failure and successor exit
  before publication after predecessor retirement. Demonstrate bounded recovery
  or an explicit reported failure/deferred state, preserved identity-scoped
  metadata and no overlapping active residents before Phase 1 closes.
- [ ] Direct termination safety unit tests, if PID termination remains: a
  genuine matching resident is terminated, stale/reused identity is refused,
  and matching identity with a wrong owner is independently refused without
  signaling. These direct tests do not rely solely on end-to-end restart proof.
- [ ] Phase 2: new activation-path regression tests exercise muxless and
  superseded-runtime replacement and an actual cutover transition (not only
  mocked control-server calls). Demonstrate that a legitimate replacement
  eventually serves within the defined transition deadline, losing
  candidates cannot publish or delete another owner's metadata, and
  the predecessor cannot continue serving once the successor is confirmed
  promoted; existing cutover/control-server test suite stays green.
- [ ] Reader-state regression: pause an actual contender after lease acquisition
  but before metadata publication and prove readers distinguish it from no
  owner and from a validated serving resident. Cover stale metadata and the
  Phase 2 ownership-handoff window; no transition may fabricate absence,
  healthy service or an unbounded uncertain state.
- [ ] Shared-cutover compatibility: exercise unchanged/default behavior for
  existing `zdd.cutover` consumers and the selected singleton handoff behavior.
  Verify development and release-materialized imports, synchronized vendored
  copies and all affected consumer changefiles before the Phase 2 PR merges.
- [ ] Platform accounting for both phases: complete the design and
  validation obligations below on Windows, Linux and macOS. Any unavailable
  proof requires an explicit justified exemption documenting the degraded
  behavior and its impact on the cross-platform guarantee before that
  phase can be considered complete; Linux results alone cannot satisfy it.

  | Platform | Design obligation | Validation obligation |
  |----------|-------------------|-----------------------|
  | Windows | Identify the native process identity and exit signal, including a retained handle/record after termination; do not equate PID visibility with active service. | Exercise real predecessor termination and termination failure, retained terminated-process evidence, PID-reuse protection and concurrent-owner metadata publication; prove exclusive admission and bounded zero-coverage windows. |
  | Linux | Distinguish execution-capable processes from terminated zombies using identity-bound exit evidence; any `/proc` or `pidfd` use is Linux-specific. Reaping remains cleanup, not an admission prerequisite. | Keep a terminated child unreaped and prove it cannot serve or defer replacement indefinitely; also cover termination failure, PID reuse and concurrent-owner publication through real lifecycle boundaries. |
  | macOS | Specify native identity and termination evidence without assuming `/proc` or Linux `pidfd` availability; distinguish terminated unreaped children from execution-capable incumbents. | Demonstrate the native path on macOS, including terminated/unreaped-child admission, termination failure, PID-reuse protection and concurrent-owner publication; prove exclusive admission and bounded zero-coverage windows. |

- [ ] After both phases merge: re-read Vision
  `status-monitor-singleton`'s Behaviors section and confirm each one holds
  for all four named transitions (ordinary cold start via #5412; restart
  via Phase 1; replacement and promotion/cutover via Phase 2). Prove
  at-most-one active server throughout, bounded intentional zero windows,
  and exactly one serving resident at healthy steady state while service is
  demanded, preserving healthy zero-resident idle shutdown; record the
  confirmation in the Journal rather than assuming it.

## Proposal

_Pending — design work starts at Phase 1 after the planning review gate;
this draft does not implement or validate either phase._

## Journal

### 2026-10-06 — Kickoff
- Effort created after reverting PR #5412's replacement-path and
  restart-wait exclusivity extensions back to the narrow ordinary-cold-start
  fix (operator-directed scope-downs), and after authoring Vision
  `plugins/agent-worktrees/status-monitor-singleton` to state the
  all-transitions standard those extensions were failing to meet piecemeal.
- Across more than ten review rounds, attempts to extend exclusivity into
  muxless/superseded-runtime replacement and restart surfaced real
  concurrency bugs. Both extensions were reverted rather than accumulated
  as point fixes. The cold-start bug was a burst of concurrent starts
  producing 100+ duplicate residents; its fix did not prove the remaining
  transitions safe.
- The restart attempts used a bounded wait with `pid_alive()`, then a
  post-wait re-check. Termination failure could skip the wait; unconditional
  metadata deletion could clobber a concurrent new owner; PID-existence
  probes could misclassify zombies or reused identities. These independent
  failure modes require exit-aware, identity-scoped ownership transfer,
  not simply restoring the reverted wait.
- Filed #5512 (restart-seam races) alongside the pre-existing #5453
  (promotion/cutover) as this effort's two sub-issues/phases.
- Verified #5412 merged on 2026-10-06, satisfying the ordinary-cold-start
  prerequisite only; implementation phases remained unstarted.

### 2026-10-10 — Planning review corrections
- Confirmed #5412 merged on 2026-10-06, satisfying Phase 0 only.
- Made the deferred replacement path an explicit Phase 2 deliverable and
  validation target under #5453; no implementation phase has started.
- Corrected inception dates to the PR's 2026-10-06 creation/review history.
- Aligned the vision subject with the at-most-one serving invariant and
  intentional bounded zero-coverage windows.

### 2026-10-12 — Coordination and timeless planning
- Added the solo ownership table, sequential branch/PR topology and
  explicit ownership handoff. The coordination contract prevents concurrent
  writers and preserves the effort and each phase's review history.
- Moved review chronology and scope-down history into this Journal while
  keeping technical causes and the original request in the planning body.
  Cold-start serialization remains the completed prerequisite only;
  restart, muxless/superseded-runtime replacement and promotion/cutover
  remain separate, unimplemented deliverables.

### 2026-10-12 — Exit semantics and platform proof
- Corrected exit-awareness to treat a terminated zombie as non-serving
  even while its record is unreaped. Requiring record disappearance would
  let PID-existence probes defer replacement indefinitely; identity-bound
  termination evidence must reject PID reuse without mistaking visibility
  for active service.
- Added explicit Windows, Linux and macOS design and validation obligations
  for both phases. Linux-specific liveness facilities cannot establish
  macOS coverage; a missing platform proof requires a justified exemption
  with its degraded behavior and guarantee impact recorded.
