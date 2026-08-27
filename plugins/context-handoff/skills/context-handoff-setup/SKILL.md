---
name: context-handoff-setup
description: >
  Troubleshoot the context-handoff Copilot CLI extension when it is missing or
  not loading. The extension is contributed directly by the context-handoff
  plugin (no runtime install step) -- this skill verifies the two conditions
  that gate it: the plugin is enabled, and experimental mode is on. Use when the
  context-handoff extension is not loading or its tools are absent. Trigger
  phrases include:
  - 'context-handoff not loading'
  - 'context-handoff extension missing'
  - 'handoff extension not working'
  - 'no handoff reminders'
  - 'generate_handoff_prompt missing'
  - 'enable context-handoff'
  - 'set up context-handoff'
---

# Context Handoff Setup

The **context-handoff extension** (the live context-window monitor: token
tracking + cost-aware 150K/250K caps with 55%/70% small-window fallbacks +
`generate_handoff_prompt` /
`save_handoff_prompt` / `continue_handoff` tools, plus `/handoff-continue` and
`/resume-handoff`) is **plugin-contributed** -- there is no runtime install
step. The Copilot CLI discovers it directly from the plugin's `extensions/` dir
when the plugin is enabled. This skill is for the case where it is **not**
loading.

For the `/handoff` authoring workflow itself, see the **context-handoff** skill.

## How it loads

When `context-handoff@copilot-extensions` is enabled, the CLI scans
`~/.copilot/installed-plugins/copilot-extensions/context-handoff/extensions/`
at session startup and loads `context-handoff/extension.mjs` as a `plugin`-source
extension. No installed runtime, venv, binstub, copy to
`~/.copilot/extensions/`, `scripts/install.*`, or manifest.

## Two conditions gate it

Both must hold. Check them in order, then start a fresh session.

### 1. The plugin must be enabled

A marketplace plugin's `extensions/` dir is only scanned when the plugin is in
`enabledPlugins`. Confirm `copilot plugin list` shows
`context-handoff@copilot-extensions`. If missing, fetch/enable the marketplace
plugin (this is not a context-handoff runtime installer):

```bash
copilot plugin install context-handoff@copilot-extensions
```

To enable it everywhere on a machine, add it to the user settings file
`~/.copilot/settings.json`:

```json
{ "enabledPlugins": { "context-handoff@copilot-extensions": true } }
```

Or enable it per-repo in that repo's `.github/copilot/settings.json`.

### 2. experimental mode must be on

The CLI gates **all** extension loading behind `"experimental": true` in
`~/.copilot/settings.json`. If extensions are not loading at all, set it there
directly (or use whatever repo/machine bootstrap normally manages your Copilot
settings) and start a fresh session. This plugin does **not** require
worktree registration.

## Verify

Start a fresh Copilot CLI session. A loaded extension exposes
`generate_handoff_prompt`, `save_handoff_prompt`, and `continue_handoff`, and
registers `/handoff-continue` and `/resume-handoff`. `/extensions` lists
`context-handoff` with source **plugin** (exactly once -- if you see it twice, a
stale copy exists under `~/.copilot/extensions/context-handoff/` or a project
`.github/extensions/`; the CLI loads every source with no dedup, so remove the
redundant copy). It intentionally does **not** log a user-visible "Session
started" breadcrumb.
