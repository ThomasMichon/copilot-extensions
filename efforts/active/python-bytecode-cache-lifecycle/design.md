# Shared Python bytecode cache

Back to [the canonical effort](README.md). This is a proposed design, not
implemented behavior or installation authority.

## Decisions

1. Put suite-owned bytecode under the platform's user-cache directory, not
   under an application's runtime/state root.
2. Cache completed immutable environments; do not allocate a disposable
   directory merely to run an unsealed interpreter. Use Python's `-B` fallback.
3. Keep a completed environment's cache while its runtime slot is retained.
   Reuse the runtime owner's construction, retention and deletion decisions;
   do not implement a second service-retirement policy.
4. Run finite incremental collection from normal update/retirement operations.
   No collector service, scheduled task, sessionStart sweep or whole-home scan.
5. Reclaim recognized legacy bytecode with affirmative ownership and non-use
   evidence. Unknown content is not abandoned silently, but neither a name,
   an old timestamp nor an incomplete process scan authorizes its removal.

These are agent-recommended implementation choices for the operator's Goal.
They extend the accepted #11122 foundation. They do not change the parent's
prohibition on automatic deletion during worktree finalization.

## Placement and identity

Use the following user-cache bases:

| Platform | Base |
|---|---|
| Windows | Absolute `LOCALAPPDATA`, otherwise the native Local AppData known folder |
| macOS | `~/Library/Caches` |
| Other POSIX | Absolute `XDG_CACHE_HOME`, otherwise `~/.cache` |

Relative overrides are not resolved against a caller's working directory.
Failure to resolve or prepare an ordinary writable cache is a cache miss:
continue with `-B`, never write into the payload, runtime slot or source tree.
Do not change the user's environment, Python installation or global settings.

Bytecode is executable input: an owner record is not an OS access boundary.
Before selecting a prefix, require current-user ownership and protection from
other unprivileged users' writes or path substitution. Create new owned
directories privately; validate existing directories and relevant ancestors,
using native ownership/mode checks on POSIX and ownership/DACL checks on
Windows. A shared writable override, foreign-owned pre-created namespace or
unverifiable protection takes `-B` without reading that namespace's bytecode.
Windows bypass-traverse semantics also require checking existing bytecode
descendants, not merely their parent: independently granted child access is
effective even below a private directory. Admit only ordinary, privately
owned/protected descendants in a metadata-only pass capped at 256 entries
and 50 ms; exceeding either budget takes `-B` without changing the cache.
An individual filesystem/security call is not preemptible, so the time cap is
checked between calls rather than claiming a hard syscall deadline.
Do not repair someone else's permissions or treat a matching record as trust.
Apply the same protection checks before admitting collector metadata.

The owned subtree is `copilot-extensions/python/v1/`. Its version is a cache
layout version, not a package version or interoperability claim.
Namespace identity includes the normalized absolute installation root, exact
target runtime path and the existing completion/RECORD-derived installed
identity. Different installation cells, target paths and completed content
cannot alias. Native `.pyc` cache tags and optimization suffixes retain Python's
interpreter separation; do not infer a target interpreter's ABI from the caller.
Windows ordinary and extended path spellings have one identity.

The namespace has a small versioned owner record beside, not inside, its
`bytecode` directory. It records the exact runtime root, slot and installed
identity used at allocation. The bytecode subtree contains only the normal
Python source-path mirror. Reuse an existing matching record; refuse conflicting,
linked, oversized, malformed or unsupported records rather than adopting them.
Ownership records are local implementation metadata, never public reports.
Their presence does not qualify a runtime for installation or execution.

Keep the existing source of installed identity canonical in `agent-procutil`.
The shared collector and bootstrap users consume that implementation through
the repository's existing vendoring/build owners. Do not copy an independently
maintained root resolver or cache algorithm into each plugin. Bootstrapping
code stays stdlib-only and is physically self-contained before a venv exists.

## Producer policy and compatibility

The normal installed path keeps bytecode enabled and reuses one namespace per
completed target. Unsealed, mutable, missing or unverifiable environments run
with `-B`; they do not get a fresh UUID on every invocation. Installation
construction already has no-bytecode controls and keeps them.

