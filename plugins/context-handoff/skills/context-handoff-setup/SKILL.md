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

- A declarative `sessionStart` hook injects the concise owner-marked continuity
  kernel through `additionalContext`.
- The **context-handoff extension** provides the live context-window monitor: token
tracking + percentage-based 55%/70% defaults with optional repository config +
`generate_handoff_prompt` /
`save_handoff_prompt` / `continue_handoff` tools, plus `/handoff-continue` and
`/resume-handoff`.

For the `/handoff` authoring workflow itself, see the **context-handoff** skill.

## How it loads

When `context-handoff@copilot-extensions` is enabled, the CLI reads the
plugin-declared `hooks.json` and invokes `scripts/emit-guidance.sh` or
`scripts/emit-guidance.ps1` at session start. The hook needs only the enabled
plugin payload. If an adjacent agent-worktrees payload exists, the producer
also includes a bounded catalog for that exact adjacent command with
`adjacent-compatibility` provenance. Adjacency checks payload presence rather
than current-session enablement; the command is `ready` only when its command
and installer are both present, otherwise `unavailable`. This is a deliberate
best-effort exception while the current Copilot CLI hook-aggregation defect
(#1234) is owned by a separate effort: when this result survives, the agent
retains the exact worktree command, but the plugin cannot guarantee that its
kernel wins or preserve every competing plugin's context. The POSIX
compatibility catalog requires a system `python3` or `python`; the continuity
kernel still emits without it. Once #1234 provides deterministic aggregation,
remove this compatibility catalog and rely on agent-worktrees' own producer.
Standalone context-handoff installations remain independent.

Separately, the CLI scans
`~/.copilot/installed-plugins/copilot-extensions/context-handoff/extensions/`
at session startup and loads `context-handoff/extension.mjs` as a `plugin`-source
extension. No installed runtime, venv, binstub, copy to
`~/.copilot/extensions/`, `scripts/install.*`, or manifest.

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

Start a fresh Copilot CLI session. When the hook loads, the agent's additional
context begins with `[owner: context-handoff@<version>]`. A payload failure
emits `{}` and the stderr diagnostic
`[context-handoff] no guidance context emitted` instead of blocking startup.
With a sibling agent-worktrees plugin, the additional context also contains
`## agent-worktrees session command catalog` when the platform can construct
the compatibility catalog.

A loaded extension exposes
`generate_handoff_prompt`, `save_handoff_prompt`, and `continue_handoff`, and
registers `/handoff-continue` and `/resume-handoff`. `/extensions` lists
`context-handoff` with source **plugin** (exactly once -- if you see it twice, a
stale copy exists under `~/.copilot/extensions/context-handoff/` or a project
`.github/extensions/`; the CLI loads every source with no dedup, so remove the
redundant copy). It intentionally does **not** log a user-visible "Session
started" breadcrumb.
