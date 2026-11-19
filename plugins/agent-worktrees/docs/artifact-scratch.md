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

The pilot admits session-owned worktrees only. System/bridge owners are refused
before allocation; their complete native managed-removal flow is not a qualified
consumer of this pilot. Shared record-retirement protections still preserve
retained artifact metadata as described below.

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
Transient contention waits up to a ten-second monotonic acquisition deadline.
Only lock contention is retried, never allocation/disposition effects; expiry
preserves the existing token and unresolved responsibility.

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
Ordinary contained runs, not just native-application qualification, collect
daemon lifecycle cleanup outcomes. Held outcomes or teardown errors propagate
as unknown process custody before sandbox disposition.

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
The runner supplies its final normalized partition outcome before disposition:
accepted empty filtered/guard groups do not become false failure bundles, while
genuine assertion failures and all-empty filtered failures remain failures.
It checks the aggregate deadline after the group cleanup worker joins and before
owned disposition; overdue cleanup records exit 124 in the retained diagnostic.

The provider publishes settled physical state, then requests `retain` through
the normal exact-subject ledger transaction. Successful consumed output may be
removed immediately and absence-closed. Failed removal remains honestly retained.
Logical finalization accepts safely retained bytes; it does not require deletion.
Retirement keeps the existing ledger while artifact bytes remain retained,
including both records at paired hard-deletion and a failed tombstone save.
Direct record deletion refuses retained artifact metadata. The managed removal
adapter uses record retirement instead of erasing that metadata.

Independent cleanup is the separate Python owner API `preview_cleanup` then
`cleanup`, requiring a finalized or archived creator and exact preview. It rechecks current
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

## Other disposable producers

The opt-in repository command below shares the same allocation context and
process-containment owner. It is source tooling, not an installed Worktrees
command. Use an interpreter that already contains the compatible Worktrees
Python owner; it never installs one.

```text
python tools/run-owned-producer.py --owner <machine/project/worktree> --kind spike -- <absolute-python> -m ast <absolute-source-file>
```

`--kind` is one of `spike`, `build`, `archive-extraction`, `report` or
`source-worker`. It classifies the diagnostic, not permission to act on an
input, service or output. The required `--` separates a literal command argument
vector. Its executable must be absolute. No shell is inserted, and unknown
wrapper options are rejected.

The command's working directory is the allocation's `work` child, separate from
the owner's identity marker and process records. `{scratch}` in a command
argument expands to that working directory; `OWNED_PRODUCER_SCRATCH` supplies
the same value to the child. Other argument bytes, including spaces, empty
arguments and unrelated braces, remain unchanged. The marker is not a shell
expression; do not interpolate it into source code when it can be a separate
path argument.

The existing isolated environment places child temporary and test-state roots
inside the allocation and detaches inherited suite/session/credential state.
There is no host-state or daemon-escape option. This is process containment and
environment redirection, not an OS filesystem/network sandbox. Input handling,
explicit external writes and access to pre-existing services keep their existing
authorization and isolation requirements.

`--timeout` bounds the foreground work and checks for overrun after joined
cleanup; the default is 900 seconds. `--max-processes`, `--max-memory-mb` and
`--max-temp-mb` preserve the existing defaults of 128, 4096 MiB and 2048 MiB.
All bounds must be finite and positive. Ordinary command exit codes are returned;
a POSIX signal maps to `128 + signal`, timeout to 124, and an owner/containment
failure to 125. A parser refusal returns 2 before allocation.

Consume disposable results during the command, for example build and verify
an archive, extract and inspect an admitted input, or generate and print a
transient report. Successful work closes its exact consumed allocation.
Known failure keeps only the configured bounded summary under diagnostic schema
`copilot-extensions.owned-producer-diagnostic/v1`, not arbitrary files or child
output. Unknown descendant cleanup retains the complete payload and open
responsibility. Partial physical removal is reported as retained; command
success is not a claim that every byte was removed.

The wrapper does not own arbitrary external output paths. A durable report,
published build or user deliverable needs its own admitted destination and
verified disposition. Reusable caches and execution history are not disposable
command output. A `source-worker` may consume a transient qualification image;
an ongoing/editable source checkout still belongs in a registered worktree.

The selected real consumer packet is `tests/test_owned_producer.py`: it runs
the standard-library AST analyzer, builds and executes a Python zip application,
extracts and consumes a ZIP input, produces and reads a profiling report, and
consumes a Git-archive qualification image. It also checks actual command
failure/timeout, exact disposition and unchanged cache/sibling data.
`test_artifact_runner_contract.py` covers parsing, ordering and unsupported-owner
refusal. Run both through the canonical runner with `--test-file` and select
`--allow-explicit-tiers` for the T3 native packet. These are selected consumers,
not universal producer adoption or installed rollout.
