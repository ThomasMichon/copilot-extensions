# Bounded Passive Git Observation

- **Slug:** `bounded-passive-git-observation`
- **Repo:** copilot-extensions
- **Branch(es):** plan-only PR, then independent dependency-ordered PRs to `dev`
- **Created:** 2026-10-23
- **Status:** Active
- **Vision:** `visions/plugins/agent-worktrees/README.md`, derived status and resident authority; `visions/plugin-services/README.md`, service-scaled process ownership
- **Umbrella issue:** #7944
- **Sub-issues:** #7945

## Guiding Intent

Bound the complete initial/default-branch Git observation, report uncertainty
truthfully, and prevent passive classification from restarting a dark
worktree's fsmonitor. Preserve actual classification, live-session watchers,
operator configuration and unrelated mutation admission.

This closes existing bounded-observation and service-ownership intent; it
does not extend the vision or authorize implementation. Follow the existing
performance-measurement, headless-launch and process-containment patterns.

### Current admitted slice

The Windows-first R1a amendment includes local deadline propagation, atomic
creation-time Job ownership, retained reader/root custody, and the narrowly
admitted guarded recovery integration. It does not admit R1b lock separation,
R2 passive-fsmonitor policy, POSIX observation containment, or any F1/P/H2
redesign. Ordinary POSIX and genuinely absent-predecessor branches retain
compatibility behavior.

The receiver-bound `guarded-undrain/v1` operation now exists on the existing
authenticated control transport. Preflight retains one existing token and
requires the capability before drain/spawn/publication. The receiver checks
its PID, native start, runtime generation, exact restored route and listening
port under the monitor state lock followed by the existing routing lock;
only then may the existing undrain callback reopen admission. A strictly typed
positive receipt is required. There is no legacy-undrain fallback.

The shared ZDD rollback checks predecessor identity during restoration and
again before undrain. Those caller checks do not replace the receiver's atomic
route fence. The two retained counterexamples now pass. Canonical and existing
physical vendored rollback source remain identical; no dependency-layout
migration or new ZDD API is part of this slice.

Separate opt-in native tests use only disposable, no-breakaway Job-owned
fixtures. They exercise the real installer helper/orchestrator/client/parser/
receiver pairing, capability skew, malformed frames, identity/route mismatch,
third-owner and routing-lock barriers, legitimate restored election generation,
lost/untyped replies, uncertain publication and unconfirmed successor exit.
Actual Git alias descendants additionally prove fixed 15-second D with the
five-second cleanup reserve, a dead Git root with held output pipes, caller
death during execution and before first native resume, verified empty Jobs,
closed native root/readers, cwd/env/text output and ordinary binary input.

These are source/native-fixture receipts, not source acceptance, installed
qualification, a nominal CPU/process benchmark or broader campaign completion.
Current-head review and required CI still gate #8458; keep #7944/#7945
and the unadmitted R1b/R2/platform/integration work open. Earlier hold and
fail-first journal entries below are historical and remain evidence, not the
current implementation status.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Plan author | Source trace, bounded proposal, plan PR only | #7944/#7945 and this effort |
| Integration coordinator | Independent admission of exact implementation hunks | Reviewed proposal and the two tracking issues |
| R1a implementer | Admitted local deadline and opt-in cleanup only | `buzzing-clasp`; #7944 |
| R1b implementer (unassigned) | Observation-lock separation after shared-owner admission | Separate managed worktree after admission |
| R2 implementer (unassigned) | Invocation-local passive fsmonitor suppression | Separate managed worktree after R1 API admission |
| Existing owners | Roster/feed publication, resident control and receipt storage | Existing issue/effort reservations; no reassignment |

## Coordination

- **Topology:** plan-only PR followed by the staged graph in
  [contract.md](contract.md); no functional changes in this planning slice.
- **Host (owns PRs):** plan author owns the plan PR only. Admitted source
  implementers own their individual PRs and validation.
- **Delegates:** none assigned to implementation by this document.
- **Handoff:** return the reviewed exact plan for independent integration
  admission. A merged plan is not permission to start functional work.
- R1 and R2 share `git_ops.py`; run them serially, not as concurrent writers.
  Re-read source and public task/issue/PR claims at each admission.
- Existing F1 roster/entry/feed/publication semantics and P/H2 resident
  control/receipt-storage reservations are exclusions, not work delegated
  here. Required monitor/producer handoffs need their owners' exact-hunk
  admission before execution; otherwise stop at the local deadline slice.
