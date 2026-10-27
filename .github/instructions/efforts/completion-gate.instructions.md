---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"dac7fba4c39ab389c953954d4c9341a5017fb35eac04ce34f10f51928f42147a","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/efforts/completion-gate.instructions.md","plugin":"efforts@copilot-extensions","pluginVersion":"0.1.0-dev22","renderedBytes":2635,"schema":"copilot-extensions.instruction-projection","sourceId":"completion-gate","template":"instructions/completion-gate.instructions.md","templateBytes":1623,"templateSha256":"0d098d266f53c7167c5c0a9ad6a579c8115a9b183a87a3cbb92f81118ba92bde","version":1} -->


> If `.github/instructions/efforts/completion-gate.local.instructions.md` is available, compare
> `pluginVersion` and prefer whichever is newer. On a tie,
> compare `templateSha256`: matching means prefer local;
> differing means prefer this checked-in file.

# Effort completion fallback

**Fallback policy `[owner: efforts@0.1.0-dev22]`:** In a repository that requires efforts for substantial multi-step work, create or resume the canonical effort with `planning-efforts`. The rightful head must not declare the objective complete until the effort is explicitly Done and every Plan and Validation Plan item is resolved or transferred to a named tracked objective. A completed phase, pull request, handoff, or session is not completion. **Only two terminal outcomes are acceptable for a worktree/session bound to an active effort:** the effort is fully Done (every Plan/Validation Plan item resolved or transferred — recommend finalizing the worktree), or a context handoff (the effort stays open for a successor). A merged PR, a completed task, or "nothing actionable remains" is never itself either outcome. **After any PR tied to an effort merges, circle back to the effort before reporting status or ending the turn:** re-read the effort's own README (never rely on a merged-PR summary alone), resolve the Plan/Validation Plan items that PR actually closed, journal what landed, and name or start the next stretch. **Durable journaling licenses relentless driving:** keep selecting and executing the next Plan item — including watching a reviewable gate through to merge, not stopping once it's merely opened — without waiting to be re-prompted. Pause only for an error needing diagnosis, a design crossroads only the operator can decide, a required safety/administrative confirmation, or a handoff boundary when automatic cutover isn't available.

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"708c2cb1e282a33b45dc4fc12cdda9f5e2cc484df2e1068d357599ebf11fcec7"} -->
