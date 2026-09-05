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
repo into it. The plugin itself has no runtime setup; this skill only scaffolds
repo-local effort state, adoption policy, and the minimal always-on projection.
The payload also ships cross-platform policy producers, but issue #1234
currently prevents Copilot CLI from deterministically joining multiple plugins'
`sessionStart` context. Until that runtime defect is fixed, the marked fallback
below is the authoritative ambient delivery and the producers remain
direct-testable but unregistered.

## The model: skill governs, repo adds an addendum

The `planning-efforts` skill is the single source of truth for the effort
pattern (folder layout, README schema, lifecycle, journal, participants seam).
An adopting repo does **not** redefine it — it writes a short **addendum** that
specializes only the bindings. Keep the addendum to deltas; never re-explain the
core pattern.

## Adoption workflow

### 1. Declare repository adoption

Create this exact committed file in the repository:

```text
.copilot-extensions/efforts/config.json
```

```json
{
  "version": 1,
  "enforcement": "required"
}
```

The version 1 semantic is exactly
`{"version": 1, "enforcement": "required"}`:

- a valid file declares that the repository supports efforts and requires the
  effort lifecycle for substantial multi-step work;
- an absent or malformed file means the repository has not adopted efforts;
- unknown keys, alternate types, other versions, and other enforcement values
  are invalid rather than silently treated as advisory;
- the config path and every parent below the repository root must be regular,
  contained, and free of symlink/reparse indirection.

Directory presence is not adoption. An `efforts/` tree may be historical,
vendored, or incomplete; agents and cross-repository callers rely on the exact
config above.

### 2. Scaffold the `efforts/` tree

Create, in the repo root:

```
efforts/
├── README.md      # repo effort index + the Local conventions addendum
├── TEMPLATE.md    # copy of the canonical template
└── active/        # in-flight efforts (add a .gitkeep so the dir is tracked)
```

- Copy `../planning-efforts/assets/TEMPLATE.md` to `efforts/TEMPLATE.md`,
  adjusting it to match the addendum (e.g. rename `Participants` → the repo's
  label, or omit optional sections the repo won't use).
- `efforts/README.md` is the repo's effort landing page: a one-paragraph
  description, the active-effort index table, and the **Local conventions**
  addendum (below).

### 3. Write the addendum

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
| **Cross-repo placement** | this repo's stance when an effort touches another repo — host efforts for its own subjects, and prefer local / tracking-only / build-in-target / hybrid | all models available (see `planning-efforts` § Cross-repo efforts) |

Choose **flat** grouping when the repo is itself the primary unit of work;
choose **by-repo** when the repo coordinates work across many target repos
(then archive as `efforts/<YYYY>/<repo>/MM/DD <slug>/`).

### 4. Bind the participants seam

Name the executor the repo dispatches to, and how the effort reaches it:

| Binding | Participant | Reached via | Executor plugin |
|---------|-------------|-------------|-----------------|
| machine fleet | a workstation/server | SSH alias, agent-bridge | `agent-bridge` |
| CodeSpaces | a GitHub CodeSpace | `agent-codespaces` | `agent-codespaces` |
| containers | a local dev container | `agent-containers` | `agent-containers` |
| branches | a working branch | git; agent-worktrees helpers when present | optional `agent-worktrees` |

Record the chosen binding (and the section name, e.g. `## Machines`) in the
addendum so `planning-efforts` uses it.

### 5. Point the repo's conventions at efforts

So efforts are actually used, add to the repo's agent instructions
(`AGENTS.md` / `.github/copilot-instructions.md`) and doc/skill routing:

- "New planning work starts as an **effort**, not a fresh design/plan doc."
- A knowledge-routing entry: *plan/status/coordination for a stretch of work →
  the effort under `efforts/active/<slug>/`.*
- If the repo had a legacy `plans/` (or similar), mark it superseded and treat
  existing plans as a backlog of efforts-in-waiting.
- **Install the minimal completion-gate projection.** Its canonical source is
  `instructions/completion-gate.instructions.md`, declared by
  `instruction-projections.json`. Do not copy its prose into `AGENTS.md`.
  Run the manager shipped by
  `customizing-copilot:reviewing-customizations`:

  ```bash
  python3 <reviewing-customizations-skill-dir>/scripts/manage-instruction-projections.py \
    sync <repo-root>
  ```

  The manager creates only the declared
  `.github/instructions/efforts/completion-gate.instructions.md` destination,
  maintains `.github/copilot/context-projections.json`, refuses ambiguous or
  locally modified ownership, and never deletes files. Review and commit the
  diff. If the manager is not installed, efforts remains independently usable,
  but this reference hook-less projection workflow is unavailable; enable the
  manager or use another implementation of the same inert contract rather than
  hand-copying the template.
- **A persistent cross-repo sequencing rule — keep the compatibility/fallback
  rule until plugin injection exists.** This plugin currently ships only
  on-demand skills, so setup must still add a concise rule to the adopting
  repo's always-on instructions (`AGENTS.md` /
  `.github/copilot-instructions.md`). This is a behavior-safe compatibility and
  fallback path, not the target ownership model: future plugin-owned ambient
  guidance should follow `customizing-copilot:authoring-skills`
  § *sessionStart context injection*, then shrink the static copy without losing
  the invariant or launch-path fallback.

  Reconcile the rule idempotently inside this stable owner region (or in a
  dedicated `efforts`-named instruction file):

  ```markdown
  <!-- efforts:cross-repo-sequencing:start -->
  When an
  effort in this **review-gated** repo also drives a change in a related repo you
  push **directly** — no PR, no pre-merge review — land the **effort-update PR
  first**, before the direct push that realizes it; the reviewed plan/intent must
  clear review **ahead of** the unreviewed push. Only **completion markers**
  (journal "done" entries, `Status:` flips, checklist ticks, "shipped in
  `<commit>`") are recorded **after** the cross-repo work — everything stating
  intent or plan belongs in the earlier PR.
  <!-- efforts:cross-repo-sequencing:end -->
  ```

  A repo that already carries
  equivalent standing guidance need only confirm it covers this ordering
  (the **equivalent-guidance** path). A repo that is *not* review-gated, or that
  never pushes directly to a related repo, can skip it. Future setup versions
  use the owner marker to update, shrink, or remove the compatibility text
  without appending duplicates or editing neighboring repo-owned prose.

