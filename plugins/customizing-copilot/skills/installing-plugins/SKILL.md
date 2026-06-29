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

## Keeping a repo's plugins fresh automatically

Some control harnesses reconcile a repo's `enabledPlugins` on each interactive
session launch -- ensuring every enabled plugin's payload is installed and, for
runtime plugins, that the deployed runtime matches the installed payload version
(acting only on drift). Where that exists, booting via the harness's launcher
keeps the plugin set fresh without manual `plugin update` calls. (Headless
`copilot -p --autopilot` runs do **not** merge repo `enabledPlugins`, so those
machines need required plugins installed globally, out-of-band.)
