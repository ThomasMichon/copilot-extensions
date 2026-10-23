# Proposed configuration-cache seam — #7940

Back to [Actions Dialog Isolation](README.md).

**Proposal only; not admitted for implementation.** These are agent-recommended
mechanics. Source-plan review remains required. The existing Actions claim does
not silently absorb #7940 or startup ownership.

## Evidence and invariants

The source at the merged #8126 commit
`0327e0e6d30b831b9bd54eeccff62dad4a61d075` still has the same relevant
`production_picker/project_config.py` interface:

- `ConfigCacheSession(ttl)` discards `ttl`; `scope()` is a null context.
- `_cached_project_yaml`, `_cached_repo_yaml` and `_load_machines_yaml`
  use unbounded lifetime memoization. The first two include scalar binding
  and epoch in their keys; the machine cache is keyed only by repository path.
- `clear_caches(reset_binding=False)` advances the scalar epoch and clears
  caches without discarding accepted scalar values.
- `bind_bootstrap` replaces the scalar cache object, isolating old workers.
- `_input_scalar` supplies already-prepared values to the event loop;
  `_scalar` refuses cold event-loop lookup.

Preserve all of those scalar/binding protections. Do not change `_ScalarCache`,
`bind_bootstrap`, `_input_scalar`, `_scalar`, `_project_dir`, `_repo_dir`,
`_machine_name`, their scalar memoizers, or `_install_dir`. Do not change engine
protocols, bootstrap payloads, pending-binding states, setup epochs, telemetry
reporters or authoritative row publication.

## Exact proposed ownership

All paths below are relative to
`worktree-manager/src/worktree_manager/production_picker`.

| Surface | Proposed change | Boundary |
|---------|-----------------|----------|
| `project_config.ConfigCacheSession.__init__`, `.scope` | Retain signatures and yielded session; retain supplied TTL; enter a worker-owned file-snapshot scope | No engine lookup, UI wait or new thread at scope construction |
| `project_config.cached_load_config_scope` | Enter the same implementation with compatibility defaults | No second cache implementation |
| `project_config.clear_caches` | Add one file-cache invalidation notification after existing scalar/epoch behavior | Existing scalar statements, signature and reset behavior unchanged |
| `project_config._read_yaml`, `_cached_project_yaml`, `_cached_repo_yaml` | Route file reads through bounded snapshot entries; distinguish missing, malformed and unreadable input | Preserve public value shapes; no cached error presented as valid empty config |
| `project_config._load_machines_yaml`, `load_machines_yaml`, `machines_yaml_path` | Include binding/project and both candidate paths plus any actual supplier in dependency identity | Preserve merge precedence, alias handling and existing parser/normalizer |
| `project_config.load_config` | Route default-branch registry fallback through the same read scope | No refresh of pinned engine scalar values |
| Focused new cache tests | Fake clock, controlled file replacement, barriers, work counts | No native benchmark or production UI |

`engine_loading._load_config_cache_scope` remains the owner of the session
instance and `60.0` TTL. Its callers already enter the scope on setup/reload and
prewarm workers. No edits there are requested for the cache-storage portion.

## Data and algorithm

1. Replace, rather than layer over, the three indefinitely memoized file stores.
   Use one private store keyed by **scalar binding object identity, captured
   scalar epoch, project, operation and resolved input paths**. Binding identity
   is an opaque key; the file cache never edits it. Add a separate file revision
   and invalidation generation, not another engine/setup epoch.
2. A cache entry holds the last accepted value, dependency paths (including
   absent higher-precedence candidates), content signatures, successful
   validation time, last attempt time, and `ready/missing/error` outcome.
   Internal values are immutable snapshots or defensively copied; callers
   cannot mutate shared state. Bound storage to the current binding and active
   readers; completed obsolete generations cannot accumulate indefinitely.
3. TTL is demand-driven: the next **background read** at or after 60 seconds
   validates the relevant bounded dependency set. It is not a promise of a UI
   update exactly 60 seconds later while idle. A scope pins one revision for a
   logical multi-read pass, so expiry midway cannot mix generations.
4. Before expiry, hits are in-memory only: no stat, Git, engine command, network
   or registry traversal. At expiry, read the known dependency files once;
   compare bytes/content signatures and reparse only changed inputs. Do not
   rely solely on mtime/size: same-size replacement with preserved timestamps
   must still be visible within the TTL. This is bounded YAML-content work,
   never a repository/tree hash or whole-registry materialization.
5. One in-flight validation per logical key/generation: a short metadata lock
   installs a future/event, then the worker performs I/O outside that lock.
   Other background readers join that result. No UI thread waits on its event
   or lock. A cold/expired UI read reports not-ready; it does not perform a
   synchronous reload or silently return stale data as fresh.
6. Publication checks binding identity, captured scalar epoch and file
   invalidation generation again. Manual clear/rebind while blocked discards
   the old completion. Existing scopes may retain their old immutable snapshot
   internally, but cannot publish it into the current generation.
