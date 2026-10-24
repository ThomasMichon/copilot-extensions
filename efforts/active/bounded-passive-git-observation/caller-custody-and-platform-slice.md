# Caller custody and bounded Windows-first alternative

Back to [the effort](README.md). **Design only; not amended implementation
admission.** This answers the new High `discussion_r4366058208` and the blocking
Medium `discussion_r4365960075` on #8458. The three earlier High fixes remain
accepted source corrections. No caller, shared native API or platform dispatch
is implemented by this document.

## 1. Actual continuation paths at the inspected head

Evidence baseline: `db6229b4247834752ab282d918a1f018498c2b50`.
`A` below means `plugins/agent-worktrees/src/agent_worktrees/`.

| Path | Existing owner/state and concrete escape |
|------|------------------------------------------|
| `A/status_updater_cli.py:486-555` | One `cmd_status_updater` frame owns fixed `path`, `sess`, token, strikes and interval. Its broad render catch replaces the error with an empty segment and the next tick probes again. Gone, superseded, token-loss and strike exits can drop pending custody. |
| `A/__main__.py:3315-3387` | `_StatusSegmentCache` is synchronous, not a worker thread. Its monitor-owned instance has `_entries`, `_aliases`, `_lock`; `get` releases the lock before rendering. It currently retains neither errors nor in-flight admission, so a snapshot callback can race another render and invalidation can induce another probe. |
| `A/__main__.py:3132-3188,4240-4277` | Served-session rendering and resident `snapshot` both use that same segment-cache instance. The served catch discards errors; a later handoff callback can also run for the failed target. Existing project/config/title mutation stays under `project_lock`. |
| `A/status_monitor_cli.py:343-417,552-559` | `cmd_status_monitor` creates the cache once and binds it to resident push, hook snapshots and sweeps. It also owns `worktree_status_runtime` and passes raw assembly into it. This is the concrete common lifetime, not a new service. |
| `A/worktree_status_compute.py:75-122` | Raw bundle assembly catches every Git failure, converts it to an unconfirmed fact and continues. A wrapper outside this call cannot retain cleanup custody unless this typed failure first escapes the broad fact catch. |
| `A/worktree_status_cache.py:329-412,503-527` | Request refresh computes outside its cache lock; `sweep_due` catches and skips per-entry failures. Its `_entries` is durable bundle data, not a place for native handles. The background daemon loop also catches and continues. |
| `A/__main__.py:3276-3294`; `A/worktrees_producer.py:13-31` | Warm-roster gather classifies before publishing. `publication_failed` saves only an error string; the next sweep gathers again. The fallback warm-list loop can classify each demanded shape as well. |
| `A/worktree_status_daemon.py:303-325,348-365`; `A/__main__.py:815-831` | Generic daemon unavailability starts direct caller fallback. Throwing an ordinary server error for *known* pending cleanup can therefore cause another process to probe the same target. |
| `A/status_monitor_cli.py:622-665,835-854,967-989` | Drain counts request work but not retained cleanup. Shutdown closes routes/runtimes; the bounded sweep join does not establish handler completion. Supersession grace currently exits even while busy. |

Thus changing only two `except Exception` clauses is insufficient. The common
owner must retain native custody before any cache/fact/transport layer turns
the failure into data, and all of its ways of starting the affected observation
must consult the same gate.

## 2. Minimal retained-custody carrier and admission

**Proposed carrier:** the already-existing monitor-owned `_StatusSegmentCache`
instance. Add one instance-only cleanup/admission slot table under its existing
`_lock`; do not put handles in `_entries`, SQLite, current-roster rows, a module
global, a new registry, or a new daemon. The standalone updater instead retains
one local slot in its existing frame.

A slot strongly owns the actual `ObservationCleanupError`, original process,
Job, pipe/reader objects, original D and native settlement phase. It is not an
error string or a PID-only reference. Clearing text TTL entries, aliases,
demand, expired bundles or a removed tracking record never clears this slot.

