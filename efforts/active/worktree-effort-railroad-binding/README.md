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
- **Sub-issues:** #3582 · #3583

## Guiding Intent

A worktree bound to an effort should stay tightly railroaded to that effort as
it progresses -- in its title, in its record of completed work, and in the
guidance an agent gets mid-session -- rather than gradually drifting off the
plan the way a long or context-pressured session tends to today.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|--------------|
| Driving agent | Authors and drives all three phases | The effort's active worktree |

## Coordination

- **Topology:** independent per-phase PRs (each phase is independently
  reviewable and shippable).
- **Host (owns PRs):** Driving agent.
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
- [ ] _(agent-recommended, added from PR review analysis, not the original
      operator request)_ Define explicit title precedence/migration rules,
      not a bare "by default": (a) **binding a worktree that already
      carries a freeform asserted title** — the effort-anchored title takes
      over and *replaces* it going forward (the freeform title is
      superseded, not merged or preserved as a prefix); (b) **writing
      `status --title` while a binding is active** — the explicit write is
      rejected or immediately re-derived back to the anchored form, so an
      agent cannot silently unanchor a bound worktree's title through the
      ordinary title-write path; (c) **re-binding to a different effort, or
      a phase/slice transition** — the title re-derives to match the new
      binding/phase; (d) **unbinding** — the worktree reverts to today's
      freeform `status --title` behavior, keeping whatever title it last
      had until explicitly changed.
- [ ] Implement derivation from the bound effort's slug + current
      phase/slice per the rules above.
- [ ] Only phase/slice transitions change the title; a session merely
      rephrasing the same phase does not.
- [ ] Unbound worktrees keep today's freeform `status --title` behavior
      unchanged (rule (d) above).

### Phase 2 — Auto-derived current slice + mid-session railroad nudge (#3583)
- [ ] _(agent-recommended, added from PR review analysis, not the original
      operator request)_ **Limit auto-derivation to single-worktree
      bindings; never auto-derive across a multi-worktree effort.** The
      `plugins/efforts` vision explicitly permits several worktrees to
      contribute *distinct declared slices* to the same effort, and
      `duplicate_binding()` keys on `(effort path, slice)` to tell separate
      contributors apart. An effort-wide "first unchecked checklist item"
      auto-derivation would make every worktree bound to that effort
      converge on the *same* derived slice, wrongly flagging legitimate
      concurrent contributors as duplicates. Before canonicalizing anything:
      auto-derivation applies **only** when the effort has exactly one
      active binding (no other worktree currently holds a declared slice
      against it); a binding with an explicitly reserved/declared slice — or
      any effort with multiple concurrent bindings — is never overwritten by
      auto-derivation and keeps its hand-declared `--slice` as-is.
- [ ] _(agent-recommended, added from PR review analysis, not the original
      operator request)_ **Establish one canonical source of truth for the
      binding's slice** (within the single-binding case the item above
      scopes this to). `ActiveEffort.slice` is persisted and already
      consumed by `inspect_effort()`, `orientation()`, binding validation,
      and `duplicate_binding()`; deriving a *different* value only for the
      orientation/nudge path (without updating the persisted field) would
      leave those other consumers stale and create two disagreeing notions
      of "current slice." Specify the atomic update/revalidation semantics
      before implementing: when the derived value differs from the
      persisted `--slice`, the persisted binding is revalidated and updated
      to match at a defined point (e.g. on each orientation/nudge
      computation, or on the operation that changed the checklist) — never
      left to silently diverge.
- [ ] _(agent-recommended, added from PR review analysis, not the original
      operator request)_ **Derive a stable phase/item representation, not
      raw bullet text.** `validate_binding()` currently requires
      `ActiveEffort.slice` to match a declaration in Plan/Coordination, and
      parses checklist bullets separately for completion — writing the
      first unchecked bullet's raw text back as the slice would both
      bypass that declared-match requirement and risk exceeding the
      existing 180-character label limit. Define a stable identifier
      instead (e.g. the enclosing Plan phase heading, optionally with an
      item index within it — "Phase 2, item 3" — never the free-text
      bullet body), and update `validate_binding()` and every other slice
      consumer to recognize and accept that derived form consistently,
      before implementing derivation.
- [ ] Extend `effort_focus.py` to derive "current slice" from the first
      unchecked Plan/Validation Plan item, falling back to the declared
      `--slice` string only when the effort has no checklist structure yet,
      and writing the derived value back per the canonicalization rule
      above.
- [ ] _(agent-recommended, added from PR review analysis)_ **Define the
      terminal case — a checklist that exists but has nothing left
      unchecked.** The "no checklist structure" fallback only covers an
      effort with no checklist at all; it says nothing about an *open*
      effort whose Plan/Validation checklist is fully checked (mid-way
      through closing out, before `Status: Done` lands). There is then no
      "first unchecked item" to derive from, yet the persisted
      `ActiveEffort.slice`, `validate_binding()`, `orientation()`, and the
      anchored title all still require *some* value. Define an explicit
      terminal-state slice (e.g. a fixed "closing out" identifier, or the
      last checked item) before implementing derivation, rather than
      leaving this case to fail or silently fall back unpredictably.
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
- [ ] A worktree carrying a pre-existing freeform title is bound to an
      effort: the title is replaced by the anchored form, not merged or
      left as-is. An attempted freeform `status --title` write while bound
      does not silently unanchor it. Unbinding restores ordinary freeform
      behavior.
