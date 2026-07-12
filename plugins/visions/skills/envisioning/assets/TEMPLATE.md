<!--
  VISION TEMPLATE — copy this file to visions/<path>/README.md and fill it in.
  Delete the guidance comments (like this one) as you go. Keep the section
  headings stable; agents and humans navigate by them. Your repo's addendum may
  rename or add sections — follow the addendum where it differs from this default.

  A vision is the STANDING NORTH STAR for its subject: the durable "what should
  be." It is:
    - INTENT-LEVEL, not a specification. State WHAT should be true; leave HOW to
      the agents/efforts that realize it. Do not pin APIs, schemas, or step-by-step
      mechanics here.
    - PURE SHOULD-BE. Describe only the reality you want/expect. Do NOT list gaps,
      TODOs, deviations, or "known issues" — the delta vs. reality is DERIVED
      (diff → issues → efforts), never stored in the vision.
    - REVISED IN PLACE. Git is the version history; there is no archive. Changing
      a vision REPLACES its old ideas.

  A BRANCH vision (a folder with child visions) stays higher-level and links its
  children. A LEAF vision is concrete. Depth = specificity.
-->

# <Subject> — Vision

- **Subject:** <the system / service / tool / product / domain this envisions>
- **Scope:** <branch (links child visions) | leaf (concrete component)>
- **Status:** Active <!-- Active | Draft | Superseded -->
- **Last revised:** <YYYY-MM-DD>
- **Reality docs:** <link(s) to the architecture/README that record what IS> <!-- optional; navigation only -->
- **Supersedes / superseded by:** <optional; link, only when Status warrants>

## Purpose & Intent

<!-- The north star. What this subject is FOR, why it exists, and what success
     ultimately looks like. One to a few paragraphs. Stable; the reason the rest
     of the vision hangs together. -->

## Concepts & Components

<!-- The high-level mental model: the major concepts and components the subject
     is composed of, and how they relate. This is the map, not the blueprint —
     name the parts and their roles, not their implementation. For a BRANCH
     vision, this is largely a description of (and links to) child visions.

     DECOMPOSE LIBERALLY: keep this README a lean map. When a component here is
     really its own subject, or a §Features/§Behaviors area below grows large,
     push that detail DOWN into a child (leaf) vision and link it from here with
     a one-line summary — don't absorb it inline. A branch README loads whole
     when read, so breadth of linked child visions beats depth in one file;
     agents follow a child link only when their task needs that subtree. Link
     out and back; no orphan visions. -->

## Features

<!-- The capabilities expected of the subject, enumerated. Give each a STABLE
     heading or id so an issue can cite it precisely (e.g. "Vision <subject>
     §Features/<name>"). State WHAT the feature is, not how it's built. -->

### <feature-name>
<!-- What this feature is and why it's expected. -->

## Behaviors

<!-- How the subject should BEHAVE: semantics, invariants, UX expectations,
     failure-mode expectations, performance/latency intent. Again enumerated
     with stable headings; intent-level, not a spec. -->

### <behavior-name>
<!-- The expected behavior, stated as an outcome/property, not a mechanism. -->

## Non-Goals / Boundaries

<!-- What this subject is deliberately NOT, and where its edges are. Keeps the
     vision self-consistent and bounded, and prevents scope creep from the
     agents realizing it. -->

## See Also

<!-- Navigation only (NOT a gap list): parent/child visions, the reality docs
     for this subject, and related visions. -->

- Parent vision: <link or "none">
- Child visions: <links or "none (leaf)">
- Reality docs: <links>

<!--
  OPTIONAL: Provenance / Journal (delete this whole block if unused).

  A vision MAY carry a "## Provenance" (or "## Journal") section: a called-out
  revision history / derivation trail of the vision itself — when ideas were
  conceived or revised, and where the intent was mined from. Since visions are
  revised in place (Git holds the real history), this is a human-facing
  convenience, NOT the authority.

  HARD RULE — it is an easter egg, not a source of deltas. This section is
  EXCLUDED from delta derivation: when diffing the vision against reality to
  carve issues/efforts, ONLY the should-be body counts (Purpose & Intent,
  Concepts & Components, Features, Behaviors, Non-Goals). The Provenance/Journal
  never generates a delta, an issue, or an effort, and it records the VISION's
  own history — never the subject's implementation status (that would be a gap
  call-out, which visions do not carry).
-->

## Provenance

<!-- OPTIONAL, non-authoritative. Dated notes on how this vision came to be /
     was revised. Not part of the should-be spec; not diffed for deltas. -->

- **<YYYY-MM-DD>** — <what was conceived/revised, and the source it was mined from>

