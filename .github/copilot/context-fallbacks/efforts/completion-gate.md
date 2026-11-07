---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","bodySha256":"1e91d29fb5550458d664f639c90a97626f949125976bb893eeaa6696df3ce675","customizationKind":"instructions","deliveryKind":"body","destination":".github/instructions/efforts/completion-gate.instructions.md","plugin":"efforts@copilot-extensions","pluginVersion":"0.1.21-dev1","renderedBytes":4801,"schema":"copilot-extensions.instruction-projection","sourceId":"completion-gate","template":"instructions/completion-gate.instructions.md","templateBytes":4049,"templateSha256":"358a415f55c02e347f02a3f245057d859cded0a7985e6bd275041bf88cbfc36a","version":1} -->


# Effort completion fallback

**Fallback policy `[owner: efforts@0.1.21-dev1]`:** In repositories requiring efforts for substantial multi-step work, use `planning-efforts` to create/resume the canonical effort. Its rightful head may declare completion only when it is explicitly Done and every Plan/Validation Plan item is resolved or transferred to a named tracked objective. A completed phase, PR, handoff or session is not objective completion. **Only two terminal outcomes are acceptable for a worktree/session bound to an active effort:** the effort is fully Done (every Plan/Validation Plan item resolved or transferred — recommend finalizing the worktree), or a context handoff (the effort stays open for a successor). A merged PR, a completed task, or "nothing actionable remains" is never itself either outcome. **After any effort-linked PR merges, return to the effort before reporting status or ending the turn:** use the planning skill's effort digest to re-read current Plan/Validation Plan state and touched sections, not the whole document tree or just the merged-PR summary; resolve only the items it closed, journal what landed, and name or start the next stretch. **Durable journaling licenses relentless driving:** keep selecting and executing the next Plan item — including watching a reviewable gate through to merge, not stopping once it's merely opened — without waiting to be re-prompted. Diagnose ordinary uncertainty/failures autonomously within scope and bounds; preserve waiting or other authorized work instead of asking for the known goal again. Involve the Operator only for an actual human decision, authority/permission or safety gate. Required review, explicit holds, resource limits and handoff boundaries still apply.

Parent reconciliation does not force depth-first order: preserve each program's
owner and gate, but follow a finite delivery queue unless a real dependency or
authorized priority change requires deeper work. A parked note is not transfer.

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

<!-- copilot-guidance-body-end:v1 {"bindingSha256":"3e3e061cc88592c02b794df70de626d9c460c3f7ec35c5b43f4b9e8eac5b9a5c"} -->
