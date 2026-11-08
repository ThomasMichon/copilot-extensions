# Mux Question Answers

- **Slug:** `mux-question-answers`
- **Repo:** copilot-extensions
- **Created:** 2026-11-07
- **Status:** Active
- **Vision:** [interactive-session-representation](../../../visions/plugins/agent-bridge/README.md#interactive-session-representation)
- **Umbrella issue:** #11371

## Guiding Intent

Let an explicitly authorized caller relay an answer to a single native
interactive form through its existing mux when native question answering is
unavailable. Preserve the session, permission boundary and truthful uncertainty.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| Protocol driver | Plan, source, qualification and delivery | This effort and #11371 |

## Coordination

- **Topology:** serial reviewed plan, implementation and closeout.
- **Host:** Protocol driver.
- **Delegates:** none.
- **Handoff:** retain source, review, promotion and explicitly authorized
  adopter-rollout obligations; never treat a source merge as live deployment.

## Context

#11309 delivers the preferred native pending-question RPC path. Older clients,
daemons or interactive extensions may not expose it. #11371 records an
explicit mux fallback prototype for single-question rendered forms.

This closes existing interactive-representation intent. Apply the
[contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md)
and existing authenticated local control, session-binding and observation
owners. No new daemon, prompt injection, cancellation or session takeover.

## Request

Public-safe capture of the assignment; private examples and rollout targets
are omitted, not published:

> Add an opt-in `agent-bridge answer --via mux` (or the shape you judge right
> per the plugin's conventions) fallback used when native answering isn't
> available for the target: resolve the session's mux pane from
> worktree/session identity, parse the pending form from the pane, select
> option or freeform, send, and verify via recapture; fail closed on ambiguity
> (multi-question form, form changed between read and send,
> answered-by-someone-else race, pane not found).
> Permission prompts stay terminal-owned. Tests and docs per repo norms.
>
> Once merged, drive normal release promotion to `main`, then the supported
> update path for the explicitly authorized adopter environments. Verify a
> synthetic pending single-question form end to end on both. Report merged,
> promoted, installed and live-verified as separate facts. Do not touch other
> agents' pending forms or restart a session process.

## Scope Approvals

Source changes, bounded tests, public issues/PRs, ordinary review corrections
and normal promotion are assigned. Adopter rollout authorization remains with
the private originating owner and its exact two-environment selector.
Before any authorized updater restarts Bridge, inspect undelivered queued rows
and let them drain or record them. No session-process restart, unrelated
runtime remediation, elevated operation or destructive cleanup is authorized.

## Plan

### Phase 1 - Reviewed contract
- [x] Claim #11371 and inspect native, session-binding and mux seams.
- [x] Land this plan before implementation.

### Phase 2 - Explicit degraded transport
- [x] Add independently negotiated mux-answer support and CLI inspection/
  answer selection without changing existing owned/native syntax.
- [x] Resolve the exact registered local native session through its configured
  worktree owner's authoritative live process-to-pane binding; never guess
  a pane, redirect to an alias, cross execution spaces or auto-start a daemon.
- [x] Parse only positively recognized single-question choice/freeform forms.
  Refuse permissions, multi-field/multi-select, unsupported rendering, edited
  freeform state, absent or ambiguous cursor and oversized/control-bearing text.
- [x] Require an expiring one-use inspection token bound to the exact pane
  incarnation, question and current screen. Prefer an available native path.
  Recheck identity and form before every key effect; stop on detected races.
  Exclude only the positively recognized native clock and greeting blink,
  never question, choice, cursor, input or other viewport changes.
- [x] Audit caller, answer, inspection and actual outcome in existing session
  observations. Consume admission before effects and never replay uncertain
  input. Verify a new matching answer echo and form disappearance, not merely
  successful key delivery. Report raced/unverified outcomes honestly.
- [x] Document that capture/send cannot form an atomic transaction with a
  human terminal; no atomic human-exclusion or SDK-acceptance claim is made.

### Phase 3 - Delivery and rollout
- [x] Land qualified implementation after whole-surface audit, review and CI.
- [ ] Verify ordinary promotion to `main`; diagnose actual failing jobs and
  fix forward rather than bypassing a red release gate.
- [ ] Complete the separately authorized adopter updates and synthetic proof
  on both environments, retaining undelivered-row and session-preservation
  evidence privately. Report each delivery layer separately.
- [ ] Reconcile #11371, archive this effort through review and finalize.

## Validation Plan

- [x] Focused parser and orchestration cases cover valid choice/freeform,
  permissions, ambiguous/multiple questions, stale inspection, changed pane/
  process/screen, concurrent callers, terminal-first resolution, key failure,
  timeout, mismatched/stale echo and unknown outcomes without retry.
- [x] Actual authenticated HTTP/CLI and unsupported-provider controls preserve
  native preference and legacy syntax; audit admission precedes key effects.
- [ ] Use fixture-owned mux namespaces for actual Windows and POSIX command
  qualification. Never target a pre-existing session or shared mux shutdown.
- [x] Run affected lint, unit/contract, install, type-admission and documentation
  gates; keep exhaustive source capture outside required fast unit CI.
- [ ] Verify source merge, release inclusion, selected installed generation,
  actual synthetic answer and same-session continuation on both authorized
  environments. Preserve session processes and queue observations.
- [ ] Resolve every plan/validation row or explicitly transfer it to a named
  tracked objective before declaring Done.

## Journal

### 2026-11-07 - Contract and scope

- Claimed existing #11371. The prototype is reference evidence only, not a
  reusable owner or a proof of race-free input.
- The native path remains preferred. An authenticated explicit fallback needs
  its own inspected-form identity and truthful audit, not permission authority.
- The implementation and qualification plan above is an agent recommendation
  within the assigned fallback scope. No existing form has been touched.

### 2026-11-07 - Reviewed plan and private native controls

- #11383 merged after exact-head approval and required CI.
- The authenticated provider uses expiring one-use inspections, current native
  request corroboration and the selected local Worktrees process/pane binding.
  Durable admission precedes any key; original audit survives registration loss.
- Initial focused packet passed 35 cases. The actual private native form then
  qualified native and mux option answers; Other conservatively stopped before
  Enter because selecting it expands the text box and shifts viewport history.
- Added exact native Other-field recognition without weakening request,
  question, option, cursor or process checks. All 38 focused/native cases passed,
  including actual text acceptance and same-conversation continuation.
- Required contract capture, final whole-surface audit, review, promotion and
  authorized installed qualification remain open.

### 2026-11-07 - Whole-surface admission audit

- The expanded 40-case focused packet passed, including original audit reads,
  exact selected owner and strict CLI/unsupported-provider controls. Nine
  actor-free parser and completion cases passed; all 40 currently admitted
  strict typing projects passed both analysis targets, with Bridge still
  pending independent type admission.
- The final audit added mutation-boundary exclusion for another in-flight or
  uncertain answer, including a stale pre-read in another serving generation.
  A preissued inspection cannot turn an unknown sibling result into a retry.
- The current HTTP-52 controlled portfolio expands to 310 cases. Qualification
  must be captured and independently verified before publication readiness.

### 2026-11-07 - Complete current source qualification

- The explicit collector now executes all 310 declared cases; its newly linked
  25 mux cases also passed separately through that same collector.
- Source-pinned run 42420734839 passed full capture and independent no-write
  verification. Its first historical-guard attempt hit the existing 30-second
  reproducibility-test deadline; one same-source failed-job retry passed every
  historical guard and canonical export without changing a timeout or limit.
- The native publication owner materialized 155 verified current outputs.
  The independent local contract guard passed with two contracts and 1,066
  retained fixtures. No historical generation was rewritten.
- The new backend also resolved an actual selected-owner Windows binding
  read-only. This is not an installed answer claim: normal review/CI, source
  merge, promotion, authorized updates and both installed live proofs remain.

### 2026-11-07 - Completion-evidence review correction

- Review of #11390 identified prefix question matching and arbitrary answer
  suffix matching that could falsely classify an answer as accepted.
- Completion now requires the full question message followed immediately by
  its exact rendered field-label/value pair. Negative controls reject question
  extensions, misleading value suffixes, other labels and detached answers;
  stale echoes remain insufficient.
- This changes source semantics. Repeat affected validation and canonical
  source qualification before the next whole-surface audit and review round.

### 2026-11-07 - Corrected source qualified

- The corrected completion matcher passed 45 focused cases, including real
  private native option/freeform completion and authenticated HTTP controls
  retaining `unverified` outcomes for mismatched completion. Nine actor-free
  parser/input/echo cases, affected lint, install and documentation guards passed.
- Whole-surface audit covered all source wiring, exact local binding, parser,
  mutation admission, client negotiation, native preference, audit and CLI
  refusals. The completion fix does not weaken permission or replay boundaries.
- Initial recapture refused stale semantic source registration before tests;
  refreshed declarations through their existing preparation owner and expanded
  the probe declaration to all 312 cases. No failure or timeout was waived.
- Source-pinned run 42424290416 passed capture, independent no-write
  verification, historical guards and canonical export. The publication owner
  imported all 155 current outputs; the independent contract guard passed
  two contracts and 1,066 retained fixtures. Historical generations are unchanged.
- Return #11390 to review. Source merge, Main promotion, both authorized
  adopter updates and installed same-conversation proofs remain unfinished.

### 2026-11-07 - Restored-serving and storage review corrections

- A further review found that retained candidate state discarded the new mux
  owner, and migration-free adoption did not require the new audit table.
- Retained serving now receives fresh native/mux question owners before
  activation. Schema 43 requires the full answer table, including unique screen
  admission, through writable preparation and both migration-free checks.
- All 71 focused cases passed, including authenticated restored inspection,
  submission and audit, stale-token refusal, malformed/missing storage,
  previous-schema preparation and retained session preservation.
- Rebasing the owned PR preserved concurrent schema-42 acquisition changes.
  Only current derived evidence was reconciled; immutable generations and
  upstream source content were retained. Fresh qualification must bind the
  combined source, not either earlier capture.
- The declared capture portfolio now includes these four additional controls,
  for 316 cases. Requalify, audit the complete delivery graph and request review
  before source merge; release and both installed proofs remain open.

### 2026-11-07 - Combined delivery source qualified

- The four new controls passed through the exact capture collector before the
  complete run. Source-pinned run 42427151265 then passed all 316 cases,
  independent no-write verification, immutable historical guards and export.
- The native publication owner imported 157 verified current outputs.
  Independent contract validation passed two contracts and 1,068 retained
  fixtures against the rebased contribution base.
- The whole-surface audit now follows ordinary and retained serving factories,
  question-owner replacement, writable preparation, read-only admission and
  writable adoption alongside all answer, audit and native-preference paths.
  Earlier source qualification is not substituted for this combined proof.
- Request a fresh review of the qualified head, then require full PR CI and
  actual merge before following promotion and both authorized installed proofs.

### 2026-11-07 - Reviewed implementation merged

- #11390 merged as `c083955fc687b45db481b1935bc037abaa6a04af` after
  exact-head approval, resolution of the fixed restored-serving thread and
  successful current PR CI. The managed completion path retained required
  check enforcement until the latest CI run finished.
- The nonrequired review-request job rejected its requested reviewer login;
  the subsequent review completed successfully. #11427 tracks this automation
  failure separately from source qualification and required PR checks.
- Continue ordinary release promotion, then both authorized adopter updates
  and fresh owned synthetic proofs. No existing session or form is a fixture.

### 2026-11-07 - Installed qualification corrections

- Installed synthetic qualification reached a real native form. The answer
  was accepted and the same conversation continued, but the terminal's
  right-aligned elapsed duration caused a conservative `unverified` result.
  Recognize only the native seconds/minutes annotation after the complete
  exact question; retain exact adjacent field/value and stale-echo checks.
- Native Windows qualification also demonstrated that PSMux can reuse pane
  IDs between sessions. The existing identity guard refused a bare-pane
  lookup before input. Qualify every identity, capture and key target with
  its authoritative mux session, not just its pane ID.
- These are source corrections, not installed success claims. Repeat affected
  validation, review, promotion, authorized updates and both installed proofs.

### 2026-11-07 - Installed corrections source qualified

- The corrections passed 43 focused cases, all three real private native
  roundtrips and nine actor-free cases, plus lint, install and documentation
  guards. The canonical collector now includes the new duration regression.
- Source-pinned run 42439721383 passed all 317 current cases and independent
  no-write verification. Its first historical guard hit the existing
  30-second test deadline; one unchanged failed-job retry passed every
  historical guard and canonical export. #11477 retains that deadline failure;
  no timeout, test or historical fixture was changed or waived.
- The native publication owner materialized all 157 verified current outputs.
  Independent local contract validation passed two contracts and 1,068
  retained fixtures. The changes preserve exact native binding, admission,
  field/value completion and no-replay semantics.
- Return #11467 to normal review and required CI, then continue ordinary
  promotion, coordinated authorized updates and both installed proofs.

### 2026-11-07 - Reconciled concurrent source

- Current `dev` introduced concurrent configuration, installation-context and
  receipt changes with its own canonical outputs. The supported reconciliation
  preserved those sources and all immutable history.
- Replayed both mux corrections and their tests, keeping upstream current
  evidence instead of overwriting it with the older capture. Refreshed only
  current declarations for the combined source.
- The earlier capture remains historical validation, not qualification of this
  new source combination. Repeat exact-source capture and independent
  verification before returning the PR to review readiness.

### 2026-11-07 - Delayed native rendering controls

- Delayed real Windows forms exposed a clock-only change between inspection
  and submission, followed by a greeting-eye blink. Preserve all semantic
  checks while excluding only those narrowly recognized native decorations
  from both inspection identity and first-effect screen comparison.
- Two inspections separated only by clock ticks retain the same one-use
  admission identity. Question, option, cursor, unrelated text, unknown
  status and changed native request/process controls remain refusing.
- PSMux 3.3.5 also collapses interior blank cells, concatenating a completion
  question and duration. Keep that ambiguous echo unverified. Qualify against
  the released 3.4.0 fix in disposable namespaces, not by weakening exact
  question matching or upgrading existing session servers.
- The controlled portfolio now includes both additional decoration/admission
  cases. Repeat current qualification after these source corrections.

### 2026-11-07 - Bounded historical-checker cost correction

- #11477 recurred during the rebased source's historical guard. Read-only
  profiling measured 524 Git subprocesses taking 57 of 62 seconds.
- Batch commit/path and metadata reads through the existing local-only Git
  object owner. Preserve exact blob IDs/hashes, version/generation checks,
  missing-object behavior and malformed-response refusal. Reject batch
  delimiters before any object read.
- The same profiled corpus now uses 42 subprocesses and completes in 10.4
  seconds. Real Git controls verify three processes for the joined prefetch
  and metadata flow, including spaced paths and missing objects.
- Checker/rendering controls passed; the UTF-8 metadata fixture also passed
  after selecting task-private temporary state and enabling long paths only
  on its own Git invocations. No shared temporary directory or global
  configuration was changed. Finish fresh canonical qualification before
  treating this as a resolved release gate.

### 2026-11-07 - Final native correction qualified

- Source-pinned run 42444640526 passed all 319 current cases, independent
  no-write verification, every historical guard, canonical validation and
  export on its first attempt. The historical deadline was not increased.
- The native publication owner materialized all 157 current outputs.
  Independent local validation passed two contracts and 1,068 fixtures.
- Delayed real Windows option/Other answers and same-conversation continuation
  passed. Read-only checks on both platforms also matched the actual selected
  Worktrees native-process binding to canonical mux targets and captures.
  These are source/transport checks, not installed answer claims.
- Return #11467 to normal review and required CI, then continue release and
  the authorized adopter proofs. Existing sessions and lossy older mux
  servers were neither restarted nor upgraded for qualification.

### 2026-11-07 - Required unit admission correction

- The final qualified head received approval, but required unit CI refused
  before execution: two admitted parser/test source hashes still described
  the earlier bytes.
- Updated only those two hashes for the reviewed actor-free module and tests.
  Node inventory, coverage targets and budgets are unchanged. The unit
  manifest is outside the canonical capture's input closure; no production
  behavior or qualified capture input changed.
- Require the corrected current head's actual unit execution and normal review
  before merge. An earlier approval or the unchanged capture is not a CI waiver.
