---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"1932b01f0a01ecd9d229e8e934f525ec62c5c06f7972ffd10c015afb5bbd6796","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/customizing-copilot/session-guidance-writers.instructions.md","plugin":"customizing-copilot@copilot-extensions","pluginVersion":"0.1.0-dev97","renderedBytes":1815,"schema":"copilot-extensions.instruction-projection","sourceId":"session-guidance-writers","template":"instructions/session-guidance-writers.instructions.md","templateBytes":1012,"templateSha256":"bb31036d7da5f914a3cc3c55dc32d273b947737b321d53f19f26b24d1b8bda83","version":1} -->


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

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"49f849e02dcd88a625e6b9813efc86e71b7e5532d84dd29e3955508033ce0672"} -->