7. Errors are shared by concurrent readers for that validation attempt, with
   explicit outcome and bounded retry timing; they do not reset the successful
   freshness timestamp. Missing optional files are distinct from malformed or
   inaccessible files. Required missing/invalid input uses existing failure
   propagation, not a successful `{}` or authoritative empty roster. Retaining
   old data for presentation requires a visible stale/error state at the
   consumer; without that consumer contract, fail the refresh.
8. `clear_caches()` remains an immediate invalidation, even inside the TTL.
   `reset_binding=True` retains its existing semantics. `ttl=0` compatibility
   means reuse inside one scope only; independent scopes revalidate. Nested
   scopes restore their prior context on exit. Scoped worker access can use
   `ContextVar`, but each existing thread must explicitly enter the shared
   session as it does today; no implicit cross-thread propagation.

## Supplier and template inputs: explicit admission dependencies

The dependency set is not just one `machines.yaml`:

- project config at the accepted `config-dir`;
- both repository config candidates, including a previously absent preferred
  candidate;
- both repository machine-roster candidates, preserving canonical-over-legacy
  merge precedence;
- a selected knowledge-supplier config/anchor and its candidate roster paths
  when fallback is actually used;
- registry files contributing the default-branch or supplier selection.

A changed parent input must recompute its selected dependency set in the same
worker attempt. Supplier switches, newly created preferred files and deletion
of a prior supplier invalidate the derived result. No broad scan is justified
merely because a TTL elapsed.

**Current limitation requiring a decision:** `machines_yaml_path` uses
`harness_state.project_config` plus `harness_state.build_projects` for fallback.
The latter materializes all registered projects and rosters; hiding it behind
a TTL would retain a broad cold path. A complete bounded supplier resolver needs
the existing registry owner to expose or approve a direct selected-entry path
and its input dependencies. Do not copy its selection rules into a new
Manager-owned registry authority. This proposal does not authorize edits to
`harness_state`.

The actual `machine_transport.parse_machines_yaml_file` reads a YAML document
and parses its `machines` mapping; this trace establishes no external template
or include expansion. Therefore do not claim referenced-template invalidation
is solved. If a configuration supplier expands templates, its owner must supply
the expanded dependency list/revision. A template fixture must exercise that
real supplier contract, not invent template syntax in this cache.

## Consumer freshness is a separate admitted seam

A cache-only fix cannot establish that the displayed roster refreshes:
`engine_pivots._machine_key_map` retains `_mkey_map` indefinitely after its first
successful prewarm, and current setup consumers need an existing event/refresh
to request new config.

The proposed cache can satisfy **fresh reads at the next demand**, but full
#7940 closure additionally requires the consumer owner to accept:

- invalidate derived mapping/config views on the existing manual reload and
  successful new file revision, without resetting unrelated selection;
- route renewed demand through existing workers rather than polling or
  creating a service;
- admit completion only for the current setup/source generation, and display
  stale/error/not-ready state when a refresh fails.

Do not edit `engine_pivots`, `engine_runtime` or `engine_loading` under the
Actions-only file ownership. The integration owner must name an executor for
these exact consumer hooks, or retain them as explicit open #7940 work.

## Test matrix and stop conditions

| Case | Deterministic assertion |
|------|-------------------------|
| Warm, unchanged, before TTL | Zero file/stat/engine/subprocess calls; equivalent values |
| TTL boundary | Fake clock at 59.999/60.0; first expired worker revalidates; no wall-clock sleep |
| Atomic/same-metadata replacement | Updated bytes observed at TTL even with same size/mtime; changed input parsed once |
| Concurrent cold/expired access | Barrier-start multiple workers; one read/parse per key and generation, shared outcome |
| Different binding/project, identical paths | No result reuse across binding identity; no cross-source publication |
| Manual invalidation during blocked read | Old completion discarded; new generation wins; unrelated selection retained by owner test |
| Bootstrap rebind during blocked read | Existing scalar epoch/object isolation preserved; no new scalar calls on UI thread |
| Error then repair | Error is explicit, old value not marked current; retry/manual clear accepts repaired input |
| Missing/deleted/preferred file appears | Absence is tracked; precedence/dependency graph changes correctly |
| Supplier switches or changes alone | Derived roster changes without root YAML edit; no all-project discovery |
| Referenced template changes | Pending real supplier dependency contract; cannot mark passed with a synthetic unsupported parser |
| UI reads while worker blocked | No lock wait or I/O; explicit not-ready/stale outcome; Escape remains independent |
| Nested scope/exception/cancellation | Context restored; waiter released; no poisoned in-flight entry |
| Scope expires mid-pass | One coherent revision for the pass; next demand observes expiry |
| Older-engine/fixture compatibility | Scalar fallback and public return shapes retained; `ttl=0` behavior explicit |
| Derived map/manual reload | Owner integration test proves map refreshed and selection preserved; cache unit test is insufficient |

Stop before code if the file-store seam, supplier dependencies or consumer
owner is not approved. Native/menu/input budgets, release and installed
qualification remain open and separate. No benchmarks are requested here.