- Do not contact or inspect existing Bridge agents, use agent-chat or launch
  remote workers. Do not restart services, update runtimes, manipulate a live
  fixture or change production fsmonitor configuration.
- Preserve #2619 and #6086 as separately owned open dependencies. This effort
  cannot complete either issue or the broader integration campaign.
- **Later bounded admission:** The original unassigned full-R1/R2 table and P0
  gate do not erase the subsequently declared Windows-first R1a episode under
  its current source custodian. Planning #8372/#8379 is delivered; that narrow
  source admission is not R1b/R2/POSIX/native admission or a new appointment.
  Preserve surrounding F1/P/H2, owned-process and broker reservations.
- **Continuing source custody:** Exact-hunk admission applies to actual shared
  overlaps, not a generic ACK tour. Keep `git_ops.py` writers serial. R2 may
  follow settled R1a while R1b waits on its real snapshot/publication handoff;
  broader #2619/#6086 and cleanup/native owners retain independent duties.
  No arbitrary timeout inflation, process-name kill or all-parent/native-on-
  every-host prerequisite is added.
- **Receiving boundary:** Normal reviewed delivery and complete current
  Plan/Validation/Coordination/Journal readback precede any separately supported
  administrative source judgment. Generic Taking or historical open text is
  not a new token ceremony; actual specific conditions, active source work and
  resource authority remain. No technical completion or claim release follows.

## Context

The bounded source trace uses commit
`68f40c7209f1ed6c66d381bc80ac02611bb3d680`. It confirms the two reported
mechanisms, not a repeat of the preceding audit. Evidence, minimum hunks and
API changes are in [contract.md](contract.md); test commands and stop
criteria are in [validation.md](validation.md).

The source already has a 15-second *per-spawn* classification timeout,
10-second optional remote-default query, five-second bounded-run drain, a
contained Windows subprocess owner, and a five-minute fsmonitor reap
cooldown. None proves a total observation deadline or prevents a later
passive `git status` from restarting a stopped watcher.

## Request

Public-safe capture of the bounded request; private coordination identifiers
and paths are omitted, not transferred to this public plan:

> "establish precise non-overlapping implementation plan for initial/default
> Git probe deadline and passive fsmonitor restart issues"
>
> "single remaining monotonic TOTAL observation deadline; timedout state
> UNKNOWN/degraded, existing classification semantics; Git/network outside
> mutation-admission locks; contained descendants/pipes cleanup"
>
> "Actual repeated classify-cleanup-classify on isolated supported Windows
> Git, dark/finalized none, live unchanged, no process-name kill/global config
> rewrite."
>
> "no new shared framework/service/cache/watcher or arbitrary deadline policy"
>
> "Stop at reviewed plan+proposal (not implementation)"

The requester also requires isolated stall/unrelated-publication and
admission-progress proof, explicit source/API/test ownership, and preservation
of broader #2619/#6086 acceptance. All API names, the partition below and
test-fixture details are **agent-recommended proposals**, not existing APIs
or additional operator authorization.

## Plan

### P0 — Reviewed proposal only
- [x] Confirm the two mechanisms against bounded current source and tests.
- [x] Specify separate R1/R2 contracts, exclusions and test-tier obligations.
- [x] Address a fresh independent review and land the docs-only plan through
  source review and required CI.
- [x] Return exact plan/head/claims to the integration coordinator; obtain
  independent admission before any functional edit. R1a only is admitted;
  R1b and R2 remain gated.

### R1 — One deadline and unlocked observation
- [ ] Implement R1a local deadline propagation and contained cleanup from
  [contract.md](contract.md), without changing global `git()` defaults.
- [ ] Admit and implement R1b's monitor snapshot/observe/commit handoff with
  the existing publication/control owners, not by dropping their lock.
- [ ] Prove initial/default stalls, budget exhaustion, retained semantics,
  descendant/pipe cleanup and unrelated progress through real boundaries.

### R2 — Passive reads do not start watchers
- [ ] After R1 API settles, implement the invocation-local policy and focused
  work-count tests from [contract.md](contract.md).
- [ ] Qualify repeated native classify-cleanup-classify for dark/finalized
  checkouts and the genuine-live-session control.
- [ ] Record review, CI, release and separately authorized installed proof
  independently; no rollout is authorized here.

### Direct retained acceptance from #7944/#7945

These are **agent-recommended retention rows**, not implementation admission
or executed evidence.

