# visions

The **visions system** for GitHub Copilot CLI.

A **vision** is a persistent, self-consistent statement of what a system,
service, tool, or product is *ultimately meant to be* — its purpose, its
high-level concepts and components, and the **features** and **behaviors**
expected of it. It is the standing **north star**: the durable *what-should-be*
against which reality is measured. One vision, one folder, one README that
humans and agents both read and revise.

A vision is **intent-level, not a specification.** It states *what* should be
true and leaves agents **latitude in how** to realize it. It is revised **in
place** — Git is its version history; there is no "archive." When a vision
changes, its old ideas are simply *replaced*, and any implementation built to
the old vision accrues **code debt** until an effort closes the gap.

## Visions, efforts, docs, issues

The vision sits alongside the [`efforts`](../efforts) planning system as its
persistent counterpart:

| Construct | Answers | Tense | Lifecycle | Home |
|-----------|---------|-------|-----------|------|
| **Vision** | "What should this *ultimately* be?" | should-be (standing) | revised **in place**, Git-versioned, rarely superseded | `visions/` |
| **Effort** | "What are we doing now, and how's it going?" | should-be (a campaign) | time-boxed, archived when done | `efforts/` |
| **Doc** | "How does it *actually* work?" | is (truth) | tracks reality | docs |
| **Issue** | "What discrete thing needs doing?" | to-do | closed when done | the tracker |

The load-bearing relationship: **efforts are carved from the delta** between a
vision (should-be) and the architecture docs (is). Diff a vision's expected
features/behaviors against what the docs say exists, and each misalignment is a
file-able issue that efforts then close. Visions **feed** efforts; efforts
**realize** visions; docs **record** the result.

## What this plugin ships

This is a **pure skill plugin** — no runtime, no service, no installer. It
delivers two skills via the Copilot CLI plugin marketplace:

| Skill | Role |
|-------|------|
| **envisioning** | The workflow: create, revise-in-place, and (rarely) supersede a vision; keep it intent-level and pure should-be; derive the delta → issues → efforts. Governs the canonical vision pattern (folder-per-vision layout, README schema, lifecycle, the organization seam). Ships the reference guide and the vision README template as skill assets. |
| **visions-setup** | Adoption: how a repo takes on the visions system — create the `visions/` tree and write a short repo **addendum** that specializes the bindings (chiefly *organization*). |

## The skill governs the pattern; each repo adds an addendum

The `envisioning` skill is the **single source of truth** for the vision pattern
(folder-per-vision layout, README schema, lifecycle, the organization seam). An
adopting repo does not redefine it — it writes a short **addendum** that
specializes only the bindings:

| Binding | What the addendum sets | Examples |
|---------|------------------------|----------|
| **Organization** | how `visions/` is structured; how deep is a vision | mirror the code layout (`visions/services/<name>/`) · by product · by domain |
| **Sections** | any additions/renames to the README schema | add a `Principles` section; rename `Concepts & Components` |
| **Linkage** | which tracker holds issues; how a vision points at its reality docs | Gitea/GitHub; a `See Also` convention |

The addendum lives in the adopting repo (its `visions/README.md` or a binding
doc such as `docs/visions.md`), keeping repo- and environment-specific details
out of the portable core. **Organization is deliberately a repo binding** — the
plugin does not mandate a top-level hierarchy.

## Vision vs. specification (a deliberate boundary)

Visions overlap with "specifications," and the distinction is intentional:

| | **Vision** | **Specification** (not part of this plugin) |
|---|---|---|
| States | intent + expected features/behaviors | exact, implementation-level requirements |
| Agent latitude | **wide** — chooses how to realize it | narrow — conform to the spec |

Keep a vision at the **intent** altitude. If vision→reality translation proves
too loose in practice — too much back-and-forth to converge — the intended
remedy is a *separate* **specifications** middle layer between visions (intent)
and reality (implementation), **not** hardening a vision into a spec. That layer
is a future option; this plugin names it as the escape hatch and stops there.

## Install

A pure skill plugin needs no runtime install. Add the marketplace and the
plugin:

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install visions@copilot-extensions
```

Then run the **visions-setup** skill in a repo to adopt the system.

## See also

- `skills/envisioning/references/visions.md` — the full reference guide
- `skills/envisioning/assets/TEMPLATE.md` — the vision README template
- [`../efforts`](../efforts) — the efforts planning system visions feed
- [docs/install-contract.md](../../docs/install-contract.md) — plugin/runtime
  contract (visions has no runtime; payload-only)
