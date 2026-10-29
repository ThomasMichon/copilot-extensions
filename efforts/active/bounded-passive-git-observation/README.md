# Bounded Passive Git Observation

- **Slug:** `bounded-passive-git-observation`
- **Repo:** copilot-extensions
- **Branch(es):** plan-only PR, then independent dependency-ordered PRs to `dev`
- **Created:** 2026-10-23
- **Status:** Draft
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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Plan author | Source trace, bounded proposal, plan PR only | #7944/#7945 and this effort |
| Integration coordinator | Independent admission of exact implementation hunks | Reviewed proposal and the two tracking issues |
| R1 implementer (unassigned) | Initial/default deadline and observation-lock separation | Separate managed worktree after admission |
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
- [ ] Return exact plan/head/claims to the integration coordinator; obtain
  independent admission before any functional edit.

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

- [ ] Complete the plan-only checks in [validation.md](validation.md).
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
