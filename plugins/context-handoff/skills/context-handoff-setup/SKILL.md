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
  agent to read it. A separate contributor retains the concise
  `additionalContext` kernel as a best-effort supplement.
- The **context-handoff extension** provides the live context-window monitor:
  token tracking + percentage-based 55%/70% defaults with optional repository
  config + `generate_handoff_prompt` / `save_handoff_prompt` /
  `consume_handoff` / `trigger_handoff`, plus `/handoff-continue` and
  `/resume-handoff`.

The extension is intentionally **process-manager agnostic**. It does not need
or install mux, tmux, psmux, or any other cutover runtime. External control
planes may act on the pending-handoff signals it emits, but they are not part
of this plugin's installation contract.

## How it loads

When `context-handoff@copilot-extensions` is enabled, the CLI reads the
plugin-declared `hooks.json`. One hook invokes the full `emit-guidance`
producer with `--own-only` and atomically writes its result beneath the exact
session's `instructions/context-handoff/` folder. The projected static pointer
directs the agent to that file. The existing authority-aware contributor
continues to emit the compact `--aggregate` kernel through `additionalContext`
as a best-effort supplementary channel.

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
`consume_handoff`, and `trigger_handoff`, and registers `/handoff-continue`
and `/resume-handoff`. `/extensions` lists `context-handoff` with source
**plugin**. It intentionally does **not** log a user-visible "Session started"
breadcrumb.
