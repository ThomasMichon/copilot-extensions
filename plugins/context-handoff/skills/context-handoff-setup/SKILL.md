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
  - 'trigger_handoff missing'
  - 'enable context-handoff'
  - 'set up context-handoff'
---

# Context Handoff Setup

The plugin has two independently loaded ambient components with no runtime
install step:

- A declarative `sessionStart` hook writes the full owner-marked continuity
  contract to the exact session folder, where a static pointer instructs the
  agent to read it. The hook emits only `{}`.
- The **context-handoff extension** provides the live context-window monitor:
  token tracking + percentage-based 55%/70% defaults with optional repository
  config + `generate_handoff_prompt` / `save_handoff_prompt` /
  `consume_handoff` / `trigger_handoff`, plus `/handoff-continue` and
  `/resume-handoff`.

Native live handoff requires Node.js, the tested Copilot CLI 1.0.84-3 native API,
and either the native-aware Herdr `copilot-pane` launcher or agent-worktrees.
This plugin does not install those hosts. Legacy text-only signal pickup does
not require them. Do not reload an older CLI and claim that its runtime upgraded.

Use official `copilot plugin list`, `install`, `update`, and `uninstall`
commands, or the native plugin management UI, to resolve duplicate direct/
marketplace copies; never edit an installed cache.
For a versioned local candidate, `copilot plugin install <source-plugin-dir>`
is the official source install. Disable the competing marketplace entry through
the native plugin UI first.
Verify the enabled source/version in a fresh CLI, without changing its model or
permission defaults. Roll back through the original official install source,
not by undoing consumed handoffs or reviving retired sessions.

An empty profile may ask for native first-use trust in the extension's existing
capabilities. This is separate from session permission mode; never widen the
latter to make a test pass. Paused/exhausted/completed GoalPanel is intentionally
hidden and must not be made visible by granting credits or enabling autopilot.

## How it loads

When `context-handoff@copilot-extensions` is enabled, the CLI reads the
plugin-declared `hooks.json`. One hook invokes the full `emit-guidance`
producer with `--own-only` and atomically writes its result beneath the exact
session's `instructions/context-handoff/` folder. The projected static pointer
directs the agent to that file. No cross-plugin authority or competing startup
output is involved.

Separately, the CLI scans
`~/.copilot/installed-plugins/copilot-extensions/context-handoff/extensions/`
at session startup and loads `context-handoff/extension.mjs` as a `plugin`
source extension. No installed runtime, venv, binstub, copy to
`~/.copilot/extensions/`, `scripts/install.*`, or manifest is involved.

If extension registration fails, the enabled plugin payload still contains
`extensions/context-handoff/handoff-cli.mjs`. Resolve it relative to the
verified `COPILOT_PLUGIN_ROOT` (or an installed `context-handoff/plugin.json`
whose `name` is exactly `context-handoff`) and invoke it with `node`; do not
look for a PATH binstub or run an installer.

## Verify

Start a fresh Copilot CLI session. When the writer hook loads, the exact
session folder contains
`instructions/context-handoff/session-guidance.instructions.md`, beginning
with the `# Context handoff session guidance` heading and an
`[owner: context-handoff@<version>]` marker.

A loaded extension exposes `generate_handoff_prompt`, `save_handoff_prompt`,
`consume_handoff`, `continue_handoff`, `retry_handoff_cutover`, and
`trigger_handoff`, and registers `/handoff-continue`
and `/resume-handoff`. `/extensions` lists `context-handoff` with source
**plugin**. It intentionally does **not** log a user-visible "Session started"
breadcrumb.