- [ ] **P-7944-ORIGIN:** Retain the complete #7944 initial/current/default
  total-deadline, honest UNKNOWN/classifier and contained descendant/pipe
  contract plus served-segment AND warm-roster observation outside mutation
  admission with actual unrelated progress. Reuse existing R1a/R1b
  Plan/contract rows; do not add fresh per-spawn windows or drop locks without
  explicit snapshot/fence authority.
- [ ] **P-7945-ORIGIN:** Retain the complete #7945 invocation-local opt-out OR
  reviewed owner session-lifetime outcome; the reviewed choice is invocation-
  local suppression. Preserve dark/finalized and genuine-live controls,
  operator config and exact cleanup, not a new watcher/lifetime framework.
- [ ] **P-ADMISSION-SCOPE:** Credit merged #8372/#8379 planning and record only
  the already declared narrow Windows-first R1a episode under its current
  custodian. Preserve the still-open P0/full-R1/R2 gates without treating their
  original generic unassigned wording as cancellation of that later narrow
  admission. #8458 was observed OPEN/non-draft, not merged or permission.

## Validation Plan

- [x] Complete the plan-only checks in [validation.md](validation.md).
- [ ] Exercise all R1 unit and real-process cases, including concurrent
  unrelated admission/publication and stale observation rejection.
- [ ] Exercise all R2 policy tests and supported-native cycles; preserve
  operator config bytes and the genuine live watcher.
- [ ] Keep runtime timing qualification unavailable until a representative
  non-saturated host can produce complete attributable samples; mock clocks
  and source reasoning are not a performance pass.
- [ ] Retain #2619's event/controller/backstop and #6086's quiet/concurrent
  spawn-versus-peak gates with their current owners.
- [ ] **V-7944-ORIGIN:** Retain all existing deterministic/real-process
  first/default/segment bounds, original classification/fetch semantics,
  total cleanup/EOF/root proof, stale result refusal and unrelated admission/
  publication before releasing the stall, including negative held-lock
  control. No tests are run by this amendment.
- [ ] **V-7945-ORIGIN:** Retain at least three supported-native classify-
  cleanup-classify cycles for dark active/finalized and a genuine owned live
  session; inspect after each classification and cleanup, preserve config bytes
  and refuse unsupported qualification rather than count a passing skip.
- [ ] **V-ADMISSION-SCOPE:** Map latest exact source-scope declarations and
  actual closeout metadata to the receiving Plan/Validation before inferring
  progress. Source, pure injected checkpoints, native process/latency, release
  and installed proof remain distinct; historical reported host saturation is
  not a new current measurement.

## Proposal

**Agent-recommended, awaiting review and coordinator admission.** Reuse the
existing classification timeout value as a *single total observation
ceiling*, propagate a remaining monotonic deadline through the actual first
and default probes, and use contained subprocess ownership. Split observation
from admitted mutation without introducing another authority or cache.
Prefer invocation-local `-c core.fsmonitor=false` for passive classifier
status reads over a new session-lifetime configuration policy.

The blocking POSIX per-probe census finding has an
[observation-scoped confirmation proposal](posix-confirmation-proposal.md).
It is a design-only request for narrow shared-owner agreement, not admission
to alter procutil or a waiver of the Medium finding.

The [caller-custody and Windows-first design](caller-custody-and-platform-slice.md)
maps the exact cache/background/roster/owner-loss gates. It rejects implementing
the unproved POSIX anchor-lifetime candidate and proposes an explicitly
review-gated platform slice, not disabling ordinary POSIX classification.

## Journal

### 2026-10-28 - Direct retained acceptance and bounded-admission reconciliation

- Retained #7944/#7945 directly in this ROOT's Plan, actual Validation and
  continuing Coordination, preserving original Request, Draft status,
  participants, checked planning credit and first unchecked gates.
- Reconciled the original generic unassigned/full-R1 language with the later
  narrow Windows-first R1a source episode; no new executor or R1b/R2/POSIX/
  native admission is created. Planning credit is not source or installed
  qualification, and observed open #8458 is not a delivered result.
- This paper amendment runs no test, contacts no owner/venue and releases no
  claim/resource. Actual reviewed merge and complete receiving readback
  precede a separate source judgment under its literal conditions.

### 2026-10-23 — Bounded planning
- Resolved a local worktree-class target and compatible effort adoption.
  Exact issue/task/PR searches found no competing #7944/#7945 plan; posted
  public planning-only reservations. Existing implementation owners remain.
- Native child binding reports bound/head true. Platform lifecycle
  qualification remains unavailable; registration is not liveness proof.
