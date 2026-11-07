# Mux Question Answers

- **Slug:** `mux-question-answers`
- **Repo:** copilot-extensions
- **Created:** 2026-11-07
- **Status:** Draft
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
- [ ] Land this plan before implementation.

### Phase 2 - Explicit degraded transport
- [ ] Add independently negotiated mux-answer support and CLI inspection/
  answer selection without changing existing owned/native syntax.
- [ ] Resolve the exact registered local native session through its configured
  worktree owner's authoritative live process-to-pane binding; never guess
  a pane, redirect to an alias, cross execution spaces or auto-start a daemon.
- [ ] Parse only positively recognized single-question choice/freeform forms.
  Refuse permissions, multi-field/multi-select, unsupported rendering, edited
  freeform state, absent or ambiguous cursor and oversized/control-bearing text.
- [ ] Require an expiring one-use inspection token bound to the exact pane
  incarnation, question and current screen. Prefer an available native path.
  Recheck identity and form before every key effect; stop on detected races.
- [ ] Audit caller, answer, inspection and actual outcome in existing session
  observations. Consume admission before effects and never replay uncertain
  input. Verify a new matching answer echo and form disappearance, not merely
  successful key delivery. Report raced/unverified outcomes honestly.
- [ ] Document that capture/send cannot form an atomic transaction with a
  human terminal; no atomic human-exclusion or SDK-acceptance claim is made.

### Phase 3 - Delivery and rollout
- [ ] Land qualified implementation after whole-surface audit, review and CI.
- [ ] Verify ordinary promotion to `main`; diagnose actual failing jobs and
  fix forward rather than bypassing a red release gate.
- [ ] Complete the separately authorized adopter updates and synthetic proof
  on both environments, retaining undelivered-row and session-preservation
  evidence privately. Report each delivery layer separately.
- [ ] Reconcile #11371, archive this effort through review and finalize.

## Validation Plan

- [ ] Focused parser and orchestration cases cover valid choice/freeform,
  permissions, ambiguous/multiple questions, stale inspection, changed pane/
  process/screen, concurrent callers, terminal-first resolution, key failure,
  timeout, mismatched/stale echo and unknown outcomes without retry.
- [ ] Actual authenticated HTTP/CLI and unsupported-provider controls preserve
  native preference and legacy syntax; audit admission precedes key effects.
- [ ] Use fixture-owned mux namespaces for actual Windows and POSIX command
  qualification. Never target a pre-existing session or shared mux shutdown.
- [ ] Run affected lint, unit/contract, install, type-admission and documentation
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
