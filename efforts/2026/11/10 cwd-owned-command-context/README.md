# CWD-Owned Command Context

## Goal

Remove AGENT_WORKTREES_OWNER_REF as an environment variable. Owner identity
comes from CWD or explicit command-line arguments, never inherited environment.
Intentionally detached bridge-launched CLI sessions are standalone owners.
Sweep other non-test environment inputs and fix leaking identity, owner or
runtime-root state, or track each remaining case.

- **Slug:** `cwd-owned-command-context`
- **Repo:** copilot-extensions
- **Branch(es):** independent PRs against `dev`
- **Created:** 2026-11-09
- **Status:** Done
- **Vision:** agent-fabric `derive-dont-duplicate`,
  `trusted-cooperation-with-economical-evidence`
- **Umbrella issue:** #11883
- **Sub-issues:** #11891 (runtime/state), #11892 (identity), #11893 (carriers)

## Guiding Intent

Current worktree truth, not a launch-time snapshot, determines ownership.
Retain managed-caller safety without allowing stale inherited context to
override a correctly bound current session or another command's runtime.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Author | Plan, implementation, audit, validation and PR delivery | This effort and #11883 |

## Coordination

- **Topology:** sequential plan and implementation PRs; independent audit
  follow-ups may land separately.
- **Host (owns PRs):** Author.
- **Delegates:** None.
- **Handoff:** Preserve the current slice, unresolved gates and issue links.

## Context

An inherited owner reference can pair a child's worktree with its parent's
session. Comparing that reference to the child's native session ID then
refuses dispatch even after rebinding correctly updates the worktree head.
An inherited runtime-root variable can likewise select another command's
installation. See #11883.

This closes the agent-fabric vision's derived-state and economical ownership
checks. It follows architecture principles 3 (installation-owned runtime),
6 (literal failures), 7 (attributable commands) and 9 (proportionate safeguards).
No new central service, identity registry or attestation layer is proposed.

## Request

Public-safe operator direction: "We won't want to use environment variables
for any of this 'owner' tracking; that's part of the bug. We use CWD for owner
tracking, or explicit command-line. But if we intentionally detach-launch the
bridge into CLI, we don't need to track owner anyway; it stands on its own.
We should *remove* AGENT_WORKTREES_OWNER_REF as an env var, and do a sweep of
other potential env vars that don't exist solely for testing."

## Scope Approvals

Source changes, bounded validation and normal issue/PR delivery against `dev`
are directed. No live host rollout, runtime replacement or service restart
is included. Required review, CI and merge gates remain intact.

## Plan

### Phase 1 - Reviewed design
- [x] Land this proposal through normal review (#11884).

### Phase 2 - Remove inherited owner references
- [x] Remove launch writers and replace readers with CWD or explicit arguments.
- [x] Preserve managed caller checks against the resolved worktree head.
- [x] Update affected documentation and focused regression tests.
- [x] Deliver the implementation through reviewed merge (#11895).

### Phase 3 - Environment audit
- [x] Inventory non-test environment inputs across plugins, libraries and tools.
- [x] Post a per-variable classification table on #11883: test/diagnostic,
  legitimate host input, or leaking identity/owner/runtime state.
- [x] Deferred to `#11891, #11892, #11893`: Fix category-three cases through
  reviewed PRs or transfer each to a linked tracked issue with concrete evidence
  and acceptance criteria.

## Validation Plan

- [x] Prove owner references no longer select ownership through the environment.
- [x] Cover detached child identity, valid current head, stale/non-head callers,
  explicit caller context and missing managed context.
- [x] Run affected tests through bounded test supervision and Python lint.
- [x] Run relevant install-contract and documentation checks.
- [x] Audit the final public diff and live issue/PR surfaces for private data.
- [x] Verify reviewed implementation merges and finalized source worktrees;
  report source delivery separately from release or rollout.

## Proposal

Resolve each reader at its owning boundary; do not replace one inherited
owner hint with another. Native host-provided session identity remains an input
to checks, but the current worktree/head comes from authoritative resolution.
Existing explicit CLI context remains supported. Test-only controls and
legitimate host inputs are not removed merely because they are variables.

## Journal

### 2026-11-10 - Source delivery and audit complete

- Implementation #11895 merged after a current-head Copilot Comment with no
  open findings and required green Linux CI. Caller impact from deliberately
  removing environment-owner fallback was assessed against the explicit removal
  intent, documented CLI forms and regression coverage. The implementation
  source worktree was finalized after provider-confirmed merge.
- Final audit: 595 distinct inputs/declarations, classified as 52
  test/diagnostic, 400 operational/host inputs and 143 migration candidates.
  The first 537 rows and the 58-row indirect/declaration closure are published
  on #11883. Non-input constants, prefixes and probe labels are excluded.
  Declaration/scrub-only entries do not assert active readers or live defects.
- Every remaining migration candidate is individually checklist-tracked:
  #11891 owns 60 runtime/state selectors, #11892 owns 42 identity markers and
  #11893 owns 41 retained command carriers. Live readback verified all 143
  items. This completes the requested removal-and-audit scope, not those broader
  migrations. #11785 retains the narrower credential-client symptom.
- Final focused controls include 258 Bridge caller/registry/head cases,
  115 Worktrees ownership/claim cases, two actual owner-query identity cases,
  one actual exact-record/foreign-head case and 42 configuration-delivery cases.
  Earlier Codespaces 120, Dispatch diagnostics 49 and shared supervisor five
  cases also passed. All pytest execution used bounded supervision.
- Module caps, exact source admission, changefile coverage, Python lint and the
  unchanged contract checker passed. All 1,086 wire fixtures and historical
  provenance remain unchanged; caller-only lookup uses the existing exact
  project record interface instead of altering the service-side head reader.
- The native Windows full unit attempt stopped after 2,620 passes at the
  known symlink-privilege fixture gap #6093. No privilege, skip, selection,
  effect allowance or budget changed, and local packet completion is not
  claimed. Required Linux CI passed the admitted unit gate.
- Public commit and issue/PR readback audits passed. No live host rollout,
  runtime replacement or service restart was performed or authorized.

### 2026-11-09 - Kickoff

- Captured the public-safe request and claimed #11883.
- No existing canonical effort was bound to this work.
- Target adoption supports a target-owned effort. This is the canonical
  upstream plan; private operational details remain outside this repository.
- Proposal #11884 merged after current-head Copilot approval and green CI.
  Phase 2 now removes the inherited identity paths; phase 3 inventories other
  environment inputs independently.
- Bounded regression packets: Worktrees 287 passed before the cross-project
  origin fix; its affected 112-case owner/claim packet passed afterward.
  Bridge 289/290 initially passed; the remaining fixture was corrected and its
  26-case file passed. Codespaces 120, Dispatch diagnostics 49 and shared
  supervisor 5 passed. Ruff, install contract (13 plugins) and documentation
  consistency (24 plugins) passed.
- The source-linked audit classifies 537 inputs: 43 test/diagnostic, 374
  operational/host inputs and 120 migration candidates. Each migration input is
  checklist-tracked in #11891 (53), #11892 (32) or #11893 (35), with preservation
  and actual-boundary regression criteria. #11785 retains the narrower
  credential-client symptom. No live rollout is authorized.