- Completed a bounded current source trace. No code, runtime, service,
  production watcher or live stall was changed or tested.
- Runtime timing qualification is unavailable under reported host CPU
  saturation; this is a qualification limitation, not a new benchmark.
- Fresh independent source-grounded Sol review returned PASS for the three
  plan files only; this is not functional implementation admission.
- Plan #8372 merged after current-head source Copilot approval and required
  green CI. Its two Low findings prompted a discoverability-row follow-up
  and repair of a multiline PR-description transport loss. No contract,
  implementation or rollout authority was expanded.
- Follow-up #8379 merged after the existing #8392 repair, preserved repair
  bytes, fresh source approval and green required CI. The coordinator
  admitted only R1a. First targeted units exercise local deadlines, failure
  semantics and cleanup composition; actual native consumers and broader
  concurrency/fsmonitor qualification remain open.
- R1a first source checkpoint: 83 targeted new/existing contracts passed,
  with 99 unrelated/native cases deselected. Focused Ruff, unchanged module
  ceilings, install contract, effort structure, docs consistency and whitespace
  checks passed. New cases cover one deadline, initial/default failures,
  actual fetch attempts, UNKNOWN versus finalized display, reserved cleanup,
  cancellation and unchanged legacy binary/input/timeout behavior.
- The initial Python 3.9 invocation lacked `tomllib`; validation used an
  already available modern interpreter and the normal isolated test wrapper.
  Two failing test adapters/schema assertions were corrected and the combined
  selection rerun green. No native timing/process qualification is inferred.
- Requested checkpoint precedes actual isolated consumer/shim, held-pipe and
  native cleanup expansion. R1b, R2, installed proof and root acceptance remain
  unadmitted/open; no service or production fixture was changed.
- Source review #8458 identified three High cleanup/capability faults and a
  blocking Medium per-probe global-census cost. Seven pure regression cases
  failed before the admitted High fixes; all 90 combined targeted cases then
  passed, with 99 unrelated/native cases deselected. Full capability preflight,
  failed-Job custody and cleanup-error propagation are corrected in R1a-owned
  hunks only. Legacy non-observation paths remain unchanged.
- The Medium stays blocking. Its observation-scoped API proposal is documentation
  only and requires coordinator/shared-owner agreement before any implementation.
  No procutil/shared API, native fixture, service or production deadline changed.
- Fresh review confirmed all three original High findings resolved, and raised
  a new High for resident callback loops discarding cleanup custody. The scoped
  proposal records those separately owned caller gates and the unresolved
  caller-death/lifetime boundary. Neither that High nor the Medium is waived;
  no unadmitted callback or shared-library implementation was changed.
- Bounded follow-up design traced the actual segment cache, snapshot, bundle
  request/background cache, classify, warm-roster/list and drain continuations.
  The concrete common carrier is the monitor's existing segment-cache instance,
  with frame-local custody for the standalone updater. The design names exact
  typed catches, next-tick settlement, stale-data/fallback gates and lifecycle
  amendments rather than treating two callback catches as a complete fix.
- No proved universal POSIX outer-lifetime boundary was established. Do not
  implement the anchor candidate. The proposed Windows-first amendment retains
  ordinary POSIX behavior and its open #7944 work; neither alternative nor
  caller/native-API hunk is implemented or admitted by this design checkpoint.

### 2026-10-24 — Admitted Windows-first source checkpoint

- Implemented the admitted same-instance segment-cache carrier and explicit
  bundle/classify/request/background/snapshot/served/warm-roster continuations.
  The standalone updater retains frame custody and its existing native lease.
  Project/config/title effects remain under their existing locks.
- Added the narrow canonical `JobHandle.try_terminate_and_confirm_empty` API.
  False/error retains the actual handle; successful emptiness alone does not
  release root/readers. The legacy positive-timeout contract is unchanged.
- Closing rejects new observations, not late cleanup transfer. Invalidation
  and record removal cannot discard tickets or refresh stale evidence. Lost
  served-path hints consult retained ownership before activation; unresolved
  unknown scopes defer while independent known projects continue.
- Fail-first regressions reproduced capability/handle/fallback, carrier and
  actual continuation failures. Two additional fail-first served-path cases
  exposed discovery before custody gating; a POSIX network-timeout case
  exposed and corrected a compatibility regression. Actual updater/monitor
  negative controls detect discarded custody or premature release.
- Latest bounded selection: **124 passed, 99 deselected**; canonical shared
  Job tests: **22 passed, 1 real-process case deselected**. See
  [validation.md](validation.md) for commands, receipts and exclusions.
