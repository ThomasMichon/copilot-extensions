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

This is a **payload-only skill plugin** — no runtime, virtualenv, service,
installer, or binstub. Enabling the plugin is the whole install. It delivers two
skills via the Copilot CLI plugin marketplace:

| Skill | Role |
|-------|------|
| **planning-efforts** | The workflow: start, plan, resume, and archive efforts. Governs the canonical effort pattern (folder layout, README schema, lifecycle, journal, the participants seam). Ships the reference guide and the effort README template as skill assets. |
| **efforts-setup** | Adoption: how a repo takes on the efforts system — create the `efforts/` tree and write a short repo **addendum** that specializes the bindings. |

## The skill governs the pattern; each repo adds an addendum

The `planning-efforts` skill is the **single source of truth** for the effort
pattern. An adopting repo does not redefine it — it writes a short **addendum**
that specializes only the local bindings:

| Binding | What the addendum sets | Examples |
|---------|------------------------|----------|
| **Grouping** | flat vs. by-repo folder layout | `efforts/active/<slug>/` (flat) · `efforts/active/<repo>/<slug>/` (by-repo) |
| **Participants seam** | what executes the work, and how it's reached | machines (SSH/agent-bridge) · CodeSpaces · containers · branches |
| **Sections** | any additions/renames to the README schema | rename `Participants` → `Machines`, add repo-specific sections |

The addendum lives in the adopting repo (its `efforts/README.md` or a binding
doc such as `docs/efforts.md`), keeping repo- and environment-specific details out of the
portable core.

## How executor plugins build on efforts

The README's **participants seam** is where executor plugins can plug in. The
efforts plugin is standalone: a repo can use the README contract with no
agent-worktrees registration and no executor plugin at all. When executor
plugins are present, the adopting repo's addendum binds the seam to a provider:

- [`agent-codespaces`](../agent-codespaces) → GitHub **CodeSpaces**
- [`agent-containers`](../agent-containers) → local **containers**
- SSH/[`agent-bridge`](../agent-bridge) → **machines** in a fleet

The efforts plugin owns the planning document and lifecycle; executor plugins
own only their provider-specific borrow/dispatch/claim mechanics. Keep the
schema and lifecycle executor-neutral — anything provider-specific belongs in
the participants binding, not the core.

## Enable

There is no plugin-local setup command. Enable `efforts@copilot-extensions` in
the normal Copilot CLI plugin configuration/marketplace flow for your harness;
because this is payload-only, that simply makes the skills available.

Then run the **efforts-setup** skill in a repo to adopt the system.

## See also

- `skills/planning-efforts/references/efforts.md` — the full reference guide
- `skills/planning-efforts/assets/TEMPLATE.md` — the effort README template
- [docs/install-contract.md](../../docs/install-contract.md) — plugin/runtime
  contract (efforts has no runtime; payload-only)
