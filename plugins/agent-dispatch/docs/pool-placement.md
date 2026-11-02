# Atomic pool claim and affirmative start

The coordinator's explicit `POST /pool-placements` operation implements one
allocator-owned claim transaction. It requires the configured control
credential and accepts version `1`, `task_id`, `reserved_by`, `catalog_revision` and an
optional `driver: "cli"` (the only qualified startup driver in this provider).
Other drivers, including ordinary and restricted Bridge, are rejected before
claiming; callers must inspect `supported_drivers` in the executing contract.
It does not accept a preferred pool subset,
winner or available-slot count.

`GET /pool-placements/contract` describes this executing provider's
`pool_placement/v1` support independently of package versions and the
metadata-only pool catalog contract. It explicitly does not advertise automatic
lane migration or the complete startup retry policy. The placement response
carries the same protocol/version.

The queue rechecks due time, holds, producer admission, the complete configured
catalog and its revision under the reservation's `BEGIN IMMEDIATE` transaction.
HTTP placement uses the same in-flight claim/drain gate as direct claims: a
draining provider refuses admission and drain waits for already-admitted writes.
Intrinsic `requires` selectors (`machine:`, `repo:`, `env:`, `pool:` and
`worktree:`) match the actual selected execution identity separately from hard
capabilities. A worktree selector needs a known target, affinity or carried workspace;
placement cannot invent a future workspace to satisfy it. Other requirements
must be explicitly supplied by the selected pool's capability predicate;
an unconstrained filter does not invent worker capabilities.
Exclusions match all supplied capabilities and the execution repo, machine,
environment, pool and worktree selectors, not only the task's requirements.
After resolving carry-forward, cleanup and driver compatibility, selection checks
the actual retained workspace under the same lock. An excluded carried checkout
cannot acquire a claim or slot merely because the task has no explicit target
or affinity. Discarded optional affinity is not routing evidence for the resulting
workspace. It selects the first matching instance in published priority order with free
capacity, writes its reservation and retained pool assignment and changes the
task to `CLAIMED`. The assignment contains the actual registration, execution
machine, environment, reservation key, catalog revision and task generation.
The reservation and assignment response are captured inside that same
transaction, including replay. A concurrent later retry cannot pair an older
reservation with its newer assignment generation. The existing `reserve_spawn`
queue surface retains its two-value result; the explicit pool operation returns
the typed transactional placement.
No workspace, worker or native identity is invented. Workspace preparation and
startup must follow that commit through the existing allocation/startup owners.

Capacity counts accountable `reserving`, `spawned`, `cold` and `releasing`
reservations, failed native/startup effects and unresolved transport custody,
not caller estimates or task status. Immutable per-reservation pool bindings
retain older attempts even when the task's current assignment advances.
Unresolved launch/resume/delivery sends consume the slot of their recorded
reservation, not every predecessor in the same lineage. Wake sends retain
capacity for their actual native/workspace target; missing native identity
keeps matching workspace custody ambiguous. Wake host matching uses the same
strip/lower normalization as workspace validation. Admission still reconciles the
entire task/exclusive lineage before any fresh execution.
A terminal pool attempt enters the existing release/retirement path with
`native_release_pending`, retaining its historical state rather than becoming
another active assignment. Release intent is not exact-absence evidence:
historical capacity stays accountable until its original native is retired.
An identical release replay after qualified retirement preserves that evidence
instead of reasserting pending native retirement. Newly returned workspace/native
effects remain accountable, and replay never clears the reservation's unresolved
transport custody.
A qualified handleless root retirement is distinct from inherited native
retirement: its first returned native remains pending release even when the
workspace was already recorded or failure preceded release. Its empty-native
absence evidence cannot retire a native that had not yet been recorded.
Successors carrying that allocation may publish or retain their own first native:
the history classifier distinguishes qualified empty absence from a retired
native identity. Pending retirement without a known native cannot authorize
publication of a presumed distinct replacement.
Publication and affirmative start refuse a native whose historical retirement
is still pending, without misclassifying that intent as proven absence.
The supervisor observes that native without ending or concluding a successor's
workspace. Historical retirement preserves workspace-cleanup metadata; cleanup,
rearm and allocation replacement remain excluded while native release is pending.
Operator rearm and fresh-conversation authorization also inspect every original
pool-bound attempt before changing retirement markers: failed state or a
no-live-holder attestation cannot replace qualified startup/native/transport
retirement. Unassigned legacy recovery retains its existing behavior.
Fresh sibling reservation cannot overtake an unreleased historical exclusive-key
holder whose native, startup or transport remains accountable, even with spare
pool capacity. Existing active replay remains observation-only; a fresh attempt
of the same task also waits while its historical native release is pending.
Failure, rearm and native retirement alone do not prove original startup closure.
Capacity is released only after its startup is completed or fenced and its
native and transport evidence permits that release. An active replay returns
the same reservation with `reserved: false`; even an expired catalog cannot
turn that observation into another spawn. A retained instance waits for its own
slot or reports unsatisfiable routing instead of switching to another pool.
An unaccounted active legacy reservation blocks new placement; the coordinator
does not guess its pool or silently adopt its effects.

