# Retiring an ambiguous legacy local PR attempt

This is **not** never-started cancellation, provider closure or merge. It is an
explicit owner-mediated local disposition for an owner-stamped legacy
`creating` / `creation.phase: publishing` reservation that lacks the durable
prepared first-push source. Its original transport outcome remains **unknown**.
Use ordinary publication recovery when prepared-source evidence exists, and
[cancellation](pr-creation-cancellation.md) only for an eligible never-started
attempt. Neither empty provider fields nor a clean checkout establishes eligibility.

## Discover support from the executing runtime

The selected executable's `pr-status <worktree-id> --all --no-live --json`
exposes immutable `pr_id`, `pr_revision` and the retained `creation` evidence.
This receiver-only implementation does not advertise
`creation_reconciliation_contract`: complete historical admission coverage
remains unsupported. Its `legacy_publication_admission` descriptor explicitly
reports `recovery_supported: false`. Do not invoke recovery from this draft.
Package versions, newer payload help and another installation do not establish
support. Missing declaration means unknown support: keep the original attempt.
Old executables strictly reject the new arguments; do not retry an effectful
invocation with guessed legacy syntax.

Existing numbered abandonment and `--creation` cancellation are unchanged.
This provider-first expansion does not activate a caller, update an installation
or authorize a live recovery. No old syntax is retired. Compatible readers
preserve the additive string receipt through the existing serialization owner.
A rollback that loses it loses reconciliation proof; retain the record and
select a capable runtime through an independently authorized update, rather
than fabricate provider state or force finalization.

## Prerequisites and scope

Only the unchanged owning worktree incarnation, frozen attribution, immutable
attempt and same-repository GitHub destination are supported in version 1.
The owner may be `active`, `complete` (including legacy `completed`) or `pushed`; closing and finalized owners
are not admitted. Both loaded snapshots must pass the native execution-space
mutation guard.
The checkout must be the exact recorded Git root, share the configured anchor's
common Git directory, and be clean and on its recorded branch. Untracked files
and dirty submodules cannot be hidden by Git configuration. Ancestry checks ignore
replacement objects. Its source branch and
**every existing tracked local PR branch** must be ancestors of the configured
remote default branch. The command never resets, rebases, deletes or archives
source. That tracking ref must match the freshly observed remote tip at the
unchanged destination; unavailable/stale observations require normal sync, not
an inferred landing. Tree equality and an operator's assertion of semantic equivalence are
not substitutes for that Git ancestry check.

If a historical planning/source ref is superseded but not an ancestor, stop.
Preserve useful history and evidence, obtain explicit authority for that exact
local ref reconciliation, and use its existing Git owner before reconsidering
this command. Retirement does not supply that authority or perform the change.

The command requires a successful all-state exact-head provider observation:

* **No PR:** the result must be explicitly empty and the remote head absent.
  This describes the current observation, **not** a historical never-published
  fact or permission to replay an unknown effect.
* **Duplicate reservation:** `--merged-pr <number>` must name a separate proper,
  already tracked merged association for the exact same branch, repository and
  provider. The provider must return exactly that one PR with its exact merged
  head and same-repository source. The remote ref must be absent or equal that
  merged head. The original reservation is not reassigned to that PR.

An open PR, multiple results, unsupported provider, incomplete/failed reads,
changed remote/endpoint, active publisher, prepared-source attempt or authority
drift refuses or waits on the existing bounded locks. Missing owner stamps
cannot be reconstructed from branch names or source equality.

## Confirm one exact attempt

First request the alternatives-first warning; this does not retire anything:

```text
agent-worktrees pr-abandon <worktree-id> --reconcile-creation <pr-id> --reason "<specific reason>"
```

Only after explicit approval for that attempt, revision and source:

```text
agent-worktrees pr-abandon <worktree-id> --reconcile-creation <pr-id> --expected-revision <revision> --expected-head <full-commit> --reason "<specific reason>" --confirm --json
```

Add `--merged-pr <number>` only for the duplicate-reservation case above.
Do not combine this selector with `--creation`, `--pr` or a repository override.
Revision/source drift requires renewed qualification, not an automatic changed
confirmation. An identical retry returns the original receipt without rewriting it.

