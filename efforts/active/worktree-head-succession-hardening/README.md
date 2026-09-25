# Worktree Head-Succession Hardening

- **Slug:** `worktree-head-succession-hardening`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase model
- **Created:** 2026-09-24
- **Status:** Draft
- **Vision:** `plugins/agent-worktrees` §Concepts/Current head and succession,
  §Concepts/Discovery for duplicate-effort detection,
  §Features/single-authorized-head-claimant, §Features/duplicate-effort-discovery,
  §Behaviors/atomic-acknowledgement-transfer
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

### Phase 1 — Acknowledgement-gated head succession (#3584, closes groundwork for #84)
- [ ] Restrict head-claiming-from-**vacant** to the `sessionStart` hook alone.
      Define "vacant" precisely as two distinct, separately-enumerated cases —
      never conflated into one "never set" test: **(a) fresh** — the worktree
      has genuinely never had a head; **(b) confirmed-recovered** — a prior
      head existed but its predecessor is proven fully abandoned through the
      existing recovery procedure, and that procedure is what marks the slot
      vacant again, not an implicit inference. No other hook/extension point
      may write the slot in either case.
- [ ] Model displacing an *existing* head as a single atomic,
      acknowledgement-gated transfer — reconciled with
      `docs/patterns/context-handoff-lifecycle.md`'s existing invariant that
      **the predecessor remains head until the successor proves it can
      recover the baton**. Explicitly reject the earlier
      "predecessor-clears-then-successor-claims" two-phase design considered
      during planning: a Copilot code review correctly flagged that an
      unconditional clear-before-claim opens a race where a delayed or failed
      launch strands the worktree with no authoritative head. The corrected
      model never separates "vacate" from "claim" into two steps.
- [ ] Confirm this composes with, not replaces, full session lineage
      journaling (every session ever associated with the worktree stays
      recorded independently of the current head).
- [ ] Add the **creation-side guard** #84 already specifies: creating a new
      session in a worktree whose head is still active is *refused* by
      default, not silently permitted, with a structured reason enumerating
      the three resolutions (reuse, hand off, sunset) plus an explicit,
      attributable break-glass override — matching agent-bridge's existing
      `409 live_cli_holds_worktree` shape. Without this, restricting who can
      *write* the head slot does not by itself stop a second session from
      being *created* while an existing head is still live, so the effort's
      "never a second session racing an existing live one" guarantee would be
      unmet. This item is load-bearing for that guarantee, not optional
      polish.
- [ ] **Carve out the legitimate handoff launch.** The default-refuse rule
      above must not also block `context-handoff-lifecycle`'s own normal
      step 4 ("launch the successor without changing the worktree head" —
      i.e. the successor session is deliberately created *while the
      predecessor is still head*, by design). A review round correctly
      caught that an undifferentiated guard would make ordinary handoff
      indistinguishable from break-glass. The guard must recognize an
      **authenticated, persisted handoff token/successor identity** — the
      same baton `context-handoff-lifecycle` already persists before launch
      — and permit exactly that launch through, while still refusing any
      *other* new-session attempt against a live head. Break-glass remains
      for the case with no such token.
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

- [ ] Simulate a handoff: successor proves it can recover the baton and the
      single atomic acknowledgement both displaces the predecessor and seats
      the successor; resuming does not land inside the predecessor's
      concluded context.
- [ ] Simulate a delayed/failed launch (successor never acknowledges): head
      remains with the predecessor, non-vacant and recoverable, never
      falsely displaced by a launch that didn't complete.
- [ ] Confirm no hook other than `sessionStart` can seat a head from vacant,
      and no path can displace an existing head without a verified successor
      acknowledgement (two targeted negative tests).
- [ ] Attempt to create a second session in a worktree whose head is still
      active: the attempt is refused with the structured reuse/hand-off/
      sunset reason, never silently allowed to proceed; a break-glass
      override is available, explicit, and attributable.
- [ ] A legitimate `context-handoff-lifecycle` handoff launch (carrying its
      authenticated persisted token) is let through the same guard without
      needing break-glass, while an unrelated ad-hoc new-session attempt
      against the same live head is still refused.
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

### 2026-09-25 — Corrected the head-transfer mechanism against an existing invariant
- The PR's automated review correctly flagged the original two-phase
  "predecessor clears `head`, then a later `sessionStart` claims the vacant
  slot" design: an unconditional clear-before-claim opens a race window where
  a delayed or failed successor launch strands the worktree with no
  authoritative head. It also flagged that this contradicted the already-shipped
  `docs/patterns/context-handoff-lifecycle.md` invariant that **the
  predecessor remains head until the successor proves it can recover the
  baton**, with head movement and successor acknowledgement happening as one
  atomic step. Revised the vision (`plugins/agent-worktrees` §Concepts/Current
  head and succession, renamed Feature `atomic-acknowledgement-transfer`) and
  this effort's Phase 1/Validation Plan to match the existing pattern instead
  of introducing a second, weaker one. The two-slot vocabulary (`head`/`backup`)
  from the original operator request is preserved as historical record in the
  Request quote above, but the design itself now models succession as one
  acknowledgement-gated transfer, not two independent slot writes.

### 2026-09-25 — Closed the creation-side race, precised "vacant"
- A further review round caught two more real gaps: (1) restricting who can
  *write* the head slot said nothing about who can *create a new session* in
  a worktree whose head is still live -- #84 already specifies a
  creation-side refusal guard for exactly this, and the effort's own stated
  guarantee ("never a second session racing an existing live one") was
  unmet without it. Added it as an explicit, load-bearing Phase 1 item plus
  a matching Validation Plan bullet. (2) "Genuinely never having been set"
  conflated two distinct vacancy cases (fresh vs. confirmed-recovered);
  re-worded to enumerate them separately so an implementation can't
  accidentally reject the recovery case or permit an unverified reclaim.

### 2026-09-25 — Carved out the legitimate handoff launch; pulled vision back to intent-level
- Review caught that the round-3 creation-guard, as written, would also
  refuse `context-handoff-lifecycle`'s own normal handoff launch (which
  deliberately creates the successor while the predecessor is still head)
  -- leaving break-glass as the only apparent way through, making ordinary
  handoff indistinguishable from an unsafe override. Added an explicit
  carve-out: the guard recognizes the authenticated persisted handoff
  token/successor identity that pattern already produces, and permits
  exactly that launch while still refusing unrelated ad-hoc attempts.
  Added a matching Validation Plan bullet.
- Separately, the vision's head-succession paragraph had drifted into
  implementation protocol (naming the session-start hook specifically,
  describing write ordering) rather than stating only the should-be
  outcome -- against the vision template's own pure-intent rule. Rewrote it
  to state the guarantee (successor proof moves authority; nothing is
  surrendered without it) and pointed to `context-handoff-lifecycle.md` for
  the actual mechanics rather than duplicating them in the vision.

### 2026-09-25 — Second intent-level scrub
- The Concept paragraph's fix wasn't enough: the paired Feature
  (`single-authorized-head-claimant`) still prescribed the realization
  ("exactly one code path", "a session-start integration", hook/extension
  write restrictions). Reworded to state only the outcome -- one
  authoritative claim, no competing overwrite path -- leaving which
  mechanism enforces it (session-start hook or otherwise) to this effort's
  own Plan rather than the vision.



