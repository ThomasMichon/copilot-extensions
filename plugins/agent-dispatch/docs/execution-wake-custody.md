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
