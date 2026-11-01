---
name: driving-effort-agendas
description: >
  Follow an explicitly adopted effort agenda: identify the next action, check
  exit criteria, append accepted work to a tail queue, and preserve scoped
  subqueues and saved continuations through planning and prerequisite work.
  Qualify revisits by relevant new context and a viable changed approach,
  not a raw visit count. Use when an operator or effort
  opts into an agenda companion, asks "what do I do next?", or needs to resume
  an agenda cursor or return from a child effort. Supports explicitly parked
  blocked branches and timed/response rechecks, not approval. Does not enable itself for
  ordinary efforts or replace planning-efforts, review, claims, or safety gates.
  Trigger phrases include:
  - 'drive an effort agenda'
  - 'resume the agenda cursor'
  - 'return from a child effort'
  - 'park a blocked agenda branch'
  - 'revisit a deferred agenda node'
---

# Driving Effort Agendas

This is an **opt-in process aide**, owned by efforts. The stateful agenda belongs
to the worktree's continuing agent, not an individual effort or session.
It can draw from several efforts, tasks and assigned asides. Use
`planning-efforts` for canonical effort contracts; this skill supplies the
agent's execution position and work stack, not another completion ledger.

## Execution policy and command availability

Ordinary accepted work joins the tail queue. In-scope decomposition puts an
ordered subqueue ahead of its saved parent continuation; it does not replace
the original root, links or later siblings. Productive revisits need genuinely
new relevant context and a viable changed approach. Unchanged repetition calls
for bounded autonomous investigation/refinement, qualified waiting or other
already-assigned work, not an Operator question solely because of a count.
See [queue and context qualification](references/context-qualified-continuations.md).

Select the execution policy explicitly. Existing cursor versions 1/2 and
request version 1 keep their strict legacy count policy; prose does not relax
their admission. The source context-qualified interface uses cursor 4,
store generation 2 and request version 2. Cursor 3 remains a migration refusal
marker, never executable state. Do not guess fields/flags or relabel a legacy
lane: use explicit fresh adoption through the
[declared source commands and worked fixtures](references/context-qualified-commands.md).
Installed copies may predate that contract. The v1/v2 assets remain strict legacy
examples; the context-qualified fixtures are separate.

## 1. Adopt a lane

Use this procedure only when the operator or canonical effort explicitly opts
in. Identify the assigned root effort(s), permitted scope and current execution
lane. For a managed worktree, keep its existing participant/slice and claims:
adopting a cursor does not reserve a different participant's work.

Resolve the worktree journal's **worktree-specific state home** through its
owner's published locator or an explicit assignment/adopter setting. Keep
the managed store (or legacy `agenda.json` and `cursor.json`) there, near the journal, without
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

Read the current work position, its canonical source and the **top frame** of the cursor.
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
known goal. Involve the Operator only for an actual human decision, permission
or safety gate. Ordinary uncertainty or an unavailable prerequisite first calls
for authorized diagnosis, preparation, waiting or another accepted avenue.
A missing effect approval should first
become bounded decision preparation and a preserved deferred branch, not a
blocking question while useful authorized work remains. Explicit all-work
holds and resource/privacy limits remain stronger.

Work on the action, record the result and answer again. A completed phase,
message, pull request or child is **not** a stopping point while authorized
effort work remains. Keep choosing the next permitted action without waiting
for another user prompt. Required reviews and safety gates still apply.

## 3. Check exits before moving

For each transition, name the node's exit criteria, supporting evidence and
permitted outcome. Evidence may be a measured result or explicitly attributed
judgment; do not pass an opinion off as a successful test. Code/test/review
evidence names the revision or inputs it actually covers.

Preserve every visit and its original evidence. In legacy mode, use only a
declared edge and enforce its finite visit limit, including successful and
no-progress cycles. Do not bypass that guard by rewriting history, identifiers
or mode labels. In an available context-qualified mode, revisiting the same
stable item needs an attributable prior-attempt/context relationship and a
viable changed approach; a lifetime count alone is not an Operator gate.

