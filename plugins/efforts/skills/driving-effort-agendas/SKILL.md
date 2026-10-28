---
name: driving-effort-agendas
description: >
  Follow an explicitly adopted effort agenda: identify the next action, check
  exit criteria, advance a coarse work graph, and preserve a bounded work stack
  through prerequisite detours and serendipity. Use when an operator or effort
  opts into an agenda companion, asks "what do I do next?", or needs to resume
  an agenda cursor or return from a child effort. Does not enable itself for
  ordinary efforts or replace planning-efforts, review, claims, or safety gates.
  Trigger phrases include:
  - 'drive an effort agenda'
  - 'resume the agenda cursor'
  - 'return from a child effort'
---

# Driving Effort Agendas

This is an **opt-in process aide**, owned by efforts. It runs without a service,
executor plugin or CLI helper. Use `planning-efforts` for the canonical effort
contract; this skill supplies execution position, not another completion ledger.

## 1. Adopt a lane

Use this procedure only when the operator or canonical effort explicitly opts
in. Identify the assigned root effort(s), permitted scope and current execution
lane. For a managed worktree, keep its existing participant/slice and claims:
adopting a cursor does not reserve a different participant's work.

Create a small reviewed `agenda.json` beside the effort README using
[`assets/agenda.example.json`](assets/agenda.example.json). Reference stable
Plan/Validation headings; do not copy their completion state. Nodes represent
work packages such as implement, validate, review and reconcile, not tool calls.

Keep one mutable cursor **per lane**, with its exact path in the assignment and
handoff. Start from [`assets/cursor.example.json`](assets/cursor.example.json).
A suggested local path is `.effort-agenda/<lane>/cursor.json` inside the working
checkout, excluded from publication using the repository's normal ignore
convention. This is not a repository-global "current effort." Independent
worktrees and participants use independent cursors.

The [format and worked return](references/agenda-format.md) define the small
manual JSON convention. The examples require adaptation to actual plan
references and exit criteria. No shell expressions or automatic effects are
embedded in the data.

## 2. Answer "what next?" before each work unit

Read the canonical effort, current agenda node and **top frame** of the cursor.
After a merge, validation outcome, detour return, handoff or context rollover,
refresh them before choosing work. Produce this short answer:

```text
Root goal: <assigned outcome and canonical effort>
Work stack: <root node -> child node -> active node>
Now: <one concrete action at the active node>
Exit: <criteria not yet met; relevant evidence>
Then: <allowed outcome -> next node, or saved parent return>
```

If the next action is uncertain, make it a bounded investigation that resolves
the uncertainty. Do not invent completion or ask the operator to restate a
known goal. Ask for steering only when an actual decision, permission or
unavailable prerequisite requires it.

Work on the action, record the result and answer again. A completed phase,
message, pull request or child is **not** a stopping point while authorized
effort work remains. Keep choosing the next permitted action without waiting
for another user prompt. Required reviews and safety gates still apply.

## 3. Check exits before moving

For each transition, name the node's exit criteria, supporting evidence and
permitted outcome. Evidence may be a measured result or explicitly attributed
judgment; do not pass an opinion off as a successful test. Code/test/review
evidence names the revision or inputs it actually covers.

Use only a declared edge and check the destination's visit limit **including
successful and no-progress cycles**. Record the cursor revision, node visit and
next action after a completed work unit. A limit requires an explicit
stop/replan/steering outcome, not another loop or silent completion.

Ordinary investigation within a node remains flexible. Failed validation may
take a declared retry edge; it does not license unrelated work. Reset means
an explicit agenda transition with a reason, never a Git reset, service
restart, discarded obligation or replay of an uncertain external effect.

## 4. Detour deliberately; otherwise park the discovery

Before calling a child effort, establish that it is needed for the assigned
goal, is within authority and fits the lane's depth/detour budget. Save the
parent frame **before** changing focus: agenda/node, return node, relevant
evidence and the exact action that should resume.

Push a child frame; never replace the root objective. The default budget is
three active frames and two detours without parent progress. An effort may
choose different finite bounds explicitly. Reaching either bound requires
replanning/steering rather than pushing again. A safety or approval gate is
not bypassed by calling it a child.

For useful but nonblocking serendipity, record a short parked item and its
appropriate tracked reference under the normal issue/effort rules, then
continue the active node. Do not turn every discovery into a child or invent
permission to create issues. Parallel delegation is a dependency, not a
blocking stack frame.

## 5. Return and continue

Check the child's own effort completion gate, not merely its PR/task/worktree
state. Preserve a blocked/failed child on the stack with a concrete next action
or steering need; do not silently pop it.

On successful return, record the child's result, pop it once, restore the saved
parent node/action, and **revalidate against current reality**. Child changes
may invalidate old evidence. Re-observe uncertain effects instead of replaying
them. Increment the parent-progress/detour counters honestly.

After completing one root effort, choose the next already-authorized queued
root, if any. New unrelated objectives require assignment, not opportunistic
queue growth. Finish only when the canonical effort gates and assigned queue
are resolved, or preserve the open stack through a real blocker or handoff.

## Resume, reminders and manual limitations

Carry the cursor path, active agenda identity, root objective and immediate
next action in a compact handoff; canonical detail stays in the effort.
On resumption compare the saved agenda/plan references with current sources.
Journal-only changes need not invalidate execution position. A changed node,
removed reference or conflicting cursor revision requires reconciliation,
never a silent reset.

Existing worktree slice reminders describe binding context, not permission to
discard the adopted top frame. Resolve conflicting instructions through their
actual owner and priority; the cursor never overrides operator steering or
safety. Optional projections can show this same position without introducing
another owner of claims or completion.

Manual JSON editing is cooperative and single-writer: it does not provide
atomic updates, crash transactions or stale-write enforcement. Read the latest
cursor before editing, preserve a pending return until reconciled, and do not
let parallel writers share it. This procedure does not prevent arbitrary
off-agenda tool use or originate agent turns. Optional helpers may make cursor
operations more objective; they must preserve these same authority boundaries.

## References

- [Format, movement and worked nested return](references/agenda-format.md)
- [Agenda example](assets/agenda.example.json)
- [Lane cursor example](assets/cursor.example.json)
