---
name: planning-efforts
description: >
  Create, plan, resume, and archive efforts -- comprehensive planning folders
  under efforts/ that represent a stretch of work (premise + plan + validation
  plan + journal + participant coordination). Use when starting or organizing
  multi-step work, turning an issue/plan/roadmap/idea into a tracked effort, or
  resuming/closing one. NOT for filing issues or writing persistent docs.
  Trigger phrases include:
  - 'start an effort'
  - 'new effort'
  - 'plan this out'
  - 'resume the effort'
  - 'continue the effort'
  - 'archive the effort'
  - 'efforts'
  - 'plan a stretch of work'
  - 'turn this into an effort'
  - 'kick off work on'
---

# Planning Efforts

An **effort** is a planning folder under `efforts/` representing a stretch of
work. It is the workspace *around* tracked work — deliberately not named
feature/bug/task (those belong to issue trackers). The effort README is a
**shared contract** read and written by the operator and every agent/participant
involved; it, not the conversation, is the source of truth.

This skill governs the **canonical effort pattern**. Each adopting repo adds a
short **addendum** that specializes only the bindings (grouping, participants,
extra sections). The full reference is
[`references/efforts.md`](references/efforts.md); the README template is
[`assets/TEMPLATE.md`](assets/TEMPLATE.md).

## First: read the repo's addendum

Before acting, find the adopting repo's **efforts addendum** — it overrides the
defaults below for this repo. Look in `efforts/README.md` (a `## Local
conventions` section) or a linked binding doc (e.g. `docs/efforts.md`). The
addendum sets:

- **Grouping** — flat (`efforts/active/<slug>/`) or by-repo
  (`efforts/active/<repo>/<slug>/`).
- **Archive layout** — the dated path pattern.
- **Participants binding** — the concrete label (machines / CodeSpaces /
  containers / branches) and how each is reached.
- **Section deltas** — any renames/additions to the README schema.
- **Repo rules** — which tracker holds issues; where new efforts are sourced.

If no addendum exists, the repo hasn't adopted efforts yet — use the
`efforts-setup` skill first.

## When to use efforts vs. other constructs

| Use… | When the thing is… |
|------|--------------------|
| an **effort** (`efforts/`) | a stretch of work to plan and drive — *what should be* |
| a **doc** | truth about how something works — *what is* |
| an **issue** (tracker) | a discrete tracked item — *to do* |

Efforts and issues go hand in hand: an effort opens an umbrella issue and
breaks into sub-issues. **Only issues in *this* repo may directly link effort
files in this repo.**

## Start an effort

1. **Find the seed.** An effort starts pointing at something that already
   exists — an issue, a plan/roadmap doc, or a stated idea. Identify and cite
   it.
2. **Derive a kebab-case slug** and confirm it with the operator.
3. **Create the folder** at the grouped path (per the addendum): copy
   `assets/TEMPLATE.md` to `efforts/active/<slug>/README.md`.
4. **Fill the header + Guiding Intent + Request** — capture the operator's ask
   **verbatim**; don't paraphrase the premise away.
5. **Catalog participants.** If the work spans machines/CodeSpaces/containers,
   fill the `## Participants` section (binding + how each is reached, per the
   addendum).
6. **Track it.** If the work warrants tracking, open an umbrella issue and
   cross-link it in the header.
7. **Commit** the effort file on the working branch.

## Plan an effort

- Fill **Context** (background + sourced issues/plans), **Plan** (phased,
  checklisted), and **Validation Plan**.
- **Be validation-driven:** every effort carries an implementation plan *and* a
  validation/test plan. An effort may *start* as a pure reproduction — a
  failing validation captured first, fix to follow.
- **Additive or subtractive.** Most efforts *build* something (an additive delta:
  a required capability is missing). An effort can equally be **subtractive** —
  its goal is to **remove** a capability. A subtractive effort must trace to an
  **explicit removal intent** (a stated "no X" / decommission decision), never to
  the *mere absence* of a mention in some source — silence is not a removal order.
  Its Validation Plan proves the capability is **gone** *and* that nothing
  depending on it broke (callers, docs, configs, downstream services).
- File **sub-issues** for discrete tracked work; link them in the header.

## Submit for review, then execute (the review gate)

Between **planning** and **execution** sits a gate: an effort's plan should be
*reviewed* before work starts against it. After the operator's own review rounds
(this is where a rubber-duck pass normally lands), **if the control repo offers
automated PR review**, submit the effort itself as a PR and let it clear that
gate before executing:

