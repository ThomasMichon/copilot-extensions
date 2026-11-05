# Embody Session State

- **Slug:** `embody-session-state`
- **Repo:** copilot-extensions
- **Branch(es):** Reviewed proposal and implementation PRs against `dev`
- **Created:** 2026-11-05
- **Status:** Draft
- **Vision:** `agent-fabric` / `legible-live-state`, `planned-maintenance-windows`
- **Umbrella issue:** #10745

## Guiding Intent

Return useful, truthful state to an embodiment caller without confusing mux
creation, native conversation liveness, prompt delivery and completed recovery.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| source-driver | Output contract, implementation, validation and publication | This effort and #10745 |

## Coordination

- **Topology:** Proposal followed by implementation and source closeout.
- **Host (owns PRs):** source-driver.
- **Delegates:** None.
- **Handoff:** Preserve the canonical effort, current gate and outstanding PRs.
- Startup prompts (#4417/#9364), launch retention (#8375), restart qualification
  and maintenance retain their existing owners.

## Context

Ordinary `embody` already emits versioned JSON with worktree and mux identity.
Its `created`/`resumed` fields describe mux creation or reuse, not whether the
native conversation registered and is live. Native conversation identity is
missing; `--verify-timeout` verifies mux existence only.

This closes existing legibility intent. Keep session records and existing
liveness owners authoritative; do not add another launch journal or daemon.
The service-contract-evolution pattern governs additive stdout fields and the
optional new CLI argument.

## Request

> We should make sure that `embody` returns meaningful state back to the caller over stdio, like the worktree id, state, session id, etc., especially with --json, so tha the caller can have confidence the agent booted or resumed correctly.

## Scope Approvals

The request authorizes this source follow-up, focused isolated validation and
normal repository publication. It does not authorize deployed-runtime updates,
real agent launches, session termination, maintenance activation or reboot.
Existing source-only repository hosting policy remains unchanged.

## Plan

### Phase 1 - Reviewed contract

- [ ] Land this proposal through automated review before implementation.
- [ ] Finalize the additive receipt fields and their evidence boundaries.

### Phase 2 - Caller-visible session state

- [ ] Preserve existing mux fields and return explicit launch action, native
  session identity and observed state for create, resume and live reuse.
- [ ] Preserve unknown/unregistered state instead of inventing a native ID or
  claiming recovery completion from launch, registration or seed submission.
- [ ] Add an optional bounded native-session wait while keeping default
  no-wait behavior and existing mux-only verification semantics.
- [ ] Keep JSON stdout parseable through the real command entrypoint and
  diagnostics on stderr; preserve existing launch and prompt custody.
- [ ] Document fields, optional support detection, uncertainty and safe retries;
  add the plugin changefile and land the validated implementation.

### Phase 3 - Source closeout

- [ ] Resolve validation and record merged source versus release/rollout state.
- [ ] Mark the finite source objective Done, archive it and finalize.

## Validation Plan

- [ ] Cover fresh launch, original-head resume, already-live reuse, concurrent
  late reuse, stale/unregistered heads and unavailable liveness.
- [ ] Prove bounded waiting, registration timeout and late registration without
  duplicating processes or replaying startup text.
- [ ] Exercise parser and captured stdout/stderr with isolated native-state
  fixtures; reject unknown CLI options and invalid timeout values.
- [ ] Preserve old-reader/new-provider fields and explicit legacy behavior;
  document unsupported optional wait behavior on provider rollback.
- [ ] Run focused tests, touched-source lint, type admission and relevant
  module/install/documentation guards; require published-head CI and review.
- [ ] Record unavailable native/model/platform lanes honestly; no installed or
  completed-recovery acceptance is inferred from synthetic fixtures.

## Proposal

The optional bounded wait is agent-recommended: callers may request confirmation
of a registered, live native conversation without changing normal launch latency.
Keep `--verify-timeout` mux-only. Add a separately identified receipt contract
inside the existing extensible JSON envelope; retain legacy fields and meanings.
Obtain observations from exact worktree/session records and existing liveness
primitives, never conversation-history scans or a new service.

An unconfirmed outcome remains inspectable; a timeout must identify the existing
target and possible launch effects, not invite an automatic create/reseed retry.
Native liveness is not an input-ready, prompt-acknowledgement or task-completion
assertion. Fresh starts must not label an older registered head as the new agent.

**Contract impact:** Provider-first additive output with an explicit receipt
identity/version. Old callers retain existing fields; new callers tolerate
missing receipt metadata as legacy/unknown support. The optional wait argument
requires support from the selected executable, detectable through its existing
help/parser surface; no effectful probe-and-fallback. Prior providers retain
their existing no-wait path. No fields or forms are retired.

**Performance impact:** One target-local observation per ordinary result.
Only explicit waiting repeats bounded exact-record/session checks until the
deadline. No new daemon, global history scan or per-event transport is proposed.

**Documentation impact:** Update the owning Worktrees CLI reference and focused
validation documentation. Existing visions need no intent amendment.

## Journal

### 2026-11-05 - Proposal

- Captured the operator request verbatim and established #10745.
- Confirmed the existing mux-only result and missing native identity.
- Qualified target-owned effort adoption. No runtime changes or rollout yet.