Record why the new information matters to the next attempt and distinguish
measured inputs from agent judgment. Schema acceptance, a timestamp or a changed
digest cannot prove semantic relevance or permission. Failed validation first
calls for bounded investigation at the current position, not an automatic
retry transition or a claim that the exit passed.

Ordinary investigation within a node remains flexible. Refine trees within
accepted scope with source references, exits, edges and an explicit definition
revision; do not create unrelated objectives or treat a new fingerprint as
new consent. Failed validation may
take a declared retry edge; it does not license unrelated work. Reset means
an explicit agenda transition with a reason, never a Git reset, service
restart, discarded obligation or replay of an uncertain external effect.

## 4. Detour deliberately; otherwise park the discovery

Append new accepted work normally to the tail. If the current item needs more
planning, preserve its continuation and elaborate only the necessary in-scope
subqueue ahead of it. Keep original sibling order, assignment links and the
parent return. A subordinate plan is not an unrelated new root or a resource
claim, and adding context never enlarges accepted authority.

Before calling a child effort/task, establish that it is a necessary
prerequisite or an explicitly assigned priority interruption, is within
authority and fits the lane's depth/detour budget. Do not invent a dependency
for an unrelated assignment; normally queue it unless priority actually changes.
Save the
parent frame **before** changing focus: agenda/node, return node, relevant
evidence and the exact action that should resume.

Push a child frame; never replace the root objective. The legacy defaults are
three active frames and two detours without demonstrated parent progress.
Explicit finite resource/continuity limits stay independent of revisit judgment
in every mode. At a bound, preserve the current position and choose an authorized
bounded replan, wait or other accepted work; do not push again or erase history.
Ask the Operator only when a real decision or permission change is required.
A safety or approval gate is not bypassed by calling it a child.

For useful but nonblocking serendipity, record a short parked item and its
appropriate tracked reference under the normal issue/effort rules, then
continue the active node. Do not turn every discovery into a child or invent
permission to create issues. Parallel delegation is a dependency, not a
blocking stack frame.

## 5. Return and continue

Check the child's own effort/task acceptance gate, not merely its PR or worktree
state. Preserve a blocked/failed child on the active stack, or explicitly park
its complete chain, original return, evidence and budgets in the version-2
deferred frontier before selecting another permitted avenue. Parking is not a
successful return, source completion or permission to reset limits. Use
[the deferred-branch procedure](references/deferred-branches.md) for movement,
decision packets, actual attempt/revisit times and due-or-response rechecks.

On successful return, record the child's result, pop it once, restore the saved
parent node/action, and **revalidate against current reality**. Carry the child's
relevant result into the parent context, explain any changed approach, and retain
old/shared visits and evidence. A child result does not automatically establish
progress or reopen permission. Re-observe uncertain effects instead of replaying
them. Preserve counters honestly; cosmetic metadata is not a progress reset.

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

Carry the cursor path, active/deferred agenda identities, root/return state,
decision-packet references, current attempted/revisit times and immediate
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

## Managed command path (preferred for adopted writable homes)

Use the payload's [scripts/agenda_cli.py](scripts/agenda_cli.py) through an
existing Python interpreter:

```text
python <resolved-efforts-payload>/skills/driving-effort-agendas/scripts/agenda_cli.py next --state-home <explicit-lane-home>
```

