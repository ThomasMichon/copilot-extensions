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
- File **sub-issues** for discrete tracked work; link them in the header.

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
