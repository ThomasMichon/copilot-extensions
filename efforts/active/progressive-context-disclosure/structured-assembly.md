# Progressive Context Disclosure - Structured Assembly

[Effort](README.md) ·
[Issue #1612](https://github.com/ThomasMichon/copilot-extensions/issues/1612)

## Question

Can independent plugin contributors describe enough semantic intent for
`context-injection` to render a coherent hierarchical document without
rewriting their content, weakening attribution, or coupling plugins to one
another?

## Alternatives

### A. Flat fragments with stronger conventions

Keep the existing contributor output unchanged. Standardize an owner marker,
critical kernel, applicability cue, and guide-reference footer inside each
fragment.

This has the lowest migration and authority complexity but can still repeat
headings and produce a poor reading order across arbitrary plugin sets.

### B. Flat fragments plus generated index

Keep owner fragments intact and generate a small table of contents/reference
index from declaration metadata. The authority does not place fragment bodies
into semantic sections.

This improves navigation while retaining the current rendering model.

### C. Semantic zones

Contributors declare one bounded role for each fragment, such as:

- critical constraint;
- repository/environment orientation;
- ownership/routing;
- readiness;
- command discovery; or
- deferred reference.

The authority renders zones in a stable order, then preserves exact
owner-delimited text inside each zone.

### D. Hierarchical structured fragments

Contributors declare bounded sections, criticality, applicability, stable ids,
and guide references. The authority constructs a document tree and renders it
deterministically.

This offers the strongest coherence and budgeting but creates the largest
schema, migration, and review surface.

## Invariants

Every alternative must preserve:

- exact source-qualified contributor ownership;
- deterministic bytes for one active set and generation;
- no content paraphrasing or semantic conflict resolution by the authority;
- stable ids independent of display headings;
- bounded critical and reference bytes;
- guide containment and explicit resolution ownership;
- no guide execution or eager loading during aggregation;
- graceful handling of older flat contributors during rollout;
- cross-platform rendering parity; and
- fail-closed behavior for ambiguous ownership, invalid structure, and path
  escape.

## Candidate document shape

The prototype may evaluate a shape like:

```text
# Session guidance

## Critical constraints
### <owner>
<exact contributor text>

## Environment and routing
### <owner>
<exact contributor text>

## Capability grounding
### <owner>
<exact contributor text>

## Exact command discovery
### <owner>
<exact contributor text>

## On-demand references
- <owner> — <when/applicability> — <contained locator>
```

The headings are renderer-owned organization. Everything beneath an owner
boundary remains contributor-owned.

## Open design questions

- Is one semantic role per contributor sufficient, or do contributors need
  several independently budgeted fragments?
- Should criticality be categorical, ordered, or inferred only from a declared
  rendering zone?
- Can applicability remain prose, or does review need a constrained cue shape?
- How does a guide locator identify whether its base is the payload,
  repository, or session-state folder?
- Should the authority render real links, faux-links, comments, or a neutral
  structured locator after the experiment chooses behavior?
- How are duplicate stable ids distinguished from merely repeated headings?
- Does the authority reject incompatible structured contributors, fall back to
  their flat rendering, or disable structured assembly for the whole stack?
- Can zone budgets avoid starving critical fragments without introducing a
  central priority policy that overrides owners?
- How should structured assembly compose with the resume witness in #1508?

## Prototype and decision

Build all alternatives over the same synthetic corpus. Compare deterministic
rendering, byte overhead, guide selection, conflict legibility, migration
complexity, and reviewer precision.

Adopt the least expressive model that materially improves agent navigation.
If flat fragments plus an index perform as well as hierarchy, do not add a
larger schema. If hierarchy wins, version the contributor and engine contracts
and retain a backward-compatible flat path through partial rollout.