### 6. Validate

- `.copilot-extensions/efforts/config.json` has exactly version 1 and
  `enforcement: required`.
- `efforts/README.md` has a `## Local conventions` addendum.
- `efforts/TEMPLATE.md` matches the addendum's section set.
- `efforts/active/` exists and is tracked.
- The repo's agent instructions route planning to efforts.
- Projection sync is clean and the lock owns exactly one current
  `.github/instructions/efforts/completion-gate.instructions.md` file.
- Projection scan succeeds without blocking findings:

  ```bash
  python3 <reviewing-customizations-skill-dir>/scripts/manage-instruction-projections.py \
    scan <repo-root> --from-settings
  ```

- Any old `efforts:static-fallback` region is reported as a legacy migration
  finding and remains untouched until a reviewer removes it manually.
- The repo's **compatibility/fallback always-on** instructions carry the
  cross-repo sequencing rule in the stable `efforts` owner region
  (effort-update PR before an unreviewed direct push; only completion markers
  after) — or equivalent standing guidance already covers it. (Skip only when the
  repo is not review-gated or never pushes directly to a related repo.)
- Direct-probe the native policy producer with a `sessionStart` payload whose
  `cwd` is inside the repository and confirm the context is owner-marked and
  below 1,024 UTF-8 bytes. Do not register another independent context hook
  while issue #1234 remains unresolved; doing so can displace a sibling's
  command catalog. The POSIX wrapper requires a usable system `python3` or
  `python` and fails open with one diagnostic when neither is available; the
  PowerShell producer is native.
- Direct-probe cross-repository capability with
  `emit-policy.sh --check-adoption <absolute-repo-path>` or
  `emit-policy.ps1 -CheckAdoption <absolute-repo-path>`. Only exact JSON
  `{"version":1,"capability":"efforts","adopted":true}` proves adoption; `{}` is
  the fail-closed answer for absent, malformed, non-local, and remote-only
  targets.

## Migrating from a legacy plans directory

If the repo already keeps prescriptive design docs (a `plans/`, `roadmaps/`,
etc.):

- **Don't bulk-move.** Leave them in place as a backlog of efforts-in-waiting.
- When work resumes on one, **promote it**: start an effort pointing at the
  plan doc and its issues, and carry live planning there.
- When fully migrated, replace the plan entry with a pointer to the effort.
- Service/tool-level roadmaps may stay where they are and act as standing
  sources of future efforts.
