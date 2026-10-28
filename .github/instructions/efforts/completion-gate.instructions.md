---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"052ed86c4f5fdb454bd49a9f702081b71dcb56db105e3998aaa5538a464b94f4","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/efforts/completion-gate.instructions.md","plugin":"efforts@copilot-extensions","pluginVersion":"0.1.6-dev1","renderedBytes":3263,"schema":"copilot-extensions.instruction-projection","sourceId":"completion-gate","template":"instructions/completion-gate.instructions.md","templateBytes":2195,"templateSha256":"47770f20212edae41dafa50c33df6092f6a2e121dcaf75c3f3d67d4f905e24ec","version":1} -->


> Prefer the current plugin copy at `.github/instructions/agent-worktrees-generated/copilot-extensions/efforts/completion-gate.local.instructions.md` when present.
> Read it without hash, fingerprint, version, provenance or receipt checks.
> Otherwise use this reviewed fallback. Restart to load later replacements.

# Effort completion fallback

**Fallback policy `[owner: efforts@0.1.6-dev1]`:** In a repository that requires efforts for substantial multi-step work, create or resume the canonical effort with `planning-efforts`. The rightful head must not declare the objective complete until the effort is explicitly Done and every Plan and Validation Plan item is resolved or transferred to a named tracked objective. A completed phase, pull request, handoff, or session is not completion. **Only two terminal outcomes are acceptable for a worktree/session bound to an active effort:** the effort is fully Done (every Plan/Validation Plan item resolved or transferred — recommend finalizing the worktree), or a context handoff (the effort stays open for a successor). A merged PR, a completed task, or "nothing actionable remains" is never itself either outcome. **After any PR tied to an effort merges, circle back to the effort before reporting status or ending the turn:** re-read the effort's own README (never rely on a merged-PR summary alone), resolve the Plan/Validation Plan items that PR actually closed, journal what landed, and name or start the next stretch. **Durable journaling licenses relentless driving:** keep selecting and executing the next Plan item — including watching a reviewable gate through to merge, not stopping once it's merely opened — without waiting to be re-prompted. Pause only for an error needing diagnosis, a design crossroads only the operator can decide, a required safety/administrative confirmation, or a handoff boundary when automatic cutover isn't available.

**Optional agenda:** When the operator or canonical effort explicitly adopts an agenda companion, use `driving-effort-agendas`. Before selecting a work unit and after validation, merge, detour return or resumption, read the lane cursor and answer: root goal, active work stack, next action, exit criteria and permitted next move. A phase or child ending means refresh and continue, not wait for another prompt. Ordinary efforts remain unchanged without opt-in; a cursor never replaces the canonical effort's completion gate or existing safety, review and ownership rules.

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"f565d376f224029a1befe09088239421d6cee38f24fa813765cfd5222eefc3d5"} -->
