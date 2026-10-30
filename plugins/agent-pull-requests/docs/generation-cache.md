# Explicit immutable generation cache

`agent_pull_requests.generation_cache.GenerationCache` implements the
[reviewed cache slice](../../../efforts/active/pr-review-workspaces/generation-cache-slice.md).
It uses the existing atomic JSON writer and kernel operation lock. It does not
fetch a provider, issue credentials, start a daemon or store review operations.

## Owner and scope

The embedding owner supplies an absolute canonical owner-specific root, namespace
and finite `max_bytes` / `max_generations`. Artifacts live only in its
`review-cache` child, separate from `watch-replay.json`, subscriptions and
durable review-submission receipts. The descriptor binds namespace, limits and
an optional existing `ReplayServiceIdentity`; these are storage consistency
checks, not backend authorization.

An explicit native private cache uses the caller's normal OS file authority.
Service embedding retains its existing writer lease, placement/volume fencing,
auth/input pipeline and optional current-owner checkpoint. The local operation
lock is not cross-host fencing or a permission grant. No additional issuer,
agent/delegate permission, provenance ceremony or service adoption is created.
The existing service must authorize the requested scope before exporting cached
bytes; a caller-supplied scope/hash never authenticates an account.

## Source embedding API

```python
from agent_pull_requests.generation_cache import GenerationCache

cache = GenerationCache(
    absolute_owner_root, existing_namespace,
    max_bytes=owner_cache_byte_limit,
    max_generations=owner_cache_generation_limit,
    # Service embedding may supply its existing identity/checkpoint here.
)
cache.provision()  # Explicit first creation, never repair/replacement.
result = cache.publish(generation, expected_current=None)
retained = cache.lookup(generation["scope"])
next_result = cache.publish(next_generation, expected_current=retained["current_id"])
```

`GenerationCache.contract()` reports this executing component's schema,
generation versions and operations. It does not claim that a cache was
provisioned, a service activated or a backend authorized. Older components
without this API are unavailable; callers must not guess support from package
version or choose another mutation backend.

`scope` contains the complete provider, endpoint, canonical project/repository,
native effective principal and PR number. Every target has its own scope-hash
pointer. Generation names are validated full hexadecimal content IDs. Current
objects and descriptor versions/types are validated on read; missing/corrupt
current objects never become empty-success lookups. Historical lookup accepts
`generation_id=` and still verifies the selected scope.

## Publication outcomes and source freshness

Normalize/fetch outside the cache lock. `publish` re-reads current state, applies
the exact expected predecessor, validates capacity, durably writes the immutable
object and atomically replaces that target's pointer. Existing objects are
validated and their durability re-acknowledged without changing ID/content.

Publication results identify the observed generation/revision separately from
the cache's confirmed/visible current IDs:

| `status` | Meaning |
|----------|---------|
| `published` | Object and pointer persistence completed normally |
| `conflict` | Expected predecessor differs; nothing force-updated |
| `full` | Configured storage capacity refuses the new publication |
| `failed` | Known pre-replace IO failure; `stage` names object/pointer |
| `unconfirmed` | Replacement happened but durability acknowledgement failed |

`unconfirmed` sets `readback_required`. Read back that same target/generation,
then retry the same immutable object with the actually observed predecessor;
do not assume absence or invent a new operation identity. Actual owner/OS
permission errors propagate unchanged. Checkpoint loss may occur after a local
cache write; it is not rollback. No backend review effect occurs.

Kernel lock contention retains the existing `OperationLockedError`. The caller
may retry under its normal bounded lifetime **with the same expected ID**;
there is no hidden poller or forced-current retry. Once the other writer commits,
that stale predecessor produces a CAS conflict.

Lookups always report `freshness: retained` and preserve original section times.
No lookup counts as a fresh backend observation or review credit. Failed/full/
conflicting publication still reports the newly observed revision; retained old
source must not be presented as that new head.

## Recovery, bounds and isolation

Missing roots do not bootstrap on lookup. Existing/corrupt provisioning, foreign
namespace/service/limits, unsafe linked paths and unexpected artifact names
refuse without overwrite or fallback. Capacities bound cache storage only, not
agent read counts or delegation time.
The byte quota counts committed managed artifacts, not the existing atomic
writer's transient replacement file or lock metadata. The filesystem needs
headroom for one bounded generation/pointer replacement; disk exhaustion is an
explicit IO failure, never permission to discard retained history.

`rebuild()` explicitly clears **valid managed cache state only**. It first
durably writes empty-pointer tombstones, then retires cache objects. Uncertain
pointer clearing stops before object retirement; prior referenced objects remain
available for recovery. A corrupted cache requires the existing owner's explicit
repair/disposal flow, not an automatic reset.

Replay/operation files outside the cache child are never read, rewritten or
deleted. Cache-only rebuild cannot erase a pending/unknown posting receipt,
revive canceled work or grant posting authority. This slice has no automatic
eviction/GC, source-ref retirement, mutable draft ledger or installed service
integration. Unreferenced immutable objects may remain after interrupted writes
and still count against configured capacity.

The contained source tests exercise real atomic files, native lock contention,
CAS conflicts, commit uncertainty, quotas, corruption, junction/symlink refusal
and unchanged replay/receipt sentinel bytes. They are not live backend, service
deployment or installed-owner qualification.
