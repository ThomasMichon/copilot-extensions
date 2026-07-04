# Visions — Reference Guide

The canonical reference for the **visions system**. This guide ships as an asset
of the `envisioning` skill; the skill is the how-to workflow, this guide is the
*why* and the full schema. An adopting repo specializes the system through a
short **addendum** (see *Adoption & the addendum* below) — it does not fork this
guide.

## What a vision is (and isn't)

A **vision** is a persistent, self-consistent statement of what a system,
service, tool, or product is *ultimately meant to be*: its **purpose**, its
high-level **concepts and components**, and the **features** and **behaviors**
expected of it. It is the standing **north star** — the durable *what-should-be*
against which reality is continuously measured. One vision, one folder, one
README that humans and agents both read and revise.

Three properties define a vision:

- **Intent-level, not a specification.** A vision states *what* should be true
  and leaves **latitude in how** to realize it. It is deliberately *not* a
  specification: no pinned APIs, schemas, or step-by-step mechanics. (See *Vision
  vs. specification* below.)
- **Pure should-be.** A vision describes only the reality it wants or expects. It
  never enumerates gaps, deviations, TODOs, or "known issues" — the delta between
  vision and reality is *derived*, not stored.
- **Persistent, revised in place.** A vision has no archive and no dated copies;
  Git is its version history. Revising a vision *replaces* its old ideas. When a
  vision changes, implementations built to the old vision accrue **code debt**
  until an effort closes the gap.

### The four constructs

| Construct | Question it answers | Tense | Lifecycle | Home |
|-----------|---------------------|-------|-----------|------|
| **Vision** | "What should this *ultimately* be?" | should-be (standing) | revised **in place**, Git-versioned, rarely superseded | `visions/` |
| **Effort** | "What are we doing now, and how's it going?" | should-be (a campaign) | time-boxed, archived when done | `efforts/` |
| **Doc** | "How does it *actually* work?" | is (truth) | tracks reality | docs |
| **Issue** | "What discrete thing needs doing?" | to-do | closed when done | the tracker |

> A doc describing the visions *system* (like this guide) is truthful "what is"
> documentation. The visions themselves are "what should be." Don't conflate the
> two: a vision is the target, a doc is the record of the target being (partly)
> hit.

## Visions and efforts go hand in hand

The load-bearing relationship in the whole system:

> **Efforts are carved from the delta between a vision (should-be) and the
> architecture docs (is).**

The cycle:

1. A **vision** states the expected features and behaviors of its subject.
2. Diffing the vision against the subject's **reality docs** (and code) surfaces
   **misalignments** — expected-but-absent or divergent items.
3. Each misalignment becomes an **issue** that *cites the vision item*.
4. Related issues are grouped into an **effort** that plans, implements, and
   validates the work (see the `planning-efforts` skill).
5. When the work lands and the reality docs are updated, the delta for those
   items disappears — reality has caught up to the vision.

The vision is **never edited to record this cycle**. It changes only when the
*intent itself* changes. Efforts, issues, and docs move; the vision holds still
until the north star itself moves.

## Vision vs. specification (a deliberate boundary)

The concept overlaps heavily with "specification," and keeping them apart is
intentional and load-bearing:

| | **Vision** | **Specification** (not part of this system) |
|---|---|---|
| States | intent + expected features/behaviors | exact, implementation-level requirements |
| Agent latitude | **wide** — chooses how to realize the intent | narrow — conform to the spec |
| Altitude | *what* should be | *how* it must be built |
| When to add | now | only if translation proves too loose |

Keep a vision at the **intent** altitude. The signal that a vision is drifting
into a spec — pinned request/response shapes, exact file layouts, ordered
procedures — is also the signal that a **`specifications` middle layer** may be
wanted: a separate system that sits *between* visions (intent) and reality
(implementation) and removes ambiguity when vision→reality translation causes too
much back-and-forth. That layer is a deliberate **future option**. This system
names it as the escape hatch and does not build it; do not absorb spec-level
rigidity into a vision to compensate for its absence.

## Layout

```
visions/
├── README.md              # the repo's vision index + the local addendum
├── TEMPLATE.md            # the vision README schema (copy when creating one)
└── <organization…>/       # per the repo's addendum
    └── <subject>/README.md
```