Use the already selected project/`RepoConfig.anchor` and canonical target path
to associate affected work. Config exposes `anchor`; no Git query is needed to
invent a gate key. Capture those inputs where the caller already selects its
project/config/record. Canonical path aliases must consult the same slot;
projects sharing a known canonical anchor are not independent scopes.
When ownership/anchor association is unknown, conservatively gate the affected
project instead of probing Git to discover the key while cleanup is pending.
Independent known projects/anchors remain admissible.

Proposed methods on that existing instance:

```python
begin_observation(project, anchor, path) -> ticket | blocked
retain_cleanup(ticket, error) -> None
finish_observation(ticket) -> None
settle_pending_once() -> pending_count
close_observation_admission() -> None
observation_counts() -> (active_count, pending_count)
```

`begin_observation` atomically claims one scope under `_lock`; it never waits
for a different observer while holding `state_lock`. It returns blocked for
an active/pending same scope, even with force refresh or an expired TTL.
On failure, `retain_cleanup` installs the strong error reference **before**
the active ticket can be released. Only confirmed settlement releases the
slot. Successful ordinary observations release normally. This is per-scope
single-flight/custody, not serialization behind a new host-wide lock.

All native settlement work happens outside the short carrier lock. Existing
project activation, render/title persistence, handoff effects and publication
remain in their current project/admission critical sections. This proposal
does not perform R1b's lock split.

## 3. Next-tick settlement without another deadline allowance

The original operation still fails at D. Do not renew its observation deadline
or start another Git command to diagnose/settle it.

For the Windows-first option below, propose one narrow native capability:

```python
# agent_procutil.JobHandle -- proposed, separately owned native hunk
try_terminate_and_confirm_empty() -> bool
```

It reuses existing Job teardown APIs: issue termination, query that owned Job's
accounting once, and close only after zero active processes. False/native error
retains the same handle. It has no polling loop, new timeout, process census,
PID lookup or child spawn. Existing `terminate_and_wait(timeout)` and its
legacy callers retain their contract. This is a specific additional
shared-owner admission requirement, not an assertion the method exists now.

An error-owned `try_settle` step records native Job emptiness only after that
actual confirmation, then attempts zero-wait root reaping and completion/join
of its existing pipe readers. If readers are not yet finished, retain the
process/pipes and the confirmed-empty phase for the next step. No second actor
may settle the same slot concurrently. A closed handle alone is never proof
that emptiness was confirmed.

The existing monitor/updater tick invokes this step once; pending A does not
consume a new five-second wait and prevent scope B from progressing. It never
counts as a successful original observation. Within original D the synchronous
cleanup path still uses only remaining D; after failure this is settlement of
an outstanding native resource, not a new passing observation.

Without this nonblocking native step, calling the existing positive-timeout
method every tick would introduce another blocking-budget policy. Do not hide
that change in an arbitrary 1ms timeout or the test headroom permission.

## 4. Exact caller hunk graph

All entries below are **future, unadmitted hunks** except this documentation.
They require fresh hunk-level deconfliction and the named owner's admission.

| Existing owner boundary | Minimum proposed change |
|-------------------------|-------------------------|
| Standalone updater frame | Add local strong pending slot; typed cleanup catch before generic catch; next-tick settlement before render; all loop exits run the same custody finalization path. Do not turn token loss/session disappearance into settlement. |
| `_StatusSegmentCache` instance | Add the slot/ticket methods above; `get` checks blocked state before a TTL hit or render; invalidate methods affect cache data only. |
| Monitor hook/sweep wiring | Pass that **same instance** explicitly to bundle/classify/producer callbacks. No module-global injection or ambient current project. The direct-render branch uses it too. |
| `worktree_status_compute.compute` Git fact catch | Re-raise typed cleanup failure before the generic stale-fact catch. Do not continue other observation-bearing fact collectors after an unconfirmed child cleanup. |
| `WorktreeStatusCache` request and sweep | Consult the same owner before refresh/freshness claims; typed capture before skip/rotation. TTL expiry and `_last_attempted` rotation cannot drop native custody or bypass the gate. |
| `_classify_daemon_compute` / `_classify_records_live` | Explicitly carry the owner through per-record assembly; record the exact failing project/anchor/path. No next record/shape in a blocked project gather. |
| `worktrees_producer.publish_active_project` and warm-list fallback | Gate *before* gather; capture typed failure before string-only `publication_failed`; retain last published rows and mark the project stale via the existing API. No new successful revision/observation timestamp. |
| `_monitor_sweep` served and warm callbacks | Use blocked/degraded results, not a swallowed exception followed by another callback for the same scope. Preserve unchanged cfg/title/effect lock placement. |
| `status_monitor_cli` drain/finally | Count active observation tickets and retained cleanup, close new observation admission, stop/join existing producers, and settle before discarding carrier or releasing normal owner completion. |
| Shared native Job owner | The nonblocking method in section 3 plus native-object retention tests; no POSIX backend or `_confirm_group` change. |