## Receipt and subsequent safety

The existing record retains its immutable ID, frozen attribution and original
creation evidence, including `phase: publishing`. Its **local disposition**
becomes retired, not merged or provider-closed. The persisted compatibility
hold remains `state: creating` with an empty actionable `branch`; the exact
original branch is retained as `creation.recovery_branch` and is still checked
for source ancestry and ordinary local-branch cleanup. No Git ref is changed by
retirement. The additive `recovery_*` receipt records contract, reason, actor,
time, confirmed revision/source, upstream and optional existing merged
association. It never writes a provider number, URL or first-push head.
JSON reports `creation_reconciled: true`, `publication_outcome: unknown`,
`local_disposition: retired`, `creation_cancelled: false`, `provider_closed: false`, and
`claim_released: false`.

This wire hold is intentional: the prior numberless healer has no actionable
branch to look up, and the prior finalization backup gate still sees a live
creation hold even with PR mode disabled. A capable runtime alone interprets a
complete, requalified receipt as locally settled. Losing some or all receipt
fields keeps the hold; it does not turn the attempt into a merged or untracked
PR. Pre-existing numberless abandoned records are not relabeled by this rule.
Never use an older override or manufacture a provider association to clear it.

The existing lifecycle leases exclude guarded source publication, source
completion and final cleanup throughout qualification. Delegated publication
leases its caller and every matching managed source owner, in stable order,
and revalidates each. An explicit unmanaged source may be a linked checkout,
but a checkout inside declared native worktree roots still needs its exact
owner. Unavailable routing refuses; newly discovered ownership invalidates
an unmanaged admission before transport. Source publication does not acquire
installation-wide creation authority merely to recheck these leased owners.
Publication and both canonical native record-creation paths share a source-path
admission lease. Creation acquires it before record locks; publication retains
it through transport, so adoption cannot introduce a new native owner after the
last unmanaged-source check. Discovered owners must pass their own receiving
project's mutation guard. The admission snapshot and pre-transport recheck also
retain the original creation nonce and complete receiving-allocation bytes.
Read-only identity inference defers adoption immediately when that lease is
held, retaining Git's identity without registering a new owner. Explicit adoption
still waits for exclusion. Missing-record `mark-complete` creation takes that
same lease before the record lock and refuses immediately on contention,
including hook reentry. It cannot turn deferred inference into an unfenced owner.
Source-owner lookup enumerates every registered ledger entry without a retained
record-count cutoff. A disposable path-routing cache reuses only unchanged file
metadata; new, removed or changed entries are incorporated, and every matching
owner is loaded and qualified from its canonical record. Archived history is
not filtered out. Cached routing retains raw record paths and resolves them in
the current invocation, so relative paths and changed directory aliases do not
become stale ownership facts. Unreadable or continuously changing routing still refuses.
Guarded completion supplies its actual workspace and
rechecks source incarnation, allocation and receiving authority at every existing
Git mutation admission point.
The canonical Git transport retains an exact
destination/head fence through transport and changes that head's opaque
invalidation generation before every attempt, including unknown/failed outcomes.
Unrelated heads retain transport concurrency. That value
is not permission, evidence of a successful push, or a parallel PR ledger.
Fence admission validates the caller's timeout before destination capture and
shares its remaining budget across capture and every head-lock acquisition.
The clone-lock owner recomputes that remainder after its own Git-directory
probe, immediately before waiting for the lock.
It does not replace the underlying transport's existing per-attempt budget.
The public push boundary captures the source object and its implicit destination
before ancestry qualification or head-fence acquisition. A reset or checkout
switch during acquisition cannot substitute another source or mix one branch's
object with another branch's destination. Transport retains that binding and
the original hook ref. Public GitHub's documented SSH-over-443 clone alias
shares the same physical push/provider head fence as its HTTPS repository;
unrelated hosts and ports do not.
An explicit short destination preserves native Git's branch/tag selection:
the captured remote refs are resolved using the original named source
before pinning its object. Ambiguous or disagreeing destinations require a
fully qualified target rather than silently publishing a tag as a branch.
Only exact native refname expansions match; an unrelated nested ref with the
same suffix is not the selected destination. Namespace observation uses the
captured transport settings and selected credentials within the acquisition budget.
Successful named-remote tracking refresh uses that
object, the unchanged fetch/push mapping and a local compare-and-swap; it does
not add a second remote observation while holding the head fence. This also
keeps an unpinned `HEAD` push associated with its resolved branch, not a
nonexistent remote branch named `HEAD`.
The real pre-push hook still receives the original native source-ref name and
the transported object ID; pinning does not bypass branch-specific hook policy.
The successful transport result reports that same object even when publication
checks are unconfigured or the local source subsequently advances.
Provider creation carries the actual published source repository and object,
including differently named forks. It retains that source head's transport
fence and the target's exact head-observation fence, then authoritatively
rechecks the expected source tip before submitting creation. The qualified
target expression `owner:branch` is not a physical base-repository branch;
an unrelated push to the base repository's `branch` stays independent.
Reachable foreign-source qualification resolves and rechecks the fork's push
remote independently of the target policy's fetch remote. A verified foreign
receipt is not settled until its active PR claim is durable under the same
owner lock. Ordinary unverified provider receipts also block finalization and
forced managed removal before owner transition or physical teardown; terminal
PR labels do not erase those receipts.
Reconciliation refuses active or intervening publication. Slow remote
observations hold neither clone-wide publication locks nor installation authority.
The commit boundary acquires authority, then publication, then metadata locks,
rechecks destination/source and both native execution-space snapshots, and
performs a fresh revision-bound record write. A stale writer cannot resurrect
the retired attempt. This does
not cancel an independently accepted provider request or make an unknown
transport outcome known; the retained evidence remains historical truth.

