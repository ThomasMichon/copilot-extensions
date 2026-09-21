# AGENTS.md vs .github/instructions Split

**Serves:** `ThomasMichon/copilot-extensions#2825` (formalize and audit the
`AGENTS.md`-vs-`.github/instructions` split), captured during
`efforts/active/pr-conduct-guidance-consolidation`.

## Problem

A repo commonly carries two different kinds of agent-facing guidance in two
different files, and it is easy to put the wrong content in the wrong one:

- **`AGENTS.md`** at a repo's root.
- **`.github/instructions/*.instructions.md`** files (static, or projected by
  a plugin's `sessionStart` hook per
  [`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)).

Without a stated rule, content drifts into whichever file a contributor
happens to be editing. This repo hit a real incident from exactly that drift:
`gim-home/odsp-web-harness`'s `AGENTS.md` hardcoded a PR-review-count claim
that silently went stale against the live GitHub branch ruleset, breaking
self-merge until diagnosed by hand -- a fact that should have been resolved
live from config, not restated as static prose in the visitor contract.

## The principle

> A repo's root `AGENTS.md` is the **universal visitor contract** -- correct
> guidance for *any* agent operating in/on that repo, regardless of whether
> the calling agent's home base is this repo or another one (branch
> conventions, PR/merge policy, review rules, build steps, repo-specific
> safety rules, etc.).
>
> `.github/instructions/*.instructions.md` is the surface for a **harness**
> to configure its own session-scoped behavior when operating **from** that
> repo as its home base -- dynamic, plugin-computed guidance (the pattern
> `dotfiles-harness`/`ai-attribution`/`agent-worktrees`'s own session-context
> work already use via `instruction-projections.json` + a `sessionStart`
> hook, per [`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)).

The distinction is not "static vs. dynamic" by itself -- it is **who the
guidance is for** and **whether it is resolvable at review time**:

| | `AGENTS.md` | `.github/instructions/*.instructions.md` |
|---|---|---|
| Audience | any agent visiting this repo, from any home base | a harness operating with this repo as its own home base |
| Authored | by hand, reviewed like any other doc | often projected by a plugin's `sessionStart` hook from live config |
| Content | durable facts a human can state once and keep true: identity, conventions, safety rules, the repo's own contribution flow | facts that are only correct *this session*, resolved from config/state a static file can't safely restate (current branch-protection state, a resolved PR-merge profile, a live topology fact) |
| Failure mode when misplaced | a live/derivable fact goes stale in prose (the odsp-web-harness review-count incident) | a universally-true rule is hidden from a visiting agent whose home base is elsewhere (it never gets a plugin-computed session file) |

## Audit heuristic

For any paragraph of agent-facing guidance, ask:

1. **Would this still be correct read by an agent visiting from a different
   home base?** If yes, it belongs in `AGENTS.md` (or stays there). If the
   guidance only makes sense when *this* repo is the operator's home base
   (e.g. "when working from here, resolve the sibling knowledge repo via
   `agent-worktrees state-root`"), it is a harness-configuration fact, not a
   visitor-contract fact.
2. **Is the fact live/derivable from config or state, rather than a stable
   human decision?** A resolved PR-merge profile, a branch-protection review
   count, a currently-bound knowledge repo -- these drift independently of
   the document describing them. Restating them as static `AGENTS.md` prose
   creates exactly the odsp-web-harness incident's failure mode. Prefer a
   dynamic, plugin-computed `.github/instructions` projection (or a pointer
   to the live command that resolves it) over a hardcoded restatement.
3. **Is it a repo-owned policy decision that will not go stale on its own**
   (a review-count *rule*, not the review count itself; a stated safety
   invariant; "never do X in this repo")? That is legitimate `AGENTS.md`
   content and should stay there even if it overlaps in subject with a
   dynamic surface elsewhere -- e.g. odsp-web-harness's own
   never-pre-patch-another-contributor's-PR rule is a durable policy
   decision, not a derivable fact, and correctly lives in its `AGENTS.md`.
4. **Is this a known failure symptom an agent can't phrase-match its way
   into?** Skills are pull-only -- they trigger only once an agent has
   already decided what it wants to *do*, so a category like "my outbound
   claim is blocking finalize" or "a dispatched task is live but
   structurally blocked" is undiscoverable if it exists only behind a skill
   trigger. If yes, it needs an **ambient index row** in `AGENTS.md` or a
   plugin's static `.github/instructions` projection -- naming the exact
   symptom and the command/skill/doc that resolves it -- not just a skill
   whose description happens to mention it. See
   `efforts/active/ambient-guidance-navigability` (the audit that motivated
   this question) and `customizing-copilot:reviewing-customizations`'
   `troubleshooting-index.json` registry + coverage guard, which enforces
   that every category a plugin claims to own is actually backed by such a
   row.

A misplacement in either direction is a defect:

- **Static restatement of a derivable fact** (drift risk) -- move it to a
  `sessionStart`-projected `.github/instructions` file, or replace it with a
  pointer to the live command that resolves it.
- **Harness-only guidance hidden from a universal visitor** (a rule that
  should apply to *any* agent touching the repo, but only appears in a
  plugin's session-scoped projection reachable by one particular harness) --
  move or duplicate the durable statement into `AGENTS.md` so a visitor from
  any other home base still sees it.

Not every subject needs both a static and a dynamic half. Many repos have
only an `AGENTS.md` (no harness ever calls it home) or only harness-side
projections layered on an otherwise-thin `AGENTS.md`. The heuristic above
applies per-paragraph, not per-file.

## Worked precedent

`efforts/active/pr-conduct-guidance-consolidation` applied this heuristic to
PR-conduct content specifically, across three repos:

- **`gim-home/odsp-web-harness`** needed a real trim: its `AGENTS.md` restated
  generic, derivable PR-conduct mechanics (wait/rebase/merge sequence,
  resolved merge-actor) that now come from `agent-worktrees`' dynamic
  session-scoped `PR:` line and `pr.notes` channel. What remained after the
  trim was genuinely repo-unique policy (its never-pre-patch-another's-PR
  rule, its live-validation-PR exception) -- correctly kept.
- **dotfiles** and **copilot-extensions' own `AGENTS.md`** were already
  correctly scoped as visitor contracts for this subject -- audit confirmed
  no change needed, illustrating that "already correct" is a legitimate,
  common audit outcome, not a sign the audit was unnecessary.

That effort's audit was narrowly scoped to PR-conduct; this pattern
generalizes the heuristic and its accompanying audit obligation to *all*
`AGENTS.md` content, tracked by `ThomasMichon/copilot-extensions#2825`.

## Rationale

Splitting by audience and resolvability -- rather than by "is it long" or "did
I write a hook for it" -- keeps `AGENTS.md` legible and durable (a human can
read it once and trust it stays true) while letting harness-specific,
live-resolved facts flow through the delivery mechanism built for exactly
that purpose. It also gives a concrete, repeatable audit question instead of
leaving placement to each contributor's judgment call.

## See Also

- [`session-scoped-dynamic-guidance.md`](session-scoped-dynamic-guidance.md)
  -- the delivery mechanism for the dynamic half of this split.
- `efforts/active/pr-conduct-guidance-consolidation/README.md` -- the
  worked PR-conduct precedent this pattern generalizes.
- `ThomasMichon/copilot-extensions#2825` -- the tracking issue for the
  repo-wide audit this pattern's heuristic is meant to drive.
- `efforts/active/ambient-guidance-navigability/README.md` -- the coverage
  half of this question: a fail-closed registry + guard test proving every
  claimed troubleshooting category actually has an ambient pointer row, not
  just a skill trigger.