The callback wrappers must also cover the worktree-status request path,
background sweep, resident hook `snapshot`, direct segment fallback, classify
IPC, current-roster gather and fallback demanded-list shapes. Separate tables
of pending errors in each of those layers would allow them to bypass each
other; they all borrow the monitor's one instance.

## 5. Truthful stale output and fallback fencing

- Mux/segment: return a static `UNKNOWN / cleanup pending` segment for the
  affected scope, without another render/title write. Do not present an
  unmarked cached FINAL string as current evidence. Leave last-good cache
  bytes/timestamps intact; existing static identity may remain displayed.
- Bundle: return the existing bundle shape with last-known values and
  `confirmed: false`, preserving original observation times. A cold result
  uses existing unknown/null fact wrappers, not invented Git counts.
  Do not persist it as a successful recomputation or renew `computed_at`.
- Classify IPC: return a valid existing unconfirmed/UNKNOWN classification
  shape, not generic daemon fallback. Pending UNKNOWN must bypass finalized
  tracking refinement; `worktree_git_facts.apply_tracking_override` currently
  promotes UNKNOWN for finalized records and is an explicit additional
  correctness hunk/test, not permission to assert completed state.
- Roster: existing `WorktreesRuntime.publication_failed(project, error)` keeps
  previous rows readable with `availability.status=stale`. The error string
  is diagnostic only; the carrier, not `_publication_errors`, owns the Job.
- A *known* cleanup-pending reply must be successful protocol data, not
  `DaemonUnavailable`, because both bundle and classify clients otherwise
  start direct fallback probes. Add a caller regression that proves zero
  fallback invocations. Do not alter F1 current-roster/feed schemas.
- During cleanup-only drain, keep the relevant read surface able to return
  that truthful blocked reply until settlement. Merely closing its admission
  into a generic fallback response would reopen the hole. This is a precise
  drain-owner handoff dependency, not a new public authority.

An uncommunicated transport failure is not knowledge of pending cleanup.
This design does not claim to fence arbitrary external/manual Git commands.
Its managed route/owner-loss behavior must be qualified with the existing
control-plane owners; no new shared marker/PID file is invented.

## 6. Owner loss, retirement and shutdown

The monitor's existing atomic singleton lease and `cmd_status_monitor` frame
own the carrier. Graceful shutdown first closes **observation** admission,
then drains active callbacks/sweep, then settles pending native objects.
Only afterward may normal teardown discard carrier references, close serving
state and release successful ownership. A late callback must still be allowed
to attach its failure to the closing carrier; "closed" must reject starts,
not reject the transfer of already-owned cleanup.

Existing five-second background-thread join and closed-SQLite guard are not
native-child settlement. Add the actual request-handler/sweep and slot counts
to `_drain_busy_reasons` / `_drain_observations`. No longer treat the
`grace_expired` branch as proof of cleanup. At a bounded drain deadline report
blocked cleanup, not success; retain the existing owner in cleanup-only state
without new work rather than dropping the objects. This changes the retirement
exit predicate and therefore needs explicit lifecycle-owner amendment before
implementation; it does **not** increase the constant or the original D.

The standalone updater currently has only a mux PID token, not the monitor's
atomic singleton lease. A minimal safe handoff needs the already-shipped
`SingleInstance` primitive held in that existing frame for its session through
settlement; keep mux token/prefix as display/routing metadata, not the native
cleanup barrier. Do not release on supersession while pending. New compatible
updaters defer on that same lease. Cross-version compatibility and the next
normal reseed must be tested; no new retry scheduler is introduced.

