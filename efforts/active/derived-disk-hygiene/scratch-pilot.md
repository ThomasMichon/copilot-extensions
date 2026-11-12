# Owned test-runner scratch pilot

Parent: [Derived Disk Hygiene](README.md).
Tracker: #10968. This is proposed implementation intent, not shipped behavior.
The source review/merge gate must pass before behavior changes begin.

## Goal and first complete consumer

Prove one real `tools/run-plugin-tests.py` sandbox lifecycle on Windows and Linux:

```text
resolve policy -> reserve responsibility -> begin allocation -> create -> bind
-> run contained tests -> verify process settlement -> dispose or retain safely
-> allow logical finalization -> independently reclaim eligible retained scratch
```

Reusable source-bound virtual environments and package caches retain their
current behavior. Neither the source checkout nor session state is a scratch
fallback. No existing machine data is adopted or deleted by enabling this pilot.

## Architecture and owners

Use the existing configuration-context/layer library for scoped source selection
and provenance, the Worktrees resource ledger for responsibility and retained
discovery, and existing scratch/process owners for physical operations.
The runner remains a caller, not a second claims database or cleanup service.

The current disposable producer API supplies reserve/begin/bind fencing and
interrupted-attempt recovery, but `close` requires verified absence. Its v1
semantics must remain unchanged. The retained-relationship API is not an allocator.
Do not label retained bytes absent, generic-release protected metadata, or bolt
allocation onto the relationship protocol without its own admitted contract.
Expand the existing owner with an explicitly declared artifact lifetime/disposition
before migrating the caller. Missing support must remain visible.

This realizes agent-fabric `resource-accountability` and durable worktree-data
`checkout-retirement-is-not-data-reclamation`: unsettled work blocks; safe retained
bytes alone do not. It does not turn finalize into deletion or a recursive scan.

## Storage policy contract

The storage adapter owns a narrow, versioned data schema rather than adding
arbitrary keys to the launch-preference graft. It consumes owner-admitted
repository, knowledge, execution-machine and repository-machine context.
No directory-basename identity, unbounded ancestor search or executable config.

Required resolution order, low to high:

1. Private per-user OS-temp capability defaults.
2. Selected repository storage policy.
3. Explicitly bound knowledge portable preferences.
4. Knowledge named-machine preferences for the exact executing machine.
5. Existing-owner machine-local storage override.
6. Explicit operation destination, including the supported scratch-root environment.

The adapter must document the selected document names and exact accepted fields
before caller adoption. Portable fields describe class organization and bounded
diagnostic retention; physical machine paths are separately scoped. Do not copy
machine registry identity, knowledge binding, credentials or executable policy
into ordinary portable settings.

An unbound optional knowledge layer is absent; a named unresolved binding,
malformed selected source or invalid explicit root is an error. Read-only
resolution does not allocate directories. Report effective source/provenance
without exposing sensitive configuration values. Changing a default never moves
or adopts an old allocation.

Scratch roots must be absolute, private, owner-admitted and outside checkout,
Git metadata and session-state homes. Use short exclusive per-task directory names
with complete owner/incarnation identity in metadata; do not make socket/path
budgets depend on full worktree IDs in directory names.
Per-child temp settings may point inside the sandbox; global temp/cache settings
remain unchanged. Explicit required destinations are not silently downgraded.

## Allocation identity and interruption

The selected owner's capability declaration must establish the actual allocator/
disposition protocol independently of package versions. Older or ambiguous
writers cannot be treated as compatible because a newer source checkout exists.

Persist responsibility before allocation and bind actual identity before
returning a writable location. Identity includes the admitted root/allocation,
creator worktree incarnation and an opaque allocation nonce; directory identity
must be re-proven before physical operations, not inferred from a pathname.
The existing attempt-specific allocation fence must prevent stale attempts
from completing or replacing newer ones.

Faults before create, after create, before/after bind and during reply delivery
leave exact discoverable responsibility. Unknown results reconcile the same
attempt; they never allocate another sandbox, start unbound work or discharge
custody from an absent path alone. Legacy unmarked data is never adopted.
No permanent second inventory is introduced: provider-owned allocation metadata
and the existing ledger have defined roles, not competing authority.

