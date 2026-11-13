# Owned test-runner scratch pilot

This is an opt-in source consumer, not installed rollout or automatic collection.
Ordinary test-runner behavior and reusable environments/caches remain unchanged.
Use an interpreter that already contains the compatible Worktrees owner and its
declared dependencies:

```text
python tools/run-plugin-tests.py <plugin> --owned-scratch-owner <machine/project/worktree>
```

The runner negotiates the loaded owner's `scratch-allocation/v1` allocator and
retained-disposition capabilities, resolves the existing owner/coordination
authority, then reads [storage policy](artifact-storage.md). Missing imports,
capabilities, owner authority, malformed configuration or unsafe roots fail
visibly; this option neither bootstraps a runtime nor silently downgrades to
unclaimed scratch. No default setting enables it.

The real `main()` entry admits/creates that private root before the legacy
cache initializer can create it with inherited permissions. The same admission
runs for direct sandbox callers; unsafe existing roots are never repaired.

## Physical and ledger ownership

`artifact_root` admits only absolute local directories outside selected
checkouts, Git metadata, project history and session-state homes. It rejects
links/reparse points, replaced directory identities, foreign ownership and
non-private permissions. POSIX ancestors must not be replaceable by another
user; shared sticky temporary parents are supported. Windows admission reads
the actual pinned handle's protected DACL, allowing only the current user,
SYSTEM and Administrators. Existing permissions are never repaired. Only the
explicit root's final missing component may be created.

`artifact_scratch` reserves an `artifact-owned` producer in the existing ledger,
begins its fenced allocation, exclusively creates a short child, writes its
canonical identity marker and binds the physical identity before returning a
writable location. Full creator, worktree incarnation, allocation epoch, nonce,
root/directory identity and creation time live in metadata, not long path names.
One fixed root operation lock serializes physical operations; it is not an
allocation inventory or a running-process lease.

`worktree-scratch` is a native provider dispatched inside the same owner.
Callbacks resolve the existing authoritative record rather than accepting a
caller-supplied ledger path or provisioning a peer runtime. The ledger remains
the responsibility inventory; the allocation marker proves physical identity.
Neither pathname nor marker alone authorizes deletion.

Interruptions before creation, marker publication, binding or reply leave the
same discoverable reservation. Reusing an operation ID refuses a second
allocation. `recover` can return only an exact never-started allocation.
An active allocation after a lost reply/crash requires process-owner
reconciliation; missing paths, released locks or elapsed time are not proof of
process settlement.

A never-started `allocated` marker proves allocation completion for bind
recovery only, not work settlement for retention. Native scratch handoff is
explicitly refused at both offer and receiving admission until receiving-owner
settlement and callback support exists.

## Disposition and cleanup

Only the integrated containment owner calls `settle_after_cleanup`, after its
descendants and cleanup workers have joined. Unknown descendant cleanup retains
the entire allocation and active ledger responsibility. There is deliberately
no CLI accepting a caller's `work_settled` assertion.

Opt-in environment preparation executes the original preparation function in a
separate worker under that same verified process-tree owner. UV and build
descendants must settle before its scratch can be pruned. Unknown preparation
cleanup propagates the ownership error and preserves the original allocation.

After known cleanup, the provider removes disposable payload without following
links. A failed run keeps only the producer-owned `runner-diagnostic.txt` summary
and identity marker. The summary contains a bounded result tail, exception type
and explicit omission information, not arbitrary logs or recursive file guesses.
The configured byte bound is enforced and truncation recorded; at most one
diagnostic file is retained. External caller-selected reports stay caller-owned.
Incomplete payload pruning remains unsettled, not a purported bounded bundle.
An aggregate failure flag survives eviction from the bounded diagnostic tail.

The provider publishes settled physical state, then requests `retain` through
the normal exact-subject ledger transaction. Successful consumed output may be
removed immediately and absence-closed. Failed removal remains honestly retained.
Logical finalization accepts safely retained bytes; it does not require deletion.

Independent cleanup is the separate Python owner API `preview_cleanup` then
`cleanup`, requiring a finalized creator and exact preview. It rechecks current
owner/incarnation, handoffs, private root, allocation identity, quiescence and
allowlisted diagnostic contents at removal. Unknown close replies reconcile the
same token; no replacement allocation is created. There is no sweep, scheduler,
legacy adoption or cache eviction, and this API does not grant effect authority.

Before removing settled bytes, the provider durably marks the exact allocation
`reclaiming`. Its original diagnostic may then be absent after a partial removal;
undeclared or changed remaining contents still refuse. A failed final directory
removal preserves this progress in the restored marker, allowing an explicit
retry without pretending the allocation disappeared.

## Qualification

`test_artifact_root.py`, `test_artifact_scratch.py` and the existing producer
tests cover root admission, transaction faults, identity changes, bounded
evidence, retained discovery and legacy ABI preservation.
`test_artifact_runner.py` is an explicit T3 filesystem/process lane: select it
with the ordinary runner's `--allow-explicit-tiers`. It executes real contained
pytest success/failure, proves actual finalize with retained bytes, separately
reclaims that fixture and preserves a reusable-cache sentinel. It also proves
unknown process cleanup is not converted into a settled diagnostic bundle.
These fixtures are not a live installed rollout or legacy-data cleanup.
