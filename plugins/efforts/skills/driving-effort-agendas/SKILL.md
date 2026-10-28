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

This is an **opt-in process aide**, owned by efforts. The stateful agenda belongs
to the worktree's continuing agent, not an individual effort or session.
It can draw from several efforts, tasks and assigned asides. Use
`planning-efforts` for canonical effort contracts; this skill supplies the
agent's execution position and work stack, not another completion ledger.

## 1. Adopt a lane

Use this procedure only when the operator or canonical effort explicitly opts
in. Identify the assigned root effort(s), permitted scope and current execution
lane. For a managed worktree, keep its existing participant/slice and claims:
adopting a cursor does not reserve a different participant's work.

Resolve the worktree journal's **worktree-specific state home** through its
owner's published locator or an explicit assignment/adopter setting. Keep
`agenda.json` and `cursor.json` together there, near the journal, without
rewriting its records or guessing an implementation-private path. A shared
journal home needs an owner-allocated compartment for this worktree.
There is no mandatory journal provider: when none is available, choose an
explicit worktree-local home such as `.effort-agenda/<lane>/`, excluded from
publication using the repository's normal ignore convention.

Use [`assets/agenda.example.json`](assets/agenda.example.json) and
[`assets/cursor.example.json`](assets/cursor.example.json) as starting examples.
The mutable agenda is the agent's accepted roster and sequencing, not a file
owned by one effort. Reference canonical efforts/tasks and stable plan headings;
do not copy their completion state. Shared effort-local boards may be reusable
templates, never the active worktree cursor.

Keep the exact state-home/cursor locator in the assignment and handoff, stable
across the worktree's sessions. Independent worktrees and participants use
independent homes. This is neither a repository-global "current effort" nor a
new session's scratch plan.

The [format and worked return](references/agenda-format.md) define the small
manual JSON convention. The examples require adaptation to actual plan
references and exit criteria. No shell expressions or automatic effects are
embedded in the data.

## 2. Answer "what next?" before each work unit

Read the current agenda node, its canonical source and the **top frame** of the cursor.
After a merge, validation outcome, detour return, handoff or context rollover,
refresh them before choosing work. Produce this short answer:

```text
Root goal: <assigned outcome and canonical effort/task reference>
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

Check the child's own effort/task acceptance gate, not merely its PR or worktree
state. Preserve a blocked/failed child on the stack with a concrete next action
or steering need; do not silently pop it.

On successful return, record the child's result, pop it once, restore the saved
parent node/action, and **revalidate against current reality**. Child changes
may invalidate old evidence. Re-observe uncertain effects instead of replaying
them. Increment the parent-progress/detour counters honestly.

An incoming assignment can extend the worktree's roster: record its source,
scope and exit criteria, then explicitly queue it or accept a priority change.
Do not silently discard the existing stack. A task or assigned aside need not
be inflated into an effort; use its stated acceptance gate. Incidental
discoveries stay parked until assigned or justified as bounded prerequisites.

After completing one root, choose the next already-authorized queued root, if
any. New unrelated objectives require assignment, not opportunistic queue
growth. Finish only when the canonical effort/task gates and assigned queue
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
