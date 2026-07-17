---
name: installing-plugins
description: >
  Install and enable Copilot CLI plugins -- repo-scoped registration via
  .github/copilot/settings.json (extraKnownMarketplaces + enabledPlugins) with
  experimental mode, versus global installs, plus the payload-vs-runtime model
  and launch-time reconciliation. Use when installing, enabling, or updating a
  plugin, adding a marketplace, or setting up a repo's or machine's plugin set.
  Trigger phrases include:
  - 'install a plugin'
  - 'enable a plugin'
  - 'enabledPlugins'
  - 'copilot plugin install'
  - 'plugin marketplace'
  - 'extraKnownMarketplaces'
  - 'experimental mode'
  - 'repo plugins'
  - 'settings.json plugins'
---

# Installing Plugins

How to install and enable Copilot CLI plugins. Two registration styles --
**repo-scoped (preferred)** and **global** -- plus the distinction between a
plugin's *payload* and its *runtime*.

Reference: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-finding-installing

## Recommended: register at repo scope

Pin the plugin set to a repo so it travels with the project and stays consistent
across machines.

1. **Enable experimental mode once per machine** -- the CLI gates **all**
   extension loading on it (`~/.copilot/settings.json`):

   ```json
   { "experimental": true }
   ```

2. **Declare the marketplace + enable the plugins** in the repo's committed
   `.github/copilot/settings.json`:

   ```json
   {
     "extraKnownMarketplaces": {
       "my-marketplace": {
         "source": { "source": "github", "repo": "owner/my-marketplace-repo" }
       }
     },
     "enabledPlugins": {
       "some-plugin@my-marketplace": true
     }
   }
   ```

   Copilot vendors the enabled plugin **payloads** when a session runs in that
   repo. A plugin's `extensions/` directory is only scanned when it is enabled,
   and a newly enabled plugin may only take effect after **restarting the active
   session** (plugins are scanned at startup).

A repo's `.github/copilot/settings.json` is merged with the user
`~/.copilot/settings.json`; `enabledPlugins` may live in either.

> **Session extensions vs. skills/payload — a scoping caveat.** Repo-scoped
> `enabledPlugins` reliably governs a plugin's **payload** — its skills, hooks,
> and agents — for sessions in that repo. A plugin's **session extension** (an
> `extensions/<name>/extension.mjs` the plugin contributes) is activated from the
> **user-level** enabled set — `~/.copilot/settings.json` `enabledPlugins` plus
> the persisted install state — which the extension loader currently reads
> *without* merging the repo's `.github/copilot/settings.json`. So a plugin whose
> value depends on a **session extension loading** should be enabled at the
> **user level** (or installed globally), not via repo-scoped `enabledPlugins`
> alone. Skills/payload are unaffected; repo-scoped activation of *session
> extensions* is a known limitation.

## Alternative: global install

Install into the user profile instead (handy for a machine with no single
control repo):

```bash
copilot plugin marketplace add owner/my-marketplace-repo
copilot plugin install some-plugin@my-marketplace
```

Manage with `copilot plugin list`, `copilot plugin update <name>@<market>`, and
`copilot plugin uninstall <name>@<market>`.

## Payload vs runtime

`copilot plugin install` / `copilot plugin update` move only the plugin's
**payload** -- its source, skills, hooks, agents, and any session extensions --
into `~/.copilot/installed-plugins/`.

- A plugin that ships **only** skills/hooks/agents/extensions is fully deployed
  by the payload install; nothing else to do.
- A plugin that ships a **runtime** (a venv, `~/.local/bin` binstubs, or a
  long-running service) **also** runs its own installer to deploy that runtime
  from the payload. For such a plugin, a full update is two steps: refresh the
  payload, **then** run the plugin's install/setup step. The plugin's own docs
  (or an install skill it ships) say how.

> The CLI's "updated successfully" message after `copilot plugin update` refers
> to the **payload** only; a runtime plugin can read "updated" while its actual
> runtime (venv/binstub/service) is unchanged until its installer runs.

## Installing a plugin's standing rule into `AGENTS.md`

Enabling a plugin makes its **skills** available, but skills are **on-demand**:
a skill's guidance applies most strongly the turn it is invoked and fades after
(see `authoring-skills` § Action-sequence vs ambient-guidance skills). So a
plugin that wants a rule to hold for the *rest of the session* — a standing or
ambient convention (planning discipline, a cross-repo sequencing rule, a
knowledge-routing entry, a safety guard) — cannot rely on enablement alone. The
rule must be **materialized into the adopting repo's always-on instructions**
(`AGENTS.md` / `.github/copilot-instructions.md`, or a small dedicated rule file
those reference).

Treat this as the **guidance analogue of payload-vs-runtime**: enabling the
plugin deploys the *payload* (skills); writing its standing rule into `AGENTS.md`
deploys the *always-on guidance*. A plugin whose value depends on a persistent
rule is only half-installed until both are done.

**The seam (generalized from `efforts-setup` / `visions-setup`):** when a
plugin's setup ships a standing rule, its setup skill should **add a short,
declarative entry to the repo's always-on instructions** that:

1. **States the rule in the adopting repo's own voice** — not a copy of the
   plugin's internal docs, just the durable "always do X" the repo must enforce.
2. **Points at the on-demand skill for the mechanics** — `AGENTS.md` carries the
   rule + a skill pointer, not the full procedure (keep it a table-of-contents
   entry, per the harness runbook's Phase 4).
3. **Is idempotent** — reconcile on re-run (audit mode) rather than appending a
   duplicate; if the rule already exists, leave it.

This is the declarative way a bootstrapped harness installs ambient guidance so
it actually persists. When authoring a `-setup` skill for a plugin that carries
standing guidance, include this AGENTS.md-materialization step; when auditing a
harness, verify each such rule is present in the always-on layer, not stranded in
an on-demand skill.

## Keeping a repo's plugins fresh automatically

Some control harnesses reconcile a repo's `enabledPlugins` on each interactive
session launch -- ensuring every enabled plugin's payload is installed and, for
runtime plugins, that the deployed runtime matches the installed payload version
(acting only on drift). Where that exists, booting via the harness's launcher
keeps the plugin set fresh without manual `plugin update` calls. (Headless
`copilot -p --autopilot` runs do **not** merge repo `enabledPlugins`, so those
machines need required plugins installed globally, out-of-band.)
