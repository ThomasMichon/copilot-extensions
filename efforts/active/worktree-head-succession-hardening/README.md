# Worktree Head-Succession Hardening

- **Slug:** `worktree-head-succession-hardening`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase model
- **Created:** 2026-09-24
- **Status:** Draft
- **Vision:** `plugins/agent-worktrees` §Concepts/Current head and succession,
  §Concepts/Discovery for duplicate-effort detection,
  §Features/single-authorized-head-claimant, §Features/duplicate-effort-discovery,
  §Behaviors/yield-clears-before-claim-arrives
- **Umbrella issue:** #3584
- **Sub-issues:** #3585

## Guiding Intent

Resuming a worktree after a handoff should always land the rightful, current
head session -- never a stale continuation of a predecessor's already-concluded
context, and never a second session racing an existing live one. Separately,
starting new work should be cheap to check against a duplicate claimant already
driving the same objective elsewhere in the fleet.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|--------------|
| Driving agent | Authors and drives both phases | The effort's active worktree |

## Coordination

- **Topology:** independent per-phase PRs.
- **Host (owns PRs):** Driving agent.
- **Delegates:** none currently.
- **Handoff:** n/a (single participant today).

## Context

Operator-observed symptom: resuming a worktree that previously handed off
sometimes lands a session into the tail end of the *predecessor's own*
context-loaded session (the handoff prompt) rather than a genuinely fresh
head -- and `/clear`ing that session then reports the handoff as already
consumed. This makes operators reluctant to resume worktrees that ever handed
off, precisely the case recovery depends on most.

This is not a net-new problem: **issue #84** ("Session lifecycle: single
current session per worktree -- head pointer + asserted conclusion + two-way
handoff chain") already names the model and has sat open, unclaimed, 0
comments. This effort supplies the concrete mechanism and drives it to
landing. Related, narrower issues: #912 (sessionStart succession awareness),
#3000 (context-handoff: reconcile authoritative handoff target across
mux/agent-worktrees/agent-dispatch), #910 (mirror handoff reference into the
worktree record).

A second, smaller and independent symptom folded in here because both concern
"which agent is authoritative right now": agents rarely check whether another
worktree is already driving the same objective before starting new work, even
though `agent-worktrees list --json` makes that cheap -- and when a related
chain of repos/worktrees is involved, the *root claimant* actually driving the
objective can be a worktree other than the one nearest at hand.

## Request

> "Still present session-tracking bugs between agent-worktrees and
> context-handoff [sic, corrected from the original "content-handoff"] mean
> that when I do resume worktrees, a non-current session
> resumes in the worktree... This makes me reluctant to resume old worktrees
> that did handoffs, because the process to get back and going is very
> tedious."
>
> "The agent-worktrees session-start hook MUST ALWAYS record the now-current
> session as the head session, and we must take care not to use other hooks
> to do the binding, unless the 'head session' slot for a worktree is empty.
> There needs to be a 'backup' slot for the head, so that during a handoff,
> the current session puts itself into the backup slot and clears the head
> position, so the next session's start can claim the now-empty head. This is
> in addition to just journaling every session ever associated with the
> worktree."
>
> "Agents need a quick way to learn about other active agents on the same
> machine+environment, as well as throughout the system... so that when the
> operator makes a potentially-duplicate request, the agent can refer to the
> user to the existing worktree already driving that effort. It's also
> important to point to the root claimant worktree in a series, not just the
> leaf."

## Plan

### Phase 1 — Head/backup-slot succession (#3584, closes groundwork for #84)
- [ ] Add the two-slot model to the worktree record: `head` (current
      authoritative session) and `backup` (most recent predecessor).
- [ ] Restrict head-claiming to the `sessionStart` hook alone, gated on `head`
      being empty; no other hook/extension point may write it.
- [ ] Make yielding atomic: the yielding session moves its own identity from
      `head` to `backup` and clears `head` in one step.
- [ ] Confirm this composes with, not replaces, full session lineage
      journaling (every session ever associated with the worktree stays
      recorded independently of the two live slots).
- [ ] Reconcile with #912 and #3000's overlapping scope so the three don't
      land contradictory mechanisms.

### Phase 2 — Duplicate-claimant discovery + root-claimant surfacing (#3585)
- [ ] Strengthen the `worktree` skill's instructions to make an early
      `list --json` check (filtered on bound effort/objective) a routine step
      before starting substantial new work.
- [ ] When a chain of related worktrees/repos is involved, resolve discovery
      to the root claimant actually driving the objective, not the nearest or
      most-recently-touched member of the chain.

## Validation Plan

- [ ] Simulate a handoff: predecessor yields, successor's `sessionStart`
      claims a genuinely empty head; resuming does not land inside the
      predecessor's concluded context.
- [ ] Simulate a crash (predecessor never yields): head remains non-vacant and
      recoverable, never falsely claimed by a session that raced in.
- [ ] Confirm no non-`sessionStart` hook can write the head slot (a targeted
      negative test).
- [ ] A fleet with an existing worktree bound to effort X surfaces that
      worktree (and, where a chain is involved, the root claimant) when a
      second agent checks before starting work on X.

## Proposal

_Pending — this effort's plan itself will be submitted for review per the
repo's `pr-self-merge` profile before Phase 1 implementation begins._

## Journal

### 2026-09-24 — Kickoff
- Effort created from a facility planning session. Deliberately did not open
  a new issue duplicating #84 -- it already names the model precisely; this
  effort supplies the mechanism and cites it plus #912/#3000/#910 as related.
  Filed #3584 (mechanism) and #3585 (discovery/root-claimant), folding the
  smaller discovery item in here rather than a fourth effort, since both are
  about "which agent has authority right now."
