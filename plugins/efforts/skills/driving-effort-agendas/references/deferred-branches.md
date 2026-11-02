# Reversible blocked branches and revisit points

[Driving Effort Agendas](../SKILL.md) owns this optional process. It does not
execute graph data, infer approval, reserve another owner's work or create turns.
The version-2 data examples below are strict legacy fixtures. The separately
versioned [context-qualified procedure](context-qualified-continuations.md)
does not reinterpret them or the version-3 refusal marker as cursor 4.

## Park an effect, not usefulness

When a real approval/resource boundary blocks a branch, first preserve the
affected operation, original evidence and return state. Continue permitted
investigation and build a decision-ready proposal instead of invoking a
blocking question while valuable authorized preparation remains.

Keep a pending decision/packet reference in the canonical effort or task.
It should explain the situation and cause, fresh evidence/uncertainty, options
and ramifications, conditional future projections, a general-class framework
and the exact post-acceptance effects/owners/limits. It is not an approval.

Refine the agenda within the assigned objective: add bounded investigation,
proposal and already-authorized work nodes, with source references/exits/edges.
Update its definition revision and reconcile affected frames explicitly.
Do not invent an unrelated root or dependency, reset budgets, or treat every
discovery as a new implementation mandate.
Missing context calls for bounded diagnosis/refinement or another authorized
avenue before an Operator question. An actual decision or safety/permission gate
remains separate from anti-spin recovery.

## Version 2 cursor

Use cursor `version: 2` for deferred branches; boards retain version 1.
Older version-1-only helpers reject this cursor rather than ignore pending work.
Legacy cursors remain valid. Manual inspection stays available without a helper.

Add `deferred: []` and a positive finite `max_deferred` (helper default 16).
Each unresolved branch has:

| Field | Meaning |
|-------|---------|
| `id` | Unique stable branch reference within this lane |
| `saved_revision` | Original cursor revision before parking; precedes the current revision |
| `frames` | Complete nonempty saved root-first chain, including visits/evidence/next action/parent returns |
| `max_depth`, `max_detours_without_progress`, `detours_without_progress` | Original branch budgets/count, retained independently of another active root |
| `reason` | Actual missing condition; “approval pending” is not proof the operator is physically absent |
| `decision_ref`, `decision_packet_ref` | Canonical pending decision and proposal; references are not acceptance evidence |
| `attempted_at` | Actual attempted qualification/check time, with a timezone |
| `revisit_at` | Explicit later condition-check point, with a timezone; not a permission renewal |
| `recheck_action` | Inspect current conditions/answer before the affected effect, not replay its saved action |
| `last_checked_at` | Optional later actual check; revisit must follow it and it must not precede the attempt |
| `response_ref`, `checked_response_ref` | Optional actual response and last processed reference; a distinct new response makes reinspection eligible early, not an effect approved |

The helper validates every saved chain and its original budgets against current
board definitions, caching each board within one bounded snapshot. It reports
`deferred`, ordered `due_rechecks` and `next_revisit_at`. Each JSON remains
bounded to 128 KiB; no repository crawl, live approval check or state write occurs.

## A deliberate parking transition

1. Read the latest cursor and current canonical scope/conditions.
2. Save the complete affected chain and budgets/counts in `deferred`, with the
   original revision, decision/packet references, actual attempt and revisit.
3. Only after that record is retained, select a declared safe branch or another
   accepted queued root and increment the cursor revision. A blocked child may
   leave the **active** chain only through this explicit parking operation;
   it remains unresolved, not popped as a successful return.
4. Preserve shared-prefix progress when working another branch of the same
   root. Visits/evidence accrued there remain part of the continuing goal.
5. Keep source/tree definitions, parking transitions and decision changes
   attributable. Do not let a second writer replace the saved return.

Revisit and response signals are reinspection, not permission or consent.
Record what the reinspection actually learned and how it changes a viable
approach before claiming a productive revisit. A new timestamp/response label
with unchanged information is not progress or a reason to erase shared visits.
The provided examples show an effect qualified at **14:48**, still pending,
then research while retaining an **18:48** revisit. Four hours is an example,
not a polling default. Never label the operator unavailable from Autopilot or
a guessed presence state.

## Recheck and return

At safe work-unit boundaries, inspect due branches and actual new responses.
Active permitted work is not automatically replaced; select a recheck
deliberately without abandoning its current return point. With no active chain,
the working helper can recommend a due recheck before another queued root.
Waiting/steering still preserves an explicit lane-wide hold.

On a pending recheck, record `last_checked_at`, the processed response reference
and an explicit future revisit, then continue eligible work. Do not send the
same blocking question repeatedly or use a native presence-poll loop by default.
If all useful safe work is exhausted, a decision-ready wait is legitimate.

On actual acceptance, re-read current authority, source definitions, privacy/
resource limits, holds and uncertain outcomes. Reconcile the saved chain with
current shared-prefix visits/evidence and the stricter applicable limits before
restoring its original node/return action. Do not overwrite intervening progress,
reset a pin/use/detour/visit budget or replay an unknown effect. Remove a deferred
entry only on this explicit restoration or a real canonical disposition/transfer;
retain its history and do not pop it twice after interrupted movement.

A complete cursor cannot contain unresolved deferred branches, even with a
terminal active node. The canonical effort/task—not clock, cursor or message—
owns acceptance and completion.
Legacy visit/detour bounds remain strict. A supported context-qualified mode
preserves the same original roots, links, saved continuations and cumulative
evidence while qualifying new attempts; it does not grant fresh resource or
effect approval because more context is available.

## No hidden scheduler or effect authority

`--now <timezone-aware ISO time>` is a reproducible inspection input, not an
alarm or agent wake. The read-only helper does not originate a turn at
`revisit_at`. While work continues, the agent checks at its normal boundaries;
if a parked all-work wait needs a wake, use only an already-authorized native/
owning-host reminder capability, retaining its normal limits and cleanup.
Do not invent a daemon or per-node polling scheduler.
