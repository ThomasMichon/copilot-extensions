---
applyTo: "**"
---

# Session-guidance writers are for computed facts

[owner: customizing-copilot]

When authoring or reviewing a plugin, add a `sessionStart` exact-session
guidance writer only for a fact that must be computed when the session starts,
such as a resolved command line, readiness or local state. Static text (a
definition, trigger phrase, normative contract or a path inside the plugin)
belongs in the owning skill, which every client loads on demand without a hook
and which survives context compaction. Each writer ships its own generated copy
of the writer engine and depends on hook delivery, so do not add one for static
text. A skill resolves its plugin root from its own base directory, not from
hook-scoped variables such as `COPILOT_PLUGIN_ROOT`.

When a writer's output is entirely static, remove its hook, writer, emitters,
pointer projection and reviewed fallback, and move the text into the skill.
Use `reviewing-customizations` for the full session-guidance conformance
classes.
