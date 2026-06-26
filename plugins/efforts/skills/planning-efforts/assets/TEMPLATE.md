<!--
  EFFORT TEMPLATE — copy this file to efforts/active/<slug>/README.md and fill it in.
  Delete the guidance comments (like this one) as you go. Keep the section
  headings stable; agents and humans navigate by them. Your repo's addendum
  may rename or add sections (e.g. Participants -> Machines, + Validation Plan)
  — follow the addendum where it differs from this default.

  An effort README is a SHARED CONTRACT: the host agent, any dispatched agents,
  and you all read and write THIS file. Keep it current — it is the source of
  truth for the effort, not the conversation history.
-->

# <Effort Name>

- **Slug:** `<kebab-case-slug>`
- **Repo:** <repo this effort lives in / coordinates>
- **Branch(es):** `<working-branch>`
- **Created:** <YYYY-MM-DD>
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Umbrella issue:** #NN <!-- optional; link to your tracker -->
- **Sub-issues:** #NN · #NN <!-- optional -->

## Guiding Intent

<!-- The north star. One or two paragraphs: what this effort is ultimately
     trying to achieve and why it matters. Stable across the effort's life. -->

## Participants

<!-- Who or what does the dispatched work — the executor seam. Your repo's
     addendum names the concrete binding (machines / CodeSpaces / containers /
     branches) and how each is reached. Omit for a single, local effort. -->

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| <name> | <what it does here> | <ssh alias / codespace / container / branch> |

## Coordination

<!-- OPTIONAL — multi-agent efforts only. Omit for solo/local efforts. When more
     than one agent collaborates on this effort, record the branch topology and
     who owns what, so a fresh agent (or a recovering host) can pick up the
     coordination from the file alone. This is the "branches" participant binding
     — its mechanics live in the agent-worktrees `git-collaboration` skill (the
     turn-key `git sync` / `feature-branch` / `merge-to-feature` helpers); keep
     only the plan here.

     Two topologies:
       - Shared feature branch: delegates ff-push slices to one branch; the HOST
         owns the PR(s). Use when slices are interdependent.
       - Independent worktrees + per-slice PRs: only when each PR leaves the
         default branch green on its own; the host watches remote PR state.
-->

- **Topology:** <shared feature branch `feature/<name>` | independent per-slice PRs>
- **Host (owns PRs):** <agent/machine>
- **Delegates:** <agent/machine — assigned section(s)>
- **Handoff:** delegates ff-merge to the shared branch (`git merge-to-feature`);
  host syncs forward (`git feature-branch <name> --sync`). Only the host opens PRs.

## Context

<!-- Background a fresh agent needs: which issue/plan/idea this effort started
     from, prior decisions, external references, API shapes, policy semantics.
     Link the sources this effort absorbs. -->

## Request

<!-- The operator's ask, VERBATIM where possible. Don't paraphrase the premise
     away — capture it as stated, including any links or code references. -->

## Plan

<!-- The implementation plan, in phases. Use checklists so progress is visible
     and a fresh agent can resume. Exploration items are fine ("- [ ] Locate X").
-->

### Phase 1 — <name>
- [ ] ...

### Phase 2 — <name>
- [ ] ...

## Validation Plan

<!-- How we'll KNOW it works. Efforts are validation-driven: pair the plan with
     a test/validation plan, and feel free to START an effort as a pure
     reproduction (a failing validation with the fix to follow). Name concrete
     checks, target surfaces, and who/what runs them. -->

- [ ] ...

## Proposal

<!-- Optional. Detailed findings from research/exploration (file paths, line
     numbers, design decisions) once the plan firms up. -->

_Pending._

## Journal

<!-- Dated, append-only running log. The record of what actually happened:
     decisions, blockers, dispatches to participants, milestones, and the
     closing archive entry. Pick newest-first or oldest-first and stay
     consistent. -->

### <YYYY-MM-DD> — Kickoff
- Effort created, premise captured.