For abrupt owner death, the Windows option relies only on the existing strict,
non-breakaway, non-inherited kill-on-close Job boundary. Native qualification
must prove owner loss terminates child/grandchild scope; a dead PID alone is
not a userland zero-process receipt. A successor must not import old handles
or call an unconfirmed previous observation fresh. If the actual launcher or
replacement path cannot satisfy that boundary, keep its slice blocked.
There is no equivalent proved universal POSIX boundary in the inspected paths.

## 7. Feasible platform decision

**Do not implement the earlier POSIX anchor-group candidate.** The monitor's
Python objects, file lease, cache locks and threads are not an OS caller-death
boundary. Its ordinary on-demand entry is not universally inside a proved
service cgroup. `_caller_owned` supplies lifetime support through an execution
helper, which is not an admitted drop-in for this slice. No narrower proved
outer boundary was established in this bounded trace.

The smallest feasible amended scope is **Windows-first R1a plus the admitted
Windows caller-custody integration**, with ordinary POSIX behavior unchanged:

1. At ordinary POSIX classifier/default/segment entries with no explicit R1a
   request, retain their pre-R1a branch/default probes, existing per-call
   classification timeout and existing foreground behavior. Do not route
   them through a new census or disable classification by returning UNKNOWN
   everywhere.
2. Remove the new POSIX per-probe confirmation path from the candidate
   implementation. An explicit unsupported bounded-observation API request
   must refuse before spawn, not silently fall through to unbounded native
   execution. Ordinary legacy dispatch is an explicit supported path, not
   an opt-in failure fallback.
3. Windows continues to use the same 15-second total D and retained-Job
   semantics, with the High caller gate above. No operational deadline growth.
4. Label the plugin docs/changefile/tests and completion claim Windows-only.
   POSIX initial/default/total-deadline and lifecycle work under #7944 stays
   **open**; no cross-platform parity or whole issue closure is claimed.
5. Review the amended plan and obtain exact implementation admission first.
   Current #8458 still contains the blocked POSIX candidate and is not ready
   to ship simply because the alternative is written down.

Expected new global censuses for the amended Windows path: **zero** per probe
and per observation (owned Job accounting only). Expected added POSIX census:
**zero**, because ordinary POSIX retains its baseline and its requested fix
is still open. This does not claim legacy POSIX descendants are newly safe.

## 8. Collision and validation gates before any future edit

Read-only open-PR file census at the evidence head found candidate overlaps
in `__main__.py` with #6009, #7611, #8447 and #8468, and in
`status_monitor_cli.py` with #8447. This is not a hunk reservation or approval.
Repeat exact live hunk/issue/task checks before editing; main must reconcile
the relevant current owners without this worker contacting existing agents.
No absence of another filename match grants shared native API ownership.

Required pure fail-first cases after amendment admission:

- A fails cleanup; segment, snapshot, forced bundle refresh, background sweep,
  classify IPC, current-roster and fallback list all start zero later Git
  probes for A. Independent known project/anchor B progresses.
- Pending survives invalidate/all, TTL eviction, demand expiry, target removal
  and late callbacks during close. Exactly one actor owns settlement.
- Next tick queries only the retained Job; a failed query keeps the same
  handle, and no observation D is renewed. Confirmed Job emptiness plus
  completed pipes/root is required before clearing the slot.
- Generic error cannot trigger client fallback for known cleanup pending;
  stale answers do not renew observation/revision freshness or become FINAL.
- Supersession/shutdown deadlines report blocked cleanup without pretending
  a cache close/thread join settled it. Compatible updater handoff is fenced.
- Ordinary POSIX source/work-count/behavior tests remain baseline-equivalent;
  Windows-only requests preserve 15/10-second caps and legacy non-opt-in paths.

No implementation or native execution is authorized here. Existing 90 targeted
High-fix results remain historical source evidence, not tests of this proposed
carrier, native method, platform split or lifecycle amendment.
