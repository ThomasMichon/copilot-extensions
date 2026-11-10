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
- **Status:** Draft
- **Vision:** agent-fabric `derive-dont-duplicate`,
  `trusted-cooperation-with-economical-evidence`
- **Umbrella issue:** #11883

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
- [ ] Land this proposal through normal review.

### Phase 2 - Remove inherited owner references
- [ ] Remove launch writers and replace readers with CWD or explicit arguments.
- [ ] Preserve managed caller checks against the resolved worktree head.
- [ ] Update affected documentation and focused regression tests.
- [ ] Deliver the implementation through reviewed merge.

### Phase 3 - Environment audit
- [ ] Inventory non-test environment inputs across plugins, libraries and tools.
- [ ] Post a per-variable classification table on #11883: test/diagnostic,
  legitimate host input, or leaking identity/owner/runtime state.
- [ ] Fix category-three cases through reviewed PRs or transfer each to a
  linked tracked issue with concrete evidence and acceptance criteria.

## Validation Plan

- [ ] Prove owner references no longer select ownership through the environment.
- [ ] Cover detached child identity, valid current head, stale/non-head callers,
  explicit caller context and missing managed context.
- [ ] Run affected tests through bounded test supervision and Python lint.
- [ ] Run relevant install-contract and documentation checks.
- [ ] Audit the final public diff and live issue/PR surfaces for private data.
- [ ] Verify reviewed implementation merges and finalized source worktrees;
  report source delivery separately from release or rollout.

## Proposal

Resolve each reader at its owning boundary; do not replace one inherited
owner hint with another. Native host-provided session identity remains an input
to checks, but the current worktree/head comes from authoritative resolution.
Existing explicit CLI context remains supported. Test-only controls and
legitimate host inputs are not removed merely because they are variables.

## Journal

### 2026-11-09 - Kickoff

- Captured the public-safe request and claimed #11883.
- No existing canonical effort was bound to this work.
- Target adoption supports a target-owned effort. This is the canonical
  upstream plan; private operational details remain outside this repository.