- **Unit:** folder-per-vision, each a `README.md`. A **branch** folder's README
  is a higher-level (abstract) vision that links its children; a **leaf** README
  is a concrete component vision. **Depth = specificity.**
- **Organization (a binding — set by the addendum):** the plugin does **not**
  mandate a top-level hierarchy. A repo may mirror its code layout
  (`visions/services/<name>/`), organize by product, or by domain. Whatever the
  repo's owners find legible — the addendum declares it.
- **No archive.** Unlike efforts, visions are never moved to a dated archive.
  They are revised in place; superseded visions stay put with `Status:
  Superseded`.

## The vision README — schema

`TEMPLATE.md` is the canonical template. The README is a shared artifact: humans
and agents both read and revise it, and it — not the conversation — is the
source of truth for the subject's intent.

| Section | Purpose |
|---------|---------|
| **Header** | subject, scope (branch/leaf), status, last-revised, reality-doc links |
| **Purpose & Intent** | the north star — what the subject is for, why it exists, what success looks like |
| **Concepts & Components** | the high-level mental model — the parts and their roles (for a branch vision, largely links to child visions) |
| **Features** | enumerated capabilities expected, each with a stable heading/id so issues can cite it |
| **Behaviors** | how the subject should behave — semantics, invariants, UX, failure modes, performance intent — stated as outcomes, not mechanisms |
| **Non-Goals / Boundaries** | what the subject deliberately is *not*; its edges |
| **See Also** | navigation only — parent/child visions and reality docs (never a gap list) |

An addendum may rename sections or add one (e.g. a `Principles` section). The
Header, Purpose & Intent, Features, and Behaviors are the irreducible core.

### Enumerate for citation

Features and behaviors should be **enumerable and stable** so the delta mechanic
works: give each a stable heading or id, so an issue can say "Vision `<subject>`
§Features/`<name>` is unrealized" and a reader can find exactly that item.

## Lifecycle

1. **Create** — identify the subject and its reality docs; flesh out the intent
   *with the operator* (visions are collaborative). Decide **new vision vs.
   revise an existing one**. Copy `TEMPLATE.md` to `visions/<path>/README.md`,
   fill the schema at intent level, set `Last revised`.
2. **Revise in place** — edit the README directly; **replace** superseded ideas
   (Git holds the history); bump `Last revised`. Keep it pure should-be and
   intent-level. A revision may legitimately widen the delta against reality.
3. **Derive** — diff the vision against reality docs/code; file issues for each
   misalignment (citing vision items); carve efforts from grouped issues. The
   vision is not touched by this step.
4. **Supersede** (rare) — if the subject is retired or wholly re-conceived, set
   `Status: Superseded` and point at the replacement. This is not archiving; the
   file stays.

There is no "archive" step and no "done" state — a vision persists as long as its
subject is intended to exist.

## Adoption & the addendum

The `envisioning` skill governs the canonical pattern above. An adopting repo
writes a short **addendum** that specializes only the bindings:

- **Organization** — how `visions/` is structured and how deep a leaf sits.
- **Section additions/renames** — any deltas to the README schema.
- **Repo-local rules** — which tracker holds issues, how visions point at reality
  docs, and how visions feed the repo's efforts.

The addendum lives **in the adopting repo** — typically a `## Local conventions`
section of its `visions/README.md`, or a dedicated binding doc (e.g.
`docs/visions.md`) that `visions/README.md` links to. The `visions-setup` skill
scaffolds the `visions/` tree and the addendum.

> Keep the addendum small. If you find yourself re-explaining the core pattern,
> it belongs in this guide, not the addendum. The addendum is deltas only.

## Relationship to executor and planning plugins

- The **`efforts`** plugin consumes the delta a vision exposes: efforts are how
  the vision→reality gap is actually closed. Visions do not plan or execute work
  — they only state the target.
- A future **`specifications`** plugin (if ever built) would sit between visions
  and efforts, translating intent into implementation-level requirements when the
  latitude a vision grants proves too wide. Until then, agents translate vision →
  reality directly, with efforts as the vehicle.
