# Worktree/Effort Railroad Binding

- **Slug:** `worktree-effort-railroad-binding`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase model
- **Created:** 2026-09-24
- **Status:** Draft
- **Vision:** `plugins/agent-worktrees` §Concepts/Title and naming,
  §Features/effort-anchored-title · `plugins/efforts`
  §Concepts/Current-slice derivation, §Features/railroad-nudge-at-drift,
  §Behaviors/journal-is-ground-truth
- **Umbrella issue:** #3581
- **Sub-issues:** #3581 · #3582 · #3583

## Guiding Intent

A worktree bound to an effort should stay tightly railroaded to that effort as
it progresses -- in its title, in its record of completed work, and in the
guidance an agent gets mid-session -- rather than gradually drifting off the
plan the way a long or context-pressured session tends to today.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|--------------|
| lambda-core | Authors and drives all three phases | `copilot-extensions.worktrees/lambda-core-wsl-20260924-231629-a05a` |

## Coordination

- **Topology:** independent per-phase PRs (each phase is independently
  reviewable and shippable).
- **Host (owns PRs):** lambda-core.
- **Delegates:** none currently.
- **Handoff:** n/a (single participant today).

## Context

Operator pain points motivating this effort (facility session, 2026-09-24):

1. Agents given naming guidance (via the disposition nudge) tend to title a
   worktree after "what we're doing right now," not accounting for the
   *previous* session's framing -- so titles swing dramatically across a
   handoff even when the underlying objective hasn't changed.
2. Effort journals/checklists are found stale relative to real completed
   work -- agents do real work across sessions without journaling it back or
   ticking items promptly, defeating `record-first-resumption`.
3. The existing session-start effort `orientation()`
   (`plugins/agent-worktrees/src/agent_worktrees/effort_focus.py`) fires
   exactly once, and its "slice" is a hand-typed string set at
   `effort-focus bind` time that silently goes stale as work advances through
   phases -- an agent can "lose the plot" on a long run with no environmental
   correction until it re-reads the README itself.

Existing substrate this effort builds on (verified in source, not proposed
fresh):
- `effort_focus.py` already regex-parses the effort README's `Slug`/`Status`
  header and every Plan/Validation Plan checklist item (`_HEADER_RE`,
  `_TASK_RE`) -- **no new structured `plan.yaml` format is needed**; the
  checklist itself is already machine-readable.
- `scripts/nudge_status.py` is a proven, already-shipped pattern: a
  `postToolUse` hook with a per-worktree drift counter/timer (25 tool calls /
  20 minutes, env-overridable) that injects `additionalContext`, currently
  wired only to the Picker disposition (summary/title/follow-up).
- Issue #3295 (open, unclaimed) proposes a **claim-provider** registry
  generalization for a related but distinct problem (claim-status lookups
  crossing plugin tiers). This effort's Phase 3 draws on the same shape for a
  different surface (orientation/nudge content) but is explicitly a stretch,
  not required for the MVP of Phases 1-2.

## Request

> "Be stronger with worktree-lifecycle and effort-planning instructions to
> tell agents that when an effort gets involved, both claim the effort, and
> also force the effort name slug into the worktree name, keeping it there
> until the effort is done... Enforce that agents more-aggressively journal
> status back to the effort... We really want an effort's journal and phase
> status to be 'ground truth' for what has been completed so far."
>
> "...our focus here should be on tighter worktree/effort binding. We could
> consider using hooks' or extensions' additionalContext hooks to nudge
> worktrees back onto an effort's railroad somehow. We could also split apart
> effort README.md files to support a 'plan.yaml' or other structured file...
> which would allow it to inject only the 'current and next step' guidance
> back into an agent session at key points."
>
> (The operator explicitly flagged the `plan.yaml` idea as a possible
> redundancy risk; investigation confirmed the regex-based checklist parsing
> already covers that need, so Phase 2 below reuses it instead of adding a
> new file format.)

## Plan

### Phase 1 — Effort-anchored worktree titling (#3581)
- [ ] When `effort-focus bind` is active, derive the worktree title from the
      bound effort's slug + current phase/slice by default.
- [ ] Only phase/slice transitions change the title; a session merely
      rephrasing the same phase does not.
- [ ] Unbound worktrees keep today's freeform `status --title` behavior
      unchanged.

### Phase 2 — Auto-derived current slice + mid-session railroad nudge (#3583)
- [ ] Extend `effort_focus.py` to derive "current slice" from the first
      unchecked Plan/Validation Plan item, falling back to the declared
      `--slice` string only when the effort has no checklist structure yet.
- [ ] Add a `postToolUse` hook (new script, or an extension of
      `nudge_status.py`'s drift-counter pattern) that periodically injects an
      `additionalContext` reminder naming the bound effort, its current
      slice, and the immediate next step -- reusing the existing
      calls/minutes threshold + reset-on-write shape.
- [ ] Confirm the nudge never fires for a worktree with no active effort
      binding (no change in behavior for unbound worktrees).

### Phase 3 — Journal-as-ground-truth enforcement (#3582)
- [ ] Strengthen the `planning-efforts` skill's wording: journal updates are
      the record of truth, expected at least once per meaningful slice, not
      only at "moments that matter."
- [ ] _(agent-recommended, stretch)_ Evaluate a lightweight local
      journal-staleness signal (time/tool-calls since the effort's own
      journal path last changed, while other worktree files did) that keeps
      the worktree assertively WIP rather than looking falsely at-rest.

### Phase 4 — _(agent-recommended, stretch, not committed)_ Disposition-contributor generalization
- [ ] Evaluate generalizing Phase 2's nudge integration into a
      claim-provider-style registry (per #3295's shape) so the `efforts`
      capability registers *content* into agent-worktrees' nudge/orientation
      surfaces, instead of agent-worktrees hardcoding effort-specific
      parsing. Only pursued if Phases 1-3 reveal real friction from the
      hardcoded approach.

## Validation Plan

- [ ] A worktree bound to an effort, re-titled across a simulated
      phase-boundary handoff, keeps its title anchored to the effort slug
      rather than reflecting only the latest session's framing.
- [ ] A test effort with a partially-checked Plan is bound; `orientation()`
      (or its Phase 2 successor) reports the first unchecked item as the
      current slice without requiring a manual `--slice` re-bind.
- [ ] A long simulated session (tool-call count past threshold) receives
      exactly one railroad nudge per drift window, matching
      `nudge_status.py`'s existing no-spam guarantee.
- [ ] An unbound worktree's behavior (titling, nudges) is unchanged by this
      effort.

## Proposal

_Pending — this effort's plan itself will be submitted for review per the
repo's `pr-self-merge` profile before Phase 1 implementation begins._

## Journal

### 2026-09-24 — Kickoff
- Effort created from a facility planning session; captured operator request
  verbatim above. Verified existing substrate (`effort_focus.py` regex
  parsing, `nudge_status.py` drift-counter pattern) before committing to a
  plan, ruling out a redundant `plan.yaml` format per the operator's own
  flagged concern. Filed sub-issues #3581 (title), #3582 (journal ground
  truth), #3583 (auto-derived slice + nudge); related #3295 (claim-provider
  pattern) as Phase 4 prior art.