### Unresolved legacy-publication admission contract

The participating-writer fences above do **not** exclude a publisher that
already passed an older implementation's admission checks. That publisher can
release its short authority/publication locks, pause before transport, and
resume after retirement commits. A new head generation cannot observe that
unregistered lifetime. This draft must not enable recovery or be adopted until
the source contract below is implemented and reviewed.

The existing ownership boundaries are:

| Existing owner | Callable boundary | Exact remaining gap |
|---|---|---|
| Worktrees PR publication | `pr_authority.publication_lifecycle`, `pr_authority.guard` | The lifecycle lease covers participating callers and sources. The authority lock does not retain every older publisher through transport and callbacks. |
| Worktrees exact-head transport | `pr_transport_fence.Head.guard`, `publication`, `provider_creation` | New invocations share the head fence and invalidate observations. Older admitted invocations do not participate. |
| Worktrees resident lifecycle | `ControlClient.drain` and its busy oracle | Draining resident handlers does not discover, close or drain independent PR publisher clients. It is not a publication-quiescence operation. |
| `agent-procutil` process custody | `owned_process.run_caller_owned` | Custody settles processes that the caller actually owns. It neither discovers a complete older publisher cohort nor closes future publication admission. |

No existing callable in those boundaries establishes the conjunction needed
here: complete relevant legacy coverage, closed future admission, and settled
or explicitly held original transports/callbacks. A process inventory, monitor
readiness, missing PID, empty provider search, package version, operator flag
or caller-supplied receipt is not that conjunction. This is a source contract
gap, not a reason to block source planning or its contained regressions.

The receiving contract remains with the Worktrees publication owner. Normal
maintainer review must settle its integration with the existing execution and
process-custody owners before adding a new public invocation. It must:

* Bind the actual executing installation and exact destination/head scope.
  Equivalent heads reached from another clone remain in scope; unrelated heads
  retain ordinary concurrency.
* Close relevant future admission before collecting drain evidence. The owner
  must distinguish a complete covered cohort from an incomplete or unknown
  population. Unknown coverage refuses; it is not an empty cohort.
* Retain native custody of already admitted operations, including their child
  transports and callbacks, until terminal settlement. An acknowledgement,
  caller exit or lock release alone does not settle a transport. Preserve
  historical unknown outcomes and never replay an original effect.
