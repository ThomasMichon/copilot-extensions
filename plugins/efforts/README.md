# efforts

The **efforts planning system** for GitHub Copilot CLI.

An **effort** is a comprehensive planning folder that represents a *stretch of
work* — its initial premise, its evolving plan, a validation plan, the running
journal of what actually happened, and the coordination surface for the
participants (machines, CodeSpaces, containers, branches) doing the work. One
effort, one folder, one README that everyone — human and agent — reads and
writes.

The name is deliberately **not** `feature`, `bug`, or `task`: those nouns are
owned by issue trackers (GitHub, Gitea, Azure DevOps). An effort is the
planning workspace *around* tracked work — it spawns and references issues, and
outlives any single one.

## What this plugin ships

This is a **pure skill plugin** — no runtime, no service, no installer. It
delivers two skills via the Copilot CLI plugin marketplace:

| Skill | Role |
|-------|------|
| **planning-efforts** | The workflow: start, plan, resume, and archive efforts. Governs the canonical effort pattern (folder layout, README schema, lifecycle, journal, the participants seam). Ships the reference guide and the effort README template as skill assets. |
| **efforts-setup** | Adoption: how a repo takes on the efforts system — create the `efforts/` tree and write a short repo **addendum** that specializes the bindings. |

## The skill governs the pattern; each repo adds an addendum

The `planning-efforts` skill is the **single source of truth** for the effort
pattern. An adopting repo does not redefine it — it writes a short **addendum**
that specializes only three bindings:

| Binding | What the addendum sets | Examples |
|---------|------------------------|----------|
| **Grouping** | flat vs. by-repo folder layout | `efforts/active/<slug>/` (flat) · `efforts/active/<repo>/<slug>/` (by-repo) |
| **Participants seam** | what executes the work, and how it's reached | machines (SSH/agent-bridge) · CodeSpaces · containers · branches |
| **Sections** | any additions/renames to the README schema | add a `Validation Plan`, rename `Participants` → `Machines` |

The addendum lives in the adopting repo (its `efforts/README.md` or a binding
doc such as `docs/efforts.md`), keeping repo- and environment-specific details out of the
portable core.

## How executor plugins build on efforts

The README's **participants seam** is where executor plugins plug in. An effort
catalogs *who or what* does its dispatched work, and different repos bind that
seam to different providers:

- [`agent-codespaces`](../agent-codespaces) → GitHub **CodeSpaces**
- [`agent-containers`](../agent-containers) → local **containers**
- SSH/[`agent-bridge`](../agent-bridge) → **machines** in a fleet

The efforts plugin owns the planning document and lifecycle; the executor
plugins register participants into an effort and run the work. Keep the schema
and lifecycle executor-neutral — anything provider-specific belongs in the
participants binding, not the core.

## Install

A pure skill plugin needs no runtime install. Add the marketplace and the
plugin:

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install efforts@copilot-extensions
```

Then run the **efforts-setup** skill in a repo to adopt the system.

## See also

- `skills/planning-efforts/references/efforts.md` — the full reference guide
- `skills/planning-efforts/assets/TEMPLATE.md` — the effort README template
- [docs/install-contract.md](../../docs/install-contract.md) — plugin/runtime
  contract (efforts has no runtime; payload-only)
