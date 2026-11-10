---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"4c22acf1450f72a10515cb991739d3391d588bef0792aba73eb60b2da2baf0f8","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/customizing-copilot/session-guidance-writers.instructions.md","plugin":"customizing-copilot@copilot-extensions","pluginVersion":"0.1.0-dev97","renderedBytes":2037,"schema":"copilot-extensions.instruction-projection","sourceId":"session-guidance-writers","template":"instructions/session-guidance-writers.instructions.md","templateBytes":1234,"templateSha256":"e0c5f42329ebdb53000e1fed2b66f447f89ac0419d1847d952f566cf02aa9b0c","version":1} -->


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

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"679d3cbc2dbd46eaf6cb8e5cfd17434225d62f24db895aea8fb7cd374d4607b2"} -->
