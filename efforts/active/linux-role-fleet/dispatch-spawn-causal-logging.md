# Dispatch spawn causal logging: first source slice

Back to the [logging integration plan](logging-conventions.md) and
[Linux role fleet effort](README.md). Tracker: [#8377](https://github.com/ThomasMichon/copilot-extensions/issues/8377).

**Status: source merged in #9044; release/running qualification remains open.** This adds causal records
through existing Dispatch logging owners, not a new collector, allocation
ledger, delivery protocol or complete fleet-logging qualification.

## Request and source boundary

Operator direction, public-safe excerpt:

> I would prioritize adding missing, non-redacted logging.

The requested outcome is a reconstructable sequence for each attempted task
spawn. The implementation decomposition and tests below are agent-recommended.
Preserve useful operation identities, stage names, elapsed time and causal
errors rather than replacing them with only hashes or generic categories.
Credentials never belong in a diagnostic record.

The existing application handler owns UTC/process/runtime attribution,
cross-process append custody, discovery and retention. Ordinary Bridge CLI
tracing is separately opt-in and cannot account for work blocked before its
subprocess boundary. `Supervisor.poll_once()` performs reconciliation before
new task selection; a live lane can therefore stop advancing before a spawn
reservation exists.

This first slice covers Dispatch-owned lane cycles, scheduling decisions,
reservation, workspace preparation and the ordinary Bridge caller boundary.
Bridge's internal workspace/native/first-prompt stages, native output capture,
CLI-mode creation convergence and full timeline export remain subsequent
owner-specific work. No source record claims those unobserved stages completed.

## Implementation plan

- [x] Verify that the actual worker-lane entry point has a configured existing
      application sink, not only its supervising daemon. Reuse that handler
      and its discovery; no new permanent log directory or service.
- [x] Emit a cycle/stage entry before each potentially blocking reconciliation,
      selection, capacity, reservation, preparation, backend and bookkeeping
      call, then a result/error with elapsed monotonic time. A missing end is
      outstanding observation, not confirmed absence.
- [x] Correlate existing supervisor, task/generation and reservation/creation
      identities as they become available. A cycle/call identifier is diagnostic
      grouping only; it never supplies execution or retry authority.
- [x] Explain bounded scheduling outcomes: exhausted retry budget, local
      concurrency limit, no fleet capacity, governance refusal and reservation
      contention. Do not dump the whole task or environment or log an
      ever-growing candidate corpus each tick.
- [x] At ordinary Bridge calls record the operation, existing reservation,
      entry, exit/timeout and useful causal diagnostics. Preserve original
      arguments, prompts, results, exception behavior and launch count.
      Use targeted credential removal, not blanket suppression of benign
      operation identities and errors. Do not add raw prompt/envelope capture
      to general application logs.
- [x] Keep the large supervisor module within its existing size guard. If
      extraction is needed, move only the cohesive cycle orchestration behind
      the existing public method; do not redesign lifecycle or allocation.
- [x] Update the existing diagnostic/spawn guides with event coverage and
      remaining gaps. Default application records and opt-in raw-call
      observation must not be described as the same facility.

The existing sink's degraded-write reporting and primary-result behavior remain
unchanged in this slice. It does not certify complete trace retention when
storage fails. A later isolated-lab policy may require a retained pre-effect
record before allowing its synthetic launch; that policy is not silently
imposed on ordinary callers here.

## Validation plan

- [x] Actual configured application output contains parseable, correlated
      entry/result records with existing timestamp/process/runtime attribution.
- [x] A blocked fake stage has an entry visible before it is released.
      A failing stage has a causal error and no invented successful end.
- [x] Capacity/refusal/contended reservation controls explain why no backend
      was called; an admitted synthetic task still spawns and records once.
- [x] Existing original-key replay, retained/pending creation, startup
      completion, bookkeeping failure and allocation tests preserve behavior.
- [x] Known credential values remain absent while synthetic benign IDs and
      causal messages remain useful; malformed/full task envelopes are not
      copied into application output.
- [x] Existing handler-degradation behavior stays visible and does not change
      the primary operation result. No new remote collector or retries.
- [x] Required CI uses focused synthetic/unit contracts. Real child/crash,
      persistent-container and native CLI qualification remain in their bounded
      isolated lanes, not a new required promotion matrix.
- [x] Independent source review and required checks pass before merge.
- [ ] Release, authorized installation and newly observed running records
      pass their separate acceptance gates.

## Completion and integration

Resolve only this slice's source gates when its implementation merges.
The parent logging inventory, native/installer/container adapters and running
qualification remain open. Do not change updater, service, credential, queue,
registrar or production configuration to demonstrate these records.

## Journal

- #9044 merged as `649c58cce494f0703c86e580647d7c0ad3b37880` after
  exact-head Copilot approval, no open findings and all required checks.
  Re-read the full parent Plan and Validation Plan after merge. Only this
  bounded source slice's implementation and synthetic validation gates close.
  #8377 and the parent remain open. Native/first-prompt capture, full timeline
  export, a persistent isolated reproducer and released/running acceptance
  remain separate work; no installation, activation or live state changed.
- 2026-10-25: #9041 accepted this bounded source plan. The implementation uses
  the existing application handler in the actual lane process and preserves
  the supervisor's ordered lifecycle behind a cohesive cycle module. Focused
  isolated regression controls passed (890; 35 platform/native cases skipped),
  including original-key startup/retry and the new real-file causal records.
  The positively admitted in-memory packet passed (1,853), adding three pure
  entry/error/privacy controls without expanding its effect permissions or
  budget. Stale test fixtures were corrected to the existing hosting flag,
  explicit admission fixture and shared daemon-launch API; no production
  launch contract changed. Source review/merge and all running-state gates
  remain open.
- Independent review identified two real boundary defects: the serving-loop
  traceback could copy credentials back into an otherwise filtered log, and
  an errno prefix could be mistaken for an opaque JSON array. The outer loop
  now retains a type-only summary alongside the detailed filtered stage, and
  genuine OS error prefixes preserve their cause. A separate synthetic control
  reproduced long-prompt prefix leakage when clipping preceded matching; exact
  supplied inputs are removed before bounded normalization/field selection.
  The affected isolated cohort passed 188 controls, including serving-loop
  privacy and original Bridge caller/timeout behavior. The same three admitted
  unit nodes cover errno and long-input privacy without new effect permissions.
- Follow-up review exposed internally caught lifecycle messages/tracebacks that
  never propagate to a stage error, and standalone lanes without the outer
  daemon's quiet-maintenance loop. The Dispatch logging adapter now filters
  cloned records and cause-first exception text through the existing diagnostic
  owner; scoped caller inputs reset on stage exit. No shared retention policy
  changes. The standalone lane invokes existing debounced maintenance before
  each iteration, including governance backoff. Actual configured-file controls
  cover caught reservation reads, caught cold-resume exceptions and 31-day
  clock advancement with and without backoff. The full affected isolated cohort
  passed 896 controls, with the same 35 explicit platform/native skips.
- A further causal-detail control retains explicit `__cause__` and visible
  implicit `__context__` summaries before stack context. Bounded chain traversal
  preserves wrapper/underlying errno details, marks cycles/limits and leaves
  original exception links unchanged. Both configured-file and admitted pure
  controls cover credential filtering in those chains. The affected isolated
  cohort passed 898 controls with the same 35 explicit skips; structured JSON
  errors also retain their selected benign cause.
