---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"614b8d99c352da5c2e5a72685b94ff76e968ec299c2e34e5e6000f6475053dcd","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/efforts/completion-gate.instructions.md","plugin":"efforts@copilot-extensions","pluginVersion":"0.1.14-dev1","renderedBytes":4540,"schema":"copilot-extensions.instruction-projection","sourceId":"completion-gate","template":"instructions/completion-gate.instructions.md","templateBytes":3788,"templateSha256":"782310de142af70541535d4322dd2798474629d72c2f2c3e291523b11f880539","version":1} -->


# Effort completion fallback

**Fallback policy `[owner: efforts@0.1.14-dev1]`:** In a repository that requires efforts for substantial multi-step work, create or resume the canonical effort with `planning-efforts`. The rightful head must not declare the objective complete until the effort is explicitly Done and every Plan and Validation Plan item is resolved or transferred to a named tracked objective. A completed phase, pull request, handoff, or session is not completion. **Only two terminal outcomes are acceptable for a worktree/session bound to an active effort:** the effort is fully Done (every Plan/Validation Plan item resolved or transferred — recommend finalizing the worktree), or a context handoff (the effort stays open for a successor). A merged PR, a completed task, or "nothing actionable remains" is never itself either outcome. **After any PR tied to an effort merges, circle back to the effort before reporting status or ending the turn:** re-read the effort's own README (never rely on a merged-PR summary alone), resolve the Plan/Validation Plan items that PR actually closed, journal what landed, and name or start the next stretch. **Durable journaling licenses relentless driving:** keep selecting and executing the next Plan item — including watching a reviewable gate through to merge, not stopping once it's merely opened — without waiting to be re-prompted. Diagnose ordinary uncertainty/failures autonomously within scope and bounds; preserve waiting or other authorized work instead of asking for the known goal again. Involve the Operator only for an actual human decision, authority/permission or safety gate. Required review, explicit holds, resource limits and handoff boundaries still apply.

**Optional agenda:** When the operator or canonical effort explicitly adopts an agenda companion, use `driving-effort-agendas`. Before selecting a work unit and after validation, merge, detour return or resumption, read the lane cursor and answer: root goal, active work stack, next action, exit criteria and permitted next move. A phase or child ending means refresh and continue, not wait for another prompt. Ordinary efforts remain unchanged without opt-in; a cursor never replaces the canonical effort's completion gate or existing safety, review and ownership rules.

Append accepted work normally to the tail; scoped subqueues preserve original
roots, sibling order and saved parent continuations. Productive revisits require
relevant new context and a viable changed approach, with measured evidence and
agent judgment distinguished. New identifiers, timestamps or freshness digests
do not prove progress or approval. Unchanged repetition calls for bounded
investigation/refinement, waiting or other assigned work, not Operator steering
from a count alone. Preserve history and all independent resource/authority caps.
Legacy cursor 1/2 and request 1 keep strict count admission; context-qualified
cursor 4/store generation 2/request 2 require actual declared source support,
not a relabelled legacy lane or the version-3 refusal marker.

For adopted writable agendas, prefer that skill's payload `scripts/agenda_cli.py`
semantic updates with expected revision and stable operation ID over hand-editing.
Use managed reads after explicit initialization/import; legacy inspection refuses
managed state rather than displaying stale JSON. Durable acceptance is distinct
from optional delivery. This is a Python payload invocation only where the tool
host supports processes/Python, not a global command or automatic migration.
Explicit legacy-lane adoption requires quiescence and the skill's durable
old-reader fence/recovery procedure; a DB marker alone cannot stop an installed
older inspector from reading stale JSON.

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"ef14e3059d570ae0954bf6947c50c049a8ff95e4d956e4e0f096463fbae85823"} -->
