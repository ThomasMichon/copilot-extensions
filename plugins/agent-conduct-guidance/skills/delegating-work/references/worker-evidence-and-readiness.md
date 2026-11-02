# Worker evidence and readiness

Back to [Delegating work](../SKILL.md).

Use this checkpoint when consuming a worker result or recovering a preparation
step. It clarifies coordinator judgment, not a new runtime protocol, scheduler
or permission grant.

## Accept an answer, not a transport boundary

1. Retain the current request, operation, session and workspace identity through
   observation and reconnect. Delivery, acceptance, tool progress, process
   liveness and semantic completion are different facts.
2. Consume the owning backend's documented final-result contract. A new detail
   reference, an idle label, a tool boundary or null/blank text is not by itself
   an answer. Accept a fresh, complete substantive result, or explicit
   full-fidelity completion evidence for an intentionally empty turn.
3. Keep partial, truncated, pending-input and otherwise incomplete evidence
   qualified. A result may hydrate later under the same reference, so an
   unchanged identifier is not proof that its content cannot become complete.
   Conversely, changed presentation metadata does not make an old result fresh.
4. Correlate errors with the current operation and event order. An earlier
   failure must not defeat a demonstrably later completed retry forever;
   current or uncorrelated failure still takes precedence. Unknown order is
   unknown, not permission to select the convenient outcome.
5. Keep existing inactivity, communication and process-budget checks reachable
   even while result acceptance is blocked. Preserve each clock's documented
   renewal rules: a received target update can establish contact without
   establishing completion; locally rereading a cache is not target activity.
   Do not let an incomplete-result branch silently bypass recovery until a
   much longer outer deadline.

Follow the shared [progress and timeout contract](../../../../../docs/patterns/progress-and-timeouts.md).
Silence or incomplete observation does not authorize cancellation, replacement,
service repair or replay of a mutation whose acceptance is uncertain.

| Observation | Coordinator conclusion |
|-------------|------------------------|
| Tool-only update, no final answer | Progress observed; continue within existing observation budgets |
| Same reference later contains a complete current reply | Evaluate the completed reply, not only the reference identifier |
| Repeated incomplete result with no qualifying new activity | Keep the result incomplete and apply the existing inactivity policy |
| Historical error and later success with uncertain correlation | Retain uncertainty; do not manufacture success or a new failure |

## Reassess functional readiness after bounded recovery

Before repair, name the task's minimum functional checkpoint and the existing
owner of the failing prerequisite. Select a bounded repair attempt and its stop
condition rather than expanding into unrelated setup work.

After that attempt, check what the task actually needs:

- the intended workspace, session/task identity and responsible owner;
- the required capability, companion or adapter, including supported dependency
  and build-hook compatibility where relevant;
- permissions and credential readiness through redacted owner-provided checks;
- a lightweight real-consumer action that demonstrates the needed function.

An installed package, a live process or a readiness flag alone does not prove
that checkpoint. A universal full build is not required when a narrower consumer
check answers the actual question. Distinguish source tests, synthetic controls
and live qualification rather than treating one as another.

If still blocked, report the failed checkpoint and literal cause to its existing
owner, preserve current custody and pending effects, and reassess the plan.
Another venue, wider permissions, a runtime update or a larger repair requires
its own authorized route; failure does not select one automatically.

Record reusable lessons with the shared capability's maintainer. Keep that
follow-up separate from the bounded consumer fix: do not add unrelated upstream
publication or access requirements to the original contributor's completion
contract.
