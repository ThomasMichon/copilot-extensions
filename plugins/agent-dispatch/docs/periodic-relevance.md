# Periodic relevance evaluation

The existing coordinator can periodically reconsider **unbound**, source-backed
finite tasks through its registered companion evaluator. This is not a second
daemon, an elapsed-time abandonment rule or ordinary completion verification.
It uses the [task relevance receiver](task-relevance.md) for every decision.

## Opt in

An active local evaluator registration can add:

```json
{
  "repo": "example/project",
  "evaluator_ref": "source-review",
  "evaluator_spec": {
    "scripts": {"source-review": ["source-review", "evaluate"]},
    "timeout_seconds": 30
  },
  "periodic_relevance": {
    "min_age_seconds": 3600,
    "interval_seconds": 3600,
    "retry_base": 60,
    "max_attempts": 8,
    "batch_size": 25
  }
}
```

Absence of `periodic_relevance` preserves the old evaluator behavior. An empty
object selects these policy defaults. Times must be finite; age may be zero,
interval and retry base must be positive. Attempts range from 1 to 32; batch size
from 1 to 100. Periodic use requires an inline trusted script registry, not
declarative lifecycle rules or a mutable evaluator-spec path.

The serving receiver advertises `periodic_evaluation: true` in its existing
`task_relevance/v1` contract. Normal server construction with control authority
provides a 60-second coordinator cadence and its actual active-route predicate.
Only opted-in registrations are considered. Library `create_app` construction
keeps the cadence disabled unless explicitly supplied with a registrar,
active-owner predicate and control authority. Installing this source does not
migrate a domain adapter or prove running adoption.

The existing registrar merge and relevance admission resolve both direct and
declared sources. Selection requires the unique repo/ref evaluator and its
active local task-authoring command emitter. Emitter source/origin attribution,
concrete/logical holds and replaced-registration holds apply. Manual tasks,
foreign emitters and unsupported/ambiguous ownership are not evaluated.

## Script contract

The existing trusted script runner receives a JSON event on stdin:
`type: "task.reconsider"`, the selected task under `task`, and its generation,
audit revision and registration observations under `relevance_snapshot`.
The same script must continue handling its ordinary lifecycle events.
It must check the authoritative backing object and return:

```json
{
  "decision": "relevance",
  "verdict": "obsolete",
  "reason": "The authoritative backing object is closed",
  "observation_ref": "source-object:revision-2"
}
```

Verdicts are `needed`, `obsolete` or `unknown`. A failed/incomplete source read
must never produce `obsolete`. Legacy `abandon`, `confirm`, `noop`, follow-up
emission, malformed output, nonzero exits and timeouts are not source judgments:
they retain the task and enter bounded retry. A `relevance` judgment is rejected
by generic lifecycle application and submitted-goal verification.

Task age selects eligibility only. The receiver separately decides whether an
obsolete task is never-claimed/resource-free or requires original-owner
closeout. A claim winning during the check invalidates the observation. Work
that was claimed and later yielded still requires owner closeout.
An evaluation already admitted while unbound retains bounded retry custody
if a claim wins: it refreshes the task snapshot and source judgment before
requesting owner closeout. It does not enroll arbitrary bound tasks for polling.
Task and registration revisions fence concurrent changes; domain adapters
remain responsible for authoritative source revisions and reopen/new episodes.
An opaque observation reference is not proof of closure.

## Durable cadence and recovery

Due work, attempts, rotating cursors, delivery leases and saved reports live in
the existing coordinator SQLite queue. A pass visits at most 25 rotating
registrations and checks at most one task per registration. `batch_size` bounds
each registration's candidate scan, not its script concurrency. Persistent
cursors prevent unattributed candidates or later registrations from starvation.
Source commands and registrar discovery run outside queue write locks.

`needed` schedules another inspection after the configured interval.
`unknown` and failures use exponential retry capped by that interval. Attempts
are bounded across restart; exhaustion remains `failed`, with its error visible
through `TaskQueue.relevance_evaluations(task_id)`. The owning library can
explicitly retry that failed registration/generation through
`retry_relevance_evaluation`; this retains an unresolved saved judgment rather
than replacing it with a new source check. These library methods are not new
remote control endpoints or direct database-edit instructions.

Before applying a judgment, the owner persists its complete request identity.
Restart recovers that original request before invoking the script again.
The atomic receiver checks the delivery token/lease together with task and
registration revision fences. Post-commit callback failures recover the accepted
receipt, not a second abandonment/steer. If receipt state cannot be read,
the original report remains retained. New effects respect active route, draining
and installation governance; shutdown cooperatively awaits the in-flight
bounded script and database work.

## Compatibility and qualification

Publish this receiver before opting domain registrations into the new event.
An older receiver may ignore the added registration property; that is not proof
that periodic evaluation works. Check the actual running contract before
activation. Prior evaluator specs, decisions and relevance HTTP forms retain
their baseline behavior. An old script receiving the new event cannot cause
ordinary abandonment through its old `abandon` decision.

Rollback retains queue history and pending reports. A receiver without this
capability cannot claim periodic enforcement. The existing closeout admission
fence must remain supported before resuming obsolete goals. Actual adapter
adoption, reopened-source episodes, original-owner session conclusion and
workspace disposition remain separate acceptance in the
[active effort](../../../efforts/active/task-relevance-reconciliation/README.md).
