---
name: envisioning
description: >
  Author and evolve visions -- persistent, in-place-revised north-star documents
  that state what a system, service, tool, or product is ultimately meant to be
  (purpose + concepts + expected features + expected behaviors). A vision is
  intent-level, not a rigid specification; efforts are carved from the delta
  between a vision and reality. Use this skill to create a vision, revise one in
  place, or derive the vision-vs-reality delta into issues and efforts. For
  first-time adoption in a repo, see visions-setup.
  Trigger phrases include:
  - 'vision'
  - 'north star'
  - 'what should this be'
  - 'grand vision'
  - 'product vision'
  - 'envision'
  - 'revise the vision'
  - 'update the vision'
  - 'vision vs reality'
  - 'carve an effort from the vision'
  - 'what features should X have'
---

# Envisioning

A **vision** is a persistent, self-consistent statement of what a system,
service, tool, or product is *ultimately meant to be*: its purpose, its
high-level concepts and components, and the **features** and **behaviors**
expected of it. It is the standing **north star** — the durable *what-should-be*
against which reality is continuously measured. One vision, one folder, one
README that humans and agents both read and revise.

This skill governs the **canonical vision pattern**. Each adopting repo adds a
short **addendum** that specializes only the bindings (chiefly *organization*).
The full reference is [`references/visions.md`](references/visions.md); the
README template is [`assets/TEMPLATE.md`](assets/TEMPLATE.md).

## First: read the repo's addendum

Before acting, find the adopting repo's **visions addendum** — it overrides the
defaults below for this repo. Look in `visions/README.md` (a `## Local
conventions` section) or a linked binding doc (e.g. `docs/visions.md`). The
addendum sets:

- **Organization** — how `visions/` is structured (mirror the code layout, by
  product, by domain) and how deep a leaf vision sits.
- **Section deltas** — any renames/additions to the vision README schema.
- **Linkage** — which tracker holds issues; how a vision points at its reality
  docs.

If no addendum exists, the repo hasn't adopted visions yet — use the
`visions-setup` skill first.

## What a vision is (and is not)

- A vision is **intent-level.** It states *what* should be true and leaves agents
  **latitude in how** to realize it. It is **not** a specification — do not pin
  APIs, schemas, or step-by-step mechanics into it. (If translation to reality
  proves too loose, the remedy is a separate `specifications` middle layer, not a
  harder vision — see the reference guide.)
- A vision is **pure should-be.** It describes only the reality it wants or
  expects. It does **not** enumerate gaps, deviations, TODOs, or "known issues" —
  the delta is *derived*, never stored in the vision.
- A vision is **persistent and revised in place.** There is no archive; Git is
  the version history. Changing a vision *replaces* its old ideas.

| Use… | When the thing is… |
|------|--------------------|
| a **vision** (`visions/`) | the standing intent for a system — *what it should ultimately be* |
| an **effort** (`efforts/`) | a stretch of work to close part of the vision→reality delta |
| a **doc** | truth about how something works today — *what is* |
| an **issue** (tracker) | a discrete tracked misalignment — *to do* |

## Create a vision

1. **Find the subject.** A vision is *about* something — a system, service, tool,
   product, or a whole domain. Name it and, if it exists, its reality docs.
2. **Flesh out the concept with the operator.** Visions are collaborative:
   discuss the intent, the concepts, and the expected features/behaviors before
   writing. Decide **new vision vs. revise an existing one** — prefer revising a
   parent/sibling vision over spawning a redundant one.
3. **Place it per the addendum's organization.** Copy `assets/TEMPLATE.md` to
   `visions/<path>/README.md`. A **branch** folder's README is a higher-level
   (abstract) vision that links its children; a **leaf** README is a concrete
   component vision. Depth = specificity.
4. **Fill the schema** — Purpose & Intent, Concepts & Components, Features,
   Behaviors, Non-Goals / Boundaries, See Also. Keep it intent-level and pure
   should-be. Give each feature/behavior a **stable heading or id** so issues can
   cite it precisely.
5. **Commit** on the working branch. Set `Last revised` to today.

## Revise a vision (in place)

Visions change **in place** — this is the core difference from efforts:

- Edit the README directly; **replace** superseded ideas rather than annotating
  them as "old." Git holds the history.
- Bump the header **Last revised** date.
- Keep it pure should-be and intent-level — resist smuggling in gap call-outs or
  spec-level mechanics.
- A revision may *widen* the delta against reality (new expectations create new
  code debt). That is expected and healthy — the debt is closed by efforts, not
  by softening the vision.
- **Supersede** (rare): if a vision's subject is retired or wholly re-conceived,
  set Status `Superseded` (and point at the replacement). Superseding is not
  archiving — the file stays; Git carries the prior life.

## Derive the delta → issues → efforts

This is how a vision *does work*: it is diffed against reality.

1. **Diff.** Compare the vision's expected features/behaviors (its lowest,
   most-specific level) against the subject's **architecture/reality docs** (their
   highest level) — and the code where docs are thin.
2. **Name misalignments.** Each expected-but-absent (or divergent) feature/behavior
   is a **delta**: file it as an issue that *cites the vision item*.
3. **Carve efforts.** Group related deltas into an **effort** (see the
   `planning-efforts` skill). The effort plans and validates the work; the issues
   track it; the vision stays untouched (it already says what should be).
4. **Close the loop.** When the work lands and the reality docs are updated to
   match, the delta for those items is gone — reality has caught up to the vision.

The vision is never edited to *record* this cycle. It is edited only when the
**intent itself** changes.

## Keep visions and reality legible

- A vision's **See Also** points at the reality docs for its subject (navigation,
  not a gap list). When those docs move, fix the link.
- When you *revise* a vision, consider whether an effort should be opened for the
  newly-created delta — but don't block the revision on it. The vision leads;
  efforts follow.

## Anti-patterns

- ❌ Acting before reading the repo's addendum (you'll use the wrong organization).
- ❌ Writing a **specification** and calling it a vision — pinned APIs/schemas/
  step-by-step mechanics belong to a (future) `specifications` layer, not here.
- ❌ Listing gaps, TODOs, deviations, or "known issues" inside a vision — the
  delta is derived, never stored.
- ❌ **Archiving** a vision or spawning a new dated copy on change — visions are
  revised **in place**; Git is the history.
- ❌ Annotating superseded ideas as "old"/"deprecated" instead of replacing them.
- ❌ Duplicating a parent/sibling vision instead of revising it.
- ❌ Editing a vision to record delta-closure progress — that state lives in the
  effort and the issues, not the vision.
- ❌ Letting a vision drift to match a half-built reality (a vision is the target,
  not a mirror of current code).
