# Next source slice: immutable generation cache

Parent contract: [PR Review Workspaces](README.md).

This **agent-recommended contract** resolves the first bounded storage choice
for review before implementation. It does not activate a service or change
backend permission.

## Reuse the current owner

The existing PR service uses JSON replay storage, the shared atomic JSON writer,
operation locks and its existing serving lease/checkpoint. The first generation
cache should reuse those primitives rather than introduce SQLite, a database
service, another watcher or an operation journal.

Keep rebuildable cache artifacts in a dedicated `review-cache` child of the
selected owner root. Do not put generations inside `watch-replay.json`, native
subscriptions or future durable review-submission receipts. The existing
service supplies its namespace/storage identity and current writer ownership.
The operation lock is host-local consistency, not cross-host volume fencing.
Existing placement/storage owners retain that responsibility; this slice never
mounts/moves a volume or infers writer authority from acquiring a lock.
An explicitly selected native private cache uses the caller's existing OS file
authority. Neither a cache descriptor, generation hash nor target label grants
backend access; agent/process context isolation adds no permission ceremony.

## Bounded representation and publication

Use immutable generation-v1 objects already produced by the normalizer. Key
targets by the complete canonical provider, endpoint, project/repository,
effective principal and PR scope, not a short repository alias or PR number.
Validate generation structure/hash and scope before persistence.

Proposed layout under the cache child:

- an owned versioned descriptor binding the existing namespace and configured
  cache limits;
- immutable generation files addressed only by validated hexadecimal IDs;
- a separate current pointer for each complete target-scope hash;
- one existing operation-lock primitive for cache publication.

Provider/source IO and normalization stay outside the publication lock. Inside
it, re-read descriptor/current state, enforce configured byte/generation limits,
compare the caller's expected current ID and publish the immutable object before
atomically replacing that target's pointer. An initial publication explicitly
expects no current ID. A stale writer returns a CAS conflict, not a forced update.
Readers must never receive a pointer to a missing, partial or mismatched object.

Use the existing atomic-write uncertainty contract. Failure before replacement
leaves current state unchanged. A durability failure after replacement is
unconfirmed and requires readback of that same generation/target before retry.
An interrupted write may leave an unreferenced immutable object; it cannot make
that object current or erase a prior complete generation.

An observed new head stays visible in the operation result even if cache
publication fails. Return the observed generation ID, confirmed retained current
ID and explicit publication outcome; never present retained old source as the
newly observed source. A cache lookup retains original section observation times
and explicitly says retained/cached, not freshly observed.

## Limits, recovery and retention

The owning configuration supplies finite cache byte/generation limits; they are
storage bounds, not agent read-count or delegation limits. A full cache refuses
new publication explicitly and preserves the previous confirmed pointer. Do not
silently delete history to make an update fit.

First provisioning is explicit. Missing, corrupt, foreign-version, wrong-scope
or unsafe linked state is an unavailable/corrupt cache, not empty success or
permission to overwrite it. Rebuilding or removing only this dedicated cache
child must leave replay state and all operation receipts untouched.

The first slice has no automatic eviction, garbage collector, source-ref
retirement or mutable draft/submission data. A later retention policy must consult
the actual operation/source owners before dropping protected history. It cannot
turn unknown posting outcomes into new work.

## API and compatibility

Add a source embedding API for explicit provision, retained lookup and CAS
publication. The native/service owner composes the same implementation; no
ordinary installed client silently starts caching or selects another backend.
Advertise support from the executing component only when it implements the
cache contract. An unavailable cache does not redirect an in-flight mutation or
claim pending operation state is absent.

The first source packet exercises private synthetic roots only. Service
integration, authorized installed use and live source/read adoption remain
separate gates. No global installation, service restart or fleet rollout follows
from this storage contract.

## Validation and admission

- [x] Review this contract before cache implementation (#9685).
- [x] Initial/unchanged/changed-head generations retain exact scope, structure,
  completeness and section times; lookups never claim fresh backend evidence.
- [x] Two writers with the same predecessor produce one confirmed publication
  and one conflict, with no half-current generation.
- [x] Pre-replace interruption, post-replace uncertainty/readback, full cache,
  corruption and missing objects retain honest outcomes and prior state.
- [x] Cross-principal/endpoint/target or linked-path state refuses without
  fallback or repair; existing replay/receipt sentinel bytes remain unchanged
  through cache provisioning, writes and explicit cache-only rebuild.
- [ ] Exercise the actual atomic file/lock consumer in the contained source
  integration lane without a backend, daemon or product checkout.
