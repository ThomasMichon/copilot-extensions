---
applyTo: "**"
---

# Session-guidance writers are for computed facts

[owner: customizing-copilot]

When authoring or reviewing a plugin, add a `sessionStart` exact-session
guidance writer only for a fact that must be computed when the session starts,
such as a resolved command line, readiness or local state. Each writer ships
its own generated copy of the writer engine and depends on hook delivery, so do
not add one for static text.

Route static text by when it is needed:

- Standing policy and critical safeguards that must apply before any skill is
  chosen stay in a declared static projection (`instructions/<topic>` template
  plus an `instruction-projections.json` entry), delivered automatically.
- Task-specific reference material (definitions, trigger phrases, procedures,
  plugin-internal paths) belongs in the owning skill, loaded on demand. A
  skill resolves its plugin root from its own base directory, not from
  hook-scoped variables such as `COPILOT_PLUGIN_ROOT`.

When a writer's output is entirely static, remove its hook, writer, emitters,
pointer projection and reviewed fallback, and move each part to the home
above. Use `reviewing-customizations` for the full session-guidance
conformance classes.
