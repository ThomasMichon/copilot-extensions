# Visions — copilot-extensions

The **standing north star** for this repo: what its plugins, services, and
shared systems are *ultimately meant to be*. A vision is pure **should-be**,
revised **in place** (Git is the history), and **intent-level** — it states
*what* should be true and leaves *how* to the work that realizes it. It is not a
spec and not a status tracker.

The canonical vision pattern is governed by the **`envisioning`** skill (from
the `visions` plugin this repo ships); this page adds only the repo's local
bindings. Copy [`TEMPLATE.md`](TEMPLATE.md) to `visions/<path>/README.md` to
author a new one.

## How visions relate to the other constructs

| Construct | Question | Tense | Home |
|-----------|----------|-------|------|
| **Vision** | "What should this *ultimately* be?" | should-be (standing) | `visions/` (revised in place) |
| **Effort** | "What are we doing now?" | should-be (a campaign) | a driver/control repo, or a future in-repo `efforts/` |
| **Doc** | "How does it *actually* work?" | is (truth) | `docs/`, per-plugin `docs/` |
| **Issue** | "What discrete thing?" | to-do | GitHub issues on this repo |

**Efforts are carved from a vision's delta vs. reality** — diff the vision's
should-be against the reality docs (and code), file the misalignments as issues
that *cite the vision item*, and group them into an effort. The vision itself is
never edited to record that cycle; it changes only when the **intent** changes.

## Vision index

| Vision | Scope | Subject |
|--------|-------|---------|
| [plugin-services](plugin-services/README.md) | branch | The plugin **service model** — how installer-deployed plugin runtimes expose, coordinate, and are reached as local services, à la carte and without shared infrastructure. |

<!-- Add rows as visions are authored. A per-plugin vision lives at
     visions/plugins/<name>/; a cross-cutting capability vision at
     visions/<capability>/. -->

## Local conventions

This is the repo's **addendum** to the canonical pattern. It specializes only
the bindings below; it does not restate the core (see the `envisioning` skill).

### Organization

Two placement lanes, depth = specificity:

- **Cross-cutting capability visions** live at the top level:
  `visions/<capability>/` (e.g. [`visions/plugin-services/`](plugin-services/README.md)).
  Use these for intent that spans plugins — the service model, the install
  contract, the credential-relay trust model.
- **Per-plugin visions** mirror the code layout: `visions/plugins/<name>/`
  (e.g. a future `visions/plugins/agent-bridge/`). Use these when a vision maps
  1:1 to a single plugin, so the vision↔`plugins/<name>/docs/architecture.md`
  diff is straightforward.

A **branch** README (a folder with children) is a lean map that links its
children; a **leaf** README is concrete. Decompose liberally — push a component
that is its own subject down into a child vision rather than inlining it.

### Schema

Use the core section set (Purpose & Intent · Concepts & Components · Features ·
Behaviors · Non-Goals / Boundaries · See Also, plus the optional non-authoritative
Provenance). No repo-specific renames or additions.

### Issue & effort linkage

- **Tracker:** GitHub issues on `ThomasMichon/copilot-extensions`. Per the
  repo's contribution rules, **claim a stretch with an issue first** (search
  open issues, then take or comment on one). Cite the vision item precisely,
  e.g. *"advances Vision plugin-services §Behaviors/collision-free-endpoints"*.
- **Public-artifact rule:** issues and commits are world-readable — keep them
  generic (no downstream-private names or context), per `AGENTS.md`.
- **Efforts:** this repo has not adopted an in-repo `efforts/` tree; the
  vision→reality delta is carved into **GitHub issues** here, and (where a
  private driver runs the work) into that **driver/control repo's** efforts,
  which link back to the public issue. If an in-repo `efforts/` tree is later
  adopted, deltas carve efforts there instead.
- **Reality docs:** a vision's *See Also* points at the architecture/README that
  records what *is* (chiefly [`docs/architecture.md`](../docs/architecture.md),
  [`docs/install-contract.md`](../docs/install-contract.md), and per-plugin
  `docs/`). Keep those links live when docs move.

### Cross-repo sequencing

This repo is **directly pushed** (branch to `main`, no PR gate required), so the
visions-system's review-gated sequencing rule does not bind here. When work is
driven from a **review-gated** control repo that *also* changes this repo,
follow that repo's ordering rule (land the reviewed intent before the unreviewed
change).
