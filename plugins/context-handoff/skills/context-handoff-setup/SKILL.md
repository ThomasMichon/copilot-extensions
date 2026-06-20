---
name: context-handoff-setup
description: >
  Troubleshoot the context-handoff Copilot CLI extension when it is missing or
  not loading. The extension is contributed directly by the context-handoff
  plugin (no install step) -- this skill verifies the two conditions that gate
  it: the plugin is enabled, and experimental mode is on. Use when the
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
tracking + 55%/70% nudges + `generate_handoff_prompt` / `save_handoff_prompt`
tools) is **plugin-contributed** -- there is no install step. The Copilot CLI
discovers it directly from the plugin's `extensions/` dir when the plugin is
enabled. This skill is for the case where it is **not** loading.

For the `/handoff` authoring workflow itself, see the **context-handoff** skill.

## How it loads

When `context-handoff@copilot-extensions` is enabled, the CLI scans
`~/.copilot/installed-plugins/copilot-extensions/context-handoff/extensions/`
at session startup and loads `context-handoff/extension.mjs` as a `plugin`-source
extension. No copy to `~/.copilot/extensions/`, no `scripts/install.*`, no
manifest.

## Two conditions gate it

Both must hold. Check them in order, then start a fresh session.

### 1. The plugin must be enabled

A marketplace plugin's `extensions/` dir is only scanned when the plugin is in
`enabledPlugins`. Confirm `copilot plugin list` shows
`context-handoff@copilot-extensions`. If missing, install it:

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
`~/.copilot/settings.json`. This is ensured by the **agent-worktrees** installer
(`Ensure-CopilotExperimental`), run on `agent-worktrees install` / `update`. If
extensions are not loading at all, run:

```bash
agent-worktrees update
```

and confirm `"experimental": true` is present in `~/.copilot/settings.json`.

## Verify

Start a fresh Copilot CLI session. A loaded extension logs
`[Context Handoff] Session started ...` and exposes `generate_handoff_prompt` /
`save_handoff_prompt`. `/extensions` lists `context-handoff` with source
**plugin** (exactly once -- if you see it twice, a stale copy exists under
`~/.copilot/extensions/context-handoff/` or a project `.github/extensions/`; the
CLI loads every source with no dedup, so remove the redundant copy).
