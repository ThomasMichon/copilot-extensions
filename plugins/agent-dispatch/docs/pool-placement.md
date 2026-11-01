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
Hard task `requires` tokens must be explicitly supplied by the selected pool's
capability predicate; an unconstrained filter does not invent worker capabilities.
Exclusions match all supplied capabilities and the execution repo, machine,
environment, pool and worktree selectors, not only the task's requirements.
It selects the first matching instance in published priority order with free
capacity, writes its reservation and retained pool assignment and changes the
task to `CLAIMED`. The assignment contains the actual registration, execution
machine, environment, reservation key, catalog revision and task generation.
No workspace, worker or native identity is invented. Workspace preparation and
startup must follow that commit through the existing allocation/startup owners.

Capacity counts accountable `reserving`, `spawned`, `cold` and `releasing`
reservations, failed native/startup effects and unresolved transport custody,
not caller estimates or task status. Immutable per-reservation pool bindings
retain older attempts even when the task's current assignment advances.
A terminal pool attempt may enter the existing release/retirement path; failure,
rearm and native retirement alone do not prove original startup closure.
Capacity is released only after its startup is completed or fenced and its
native and transport evidence permits that release. An active replay returns
the same reservation with `reserved: false`; even an expired catalog cannot
turn that observation into another spawn. A retained instance waits for its own
slot or reports unsatisfiable routing instead of switching to another pool.
An unaccounted active legacy reservation blocks new placement; the coordinator
does not guess its pool or silently adopt its effects.

An affirmative `POST /tasks/{id}/start` supplies `worker_id`,
`owner_session_id`, `expected_generation` and, when already bound, the
`expected_owner_session_id`. For a placed claim, the queue matches the real
worker machine/worktree, exact recorded native identity and assignment
generation, rejects release/cooling/startup fences and applies the existing
native acquisition authority. Only the successful transition binds the worker
and native owner. Preparation records `execution_machine` independently of
`creating_host`: targeted/reused workspaces retain their real ownership and
creation provenance instead of being relabeled as newly created. Workspace
recording and start both recheck that execution machine and current exclusions
against the retained supplied capabilities. A spawn record, reachability or delivery receipt is not an
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
