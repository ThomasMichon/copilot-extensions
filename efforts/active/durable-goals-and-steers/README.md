# Durable Goals and Steers

## Goal

> When I mention "goal", "mission" or "objective", the agent must update its effort to state it clearly. That way a handoff that points at the effort, or a re-read in a new context, shows the goal front and center. Fix efforts, handoffs, agent guidance and the new agenda system so they work together. User prompts should be relayed immediately into a durable location. Mechanically, the context-handoff extension should record user prompts and significant steers, leaving out skill loads, agent-to-subagent instructions and similar noise, and accumulate them into a list. After any session reset (observed as an event), force that corpus to be read back in. Acknowledge that a lot may have happened between steers, so each steer needs research before acting on it. The agent always sees this up front.

- **Slug:** `durable-goals-and-steers`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed plan and implementation PRs targeting `dev`
- **Created:** 2026-11-07
- **Status:** Draft
- **Vision:** [Efforts](../../../visions/plugins/efforts/README.md), durable
  effort contract and compact effort handoff
- **Umbrella issue:** [#11403](https://github.com/ThomasMichon/copilot-extensions/issues/11403)

## Guiding Intent

Operator objectives and significant amendments must remain visible across
context reconstruction. Current effort Goal is canonical; recorded prompts
retain evidence and chronology rather than silently becoming fresh authority.
This campaign closes the existing durable-effort-contract and session-relay
intent without adding a service, transcript index or mandatory sibling runtime.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Plan, continuity/efforts integration, validation and PR ownership | Managed public source handle `buzzing-knob` |

## Coordination

Independent serial plan and implementation PRs. The source driver owns review,
CI and merge for every PR; no delegate or other participant's state is modified.
Handoffs point to this README and its Goal, current slice and immediate delta.

## Context

The extension already observes root `user.message`,
`session.context_cleared`, successful compaction and handoff consumption.
The current prompt observer retains a first prompt in module state, which is
not a durable steer history. Agenda projections currently label a root by its
source reference rather than presenting the canonical effort Goal.
Existing [continuity behavior](../../../plugins/context-handoff/README.md) and
[agenda companion](../../../plugins/efforts/skills/driving-effort-agendas/SKILL.md)
remain authoritative for lifecycle, ownership and optional adoption.

## Request

1. Preserve the operator directive in Goal and make goal/mission/objective
   amendments update the canonical effort immediately.
2. Durably accumulate genuine operator prompts and significant steers, excluding
   skill loads, subagent instructions and tool noise.
3. After observed context reconstruction or handoff consumption, front-load the
   corpus and current Goal; require research of each steer against current state
   before acting, preserving supersession, holds and original authority bounds.
4. Source agenda root goals and effort-backed handoffs from canonical Goal.

## Scope Approvals

Source inspection, implementation, isolated tests and ordinary reviewed PR
delivery to `dev` are assigned. Deployment remains adopter-owned; source merge
does not itself grant installation or restart authority. Preserve current
owner, host policy, permission and review gates. Corpus capture/replay is
evidence handling, never automatic execution or a new authorization evaluator.

## Plan

Implementation choices and sequencing below are agent recommendations.

### Phase 1 - Reviewed contract
- [x] Claim #11403 and inspect existing continuity, effort and agenda entrypoints.
- [ ] Land this proposal through current-head review and required CI.

### Phase 2 - Goal and continuity
- [ ] Add top Goal to canonical and repository templates; reconcile planning,
      capture and completion guidance without removing legacy effort support.
- [ ] Implement a plugin-owned durable worktree-scoped prompt/steer corpus using
      existing local identity/state boundaries, no shared daemon or source-tree
      transcript writes. Preserve exact text, origin, event identity and ordering.
- [ ] Filter root inputs by available origin metadata and known skill,
      subagent, notification and plugin-generated envelopes; do not promote peer
      instructions into operator identity. Retain significant authorized peer
      steers distinctly where supported.
- [ ] Connect capture and replay to the actual extension lifecycle, reset/
      successful-compaction and consume boundaries, including reconnect startup.
      Persistence failure stays visible; retries must not lose or duplicate input.
- [ ] Front-load current Goal and corpus reread, with no silent history truncation;
      expose bounded reading when the corpus exceeds one model message. Unsupported
      host admission remains explicitly unqualified, not claimed enforced.
- [ ] Preserve authorization evidence and current holds; replay requests research,
      not re-execution of stale effects.

### Phase 3 - Agenda and handoff integration
- [ ] Resolve effort Goal in existing standalone/managed agenda readers, retaining
      optional/manual and task/aside roots without inventing a second goal store.
- [ ] Include canonical Goal/corpus pointers in generated and consumed handoffs.
- [ ] Document standalone behavior, privacy/storage scope and supported replay
      boundaries; add changefiles for every affected plugin.
- [ ] Land implementation through review and required CI.

### Phase 4 - Source closeout
- [ ] Record actual merged PRs and observed release state separately from adoption.
- [ ] Resolve validation, close tracking and archive the completed source campaign.
      Installed rollout and host smoke belong to each adopter's existing authority.

## Validation Plan

- [ ] Focused tests fail before the fix for exact prompt preservation, repeated
      reset/reconnect/consume, duplicate event delivery, noise filtering,
      concurrent writes, worktree isolation and visible persistence failures.
- [ ] Exercise actual registered extension event handlers with an SDK fixture,
      not only helper functions; prove replay does not capture itself.
- [ ] Verify Goal changes reach agenda and handoff projections; legacy efforts
      without Goal remain usable with an explicit fallback.
- [ ] Exercise corpus paging/order and ensure no constraints disappear silently
      when history exceeds one message/read budget.
- [ ] Run affected-plugin tests and required lint/customization/contract checks
      through existing bounded runners; document meaningful host-proof limits.
- [ ] Complete documentation-impact and publication-safety review.
- [ ] Verify every source PR merged after accepted review and required CI;
      report release and installed adoption separately, never infer live rollout.

## Proposal

Prefer an SDK-independent corpus module plus an event-registration adapter in
the existing extension. Durable state is local to the continuing worktree;
the canonical effort remains versioned planning truth. Event-time writes
must settle before a reset/replay snapshot; asynchronous work is serialized
and failures are visible rather than ignored. Origin classification is not
authentication: inherited trust and original evidence remain with their owners.
Agenda reads use the current source document, not an independently mutable
root-goal string. Research may refine these choices within the Request.

## Journal

### 2026-11-07 - Proposal
- Claimed #11403 after public duplicate searches. Confirmed root clear and
  successful-compaction observations and existing prompt-receipt registration.
- Reconciled to durable-effort-contract, compact-effort-handoff and
  session-relay intent; retain payload-only and independent-plugin patterns.
- No installed runtime change, new daemon or other participant's state mutation.
