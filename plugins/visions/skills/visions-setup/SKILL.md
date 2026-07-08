---
name: visions-setup
description: >
  Adopt the visions system in a repo -- scaffold the visions/ tree (README index
  + TEMPLATE) and write the repo's addendum that specializes the bindings
  (chiefly organization, plus any section deltas and issue/effort linkage). Use
  this for first-time adoption or to revise a repo's vision conventions, not for
  day-to-day vision work (see envisioning).
  Trigger phrases include:
  - 'adopt visions'
  - 'set up visions'
  - 'visions setup'
  - 'visions addendum'
  - 'configure visions'
  - 'enable visions in this repo'
  - 'visions conventions'
---

# Visions Setup

One-time adoption and convention management for the visions system. For
day-to-day work (create/revise a vision, derive the delta into efforts), see the
`envisioning` skill. The canonical pattern lives in that skill and its
[reference guide](../envisioning/references/visions.md); this skill wires a repo
into it.

## The model: skill governs, repo adds an addendum

The `envisioning` skill is the single source of truth for the vision pattern
(folder-per-vision layout, README schema, lifecycle, the organization seam). An
adopting repo does **not** redefine it — it writes a short **addendum** that
specializes only the bindings. Keep the addendum to deltas; never re-explain the
core pattern.

## Adoption workflow

### 1. Scaffold the `visions/` tree

Create, in the repo root:

```
visions/
├── README.md      # repo vision index + the Local conventions addendum
└── TEMPLATE.md    # copy of the canonical vision template
```

- Copy `assets/TEMPLATE.md` from the `envisioning` skill to
  `visions/TEMPLATE.md`, adjusting it to match the addendum (e.g. add a
  `Principles` section, rename a heading).
- `visions/README.md` is the repo's vision landing page: a one-paragraph
  description, the vision index (a table/tree of the visions that exist), and the
  **Local conventions** addendum (below).
- Unlike efforts there is **no `active/` and no archive** — visions are revised
  in place. The tree under `visions/` follows the repo's chosen organization.

### 2. Write the addendum

Add a `## Local conventions` section to `visions/README.md` (or a dedicated
binding doc that it links, e.g. `docs/visions.md`). Specialize only these:

| Binding | Decide | Default |
|---------|--------|---------|
| **Organization** | how `visions/` is structured; how deep a leaf sits | not mandated — pick one (mirror code layout / by product / by domain) |
| **Section deltas** | renames/additions to the schema | none (use the core) |
| **Issue linkage** | which tracker; how a vision cites reality docs | per the guide |
| **Effort linkage** | how visions feed this repo's efforts (the delta) | per the guide |

**Organization is the primary decision.** The plugin deliberately leaves the
top-level hierarchy to the repo. Common choices:

- **Mirror the code layout** — e.g. `visions/services/<name>/`,
  `visions/tools/<name>/`, plus a top-level whole-product vision. Best when a
  vision maps 1:1 to a buildable unit (makes the vision↔architecture-doc diff
  straightforward).
- **By product / by domain** — when the repo ships several products or is
  organized around domains rather than a services/tools split.

### 3. Point the repo's conventions at visions

So visions are actually used, add to the repo's agent instructions
(`AGENTS.md` / `.github/copilot-instructions.md`) and doc/skill routing:

- Introduce the vision↔effort↔doc↔issue relationship: a **vision** is the
  standing *what-should-be*; **efforts are carved from its delta** vs. reality.
- A knowledge-routing entry: *the standing intent for a system → a **vision**
  under `visions/…`; a stretch of work to realize it → an **effort**.*
- Note the key discipline: visions are **revised in place** (Git history), stay
  **pure should-be** (no gap call-outs), and stay **intent-level** (not
  specifications).
- Add visions to the repo's **sources of new efforts** — the vision→reality delta
  is a backlog generator.

### 4. Seed at least one real vision

An empty `visions/` tree teaches nothing. Author one real vision (per the
`envisioning` skill) — ideally a top-level/whole-product north star, and one
concrete leaf — so the shape is demonstrated, not just described.

### 5. Validate

- `visions/README.md` has a `## Local conventions` addendum and a vision index.
- `visions/TEMPLATE.md` matches the addendum's section set.
- The repo's agent instructions route standing intent to visions and name the
  vision→effort delta.
- At least one real vision exists and reads as **pure should-be** and
  **intent-level** (no gaps, no spec-level mechanics).

### 6. Wire vision-first adherence (optional but recommended)

Adopting the constructs makes visions *exist*; wiring **adherence** makes every
change *reconcile* to them. Follow the
[vision-adherence runbook](references/vision-adherence-runbook.md) to thread the
"vision-first change" discipline down the repo's whole guidance chain
(agent-instructions -> planning -> architecture -> implementation -> quality):
install the always-on three-kinds reconcile principle (vision-closing /
vision-extending / below-altitude, with a proportionality escape hatch), add the
per-node flow-through, and provide a "carve an effort from the vision delta" entry
point. The runbook works two ways — **bootstrap** a greenfield repo from just its
`AGENTS.md`, or **audit/repair** an adopted one (report-and-propose). It stays
**guide, not gate**; merge is the only gate, and enforcement teeth are optional.

## Migrating from scattered "vision"/"goal" prose

If the repo already keeps north-star intent informally (a "Goals" section in a
README, a "design goals" block in a plan doc, a vision paragraph in an
architecture doc):

- **Don't bulk-move.** Leave the prose where it is until you consolidate a
  subject's vision.
- When you author a subject's vision, **absorb** the scattered intent into it and
  replace the prose with a pointer to the vision.
- Keep "what is" (architecture/reality docs) separate from "what should be" (the
  vision) — do not merge them.
