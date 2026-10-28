# Manual agenda and cursor convention

Companion to [driving-effort-agendas](../SKILL.md). This version 1 convention
uses JSON so a reader can inspect it without installing dependencies.
It is a manual format, not a promise of executable schema enforcement.

## Reviewed definition

An agenda contains `version: 1`, the canonical repository-relative `effort`
README path, an `entry` node and a `nodes` object keyed by stable node IDs.
Each node has:

| Field | Meaning |
|-------|---------|
| `plan_ref` | Stable heading/item reference in the effort, not copied checklist state |
| `action` | Broad work package; the current concrete action lives in the cursor |
| `exit` | Nonempty list of criteria to check before a declared transition |
| `on` | Named outcome -> existing destination node |
| `max_visits` | Positive finite bound, counted on every entry |
| `terminal` | Optional true value: reconcile the canonical gate before finishing |

Use `on: {}` for a terminal node. Reference changes need an explicit
reconciliation decision. The effort, not a terminal node, determines completion.
Do not put shell commands to execute or effectful expressions into this format.

## Lane cursor

The example's fields are deliberately small:

- `revision`: monotonically increasing manual update number.
- `status`: `working`, `waiting`, `steering` or `complete`.
- `max_depth` and `max_detours_without_progress`: positive finite lane budgets.
- `detours_without_progress`: count of detours since demonstrated parent progress.
- `frames`: ordered work stack, **root first, active frame last**.
- `queue`: other already-authorized root agenda paths, in order.
- `parked`: nonblocking discoveries and their tracked references.

A frame records `agenda`, `agenda_revision` (definition identity, not the
whole README including its journal), `node`, `visits`, `evidence`,
`next_action` and `return_to`. The root's `return_to` is null. A child's
`return_to` records the parent's agenda, node and concrete next action.

The agenda path resolves relative to the working repository root. A
cross-repository continuation must additionally identify the owning repository
and its authoritative checkout through the existing repository owner; do not
guess a machine-local path or place a duplicate effort in the host.
A changed/archive path is reconciled explicitly, not treated as a fresh run.

Evidence entries name their criterion, result/reference, relevant inputs and
whether they are `measured` or `judgment`. A reference is not automatically
proof that the criterion passed. Missing/unavailable evidence keeps the exit
open. Avoid secrets and raw private execution artifacts in published records.

## A complete manual move

1. Read the latest cursor and canonical Plan/Validation state.
2. Verify the current exit criteria and record their evidence.
3. Select an existing `on` outcome and check the destination visit bound.
4. Update the active frame's node, visits and next action; increment `revision`.
5. Journal actual plan progress at the normal meaningful boundary.
6. Answer "what next?" and continue. Cursor movement alone never ticks the
   canonical checklist or proves an entire effort is Done.

For a terminal node, check every remaining canonical Plan/Validation item,
record any permitted transfer, and reconcile effort status before setting the
cursor complete or selecting a queued root.

## Worked detour and return

Suppose the active frame is `feature/validate`. Its validation exposes a
prerequisite parser defect:

```text
Before: [feature/validate]
Save:   return_to = feature/validate; next_action = rerun feature acceptance
Call:   [feature/validate, parser-fix/reproduce]
Child:  reproduce -> fix -> validate -> reconcile its effort gate
Return: [feature/validate]; rerun feature acceptance against changed parser
```

Keep the parent frame unchanged while the child is active. The child's
`return_to` is created before focus changes. If a second child is truly
necessary, push a third frame with its own immediate parent return point.
Unwind one frame at a time; a root return is never inferred from an empty
message or a finalized worktree.

If a return is interrupted, inspect the saved child result and current stack.
An already-popped child is not popped again. With manual editing there is no
transaction: keep the pending result visible and reconcile ambiguity before
advancing. A stale writer must reread; there is no automatic conflict merge.

If the defect is not a prerequisite, park it instead and continue the original
feature. If repeated detours produce no parent progress or the depth budget is
exhausted, the next action is a bounded replan/steering decision, not a deeper
child.

## Adoption and recovery

Persist the definition with the reviewed effort; keep the lane cursor local or
in the explicitly selected participant state home. Record the cursor locator
in the assignment/handoff so a fresh session need not guess which lane is active.
Do not create a repository-wide pointer or write into another participant's
cursor.

No cursor exists by default. Opting out leaves ordinary efforts, bindings and
claims intact; explicitly settle or transfer any active child/root
responsibility first. Do not erase the stack to make cleanup appear safe.

See [the skill](../SKILL.md) for the action loop, serendipity policy and
cooperative limitations.
