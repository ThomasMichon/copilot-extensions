# Plugin Consolidation — Proposal / Discussion

**Status:** discussion. No change is proposed for immediate execution; this
weighs whether (and how) to collapse the multi-plugin marketplace into fewer
plugins, and records the decision criteria so a future change can act on it.

**Trigger:** authoring the [Control-Harness Runbook](../harness-runbook.md)
surfaced how many plugins a real harness juggles. The runbook already carries a
curated *recommended plugin set* with Core / Recommended / Optional tiers — this
doc asks whether the *packaging* should follow that curation.

## The problem

The suite currently spans a dozen-plus plugins — see the canonical list in
[the README](../../README.md) / `.github/plugin/marketplace.json`; roughly:

`agent-worktrees`, `agent-bridge`, `agent-codespaces`, `agent-containers`,
`agent-mcp`, `agent-logger`, `agent-dispatch`, `agent-vault`, `context-handoff`,
`efforts`, `visions`, `customizing-copilot`, `copilot-extensions-harness`,
`wsl-setup`.

Costs of the current granularity:

- **Management overhead.** A dozen-plus `enabledPlugins` entries and version
  lines in `marketplace.json`, and as many install/update lifecycles to reason
  about.
- **Onboarding friction.** A newcomer must understand the whole matrix before
  choosing a set, even though most harnesses want the same handful.
- **Cross-plugin coupling is implicit.** The bridge imports codespaces +
  containers resolvers; efforts binds a participant seam the executors provide.
  These relationships aren't visible in the packaging.

Benefits the current granularity **does** buy (and that consolidation must not
lose):

- **Independent runtime lifecycles.** The runtime plugins ship a venv + binstub +
  service/task; the payload-only ones ship skills/an extension. Bundling plugins
  with different runtime scopes into one plugin muddies the payload-vs-runtime
  contract.
- **Install only what you run.** A single-machine local harness never needs
  Codespaces, containers, or dispatch runtimes.
- **Independent versioning.** A patch to `visions` (payload-only) should not
  force a version churn on `agent-bridge`.

## The seam that already exists

The runbook references **capabilities** (worktrees, bridge, efforts, visions,
review, MCP delegation), not a fixed plugin count. So the phases survive
repackaging unchanged. The **"Recommended plugin set" table is the single seam**
to update if packaging changes — nothing else in the runbook hardcodes the
fourteen.

## Options

### Option A — Status quo, better curated (lowest risk)

Keep fourteen plugins; lean on the runbook's tiers and the `building-harnesses`
skill to *curate* what a harness enables. No packaging change; the awkwardness is
managed by guidance, not structure.

- **Pro:** zero migration; preserves all independence benefits.
- **Con:** the fourteen-entry management overhead remains real.

### Option B — Group by lifecycle into a few "meta" plugins

Collapse along the **payload-vs-runtime** grain, which is the honest boundary:

- **`copilot-authoring`** (payload-only): merge `customizing-copilot` +
  `efforts` + `visions` + `context-handoff`. All skills/extension, no runtime.
  One enable line turns on "how to customize + plan + envision + hand off."
- **`agent-mesh`** (runtime): keep `agent-worktrees` + `agent-bridge` together
  (already tightly coupled), optionally folding `agent-codespaces` +
  `agent-containers` in as substrate modules behind feature flags.
- Leave `agent-mcp` and `agent-dispatch` standalone (genuinely independent,
  optional).

- **Pro:** collapses fourteen → ~4–5; the grouping matches real coupling and the
  payload/runtime contract.
- **Con:** loses per-capability enable/disable granularity *within* a group
  (you can't take `visions` without `efforts`); bigger payloads; a migration
  path for existing `enabledPlugins` is required.

### Option C — One "suite" plugin with capability toggles

A single `copilot-harness` plugin that vendors everything, gated by an in-repo
config that selects capabilities.

- **Pro:** one enable line, one version.
- **Con:** breaks the CLI's own payload-vs-runtime model, forces every machine
  to vendor everything, and re-implements plugin selection the CLI already does.
  Not recommended.

## Recommendation (for discussion)

Lead with **Option A now** — the runbook + `building-harnesses` skill already
make the granularity manageable, and it costs nothing. Treat **Option B** as the
target *if* management overhead stays painful after the runbook is in use:
consolidate along the payload-vs-runtime grain, starting with the safe
payload-only merge (`copilot-authoring`), which loses the least and is easiest to
migrate (payload-only, no runtime lifecycle to reconcile).

Explicitly **reject Option C** — it fights the runtime's own model.

## Decision criteria (revisit before acting)

Consolidate a group only when **all** hold:

1. The members share a **runtime scope** (all payload-only, or all the same
   runtime lifecycle).
2. Enabling one member without the others has **no real-world demand**.
3. A **migration path** for existing `enabledPlugins` keys exists (old keys keep
   resolving, or a documented rename).
4. The combined payload does not pull a runtime onto machines that only wanted a
   skill.

## Open questions

- Do any harnesses in the wild enable `efforts` **without** `visions` (or vice
  versa)? If not, they're a natural merge.
- Is `context-handoff` (a session extension) safe to co-ship with skill-only
  plugins, or does the extension lifecycle argue for keeping it separate?
- Should `agent-codespaces` / `agent-containers` become **substrate modules** of
  `agent-bridge` rather than peer plugins, given the bridge already imports them?

No action until these are answered; this doc is the record to answer them
against.
