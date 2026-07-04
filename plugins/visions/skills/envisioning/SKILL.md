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

## Adding, changing, and removing (positive vs. negative intent)

A vision is **open-world**: authoritative about what it *states*, and
silent-with-latitude about everything below its altitude. **Absence is not
prohibition.** An unstated detail is *unspecified* — an agent realizing the
vision fills it with sensible defaults (convention + the repo's framework
standards), not with nothing. ("A clip library the user can replay" conventionally
implies a play control; you don't have to enumerate the button.)

This gives three distinct edits, each mapping to a different delta:

| To… | Edit the vision by… | Delta vs. reality | Effect |
|------|---------------------|-------------------|--------|
| **Add / require** a capability | **stating** it (a positive Feature/Behavior) | vs. its absence | an **additive** effort builds it |
| **Stop requiring** a capability | **deleting** its entry | *none* — absence ≠ prohibition | reality may keep or drop it, at latitude; nothing is forced |
| **Force removal** of a capability | **stating a negative** (a Non-Goal / "no X") | vs. its presence | a **subtractive** (removal) effort tears it out |

The load-bearing rule: **deleting a vision entry withdraws a *requirement*; it
does not command destruction.** To make reality *lose* a capability you must
**state the boundary** — a Non-Goal, or an explicit "does not / must not."

- **Overriding an obvious default → say it.** If the conventional realization of
  a stated feature would add something you *don't* want, state its absence as a
  Behavior or Non-Goal. Omitting the mention yields the default; only a stated
  negative removes it.
- **Why the asymmetry is deliberate (a safety property):** trimming a sentence
  must never cause an agent — or an autonomous fleet acting on a committed vision
  diff — to rip out a working capability. Destructive change stays a deliberate,
  *stated* act, never a side effect of omission.

## Derive the delta → issues → efforts

This is how a vision *does work*: it is diffed against reality.

1. **Diff.** Compare the vision's expected features/behaviors (its lowest,
   most-specific level) against the subject's **architecture/reality docs** (their
   highest level) — and the code where docs are thin. **Diff only the should-be
   body** — Purpose & Intent, Concepts & Components, Features, Behaviors,
   Non-Goals. An optional **Provenance/Journal** section (see below) is **never**
   part of the diff.
2. **Name misalignments — additive or subtractive.** Each mismatch is a **delta**,
   filed as an issue that *cites the vision item*:
   - a stated feature/behavior that is **absent or divergent** → an **additive**
     delta (build / fix it);
   - a stated **negative** (Non-Goal / "no X") that reality **violates** (X
     exists) → a **subtractive** delta (remove it).
   A capability that is *merely unmentioned* is **not** a delta — absence is
   latitude, not a removal order.
3. **Carve efforts.** Group related deltas into an **effort** (see the
   `planning-efforts` skill) — additive and subtractive deltas carve additive and
   removal efforts respectively. The effort plans and validates the work; the
   issues track it; the vision stays untouched (it already says what should be).
4. **Close the loop.** When the work lands and the reality docs are updated to
   match, the delta for those items is gone — reality has caught up to the vision.

The vision is never edited to *record* this cycle. It is edited only when the
**intent itself** changes.

## Optional: a Provenance / Journal section (an easter egg, not a delta source)

A vision **may** carry an optional `## Provenance` (or `## Journal`) section: a
called-out revision history / derivation trail of the vision *itself* — when
ideas were conceived or revised, and where the intent was mined from. It's a
nice, human-facing convenience (visions are revised in place, so Git holds the
*real* history; this is color and traceability on top).

Two rules keep it from corrupting the model:

- **It is excluded from delta derivation.** When you diff a vision against
  reality to carve issues/efforts, **ignore this section entirely** — only the
  should-be body counts. The Provenance/Journal must never, by itself, generate
  a delta, an issue, or an effort. It has *no bearing* on what work gets carved.
- **It records the vision's history, not the subject's status.** Dated notes are
  about how the *vision* came to be or changed — never "feature X isn't built
  yet" (that's a gap call-out, which visions do not carry).

Keep it optional and lean; if it starts reading like a status tracker or a
backlog, it has drifted out of its lane.

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
- ❌ Treating the optional **Provenance/Journal** as authoritative — never carve
  a delta, issue, or effort from it, and never let it hold the subject's
  implementation status (it records the *vision's* history only).
- ❌ Deleting a vision entry expecting the feature to be **torn out of reality** —
  deletion only withdraws a *requirement*; state a **Non-Goal** to force removal.
- ❌ Carving a removal delta from a *merely unmentioned* capability — absence is
  latitude; only a stated **negative** justifies removing something.
- ❌ Letting a vision drift to match a half-built reality (a vision is the target,
  not a mirror of current code).