Preserve the old `cache_arguments(root, runtime=...)` argument-vector contract
for supported older wrappers, including wrappers which extract its prefix
rather than forward every argument. Introduce the new policy through an
explicit module capability and migrate canonical consumers conditionally.
A new wrapper with an older selected runtime must use the documented no-write
fallback, not emit an unsupported new CLI flag or guess from package versions.
An older wrapper with a new runtime retains its supported old behavior until
that wrapper updates; its allocations remain visible to legacy collection.
Do not claim lockstep deployment or remove the legacy helper in this effort.

Audit actual producers, not just the Worktrees binstub:

- `agent-procutil`'s process arguments and standalone import path;
- payload-invocation's PowerShell and POSIX templates and generated shims;
- installer-engine application binstubs and embedded/native launchers;
- Manager's runtime launchers;
- Worktrees' payload hooks, update driver and resident bootstrap;
- Budget Guidance's payload command and service launchers using the helper.

Regenerate declared copies through their canonical tools. Preserve quoting,
`-I` behavior, UTF-8, Windows extended paths, headlessness, process containment,
original-generation bootstrap and runtime retention.

## Collection ownership

There are two distinct operations:

**Runtime retirement.** After canonical version-slot GC actually removes an
unprotected generation, offer its matching cache owner to the cache collector.
Current, last-known-good, explicit keep, construction, retention, durable
reference and live-process barriers are the existing runtime owner's decision.
A superseded selection alone is not retirement. Never stop a process to
make its cache eligible.

**Incremental reconciliation.** A normal successful update also performs a
bounded pass over that installation's recorded cache work, including prior
deferred deletion and interrupted cleanup. A namespace whose exact runtime
still exists is retained even when it is not current. Missing runtime state,
missing ownership and unknown state are different results. A genuinely absent
slot can qualify only under the same runtime-root cleanup/publication custody
that excludes concurrent construction or reselection of that slot. Recheck
owner identity and the slot's absence at mutation time.

Keep the new metadata outside immutable runtime content. Reclamation cannot
rewrite completion markers, fabricate runtime leases or turn a cache record
into a durable runtime-retention reference. Explicit runtime retains protect
their caches naturally because those runtimes have not been deleted.

Collector failures, lock contention, unsupported metadata and retained caches
are separate maintenance outcomes. They cannot veto or roll back an incoming
installation whose readiness and complete selection succeeded.
Do not change automatic/manual update classification or maintenance inhibition.

## Legacy relief during updates

Only examine `cache/python` within installations already addressed by the
normal update plan. Never recursively delete an application's entire `cache`
directory: it can contain downloads, model data and unrelated state.
Discover only recognized `unsealed-*` and `installed-*` bytecode layouts.
The existing explicit prune command remains available, dry-run by default;
its manual age-only opt-in is not an automatic updater policy.

Use two evidence-qualified cleanup paths:

1. **Retired-runtime bytecode.** Python's mirrored source-path subtrees under
   recognized legacy prefixes can contain bytecode from a runtime slot which
   canonical GC has actually removed. Reclaim only the exact corresponding
   subtree, under that slot's cleanup/construction custody, after validating
   its ordinary bytecode-only contents. Do not remove sibling base-interpreter,
   current, rollback or other-runtime subtrees. This path supplies incremental
   disk relief even when whole-namespace process visibility is unavailable.
2. **Whole idle unsealed namespace.** Reuse the accepted pruner's positive
   complete process-reference evidence and age grace, with fresh
   mutation-boundary validation and safe removal. Add native support only
   where completeness and process identity are demonstrable. An inaccessible,
   unsupported or changing census retains the namespace. A process scan is
   not an exclusive writer lock: the implementation must cover delayed launch
   and inherited-prefix races or refuse this path.

Never infer the absence of a retained installed target from an unmatched
digest alone. Never delete non-bytecode entries, linked/reparse objects,
unexpected metadata or retained evidence. Report remaining legacy reasons
explicitly. A successful update is not a promise that every old cache was
reclaimable in that pass.