* Keep admission closed through observation and the final record mutation.
  Loss or replacement of that control invalidates the operation before commit;
  later receipt consumers need their own fresh qualification.
* Declare support from the executing owner, provider-first, independently of
  package versions. An older client does not gain exclusion by ignoring new
  metadata. Unsupported legacy coverage must fail before retirement, without
  weakening unrelated existing publication or cancellation behavior.
* Release only the acquired publication-control scope through its original
  owner. This contract does not authorize service/session termination, another
  daemon, direct tracking edits or installation.

#### Source-only boot-gated receiver

The receiver fails closed before retirement when that exclusion is
unqualified. It recognizes `agent-worktrees.pr-legacy-admission.v1`, scoped to
the historical installation-local `pr-rewrite-authority.lock` entry
(`agent-worktrees.pr-authority-file.v1`). A complete sealed journal alone is
insufficient: the canonical local installation, retained original file and
blocking directory must still have their recorded native identities. Reparse
points, retained-file aliases, unknown entries and interrupted topology refuse.
The native volume must be a fixed local drive; a mapped network drive cannot
borrow one host's boot as exclusion of publishers on another host.
The current writer uses only `writer.lock` inside the verified blocking
directory; it never creates another authority during a transition gap.

Retirement additionally requires the existing `update_driver.boot` provider's
coherent Windows execution-domain and native boot observations: the domain must
match and the boot must differ from the completed seal's boot. An unchanged boot,
another execution domain or unavailable/unsupported provider refuses recovery.
Ordinary publication does not wait for a boot; an installation with no transition
retains its existing authority entry. This requirement is not installer readiness
or permission to reboot, seal an installation or adopt the draft.

This is **receiver-only** source work. `pr-status` declares that limitation in
`legacy_publication_admission`, with `seal_operation: null`. No operational
sealing invocation is supplied. The actual sealing controller, crash/race
recovery and supported historical-entry/namespace coverage still need source
implementation, frozen old-process controls and maintainer review. The
seven-field creation format is not evidence that every relevant publisher used
the covered historical entry. Unknown or unguarded entry families remain held.

The receiver tests use real disposable files, identities and lock boundaries,
with explicitly modeled boot evidence. They do **not** prove an actual boot
crossing, native legacy sealing, complete publisher coverage or operational
quiescence. Those missing tiers continue to block recovery/adoption.

The historical creation-contract source at `f29ccb8ded` includes a concrete
unsupported family: `pr_foreign_create` calls `provider.create_pull` without the
authority entry. Its collaboration and direct-publication paths need their own
exact destination/head analysis too. The full recovery receiver therefore
**still refuses**, including with an otherwise valid authority barrier and
later-boot observation. Downstream receipt tests explicitly model the unresolved
coverage owner to isolate their own checks; they are not positive recovery
admission evidence. A native boot also does not by itself settle an already
accepted remote operation. Such outcomes remain unknown until their actual
transport/provider contract supplies adequate settlement or fencing.

`test_legacy_publisher_after_admission_blocks_retirement` is the contained
acceptance regression for the missing boundary. It pauses a legacy-shaped
publisher after its short locks have closed, runs the actual retirement CLI,
then permits a real push into the fixture's private bare repository. Recovery
must refuse and leave the original record unchanged while that unaccounted
publisher can still run. The existing active-lock control is not a substitute
for this interleaving. Positive qualification also needs a real controlled
cohort/admission lifetime, invalidation and mixed-version process controls;
a mocked successful quiescence flag is not completion evidence.

Ordinary finalization, even after PR mode is disabled, collects complete
provider/remote observations before acquiring cleanup locks. An operation-scoped
snapshot is then checked against the unchanged publication generation,
receipt/incarnation, destination and strict source/ref ancestry under the short
cleanup fence. It performs no retirement remote probes under the record or
finalization lock, and never reuses a snapshot from another operation. Source
Git probes are bounded and process-tree-aware. It still
checks all other PRs, dirty content, obligations and cleanup authority.
New effects, unknown observations, new work or drift hold finalization. No
`--force-open-pr` is required or supplied by this recovery path.