This exact payload invocation is available to CLI/App/ACP sessions only where
their tool host supports process execution and Python; it is not a global
binstub or an inferred host feature. No sibling plugin or installed runtime
is needed. Use `read`, `next`, `check`, `history`, `init`, `update` and `publish`
as described in the [managed command contract](references/managed-agenda.md).
Prefer semantic `update` submissions to hand-editing JSON: require a current
expected revision, stable operation ID and criterion/evidence references.
Transactions preserve roots, returns, all original evidence and finite limits.
Never treat caller-asserted actor/session metadata as authentication.
For a fresh context-qualified lane use `init --mode context-qualified` with
request version 2 and all four explicit resource caps. Updates use the persisted
mode and exact request-2 envelope. Discover opaque frame/item/attempt/context IDs
through rich reads and immutable records, not the coarse Snapshot alone.
See the [new-mode command contract](references/context-qualified-commands.md).

An existing manual home is adopted only through an explicit `init` while the
lane is quiescent. Board files stay unchanged; original cursor bytes are durably
preserved before its old path receives an unsupported-version refusal marker.
This fences already-installed legacy inspectors too. Interrupted adoption may
leave a fenced, unavailable lane; retry the saved initialization request using
the [recovery contract](references/managed-agenda.md#interrupted-adoption-recovery),
never restore stale JSON as live state. Missing historical entry timing remains unknown.
Do not migrate another active agent automatically. After import, read the
SQLite authority; old `agenda.py` refuses managed markers instead of reporting
stale cursor files. Keep the managed store's explicit locator and revision in
handoffs and reread after every transition.

Optional trusted publisher argv receives only a bounded committed observation.
Persisted success and pending/not-configured/delivered notification are distinct;
delivery failure calls for `publish`, not replaying a mutation. Manager/head/
generation fencing and actual presentation remain the adapter's responsibility.
No agenda command executes work, proves external approval, starts a service or
changes the canonical completion gate.

## Legacy read-only helper and recovery

The payload's [scripts/agenda.py](scripts/agenda.py) runs through an existing
Python 3 interpreter; it needs no dependencies, installer or daemon.
Invoke that resolved script with `next --state-home <journal-home>` to print the
recorded root, stack, next action, exit criteria and declared outcomes.
`check --state-home <journal-home>` performs the same structural inspection.
Add `--json` for structured output, `--expected-revision <n>` to reject a changed
snapshot, or `--worktree <identity>` to reject the wrong lane.
Version-2 cursors preserve deferred branches; `--now <timezone-aware ISO time>`
controls reproducible due inspection, not a scheduled wake or approval.
The report lists due rechecks and the next revisit; a distinct actual response
can make reinspection eligible early. Neither automatically restores a stack
or permits its saved effect. Legacy version-1 cursors remain supported.
JSON output is an inspection report, not replacement cursor state; never
redirect it over `cursor.json`.

The state home is explicit: use the directory already chosen beside the
worktree journal. The helper does not discover private tracking paths, create
state, execute graph data, mutate a cursor, pop a frame or verify external
completion. Code 0 means the inspected active/next snapshot is structurally
consistent, **not** that exit criteria or source completion passed. Code 2 is a
diagnostic requiring correction/reconciliation; source files remain unchanged.

For unmanaged homes only, manual JSON editing is cooperative and single-writer: it does not provide
atomic updates, crash transactions or stale-write enforcement. Read the latest
cursor before editing, preserve a pending return until reconciled, and do not
let parallel writers share it. This procedure does not prevent arbitrary
off-agenda tool use or originate agent turns. The helper checks a snapshot, not
the safety of a later write or the authority of its caller.

## References

- [Format, movement and worked nested return](references/agenda-format.md)
- [Agenda example](assets/agenda.example.json)
- [Lane cursor example](assets/cursor.example.json)
- [Optional read-only next/check helper](scripts/agenda.py)
- [Managed CLI and transactional command contract](references/managed-agenda.md)
- [Queue, continuations and context-qualified revisits](references/context-qualified-continuations.md)
- [Context-qualified source CLI and worked fixtures](references/context-qualified-commands.md)
- [Deferred branches, decision packets and revisit points](references/deferred-branches.md)
- [Deferred board example](assets/agenda.deferred.example.json)
- [Version-2 deferred cursor example](assets/cursor.deferred.example.json)