Supervisor capacity and release sweeps apply `native_release_pending` in the
reservation query before the bounded result limit. Cleanup retries retain their
separate `conclusion_state=pending` predicate before that limit, and overlapping
native/cleanup results are deduplicated. Newer completed history cannot hide an
older pending native or cleanup obligation.
Capacity also requests `pool_accountable=true`: a failed pool attempt can
retain native, startup or transport custody before anyone requests release.
That query includes active pool reservations too: a startup-complete CLI body
on a suspended task retains its pool slot independently of the legacy
hibernation/liveness classification, including unresolved waiter wakes.
The query uses the same transaction-local classifier as placement and applies
that classification before the result limit; it does not truncate history to
classify an unresolved identity. These results consume supervisor capacity
until qualified retirement, independently of a liveness probe or task status.
Their union with pending-native results is deduplicated by reservation key;
unassigned legacy attempts retain their existing handling.

The classifier batches acquisition receipts and unresolved sends/wakes within
one database snapshot, retaining per-reservation/native transport attribution.
An identity's own qualified retirement needs no repeated lineage scan.
Unresolved identities reuse classification only for the exact workspace,
task, creation, allocation-origin and exclusive selectors within that unchanged
snapshot. Distinct successor tasks may share retirement classification only
when a snapshot-wide check proves every reservation for each task has the same
non-null creation key: the task predicate then adds no history beyond the
creation predicate, including exclusive-handle matching. Mixed or null creation
history retains the task-specific selector. Placement stops counting once an
instance is full. No cache survives a mutation or another query/transaction.

`GET /spawn-reservations?native_release_pending=true` (or `false`) advertises
the executing filter contract in
`X-Dispatch-Reservation-Query: native-release-pending/v1`.
The updated client requires that response capability and matching result
markers when requesting the filter. An older provider that ignores the new
query is rejected visibly, not interpreted as zero pending capacity; the
capacity-critical supervisor path treats that incompatibility as exhausted
capacity. Deploy the provider expansion before migrating filtered callers.
The separate `pool_accountable=true` expansion advertises
`X-Dispatch-Pool-Accountability: pool-accountable/v1` and marks every returned
row `pool_accountable: true`. Its client independently requires both that
executing-provider response capability and the result markers; absence is a
visible incompatibility and exhausts capacity rather than silently reverting
to pending-only accounting. This expansion likewise deploys provider-first.
Unfiltered legacy requests and responses are unchanged, including rollback to
an older caller; rollback of the provider while the new caller remains active
requires upgrading the provider or retaining the visible capacity hold.