- [ ] A test effort with a partially-checked Plan is bound; `orientation()`
      (or its Phase 2 successor) reports the stable phase/item identifier
      (a Plan heading, optionally with an in-phase item index) of the first
      unchecked item as the current slice, without requiring a manual
      `--slice` re-bind, and without ever surfacing raw bullet text as the
      slice value.
- [ ] After a checklist item is ticked, the persisted `ActiveEffort.slice`
      itself updates to match the newly-derived identifier (not merely the
      orientation/nudge display) — confirmed by reading the binding through
      `inspect_effort()`/`duplicate_binding()`, not only through
      `orientation()` — and `validate_binding()` accepts the derived form
      without rejecting it as an undeclared slice or an over-length label.
- [ ] Two worktrees bind distinct declared slices of the *same* effort:
      auto-derivation never fires for either (multiple concurrent bindings
      present), both keep their hand-declared slices untouched, and
      `duplicate_binding()` still correctly distinguishes them. A *single*
      worktree bound alone to an effort still gets auto-derivation as
      designed.
- [ ] A test effort whose Plan/Validation checklist is fully checked (but
      `Status` is not yet `Done`) is bound: derivation resolves to the
      defined terminal-state slice rather than erroring or falling back
      unpredictably, and every consumer (`validate_binding()`,
      `orientation()`, the anchored title) agrees on that same value.
- [ ] A long simulated session (tool-call count past threshold) receives
      exactly one railroad nudge per drift window, matching
      `nudge_status.py`'s existing no-spam guarantee.
- [ ] A test effort whose journal/checklist is deliberately left stale behind
      real committed work (simulating the Phase 3 failure mode) is detected
      by the staleness signal, or -- if that stretch item is not implemented
      -- Phase 3 is explicitly narrowed to wording-only scope in its Plan so
      this Validation Plan does not claim to cover behavior it doesn't
      implement.
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

### 2026-09-25 — Review fixes: validation coverage + wording
- Review caught that Phase 3 (journal-as-ground-truth) had no matching
  Validation Plan item -- added one, with a fallback narrowing instruction
  if the staleness-signal stretch item isn't implemented. Also clarified
  the vision's "railroad" metaphor ("not only at the platform" -> "not only
  at its outset") which read as unclear rather than evocative.

### 2026-09-25 — Title precedence/migration rules
- Review caught that Phase 1's "derive the title by default" was weaker
  than the vision's actual anchoring intent: existing tracking treats an
  agent-asserted `status --title` as authoritative, so a worktree bound
  after already carrying a freeform title (or re-titled by hand while
  bound) could end up unanchored despite the binding. Added explicit
  precedence/migration rules for bind, write-while-bound, re-bind/phase
  transition, and unbind, plus a matching Validation Plan bullet.

### 2026-09-25 — Canonicalize the derived slice against the persisted binding
- Review caught that Phase 2's auto-derivation, as written, only fed the
  orientation/nudge display -- but `ActiveEffort.slice` is already the
  persisted field `inspect_effort()`, binding validation, and
  `duplicate_binding()` all read, so a derived value that never wrote back
  would leave those consumers looking at a stale slice. Added an explicit
  canonicalization requirement (one source of truth, atomic
  update/revalidation semantics) as its own Plan item ahead of the
  derivation work, plus a Validation Plan bullet confirming the persisted
  field itself updates, not merely the display.

### 2026-09-25 — Stable phase/item identifier, not raw bullet text
- Review caught that writing the first unchecked bullet's raw text back as
  the slice would conflict with `validate_binding()`'s existing requirement
  that `ActiveEffort.slice` match a declaration in Plan/Coordination, and
  risked exceeding the 180-character label limit. Redefined the derived
  value as a stable phase/item identifier (a Plan heading, optionally with
  an in-phase item index) instead of free-text, and required
  `validate_binding()` and every slice consumer to be updated to recognize
  that form before implementing derivation. Updated the matching Validation
  Plan bullets to check the identifier form and `validate_binding()`
  acceptance explicitly.

### 2026-09-25 — Demarcated review-derived Plan items as agent-recommended
- Review caught that several Plan items added across prior rounds (title
  precedence/migration, slice canonicalization, stable identifier
  derivation) originated from PR review analysis, not the operator's
  quoted request, but weren't visibly marked as such per the
  `planning-efforts` skill's demarcation rule. Added explicit
  `(agent-recommended)` tags to those bullets so a later reader can
  distinguish settled operator scope from agent recommendation.

### 2026-09-25 — Defined the terminal (fully-checked-but-not-Done) slice case
- Review caught that the "no checklist structure" fallback didn't cover an
  *open* effort whose checklist is fully checked but not yet `Status: Done`
  -- there is then no "first unchecked item" to derive from, yet every
  consumer (`ActiveEffort.slice`, `validate_binding()`, `orientation()`,
  the anchored title) still needs a value. Added an explicit terminal-state
  slice requirement and a matching Validation Plan bullet.

### 2026-09-25 — Scoped auto-derivation to single-worktree bindings only
- Review caught the most significant gap yet: the `plugins/efforts` vision
  explicitly permits several worktrees to contribute distinct declared
  slices to the *same* effort, and `duplicate_binding()` keys on
  `(effort path, slice)` to tell them apart. Effort-wide "first unchecked
  item" auto-derivation would make every worktree bound to that effort
  converge on the same derived slice, wrongly flagging legitimate
  concurrent contributors as duplicates. Added an explicit scoping rule
  (auto-derivation applies only when an effort has exactly one active
  binding; any multi-worktree or explicitly-reserved-slice binding keeps
  its hand-declared slice untouched) ahead of the canonicalization work,
  plus a matching Validation Plan bullet covering both the single- and
  multi-worktree cases.