Exact `cleanup --worktree-id`, managed removal and terminal conclusion use the
same fresh qualification before taking their record/finalization locks.
Exact cleanup resolves a short selector once. If that exact ledger disappears,
the native core reports it missing rather than selecting another suffix match.
Only pre-mutation qualification failures become qualification refusals; native
operation failures and uncertain effects retain their original failure path.
An automatic sweep reports such a refusal as an individual open-PR hold and
continues with later candidates; a missing checkout cannot abort the whole sweep
or turn an unqualified retirement into successful cleanup.
Observation-time lifecycle leases are released before acquiring the native
repository finalization lock. Fresh records and the cached proof are rechecked
under the actual lifecycle/record mutation locks, after any native cleanup
fetch, including forced removal. A receipt arriving after preflight cannot
skip that check. Non-forced managed removal remains non-forced at the native
remover, so a newly live mux or session is still preserved.
Managed removal acquires the launch-seed owner's removal transaction before
its record locks. That owner holds launch admission and the complete sorted
set of affected retirement records through teardown, including a reciprocal
pair's expected ledger, and rechecks that set before deletion. A non-forced
removal proves squash-aware landed content after the late-artifact check and
before physical teardown, retaining that exact branch object for the later
compare-and-swap deletion. Nested seed removal uses the same canonical mutation
locally instead of waiting for a resident handler that needs the held lock.
The scope is bound to the exact path, process and thread. Receiving execution
authority is still checked. Outside this scope, even with an unrelated record
lock held, removal keeps its version-qualified resident path and its
unknown-outcome/no-replay behavior.
Only that operation's held fences settle the local receipt for responsibility
checks; a valid-looking receipt alone never does. Reciprocal conclusion
qualifies each affected sibling independently and holds their sorted union of
head fences once, even when both own the same head. Ordinary offline scans retain
the compatibility hold rather than contacting a provider or accepting cached
proof. Use exact cleanup to request fresh qualification.

After an already-finalized checkout is gone, requalification uses the configured
anchor, including a bare anchor without a working-tree HEAD, every surviving
owned ref and the confirmed recovery commit's strict upstream ancestry.
Saved recovery commits remain owned even while the checkout exists or local
branch refs have moved. A missing non-finalized source still refuses. Successful
cleanup preserves the receipt-bearing tracking metadata, including original
transport uncertainty; a paired hard-delete or failed tombstone write must not
erase it.
Terminal conclusion retains an existing `finalized` or `archived` receipt
owner's status, including idempotent retries, so missing-source qualification
remains available without reopening that owner.

Partial recovery fields and stripped compatibility holds remain
qualification-required. Numberless provider healing and ordinary publication
must not replace or replay this local disposition as a different association.
Legitimate later work can use explicit `create-pr --new` with a distinct head;
the original retired attempt remains unchanged and that new work must land
before finalization. A damaged receipt remains held rather than being bypassed
through a fresh publication.

Source acceptance is the contained disposable-Git/CLI packet
`tests/test_pr_creation_reconcile.py`, the provider adapter controls
`tests/test_github_head_reconciliation.py`, and unchanged cancellation,
first-push and publication-state regressions. These are not installed-runtime,
native lifecycle, production recovery or deployment evidence.
Select the complete `TestRetainedFinalizationCleanup` class for its ordered
reconcile, retained-finalize and exact-cleanup stages. Each stage keeps the
ordinary item deadline; each fresh flow also has one 300-second aggregate
deadline and refuses partial/reordered selection. The fixture isolates the
unrelated optional CodeSpaces lease warning, not lifecycle or content checks.
`TestNativeFinalizationRetirement` requires both ordered stages for each
selected intact/damaged receipt: reconciliation, then native finalization
with PR mode disabled and unlocked-observation assertions. It uses the same
per-item and whole-flow bounds. Modeled boot and coverage owners remain
explicitly source-only across every stage.
`TestMissingTerminalReceipt` similarly requires all three ordered stages for
each selected finalized/archived variant: preparation, conclusion and retry
with renewed missing-source qualification.