## Bounded work and convergence

Allocation performs no process enumeration, namespace sweep, network call or
collector subprocess. Retained installed reuse does no per-invocation cache
tree walk on POSIX. Windows performs only the bounded protection admission
described above, not an inventory for collection. Oversized Windows caches
currently trade reuse for safe no-write execution; this limitation must remain
visible until a stronger native boundary can avoid descendant admission.
Cache preparation failure takes the no-write fallback.

The update collector has independent, finite budgets for namespace discovery,
filesystem entries, bytes inspected/deleted and elapsed time. Count skipped,
foreign, retained and unreadable entries too. Do not hide a full recursive
inventory behind a small deletion limit.

Persist restart-safe progress per addressed installation and layout, not one
global cursor which another plugin advances. Resume inside large namespaces;
advance past retained or refused work so it cannot starve later candidates.
Budget exhaustion is a partial result with a continuation, not a completed
sweep. Cursor corruption may restart safe discovery but never grants disposal
authority. Concurrent collectors serialize through the existing owner's
kernel-backed mechanism. Do not unlink stable lock identities.

Implementation must prove bounded discovery as well as deletion and fair
progress across repeated updates. If a portable directory enumeration cannot
resume safely, use a bounded, explicitly reported inventory phase or the
existing lifecycle owner's work queue; do not claim a bounded pass while
silently restarting an unbounded sort/walk on every invocation.

Reports distinguish removed files/bytes, retained reasons, errors, budget
exhaustion and unfinished work. Count actual successful removals, not eligible
estimates. A partially removed tree remains owned and resumable.

## Safety and verification

Reuse the accepted pruner's anchored/no-follow removal and Windows path
identity guards where applicable. Validate ownership, bytecode classification,
runtime protection and path identity at the mutation boundary, not just when
building an inventory. Newly added or replaced entries are retained/refused.
No general-purpose arbitrary-path recursive remover is introduced.

Focused source and contained native tests must cover:

- platform roots, relative overrides, unwritable cache and no-write fallback;
- pre-created foreign-owned or shared-writable cache/ancestor rejection before
  any bytecode load, including an otherwise matching owner record;
- installed reuse and interpreter/build/installation separation;
- repeated unsealed invocation without namespace accumulation;
- current/rollback/retention/construction/live-user protection;
- actual GC-to-cache reclamation and interrupted/repeated update convergence;
- legacy retired-source relief with live sibling/base-interpreter preservation;
- linked, unreadable, malformed, mixed-content and replaced-path refusals;
- concurrent allocation, collection and slot recreation;
- budgets which include discovery and skipped entries, and fair progress past
  retained candidates and through a namespace larger than one batch;
- current/prior wrapper-helper combinations and supported rollback;
- successful incoming selection despite independent cleanup failure.

Use actual parser/launcher and normal update seams in disposable fixtures.
Run native Windows and POSIX controls; do not treat emulated Windows or a
platform skip as native proof. Fresh user-cache creation needs a clean-room
first-touch control, not just an already-initialized repair test.
No fixture pass authorizes deleting a real cache or installing a candidate.

## Architecture reconciliation and delivery

This closes a regenerable-cache lifecycle gap below the installer vision's
standing product intent. It realizes
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
without expanding the installer into a periodic reaper. Incoming readiness and
all applicable current selections remain success; outgoing retirement and
bytecode cleanup remain independent. Preserved sessions, configuration,
credentials and databases are not cache candidates.

Honor the install contract's self-contained vendoring, immutable runtime and
version-skew-safe process boundaries, the patterns' per-owner lifecycle and
portable parity, and the parent's separation of allocation, ownership and
reclamation. No new resident daemon or process-termination policy is needed.
Existing installer/source-qualification PRs retain their owners; reconcile
actual overlapping hunks before implementation.

Land this effort/design first. Then deliver shared policy and canonical
producer migration, followed by retirement/update collection and legacy
relief, with focused tests and runtime documentation in those implementation
PRs. The effort is not Done until all source, validation and review gates are
resolved. A `dev` merge is pending release, and a release is pending an
independently authorized rollout.
