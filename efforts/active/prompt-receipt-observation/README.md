# Prompt Receipt Observation

- **Slug:** `prompt-receipt-observation`
- **Repo:** copilot-extensions
- **Branch(es):** managed proposal and implementation PRs
- **Created:** 2026-10-22
- **Status:** Draft
- **Vision:** `visions/plugins/context-handoff/README.md`: continuous work,
  durable recovery locator, objective and authority boundary
- **Umbrella issue:** #7942

## Guiding Intent

First-prompt receipt observation must never delay a user's turn on runtime
provisioning, identity discovery or an unavailable optional worktrees service.
Observation remains distinct from consuming a baton or acquiring ownership.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| hinged-socket | Proposal, implementation, validation and source PR owner | Managed source worktree; #7942 |

## Coordination

- **Topology:** sequential proposal and implementation PRs.
- **Host (owns PRs):** hinged-socket.
- **Delegates:** none; required independent review uses repository review.
- **Handoff:** source qualification packet; release and installed adoption
  remain outside this bounded source objective.
- Preserve existing source claims. Collision detection is read-only forge/task
  inspection; no contact with pre-existing Bridge agents, agent-chat or remote
  worker dispatch. Any review requiring such contact is a blocker, not an
  exception. Only campaign-owned native workers or newly created owned local
  agents may participate when explicitly needed.

## Context

The first `user.message` callback currently calls `agentWorktreesGet` and
`logHandoffPromptReceived` synchronously. Their shared CLI path may resolve
and provision an optional runtime. Existing async non-provisioning helpers
provide a seam, but merely wrapping synchronous work in a promise is insufficient.
This closes existing continuity intent without changing hosting authority.
Design principles: optional siblings degrade independently; payload-only
shape remains; Windows and POSIX share the lifecycle contract.

## Request

Public-safe excerpts of the authorized bounded request:

> "make first-prompt recovery receipt observation asynchronous and non-provisioning"
>
> "Implement cheap locator capture, launch-provided identity preference and bounded
> async receipt observation. NO synchronous execFileSync/installer from prompt event,
> no per-event unbounded queue/process/thread."
>
> "Missing runtime/blocked identity/absent worktrees service must not stall user's
> turn; preserve idempotent replay, pending/unavailable state and later
> reconciliation. Ownership/consumption authority is unchanged."
>
> "Whole hook objective normally instant, <=1-2s worst-case; configured timeouts
> not measured proof."

Scope: context-handoff extension/core/timeouts, their tests and directly related
documentation. Excludes native launch scripts, worktrees cache/readiness,
Manager, telemetry, mention guard, installed payload and shared services.

## Plan

### Phase 1 — Reviewed proposal
- [ ] Land this bounded plan through repository review and required CI.

### Phase 2 — Observation implementation
- [ ] Capture only the first prompt's canonical locator without synchronous CLI
  calls; prefer existing launch identity without inventing authority.
- [ ] Use one bounded async observation, with coalesced duplicates, explicit
  pending/unavailable/logged outcomes and later reconciliation.
- [ ] Prevent provisioning and bound the complete child lifecycle, including
  cancellation/cleanup, rather than only promise scheduling.
- [ ] Preserve receipt format, replay behavior and all consumption/ownership checks.
- [ ] Update consumer documentation, add changefile, review and merge source.

## Validation Plan

- [ ] Unit coverage: launch identity, missing runtime, blocked identity, absent
  service, concurrent duplicates/replay and deferred availability.
- [ ] Native consumer integration: exercise the actual registered callback with
  blocked child processes and verify return latency, event-loop responsiveness,
  process cleanup and no installer descendants.
- [ ] Isolated first-touch execution with no provisioned runtime.
- [ ] Measure callback and lifecycle durations; target normally instant callback,
  at most 1-2 seconds worst case. Do not call configured deadlines evidence.
- [ ] Run applicable source checks and required CI; assess independent review.
- [ ] Record genuinely available safe external qualification, or explain skipped
  tiers without implying installed/released/live proof.

## Proposal

Pending review. Implementation may refine internal seams after the plan clears;
new authority or ownership behavior requires a separate proposal.

## Journal

### 2026-10-22 — Bounded source intake

- Confirmed open #7942 has no existing implementation PR or competing task.
- Created isolated managed source worktree `hinged-socket` and claimed the issue.
- Traced the synchronous prompt callback, identity helper and receipt logger.
- Captured the authorized scope and latest coordination restrictions. No existing
  agent session was contacted and no shared runtime was changed.
