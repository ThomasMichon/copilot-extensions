# Mux Question Answers

- **Slug:** `mux-question-answers`
- **Repo:** copilot-extensions
- **Created:** 2026-11-07
- **Status:** Done
- **Completed:** 2026-11-09
- **Vision:** [interactive-session-representation](../../../../visions/plugins/agent-bridge/README.md#interactive-session-representation)
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
[contract-evolution pattern](../../../../docs/patterns/service-contract-evolution.md)
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
- [x] Verify ordinary promotion to `main`; diagnose actual failing jobs and
  fix forward rather than bypassing a red release gate.
- [x] Complete the separately authorized adopter updates and synthetic proof
  on both environments, retaining undelivered-row and session-preservation
  evidence privately. Report each delivery layer separately.
- [x] Deferred to `#11371`: Reconcile #11371, archive this effort through review
  and finalize. The protocol driver explicitly retains these administrative
  delivery gates on the root issue until they are actually completed.

## Validation Plan

- [x] Focused parser and orchestration cases cover valid choice/freeform,
  permissions, ambiguous/multiple questions, stale inspection, changed pane/
  process/screen, concurrent callers, terminal-first resolution, key failure,
  timeout, mismatched/stale echo and unknown outcomes without retry.
- [x] Actual authenticated HTTP/CLI and unsupported-provider controls preserve
  native preference and legacy syntax; audit admission precedes key effects.
- [x] Use fixture-owned mux namespaces for actual Windows and POSIX command
  qualification. Never target a pre-existing session or shared mux shutdown.
- [x] Run affected lint, unit/contract, install, type-admission and documentation
  gates; keep exhaustive source capture outside required fast unit CI.
- [x] Verify source merge, release inclusion, selected installed generation,
  actual synthetic answer and same-session continuation on both authorized
  environments. Preserve session processes and queue observations.
- [x] Resolve every plan/validation row or explicitly transfer it to a named
  tracked objective before declaring Done.

## Journal

- [2026-11-09 - Installed acceptance and closeout](journal/2026/11.09%20installed-acceptance-and-closeout.md):
  both current installed environments accepted the answer and continued the
  same conversation; root-issue review/publication/resource settlement retained.
- [2026-11-07/08 - Source qualification and release](journal/2026/11.08%20source-and-release.md):
  original 2026-11-07/08 entries, reviewed source and release evidence, and
  the POSIX prerequisite and first installed acceptance, before Windows
  acceptance.

Compatibility anchors for the former inline journal, newest first:

### 2026-11-07 - POSIX incoming-publication prerequisite
[Read entry](journal/2026/11.08%20source-and-release.md#journal-posix-incoming-publication-prerequisite).

### 2026-11-07 - Correction released through ordinary promotion
[Read entry](journal/2026/11.08%20source-and-release.md#journal-correction-released-through-ordinary-promotion).

### 2026-11-07 - Reviewed correction merged
[Read entry](journal/2026/11.08%20source-and-release.md#journal-reviewed-correction-merged).

### 2026-11-07 - Required unit admission correction
[Read entry](journal/2026/11.08%20source-and-release.md#journal-required-unit-admission-correction).

### 2026-11-07 - Final native correction qualified
[Read entry](journal/2026/11.08%20source-and-release.md#journal-final-native-correction-qualified).

### 2026-11-07 - Bounded historical-checker cost correction
[Read entry](journal/2026/11.08%20source-and-release.md#journal-bounded-historical-checker-cost-correction).

### 2026-11-07 - Delayed native rendering controls
[Read entry](journal/2026/11.08%20source-and-release.md#journal-delayed-native-rendering-controls).

### 2026-11-07 - Reconciled concurrent source
[Read entry](journal/2026/11.08%20source-and-release.md#journal-reconciled-concurrent-source).

### 2026-11-07 - Installed corrections source qualified
[Read entry](journal/2026/11.08%20source-and-release.md#journal-installed-corrections-source-qualified).

### 2026-11-07 - Installed qualification corrections
[Read entry](journal/2026/11.08%20source-and-release.md#journal-installed-qualification-corrections).

### 2026-11-07 - Reviewed implementation merged
[Read entry](journal/2026/11.08%20source-and-release.md#journal-reviewed-implementation-merged).

### 2026-11-07 - Combined delivery source qualified
[Read entry](journal/2026/11.08%20source-and-release.md#journal-combined-delivery-source-qualified).

### 2026-11-07 - Restored-serving and storage review corrections
[Read entry](journal/2026/11.08%20source-and-release.md#journal-restored-serving-and-storage-review-corrections).

### 2026-11-07 - Corrected source qualified
[Read entry](journal/2026/11.08%20source-and-release.md#journal-corrected-source-qualified).

### 2026-11-07 - Completion-evidence review correction
[Read entry](journal/2026/11.08%20source-and-release.md#journal-completion-evidence-review-correction).

### 2026-11-07 - Complete current source qualification
[Read entry](journal/2026/11.08%20source-and-release.md#journal-complete-current-source-qualification).

### 2026-11-07 - Whole-surface admission audit
[Read entry](journal/2026/11.08%20source-and-release.md#journal-whole-surface-admission-audit).

### 2026-11-07 - Reviewed plan and private native controls
[Read entry](journal/2026/11.08%20source-and-release.md#journal-reviewed-plan-and-private-native-controls).

### 2026-11-07 - Contract and scope
[Read entry](journal/2026/11.08%20source-and-release.md#journal-contract-and-scope).