1. **Submit the effort PR** — open a PR for the effort folder, with the
   provider's **auto-merge** enabled so an approving review lands it hands-off.
2. **Await approval + merge** — the automated reviewer (and/or the operator)
   approves; auto-merge merges it.
3. **Sync forward** — pull the worktree onto the merged (squashed) default branch
   so execution builds *on top of* the reviewed plan: `agent-worktrees git sync`
   (see the `git-collaboration` skill). Then begin executing the Plan.

**The operator may waive their *own* review — but the agent's review-gate is
non-optional when automated review is available.** Always route the plan through
it before starting the project. Three reasons this matters:

- **Reviewed plan** — execution proceeds from a plan something checked, not a
  first draft.
- **Cross-agent visibility** — a committed, merged effort is visible to *other*
  agents, who can dedupe against it or co-work on it instead of starting parallel
  work.
- **Crash recovery** — if the driving agent dies, the committed effort is a
  recovery point; work resumes from the file.

**Graceful degradation:** if the repo has no automated PR review (or isn't
PR-gated at all), the gate collapses to "commit the plan, then execute" — there
is nothing to wait on. Don't block on a gate the repo doesn't provide.

## Keep the effort current (while executing)

The README is the shared contract — keep it **ahead of the conversation**. But
**every effort edit that lands upstream costs its own PR**, so don't thrash it:

- **Batch updates** to moments that matter — a **major research or direction
  change**, a phase boundary, or when there are **other concurrent commits that
  need pushing anyway**. Routine checkbox ticks can ride along with the next
  substantive change.
- **Annotate as you go:** mark Plan items complete, adjust pending designs,
  re-prioritize on feedback, and journal decisions/blockers/dispatches.
- **By code-complete**, the README reflects the coding-done state and, at most,
  names the *next* effort that carries the work forward (deploy / smoke-test /
  delegation) — it does not try to own that next stretch.
- **Record merged PRs, not in-flight ones.** Listing a PR that the *current*
  commit is itself opening is a catch-22; record a PR only once it has merged.
  Remark open issues the effort spawned or still blocks on.

### Cross-repo & tracking-only efforts

An effort may coordinate work that lands in **another** repo (the effort folder
tracks; the real changes happen elsewhere). Same discipline, with one ordering
rule: **propose before you do.** Reviewers can't meaningfully comment on external
work that's already committed, so:

1. Submit the **proposal** (the not-yet-done plan) to PR first; await review.
2. Make the external changes once the plan clears.
3. Report completion as a **separate delta** ("this is now done"), which reviews
   easily because the only change is status.

## Resume an effort

1. Pull latest so the Journal is current, then **read the README** — Status,
   Plan checklists, Blockers, and the latest Journal entries.
2. Pick up from the last incomplete checklist item / Journal entry. The README
   is self-contained by design — a fresh agent session resumes from the file.
3. For multi-participant work, dispatch via the bound executor (the addendum
   says how) and **journal the dispatch** so the coordination record stays in
   the file.
4. Keep the Journal ahead of the conversation as you work.

## Archive an effort

1. Confirm the effort is done (or abandoned) and the Journal reflects the
   outcome.
2. **Move** `efforts/active/<slug>/` to the dated archive path (per the
   addendum), using the completion date. Preserve git history with `git mv`.
3. Set the header **Status** and write a closing Journal entry.
4. Update the active index in `efforts/README.md`.
5. **Promote durable truth:** if the effort established how something now
   *works*, capture that in the repo's docs. The archived effort is a record of
   *what happened*, not living documentation.

## Anti-patterns

- ❌ Acting before reading the repo's addendum (you'll use the wrong grouping /
  participants).
- ❌ New planning docs outside `efforts/` for fresh planning work → start an
  effort.
- ❌ Paraphrasing the premise instead of capturing the **Request** verbatim.
- ❌ Letting the conversation, not the README, hold effort state.
- ❌ Cross-repo issues linking this repo's effort paths.
- ❌ Naming an effort `feature-*` / `bug-*` / `task-*`.
- ❌ Putting participant-specific mechanics in the core schema — keep them in
  `## Participants` and the addendum, so the pattern stays portable.
- ❌ Starting execution before the plan clears its review gate when automated
  review is available (submit the effort PR → merge → sync forward → *then*
  execute).
- ❌ Recording an in-flight PR the current commit is itself opening (a catch-22) —
  record a PR only once it has merged.
- ❌ Thrashing the effort with a PR per checkbox — batch edits to direction
  changes, phase boundaries, or commits that need pushing anyway.
