---
name: efforts-setup
description: >
  Adopt the efforts planning system in a repo -- scaffold the efforts/ tree
  (README index + TEMPLATE) and write the repo's addendum that specializes the
  bindings (grouping, participants seam, archive layout, section deltas). Use
  this for first-time adoption or to revise a repo's effort conventions, not for
  day-to-day effort work (see planning-efforts).
  Trigger phrases include:
  - 'adopt efforts'
  - 'set up efforts'
  - 'efforts setup'
  - 'efforts addendum'
  - 'configure efforts'
  - 'enable efforts in this repo'
  - 'efforts conventions'
---

# Efforts Setup

One-time adoption and convention management for the efforts planning system.
For day-to-day work (start/plan/resume/archive an effort), see the
`planning-efforts` skill. The canonical pattern lives in that skill and its
[reference guide](../planning-efforts/references/efforts.md); this skill wires a
repo into it.

## The model: skill governs, repo adds an addendum

The `planning-efforts` skill is the single source of truth for the effort
pattern (folder layout, README schema, lifecycle, journal, participants seam).
An adopting repo does **not** redefine it — it writes a short **addendum** that
specializes only the bindings. Keep the addendum to deltas; never re-explain the
core pattern.

## Adoption workflow

### 1. Scaffold the `efforts/` tree

Create, in the repo root:

```
efforts/
├── README.md      # repo effort index + the Local conventions addendum
├── TEMPLATE.md    # copy of the canonical template
└── active/        # in-flight efforts (add a .gitkeep so the dir is tracked)
```

- Copy `assets/TEMPLATE.md` from the `planning-efforts` skill to
  `efforts/TEMPLATE.md`, adjusting it to match the addendum (e.g. rename
  `Participants` → the repo's label, drop a section the repo won't use).
- `efforts/README.md` is the repo's effort landing page: a one-paragraph
  description, the active-effort index table, and the **Local conventions**
  addendum (below).

### 2. Write the addendum

Add a `## Local conventions` section to `efforts/README.md` (or a dedicated
binding doc that it links, e.g. `docs/efforts.md`). Specialize only these:

| Binding | Decide | Default |
|---------|--------|---------|
| **Grouping** | flat or by-repo | flat: `efforts/active/<slug>/` |
| **Archive layout** | the dated path | `efforts/<YYYY>/MM/DD <slug>/` |
| **Participants seam** | the label + how each is reached | `Participants`, generic |
| **Section deltas** | renames/additions to the schema | none (use the core) |
| **Issue linkage** | which tracker; same-repo-only link rule | per the guide |
| **Effort sources** | where new efforts come from | issues + any plans/roadmaps |

Choose **flat** grouping when the repo is itself the primary unit of work;
choose **by-repo** when the repo coordinates work across many target repos
(then archive as `efforts/<YYYY>/<repo>/MM/DD <slug>/`).

### 3. Bind the participants seam

Name the executor the repo dispatches to, and how the effort reaches it:

| Binding | Participant | Reached via | Executor plugin |
|---------|-------------|-------------|-----------------|
| machine fleet | a workstation/server | SSH alias, agent-bridge | `agent-bridge` |
| CodeSpaces | a GitHub CodeSpace | `agent-codespaces` | `agent-codespaces` |
| containers | a local dev container | `agent-containers` | `agent-containers` |
| branches | a working branch | git | — |

Record the chosen binding (and the section name, e.g. `## Machines`) in the
addendum so `planning-efforts` uses it.

### 4. Point the repo's conventions at efforts

So efforts are actually used, add to the repo's agent instructions
(`AGENTS.md` / `.github/copilot-instructions.md`) and doc/skill routing:

- "New planning work starts as an **effort**, not a fresh design/plan doc."
- A knowledge-routing entry: *plan/status/coordination for a stretch of work →
  the effort under `efforts/active/<slug>/`.*
- If the repo had a legacy `plans/` (or similar), mark it superseded and treat
  existing plans as a backlog of efforts-in-waiting.
- **A persistent cross-repo sequencing rule — install it as an *always-on*
  rule, not an on-demand skill.** This plugin ships only on-demand skills, so a
  standing rule it wants enforced must be **materialized** in the adopting repo's
  own always-on instructions (`AGENTS.md` / `.github/copilot-instructions.md`, or
  a small dedicated rule file the guidance references). Add this rule: *When an
  effort in this **review-gated** repo also drives a change in a related repo you
  push **directly** — no PR, no pre-merge review — land the **effort-update PR
  first**, before the direct push that realizes it; the reviewed plan/intent must
  clear review **ahead of** the unreviewed push. Only **completion markers**
  (journal "done" entries, `Status:` flips, checklist ticks, "shipped in
  `<commit>`") are recorded **after** the cross-repo work — everything stating
  intent or plan belongs in the earlier PR.* A repo that already carries
  equivalent standing guidance need only confirm it covers this ordering
  (the **equivalent-guidance** path). A repo that is *not* review-gated, or that
  never pushes directly to a related repo, can skip it.

### 5. Validate

- `efforts/README.md` has a `## Local conventions` addendum.
- `efforts/TEMPLATE.md` matches the addendum's section set.
- `efforts/active/` exists and is tracked.
- The repo's agent instructions route planning to efforts.
- The repo's **always-on** instructions carry the cross-repo sequencing rule
  (effort-update PR before an unreviewed direct push; only completion markers
  after) — or equivalent standing guidance already covers it. (Skip only when the
  repo is not review-gated or never pushes directly to a related repo.)

## Migrating from a legacy plans directory

If the repo already keeps prescriptive design docs (a `plans/`, `roadmaps/`,
etc.):

- **Don't bulk-move.** Leave them in place as a backlog of efforts-in-waiting.
- When work resumes on one, **promote it**: start an effort pointing at the
  plan doc and its issues, and carry live planning there.
- When fully migrated, replace the plan entry with a pointer to the effort.
- Service/tool-level roadmaps may stay where they are and act as standing
  sources of future efforts.