An affirmative `POST /tasks/{id}/start` supplies `worker_id`,
`owner_session_id`, `expected_generation` and, when already bound, the
`expected_owner_session_id`. For a placed claim, the queue matches the real
worker machine/worktree, exact recorded native identity and assignment
generation, rejects release/cooling/startup fences and applies the existing
native acquisition authority. Only the successful transition binds the worker
and native owner. Preparation records `execution_machine` independently of
`creating_host`: targeted/reused workspaces retain their real ownership and
creation provenance instead of being relabeled as newly created. Workspace
preparation and response validation compare execution hosts with the same
strip/lower normalization, while keeping the original recorded host spelling
immutable. Equivalent casing/whitespace is replay, not a different execution
machine; a genuinely different host still refuses preparation.
Workspace recording and start both recheck that execution machine and current exclusions
against the retained supplied capabilities and current intrinsic requirements.
Retained CLI liveness and cleanup use the validated recorded execution machine
even before worker acceptance creates an owner or attachment. Legacy rows without
that field still resolve location from their accepted owner/attachment evidence,
never the observer's host.
Native publication and start consult the shared driver-qualified retirement
history. A distinct replacement native clears only a handleless inherited
retirement marker; it cannot overwrite a retained native identity or revive a
retired predecessor. A late original startup return during release records its
replacement identity without clearing release intent. Capacity still waits for
that replacement's own completed/fenced startup and retirement. History
classification for targeted/reused external workspaces does not grant ownership
to create, recover or dispose them.
CLI acquisition binds its durable Bridge receipt before normal native
publication. The receipt owner therefore applies the same replacement check
before inserting a first receipt and clears a proven inherited marker in that
same transaction. The newly bound native cannot inherit its predecessor's
retirement. Exact Bridge authority, task generation, workspace and immutable
receipt fences still apply; binding or launch alone never performs `STARTED`.
Handleless release sweeps first read the executing provider's
`reservation_native_identity_protocol: reservation-native-identity/v1` and exact driver-qualified
`reservation_native_session_id` and `reservation_native_identity_error` from
the reservation GET. A receipt-backed CLI native uses its recorded host and
the existing completed/fenced-startup plus exact-GONE retirement path; LIVE,
UNKNOWN, unsupported old-provider responses and identity errors retain custody.
Only a positively empty identity may use bounded force-fail, which rechecks
receipts in the state-transition write transaction. Native retirement alone
does not settle unresolved transport custody.
Once a receipt-backed attempt is terminal and has no pending native release,
the receipt remains historical identity evidence, not a reason to retire it
again. Subsequent sweeps use the existing claimed and validated workspace
cleanup path; terminal historical attempts with `native_release_pending`
still require their original native retirement first.
This is an additive expansion of the existing reservation GET; older callers
ignore the extra fields. Roll out the provider first, then the new recovery
caller. Rolling back that provider leaves new callers holding handleless
recovery visibly rather than interpreting missing support as an empty launch;
no legacy field or operation is removed.
Release identity checks use the original reservation's immutable pool binding,
not its successor's current routing. A release cannot overwrite a retained native
handle, including on failed or settled attempts. Unrelated pending cleanup may
discard an optional affinity, but cannot discard an explicit target or a
`worktree:` requirement and commit a claim for a different workspace: placement
refuses before reserving a slot.
The same refusal applies when an ordinary-Bridge-to-CLI driver transition
would discard an explicit target or required retained workspace; optional,
unassigned transitions retain their previous behavior.
A spawn record, reachability or delivery receipt is not an
acceptance. Failed or stale starts leave the claim and slot intact.

## Compatibility and remaining integration

These are explicit source APIs, not automatic enrollment or lane activation.
Ordinary manual/direct claims and unassigned legacy reservations retain their
existing behavior. A pool-assigned task cannot bypass placement with the old
reservation or direct-claim API. Suspended re-embodiment and CI-repair admission
remain with their existing owners; this operation does not adopt either lane.

Complete-source publication, admitted lane migration, bounded startup attempts,
decision nudges, exhaustion/cooldown and suspended recovery must still be
integrated through the existing supervisor before the full pool-owned lifecycle
is qualified. No running adoption or failure root cause follows from unit/API
coverage. The [canonical amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md)
retains those requirements.

See [spawn supervisor](spawn-supervisor.md) for orchestration ownership and
[pool publication](pool-publication.md) for the configured catalog boundary.
