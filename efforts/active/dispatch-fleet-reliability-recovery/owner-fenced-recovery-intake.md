# Owner-Fenced Recovery and CLI Intake

This is a proposed direct Plan/Validation companion to the existing Active
[Dispatch fleet reliability effort](README.md), not a new implementation
owner. It retains #2798, #3179, #8780 and #8781 as distinct finite workstreams.
Normal review, actual merge and complete current receiving readback precede
administrative tracker supersession. Product acceptance remains unchecked.

The existing Coordinator/Supervisor, issue-loop/provider, steering/control,
Bridge, ground/cleanup, reservation, CLI/client/server and native-validation
custodians retain their source, claims, tasks, allocations, approvals and
resource duties. Intake is not cleanup, replay, deployment, task authority,
claim release or a generic owner-ACK ceremony.

## Plan

- [ ] **R2798-P1:** Resolve an attributable PR for the exact issue-loop task
  through an exact task/reservation reference or scoped, unambiguous
  issue-to-PR association. Bind provider, repository, issue, task and attempt/
  generation; title/reference similarity alone is not acceptance.
- [ ] **R2798-P2:** Reconcile only actual provider-confirmed MERGED facts,
  independent of worker callback. Open, closed-unmerged, absent, synthetic,
  stale, failed and ambiguous reads stay nonterminal/visible.
- [ ] **R2798-P3:** Use the existing actual-goal evaluator and confirmation
  policy. A partial goal or missing/failing evaluator cannot complete the
  task or waive operator/evaluator acceptance.
- [ ] **R2798-P4:** Fence transitions to exact claim owner, reservation token,
  task status and generation; preserve trusted foreign/coordinator snapshots.
  Release only matching owned issue claims/resources when existing policy
  permits. Callback loss, repeated ticks and concurrent transitions remain
  idempotent, with business completion separate from pending cleanup.
- [ ] **R2798-P5:** Preserve the existing issue-loop/provider lifecycle without
  a competing store. Claim age, missing local task or uncertain provider
  state never expires an owner. A merge authorizes no process stop,
  worktree finalization/removal, task replay or unrelated reservation/key release.
- [ ] **R3179-P1:** Preserve the drain-race incident with exact task,
  reservation key, attempt, body/session and created-worktree provenance.
  Distinguish failed cleanup request, lost response, already-terminal body
  and unknown identity; later Bridge readiness is not cleanup acknowledgement.
- [ ] **R3179-P2:** Provide bounded observable retry/backoff and lifecycle-owned
  cleanup conclusion where appropriate rather than unrelated GC cadence
  alone. Preserve dirty/reused/targeted/foreign/live/unknown allocations and
  the exact cleanup owner across restart.
- [ ] **R3179-P3:** Keep the explicit no-session-handle force path distinct
  from recorded-handle confirmed absence. Do not broaden the narrow handleless
  heuristic into expiry of live/unknown bodies, held cleanup, claims or keys.
- [ ] **R3179-P4:** Keep terminal/failed reservation settlement, trailing
  session/worktree cleanup and rearm separate. Expose pending components and
  their supported settlement route; do not hide cleanup, abandon unfinished
  work or release shared exclusivity early.
- [ ] **R3179-P5:** Preserve durable cleanup-session identity through teardown
  and lost mapping. Never guess from the current head or automatically repair
  old holds. Keep preview/permission/revision/creating-host/generation/
  allocation fences and terminal versus nonterminal recovery distinct.
- [ ] **R8780-P1:** Expose BOTH optional `--expected-generation INT` and
  `--expected-owner-session-id ID` through the abandon parser and forward
  exact values to existing client/API guards beside `--expected-status`.
  Preserve integer zero, omitted defaults, worker, permit, reason, live
  override, authorization, audit and server-atomic stale-view refusal.
  A client pre-read is not the atomic server fence; add no new task authority.
- [ ] **R8781-P1:** Expose `--repo` and `--latest-only` through the existing
  reservations-list client/endpoint. Retain the original preferred
  `--task-status`/`--label` direction and optional/additive
  `--conclusion-state`/`--resume-requested` distinction rather than turning
  preferred filters into new minimum acceptance. Preserve task/state/limit
  compatibility, canonical types, false values and default limit 200.
- [ ] **R8781-P2:** Apply selected filters at the query layer before ORDER BY/
  LIMIT. Preserve overall-latest-attempt semantics; a newer nonmatching
  attempt must not resurrect an older matching one. Capped/omitted rows
  remain UNKNOWN, never proof that no reservation exists. No new store,
  benchmark threshold or live-reservation query is implied.

## Validation Plan

- [ ] **R2798-V1:** Cover exact attributable merged PR versus open,
  closed-unmerged, missing, ambiguous, stale and provider-error outcomes.
  Only the matching exact task/attempt can proceed; unknown/error is visible
  and nonterminal.
- [ ] **R2798-V2:** Cross evaluator success, partial goal, confirmation,
  missing/failing evaluator, matching/foreign claim, token/status/generation
  races, repeated ticks, late callback, already-terminal task and cleanup
  failure. No goal-policy bypass, other-claim effect, process/worktree
  operation or business replay follows.
- [ ] **R3179-V1:** Cover drain request failure, lost response, later Bridge
  readiness, terminal body and unknown identity through actual existing
  consumers; keep retry/progress/failure/held outcomes bounded and observable.
- [ ] **R3179-V2:** Separately cover handleless recovery and recorded-handle
  confirmed absence, live/unknown/foreign/missing identity and the narrow
  age/pool cases. No heuristic becomes lease expiry.
- [ ] **R3179-V3:** Cover failed/settled reservation with trailing cleanup,
  rearm refusal, component settlement, restart, exact cleanup identity
  through lost mapping, same/different-task exclusivity and newer competing
  allocation. Preserve preview/permission/revision/host/generation fences;
  no early key release, guessed identity or old-hold repair.
- [ ] **R8780-V1:** Real parser-to-client forwarding covers both values,
  integer zero and `None` defaults. Controlled same-status/new-generation
  and changed-owner races refuse atomically before transition; matched
  success and status/worker/permit/reason/live-override/auth/audit remain.
- [ ] **R8781-V1:** Real read-command forwarding and mixed repository,
  label/status and repeated-attempt fixtures with a small limit establish
  server-side selection-before-LIMIT and exact optional/false/default
  semantics for the chosen surfaces.
- [ ] **R8781-V2:** Include a newer nonmatching attempt, genuinely empty
  result and capped/omitted rows. Do not resurrect superseded history or
  report "unreserved" from incomplete evidence. Preserve preferred versus
  optional filter choices and historical impact as observations, not limits.

## Continuing acceptance and evidence

Keep all existing R2816-P1..P7/R2816-V1..V3, R2877 Plan/local/global Validation,
P5493/local/global Validation, Phase 3/7, held recovery and source-safety
contracts intact. Phase 6's earlier instruction to leave #2798 unchanged
bounded that source-safety slice; this distinct reviewed planned-work intake
does not expand its implementation reservation.

#7805's original five-clause terminal-startup source outcome and the now-merged
#7836 remain separate source-completion evidence, not live-row recovery,
installed/native acceptance or a new transfer of its current owner. Source,
review/CI, release, installed and native evidence retain their actual tiers.

This proposal inspects no live task, reservation, worker, queue, telemetry or
claim; it starts no source fix, test, native operation, cleanup or rollout.