## Closeout without mandatory deletion

Add a separately negotiated artifact disposition that can prove the allocation
is quiescent and its remaining bytes have a valid retained policy/owner without
claiming they are absent. Finalize may then succeed with that allocation present.
The receipt binds the actual allocation identity and accepted retention semantics.

The existing disposable absence-only close remains strict and supported. An
unknown/unfinished allocation, live or unconfirmed process tree, failed transfer,
missing retention owner or malformed proof remains unsettled. A free-text note,
caller-supplied path, generic state flip or copied receipt cannot settle it.

Settled retained allocations stay discoverable through the existing owner's
retained-resource view, including after checkout retirement. Keep the active
obligation query's meaning unchanged; it may intentionally omit settled edges.
History receipts are not a permanent inventory of completed user deliverables.

Successful runs may remove their exact owned disposable sandbox as output is
consumed, after the existing process cleanup owner confirms settlement. Physical
removal is not a prerequisite for source completion or finalization. A failed
removal leaves an honest retained/partial result with custody preserved.

## Failed runs and diagnostic bounds

Retain only useful diagnostics under a bounded declared policy, not entire
virtual environments, repository copies, archives or extracted datasets.
Initial engineering defaults are at most 16 diagnostic files and 8 MiB total
per run, configurable through the owning schema; these are source-design
recommendations, not a change to any current user configuration.

Use an explicit producer-owned diagnostic allowlist, not a recursive guess at
which files might be valuable. Preserve original failures and label omissions/
truncation; never report complete evidence after dropping bytes. Caller-selected
external reports remain caller-owned and are not overwritten or reclassified.
Sensitive content retains its existing handling rules; bounded size does not
make it safe to publish.

Unknown descendant cleanup is not an ordinary failed-test bundle: retain the
exact allocation and active responsibility until its process owner reconciles it.
Needed long-term evidence is promoted to its appropriate knowledge/user destination
through that destination's existing publication/delivery policy.

## Independent explicit cleanup

Cleanup is a separate previewed pass, initiated by an agent or a user under
applicable scope. Select only finalized, known-owned, no-longer-needed scratch
covered by its disposition/retention policy. Recheck actual owner, incarnation,
allocation identity, current process use and content/retention constraints at
the effect boundary. Unsupported inspection is not absence.

Exclude shared/reusable caches, history, user-delivered content and unknown
legacy files. Do not start a recurring sweep, release protected claims to make
cleanup pass, or infer deletion authority from age or source merge.
Report retained, removed, partial and unknown outcomes separately.

## Ordered implementation and acceptance

- [ ] Adapt the existing configuration layers with a documented storage schema;
      prove precedence, privacy and malformed/unresolved-source behavior.
- [ ] Expand the Worktrees owner provider-first for admitted scratch allocation,
      verified retained disposition and retained discovery; keep old APIs unchanged.
- [ ] Integrate one real run/preparation sandbox path, preserving reusable venvs
      and the process containment/cleanup owner.
- [ ] Bound selected failed-run diagnostics without hiding unknown process cleanup.
- [ ] Prove logical finalize with retained bytes, then independent explicit cleanup
      on disposable fixtures, not on real legacy machine data.
- [ ] Qualify the complete consumer and fault matrix on Windows and Linux,
      publish required exact-source unit/guard evidence and obtain source review.

Focused fault cases include stale/replaced roots and directories, links/reparse
points, unsafe permissions, duplicate/replayed allocation attempts, wrong owner/
incarnation, unavailable writer/provider, callback failure, failed removal and
cleanup races. Cache directories and caller report destinations must remain
byte-preserved by the scratch path.

Keep process-heavy/native qualification in the existing opt-in test lanes, not
unconditional required unit CI. Source tests and acceptance name the actual
revision they exercised.

## Boundaries

This reviewed pilot permits scoped source implementation and disposable-fixture
validation. It grants no installed rollout, archive enablement, user-file migration,
cache eviction, historical-data cleanup, service intervention or new access.
Deployment and any real-data cleanup retain separate existing-owner admission.
Guide-only scaffolding is not the first complete consumer or the effort's Done gate.
