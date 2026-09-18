---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","customizationKind":"instructions","destination":".github/instructions/customizing-copilot/skill-authoring.instructions.md","plugin":"customizing-copilot@copilot-extensions","pluginVersion":"0.1.0-dev77","renderedBytes":1538,"schema":"copilot-extensions.instruction-projection","sourceId":"skill-authoring","template":"instructions/skill-authoring.instructions.md","templateBytes":990,"templateSha256":"3ef8fbbf095ee52ac3059d0071c8755c005f41fc075402ef87c0ebdc66ac8b54","version":1} -->

# Skill authoring and review

Before creating or editing a published `SKILL.md`, including description-only
or body-only changes, use `customizing-copilot:authoring-skills` and read any
explicitly referenced repository/harness skill-review rulebook. Use
`customizing-copilot:reviewing-customizations` for the holistic review of the
final bytes. Apply already-active guidance without invoking it recursively.

On hosts without marketplace-skill discovery, use available source guidance
and the repository's rulebook rather than requiring an undiscoverable skill.
If the upstream helper or Copilot CLI is unavailable, report runtime conformance
as not checked; never substitute a parser or claim a pass.

For generated skills, review the source and check the generated result instead
of hand-editing it. Deliberately invalid fixtures are not published skills.
Formal evals remain optional for ordinary edits. Repository authorization and
publication rules still apply.
