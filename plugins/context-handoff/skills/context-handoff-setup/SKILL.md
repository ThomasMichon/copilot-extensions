---
name: context-handoff-setup
description: >
  Troubleshoot context-handoff when its session-start continuity guidance,
  Copilot CLI extension, tools, or reminders are missing. The plugin contributes
  both a declarative hook and an extension with no runtime install step. Use
  when context-handoff guidance or extension behavior is not loading. Trigger
  phrases include:
  - 'context-handoff not loading'
  - 'context-handoff extension missing'
  - 'handoff extension not working'
  - 'handoff guidance missing'
  - 'no handoff reminders'
  - 'generate_handoff_prompt missing'
  - 'enable context-handoff'
  - 'set up context-handoff'
---

# Context Handoff Setup

The plugin has two independently loaded ambient components with no runtime
install step:

- A declarative `sessionStart` hook writes the full owner-marked continuity
  contract to the exact session folder, where a static pointer instructs the
  agent to read it. A separate contributor retains the concise
  `additionalContext` kernel as a best-effort supplement.
- The **context-handoff extension** provides the live context-window monitor: token
tracking + percentage-based 55%/70% defaults with optional repository config +
`generate_handoff_prompt` /
`save_handoff_prompt` / `continue_handoff` tools, plus `/handoff-continue` and
`/resume-handoff`.

For the `/handoff` authoring workflow itself, see the **context-handoff** skill.

## How it loads

When `context-handoff@copilot-extensions` is enabled, the CLI reads the
plugin-declared `hooks.json`. One hook invokes the full `emit-guidance` producer
with `--own-only` and atomically writes its result beneath the exact session's
`instructions/context-handoff/` folder. The projected static pointer directs
the agent to that file. The existing authority-aware contributor continues to
emit the compact `--aggregate` kernel through `additionalContext` as a
best-effort supplementary channel.

Separately, the CLI scans
`~/.copilot/installed-plugins/copilot-extensions/context-handoff/extensions/`
at session startup and loads `context-handoff/extension.mjs` as a `plugin`-source
extension. No installed runtime, venv, binstub, copy to
`~/.copilot/extensions/`, `scripts/install.*`, or manifest.

If extension registration fails, the enabled plugin payload still contains
`extensions/context-handoff/handoff-cli.mjs`. Resolve it relative to the
verified `COPILOT_PLUGIN_ROOT` (or an installed `context-handoff/plugin.json`
whose `name` is exactly `context-handoff`) and invoke it with `node`; do not look
for a PATH binstub or run an installer. The main `context-handoff` skill carries
the exact cross-platform fallback commands.

## Loading gates

The hook and extension have different gates. Check the component that is
missing, then start a fresh session.

### 1. The plugin must be enabled for both components

A marketplace plugin's hooks and `extensions/` dir load only when the plugin is
in `enabledPlugins`. Confirm `copilot plugin list` shows
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

### 2. Experimental mode is required only for the extension

The CLI gates **all** extension loading behind `"experimental": true` in
`~/.copilot/settings.json`. If extensions are not loading at all, set it there
directly (or use whatever repo/machine bootstrap normally manages your Copilot
settings) and start a fresh session. The `sessionStart` continuity hook does
not require experimental mode. Neither component requires worktree registration.

## Verify

Start a fresh Copilot CLI session. When the writer hook loads, the exact
session folder contains
`instructions/context-handoff/session-guidance.instructions.md`, beginning
with the `# Context handoff session guidance` heading and an
`[owner: context-handoff@<version>]` marker. The separate compact contributor
still emits `{}` on failure and logs
`[context-handoff] no guidance context emitted` instead of blocking startup.

A loaded extension exposes
`generate_handoff_prompt`, `save_handoff_prompt`, and `continue_handoff`, and
registers `/handoff-continue` and `/resume-handoff`. `/extensions` lists
`context-handoff` with source **plugin** (exactly once -- if you see it twice, a
stale copy exists under `~/.copilot/extensions/context-handoff/` or a project
`.github/extensions/`; the CLI loads every source with no dedup, so remove the
redundant copy). It intentionally does **not** log a user-visible "Session
started" breadcrumb.
