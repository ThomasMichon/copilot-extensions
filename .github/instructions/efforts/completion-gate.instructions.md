---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","customizationKind":"instructions","destination":".github/instructions/efforts/completion-gate.instructions.md","plugin":"efforts@copilot-extensions","pluginVersion":"0.1.0-dev20","renderedBytes":1463,"schema":"copilot-extensions.instruction-projection","sourceId":"completion-gate","template":"instructions/completion-gate.instructions.md","templateBytes":939,"templateSha256":"1cea8d0373b8f2184b1e1e578f61b0b446901635216015e77b42c5c5f2a14d15","version":1} -->

# Effort completion fallback

**Fallback policy `[owner: efforts@0.1.0-dev20]`:** In a repository that requires efforts for substantial multi-step work, create or resume the canonical effort with `planning-efforts`. The rightful head must not declare the objective complete until the effort is explicitly Done and every Plan and Validation Plan item is resolved or transferred to a named tracked objective. A completed phase, pull request, handoff, or session is not completion. **Durable journaling licenses relentless driving:** keep selecting and executing the next Plan item — including watching a reviewable gate through to merge, not stopping once it's merely opened — without waiting to be re-prompted. Pause only for an error needing diagnosis, a design crossroads only the operator can decide, a required safety/administrative confirmation, or a handoff boundary when automatic cutover isn't available.
