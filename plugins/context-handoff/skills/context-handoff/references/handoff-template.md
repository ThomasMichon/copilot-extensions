## Effort-Backed Session Continuation

Use this compact shape when the current worktree has a valid open
`active_effort` binding. The effort README already owns the durable request,
intent, plan, validation plan, coordination, and journal; link it instead of
copying those sections into the baton.

### Active Effort
- **Path:** `<repository-relative effort README>`
- **Participant:** `<bound participant>`
- **Current slice:** `<bound slice>`

### Next Slice
<The immediate next checklist item or already-authorized phase. A completed
phase or pull request is progress, not the effort completion gate.>

### Immediate Session Delta
- **Completed since the effort journal:** <only material facts not yet durable>
- **In flight:** <uncommitted edits / active command / pending review>
- **Blockers or decisions:** <only unresolved material blockers or decisions>
- **Gotchas / failed approaches:** <session-local facts not yet in the Journal>
- **Required confirmations:** <safety, approval, or administrative gates; "none"
  when none>

### Completion Gates
- **Current handoff:** <Complete the deferred handoff task only when this relay
  leg reaches its stated goal, or after another effort-backed baton is stored.>
- **Effort / worktree:** The effort remains responsible until `Status: Done`,
  every Plan and Validation Plan checkbox is resolved, deferred/blocked work is
  checked only as `Deferred to \`<tracked objective>\`: ...` or
  `Blocked; transferred to \`<tracked objective>\`: ...`, required pull requests
  are merged, and the effort is archived.

### Re-Handoff Instructions
<If context pressure returns first, preserve the same Active Effort pointer and
update only Next Slice plus Immediate Session Delta.>

---

## Standalone Session Continuation

Use this full shape when the repository has not adopted efforts, the worktree
has no valid open binding, or the objective legitimately has no effort.

### Original Request
<The user's original ask — preserve the session's core topic>

### Continuing Objective
<The parent objective that survives this session boundary. Name the governing
effort, issue, or other source of truth; preserve explicit user scope boundaries.
A completed phase is progress, not a replacement for this objective.>

### Direction & Motivation
<Where the work is heading and WHY — the rationale behind the approach and
key decisions, so they aren't re-litigated.>

### Progress
- [x] Completed items (with key file paths)
- [ ] In-progress and remaining items across the continuing objective

### Successor Work Roster
1. <Immediate next action — begin here after consuming the handoff>
2. <Additional slice or phase already authorized by the original request>
3. <Continue listing known actionable work; do not stop at the latest milestone>

### Completion Gates
- **Current handoff:** <Complete the deferred handoff task only when this relay
  leg reaches its stated goal, or after a successor handoff carrying the same
  objective and remaining roster is durably stored.>
- **Parent objective / worktree:** <The true end state. A consumed handoff,
  completed phase, or merged PR is not sufficient while actionable work remains.>

### Re-Handoff Instructions
<If context pressure returns before the parent objective is complete, create
another handoff with this same objective, updated progress, and the remaining
ordered roster. Do not wait for the user to ask again.>

### Gotchas
<Approaches that failed, workarounds discovered, non-obvious context>
