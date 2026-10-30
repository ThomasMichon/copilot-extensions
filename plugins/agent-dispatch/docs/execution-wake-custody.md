# Coordinator wake-send custody

Back to [execution handoff](execution-handoff.md).

Both coordinator outbox drains mediate their final owner-prompt transport using
the **existing wake row and its leased delivery token**. No spawn reservation,
second outbox or independent retry lifecycle is fabricated for wakes.
This extends the qualified sender cohort; it is not a live handoff command.

## Admission and retained outcomes

In an already qualified producer scope, the drainer resolves canonical
installation governance and enrolls this coordinator's own process using the
server-created application incarnation shared with other coordinator senders.
Final admission rechecks the retained wake snapshot, current unexpired delivery
token, task/session/generation fences, producer generation, runtime enrollment
and admission closure inside the existing queue write transaction.
Closure first prevents a new owner prompt. Custody first records an in-flight
participant; a later closure cannot erase that participant or imply quiescence.
Neither transport nor installation resolution holds the queue write lock.

Custody is persisted as fields on `wake_outbox` and `run_waiter_wakes`, retaining
scope, runtime, producer generation and the original delivery-token identity.
Successful Bridge acceptance records `sent`; a false response or exception is
`unknown`, not proof of absence. Settlement failure leaves `pending`.
The existing list/readback surfaces expose the custody state and detail, and
the closure inventory includes the full retained rows.

Pending or unknown custody survives queue reopening, task advancement and lease
expiry. It cannot be reset to pending delivery, reclaimed, overwritten by a new
delivery token or hidden by a newer wake in either outbox for that task.
Reservation retry/rearm checks also refuse unresolved wake custody for the same
task or shared exclusive execution key; new reservations retain the existing
logical-workspace lineage check across task IDs. Repository/dedup identity
matches directly claimed tasks even when no reservation was ever created.
Both selectors defer conflicting task/key/logical-workspace wakes before
leasing them, including conflicts with reservation-bound sends. A blocked oldest
wake cannot monopolize a drainer and starve unrelated due work.
Final reservation-send admission also rechecks unresolved wake custody, so
obtaining a reservation before a wake sends does not bypass the barrier.
Exact reservation-send replay remains observation, not fresh permission.
Delivery-lease time is sampled after acquiring the queue write lock, not before
a potentially blocking connection/lock wait.
If unresolved custody appears after a lease but before final
admission, the drainer returns that unsent wake to pending without spending
its transport retry budget; settlement makes it eligible again.
Reconciliation requires the original retained send identity and actual transport
evidence; an expired lease is not that evidence. There is no public reconciliation
endpoint or automatic timeout-to-absence conversion.
A known `not_sent` outcome remains retained and does not grant another send of
the same wake; a newly authorized natural wake remains subject to normal fences.

## Cleanup is not another owner prompt

An accepted owner prompt is never repeated just because outbox finishing or a
detached-waiter claim release failed. Existing lease/backoff recovery can retry
bookkeeping on the same row while observing its `sent` custody. This cleanup
remains eligible after scope closure without authorizing owner execution.
`CLAIM_RELEASE_SENDER` records still bypass owner-prompt delivery and execution
custody entirely: they only release the existing hibernation claim, including
after task completion or closure. This preserves the existing cleanup exception.

Unqualified legacy wakes retain their original transport retry behavior, with
the final current-token/task/closure check. Historical qualification cannot
silently disappear into that legacy path. A qualified caller missing canonical
provenance or the coordinator application incarnation refuses transport visibly.

## Hosted native owners and CLI owners

The default owner and detached-waiter drainers select delivery from the retained
execution facts, not from the shape of the `machine/worktree` owner address.
For an ordinary hosted body, the current reservation must retain matching Bridge, native,
worktree, execution-project and host identity, with no retirement or release
intent. Incomplete or conflicting hosted facts refuse delivery; they do not
fall back to the represented CLI inbox.
Restricted bodies instead validate their own retained receipt and exact
worker/native/task generation; they do not invent an ordinary worktree or
allocation project. Native-history readers distinguish these receipt formats.

Native delivery uses the standard Bridge command:

```text
agent-bridge --json send <bridge-session> --expected-native-session-id <native-session> --idempotency-key <wake-id> --prompt-file - --no-wait
```

The original prompt goes through stdin. The native session is distinct from the
Bridge handle; the existing outbox ID is the receipt key on every retry. Bridge
owns exact-native admission, stopped-session recovery without recreation, live
HTTP contract negotiation (generation 51) and same-key receipt recovery. Dispatch
does not add an inbox, resume protocol or fallback after an unsupported,
conflicting, busy or unknown send outcome. Busy native sessions currently refuse
delivery; native queueing is not supported. Existing retry budgets and qualified
unknown-custody holds remain independent constraints.

Explicit CLI owners retain their existing queued prompt contract. Local and
remote delivery both use standard Bridge commands; remote commands use the
existing SSH transport rather than a separate message API. Source release is
provider-first: install a Bridge CLI/provider supporting native keyed sends
before adopting this Dispatch consumer. An older CLI rejects unsupported flags,
and a current CLI refuses an unsupported receiving daemon before effects. There
is no safe native-to-CLI downgrade. Legacy CLI-owner sends remain available.

These selectors do not change wake leases, current task/owner/generation checks
or final execution custody. The generation stored with initial agent creation is
not the current task generation: claiming the task advances that generation.
Current outbox fences and retained native history govern continuation.

Physical cooling and wake delivery exclude one another through the existing
database transactions. Outbox selection defers a held cooling operation without
spending an attempt; final wake admission checks it again before taking send
custody. Cooling checks outstanding/current owner wakes before acquiring its
stop token. It cannot stop a body between native acceptance and the owner's
next lifecycle action merely because the task still appears suspended.
A current detached-waiter wake, including one whose delivery exhausted retries,
retains this protection until a natural newer waiter or changed task/owner
incarnation supersedes it. Cleanup-only claim releases do not block cooling.
There is no timeout-to-absence assumption or new delivery/cooling lease.

Release and cleanup use the same outbox exclusion. A release request,
retirement publication or cleanup claim cannot overtake an admitted wake.
A release that wins before wake admission remains authoritative: the selector
and final admission refuse its releasing/retired reservation, and the delivery
adapter revalidates persisted task, target and native facts rather than using
an earlier reservation snapshot. A pending, never-admitted wake alone does not
prevent release: it must have zero delivery attempts and no settled acceptance.
The existing attempt count retains uncertainty after an unqualified send or
interrupted caller; requeueing does not turn it into proof of absence.
Accepted `sent` custody remains acceptance even when lease recovery or waiter
claim-release failure returns the same row to pending.
Current accepted or attempted wakes remain protected until the natural
owner/waiter/task lifecycle advances; unknown send custody still requires its
original reconciliation, including after task completion.

## Remaining gates

Focused tests exercise both actual drainers with synthetic transport and
authority, final-admission races, durable ambiguous outcomes, accepted-delivery
cleanup retries and cross-outbox retry refusal. Existing wake and coordinator
sender tests retain legacy behavior and cleanup-only controls.
They do not establish supervisor enrollment, all other sender mediation,
legacy qualification, adopter-effect settlement, release, running adoption or
acknowledgement/handback. The [active effort](../../../efforts/active/acknowledged-execution-handoff/README.md)
retains those gates. No live DB, installation or runtime transition is operated
by this source-and-test change.