- No native run, service/installed update, deadline growth, ready marker,
  merge or issue closure is claimed. Source review must reassess the new High
  and blocking Medium against this amendment; local units do not resolve
  either review obligation on their own.

### 2026-10-26 - Guarded recovery and isolated native qualification
- Admitted the strict receiver operation and bounded existing ZDD integration;
  retained legacy/POSIX/default-absence behavior and all other scope holds.
- Real native transport fixtures: 14 passed; full installer/orchestrator
  ambiguity/rollback fixtures: 6 passed. Actual monitor handler/custody packet:
  44 passed. Real Git/descendant/pipe/caller-death/cancellation and I/O controls: 6 passed.
- A broader combined packet exposed eight old monitor-fixture mismatches,
  including an unintended POSIX cache project lookup. The cache now resolves
  observation identity only for the Windows opt-in. Baseline cache tests run
  both platform branches, legacy doubles explicitly select the legacy contract,
  and the complete affected monitor file passes 184 cases.
- Canonical procutil/ZDD selection passed 290 cases, with three existing
  Linux-only skips (directory fsync, flock and fork); no new native case is
  waived. The final combined source/native packet passed 498 cases in 149.75
  seconds with no skips. Current-head publication gates remain pending.
- Module size initially failed at 1,022 monitor CLI lines. Cohesive health
  contract and retired-surface pruning moved to their owning helpers; the
  CLI is 998 lines without a baseline increase. Focused Ruff and install
  contract pass. No service, installed runtime or production session changed.

### 2026-10-26 - Final retained-continuation review corrections
- Re-read the still-open original review against current source rather than
  treating prior green tests as acceptance. Six last-round consumer defects
  remained real: a fallback handoff gate, delayed settled-error association,
  cached-bundle identity, pre-lease updater discovery, stale snapshot display
  and obsolete lease contenders.
- Twelve exact consumer regressions captured 11 failures and one passing
  required-replacement control before correction. All 92 affected review,
  custody and updater cases then passed. The integrated packet passed 546
  cases, zero skipped, in 146.60 seconds (153.70 including runner cleanup).
- The cache now keeps stored bytes/ages while displaying pending UNKNOWN,
  rejects wrong bundle identities, and never attaches an already-settled
  error to a new ticket. The existing fallback handoff pass uses the same
  carrier gates; no handoff choreography or authority changed.
- Windows updater discovery follows the native lease. Contention rechecks
  current owner, session and runtime; redundant/retired contenders exit,
  while a required replacement still waits for predecessor cleanup. Ordinary
  Linux/macOS paths retain baseline activation and no new lease.
- An additional real legacy-client/new-provider control passed in the full
  15-case native wire file. This complements new-caller/old-provider refusal;
  it does not permit legacy fallback in guarded recovery. Source review,
  current-tip integration, required CI and installed qualification stay open.


### 2026-10-27 - Second-round guarded lifecycle and continuation corrections

- The next independent review identified seven remaining blockers. Added
  fail-first coverage before correcting the portable injected Windows fixture,
  live previous-owner/no-listener refusal, successor activation, actual native
  predecessor retirement, stored cancellation, repeated updater cancellation,
  and invalidations deferred by observation custody.
- Guarded publication now uses the existing `on_published` seam: authenticate the
  exact elected successor, promote it, verify published/ready/admitting health,
  and retain transition demand before the existing verify-before-retire gate.
  Passive preflight health is not serving readiness. No new ZDD API is added.
- Guarded shutdown captures the existing identity-bound native exit reference
  before control. A shutdown acknowledgement alone is not retirement; only
  native exit within the same bounded budget produces a positive receipt.
- Real isolated cutover now succeeds through installer, orchestrator, client,
  parser and native exit. Promotion refusal/unready controls restore the old
  owner without retirement. A cleanup barrier proves acknowledgement cannot
  clear the breadcrumb or complete cutover while that owner is still alive.
- The 13-file affected selection passed **548 tests, zero skips**. Canonical
  procutil/ZDD selection passed **359 tests**, with **15 native POSIX-only
  skips** on Windows. The injected creation fixture uses explicit OS/CRT shims,
  not a platform skip. See [validation.md](validation.md).
- Refreshed one already-admitted procutil source hash in the promotion packet;
  its 2,407 nodes, 129 production targets, 300-second cap and effect prohibitions
  are unchanged. Actual fixed-packet execution, fresh review and required CI
  remain gates; no release, installed rollout or broader completion is claimed.
