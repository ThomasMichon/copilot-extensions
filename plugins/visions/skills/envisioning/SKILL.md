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

- A vision is **intent-level** — a boundary, not a vow of vagueness. It states
  *what* should be true and leaves agents **latitude in how** to realize it. It
  **may** name *architectural intent* (the shape a design must take — e.g. "a
  browse tier that stays responsive independent of a separate heavy-work engine"),
  *correctness/resilience intent* (guarantees — "durable before it is shown;
  the catalog is rebuildable from the source of truth"), and *interaction intent*
  (promises to the user — "no dead controls"). It is **not** a specification: do
  not pin APIs, schemas, ports, file layouts, model names, or step-by-step
  mechanics — name the shape / guarantee / promise and the *why*, never the
  wiring. (If vision→reality translation proves too loose for faithful
  regeneration, the remedy is a separate `specifications` middle layer, not a
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

## Cross-repo visions — where the vision lives

A vision is *about* a subject, and that subject often lives in **another** repo.
Three placement models — pick per the subject; none is mandatory (see
[`references/visions.md`](references/visions.md) § Cross-repo placement for the
fuller rationale).

- **Local (default).** The vision lives in *this* repo. Use when the subject is
  owned here, or the target repo hasn't adopted `visions/`.
- **Author directly in the target repo.** If the **target repo has adopted
  `visions/`** and the subject is genuinely *its own* — a tool, service, or
  product that lives there — you may author the vision **there**, through *that
  repo's* flow and addendum. A vision about a tool can belong with the tool. Work
  it as a **good citizen** of the target repo: its conventions win over this
  repo's.
- **Hybrid (split public/private).** Keep a **generalized** vision in a
  **public / portable** repo *and* a **fuller, downstream-private** vision in the
  control repo that **links to it**. The **public vision is canonical** — it is
  the north star agents cite and the one the vision→reality delta is derived
  against; the private vision *elaborates* it with facility-specific intent
  (private subjects, downstream constraints) and links back. Keep the public
  artifact **generic** per the repo's public-artifact rule.

Because visions are **revised in place** (no archive), a hybrid's two files each
evolve in place. Keep the private vision's link to the public canonical one live,
and never let the private elaboration silently contradict the public intent — when
the *shared* intent changes, revise the **public** vision (the canonical source),
then re-elaborate the private one.

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

### Extend before you regenerate (the stability bias)

An additive delta says a capability is *absent or divergent* — it does **not**
say "rebuild the subject from scratch." The default response to a delta is to
**extend or compose what already exists** to reach the vision, not to regenerate
the subject from whole cloth:

- **A blank rewrite is a trap.** From-scratch always *looks* like a cleaner match
  to a vision than a messy extension of the real thing. Resist it: a working
  system encodes edge cases and fixes hammered out over prior rounds, and a
  regeneration re-surfaces a *fresh* crop of those same classes of bug — distinct
  from, but no cheaper than, the ones already solved. Extension conserves that
  hard-won stability.
- **Hunt for prior art first.** Before proposing to *build*, search the repo and
  its tracker/history for existing components, efforts, and issues that already
  deliver part of the intent, and decide where each fits. The default outcome of a
  delta is **"extend/compose these existing building blocks,"** not "write it
  fresh."
- **Dedupe at two levels.** Don't open a **redundant issue/effort** (dedupe
  against existing trackers before carving), and don't carve an effort that would
  **produce a redundant thing** (extend or consume an existing capability instead
  of building a parallel copy alongside it).
- **"To a point" — when to replace instead.** Extension is the default, not
  dogma. Replacement is the right call when the existing thing **violates the
  vision's Non-Goals** (not merely lacks a feature), its **boundaries no longer
  fit** the intent, or **accrued complexity makes extending it the riskier path**.
  That judgment belongs to the operator and the review gate — it is deliberately
  *not* mechanical, which matters most for an autonomous fleet acting on a
  committed vision diff.

## Validate a vision (the generativity check)

A vision is only as good as what it would *generate*. To check one, run it
**backwards**: have an agent **re-derive** the subject's design from the vision
**alone**, then diff that blind proposal against reality. Where the derivation
lands close, the vision carries real generative weight; where it doesn't, the gap
is diagnostic — and tells you *which* kind of gap.

**Isolate by construction, not on the honor system.** A "design from the vision
alone" done by an agent that can *see* the reality is contaminated — it silently
leans on what it reads and reports an inflated match. The honest form gives the
deriving agent **only** the vision, with its reality pointers stripped (See Also,
reality-doc links, any Provenance), and **denies it repo/search/web access**; then
audit its tool log to confirm isolation held. A separate, reality-aware **judge**
builds a checklist of the *real* design **before** reading the proposal
(pre-registration guards against leniency) and scores each item against quoted
evidence.

**Read the delta in three bins** — this is the load-bearing judgment:

- **Vision-ahead** — the vision states intent reality hasn't built yet. This is
  the **healthy** delta (the whole point of a north star); it feeds **efforts**,
  not vision edits.
- **Genuine blind spot** — reality embodies *intent* the vision failed to state (a
  structural shape, a correctness/resilience guarantee, an interaction promise).
  **Fold it back** into the vision as pure should-be — at the detail ceiling (the
  shape/guarantee/promise, never the mechanism).
- **Spec-level detail** — ports, schemas, exact APIs, file layouts, model names. A
  vision **should not** carry these; they belong to reality docs or a
  `specifications` layer. A "miss" here is **not** a vision defect — do not inflate
  the vision to absorb it.

**Attribute the matches.** Separate what the *vision* drove from what any agent
would reach by applying the repo's generic framework standards (a default service
shape, a default store). The vision's real generativity is the vision-driven
share, not the raw coverage number.

A vision that regenerates the *conceptual skeleton* but not an implementable
design is behaving **correctly** — it is intent, not spec. That gap is the
strongest signal that binding detail wants a **`specifications` layer**, not a
harder vision. (Adopting repos may ship tooling and bindings for this check — a
sanitizer, isolated-derive/judge prompt templates, a scorecard format; see the
repo's addendum.)

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

## Keep vision READMEs lean (decompose into child visions)

A vision README is loaded whole when an agent reads it, so a sprawling one taxes
every session that opens it. Prefer **breadth of linked visions over depth in one
file** — the vision folder tree *is* the decomposition mechanism:

- **A branch README is a map, not an encyclopedia.** When a subject's Concepts &
  Components, Features, or Behaviors grow large, push the deep part **down into a
  child (leaf) vision** and link it from the parent's Concepts & Components with a
  one-line summary. The parent states the subject's shape and points at children;
  the children hold the concrete intent. Depth = specificity.
- **Why: it cuts upfront context.** An agent reads the branch vision and follows
  a child link **only when its task needs that subtree** — decomposition keeps the
  always-read layer small, at the cost of an extra read when the detail is wanted.
- **Link out *and* link back.** The parent lists each child; every child's See
  Also points back at its parent. No orphan visions.
- **Split when a single §Features or §Behaviors section dominates the file**, or
  when a component under Concepts & Components is really its own subject — that is
  the seam. Don't wait for the README to become unwieldy.

This is the same *decompose-liberally* instinct that governs docs and efforts,
expressed through the branch/leaf tree instead of sub-docs.

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
- ❌ **Regenerating a subject from scratch** to satisfy an additive delta when you
  could **extend** what already exists — regeneration re-exposes bugs the prior
  version already paid down; hunt for prior art and reuse it, and replace only when
  the existing thing violates a Non-Goal, no longer fits, or is too complex to grow.
- ❌ Letting a vision drift to match a half-built reality (a vision is the target,
  not a mirror of current code).
- ❌ In a **hybrid split**, letting the private, fuller vision become a second
  source of truth — the **public generalized** vision is canonical and the delta
  is derived against it; the private one elaborates and links back, never contradicts.
- ❌ Keeping a vision **here** for a subject genuinely owned by a target repo that
  has adopted `visions/` — author it there (as a good citizen) or use the hybrid
  split, rather than reflexively keeping it local.
- ❌ Trusting a **repo-aware "design from the vision alone"** as a generativity
  measure — an agent that can see reality is contaminated and over-scores. Isolate
  the derivation (strip the vision's reality pointers, deny search/repo/web) and
  audit its tool log.
- ❌ Folding **spec-level detail** (APIs/schemas/ports/model names/mechanics) into
  a vision to "raise coverage" against reality — that turns the vision into a spec;
  fold back only should-be *intent*, and route binding detail to a `specifications`
  layer.
- ❌ Treating a **vision-ahead** item (reality simply hasn't caught up) as a blind
  spot — it is the healthy delta; feed it to efforts, not vision edits.
- ❌ Letting a single vision README balloon with every feature/behavior inline
  when a component deserves its **own child (leaf) vision** — decompose into the
  branch/leaf tree and link, so agents load only the subtree their task needs.
