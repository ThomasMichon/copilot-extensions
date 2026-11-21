# Worktree Dehydration - Architecture Proposal

**Status:** proposed; no runtime behavior is implemented by this document.
Back to the [canonical effort](README.md). Standing intent:
[checkout hydration](../../../visions/plugins/agent-worktrees/checkout-hydration/README.md).
Tracking: #13781; delivery order #13783 -> #13784 -> #13785.

## 1. Contract and boundaries

Dehydration removes a managed Git checkout and its Git worktree registration
while retaining the local branch and Worktrees' agency record. Rehydration
recreates that checkout before entry. Neither operation finalizes responsibility,
settles claims, lands commits, closes PRs, changes an effort or concludes a session.

The operator's content contract is an empty live Git status: no staged, modified
or untracked files. Ignored content is explicitly disposable. Agents use
agent-scratch for temporary material; declared build output, dependencies and
instruction projections are reproduced through their normal owners.

**Existing domain-data exception:** a checkout-local `.effort-agenda` can contain
durable history, not reproducible output. Preserve the existing
[retained-data hold](../../../plugins/agent-worktrees/docs/worktree-data.md#checkout-local-agenda-preservation)
until its data owner preserves/rescues that history and explicitly retires the
local source through the supported procedure. Empty, malformed, linked or
unreadable homes retain the existing conservative hold. A branch, locator or
copied snapshot cannot restore this history or discharge that hold. This is an
existing ownership constraint, not a new per-build-artifact approval policy.

This is not terminal cleanup, branch archival, a source backup, checkout
relocation or a new scratch owner. Preserve existing behavior for unmanaged,
reference, main-anchor and externally managed checkouts; they are outside
automatic dehydration. Qualify or explicitly refuse special layouts such as
submodules rather than passing them to a force-delete fallback.

## 2. State and discovery

Hydration is independent of lifecycle and Git classification. The stable states
are `hydrated` and `dehydrated`; a bounded operation record distinguishes
`dehydrating`, `rehydrating` and recoverable failure. These names are
agent-recommended schema terms, not released fields.

The Manager suffix is `(D)`, for example `WIP (D)`. A retained record whose
checkout was intentionally removed is not `GONE`, an orphan, completed or
ownerless merely because the path does not exist. Cached CONVO/WIP/MERGED/FINAL
labels remain last-observed where a current value cannot be derived from the
retained branch; report freshness honestly.

Use the repository's resolved worktree root and keep the catalog nearby:

```text
<worktree-root>\
  .dehydrated\
    <worktree-id>.json
  <hydrated-worktree-id>\
```

For mixed or relocated worktree roots, retain the root's registration through
the existing placement owner. Recovery enumerates registered roots, including
roots with only catalog entries; it does not depend on a surviving checkout.
Do not hardcode a sibling of the anchor or infer identity from a directory name.

Periodic roster reconstruction joins existing records, native Git membership,
session evidence and these catalog entries. Deduplicate by qualified
repository/worktree/incarnation identity. The catalog is native evidence of
intentional absence; it does not replace Git's authority for materialized
checkout membership or Worktrees' authority for current responsibility.

## 3. Snapshot and authority

Create the snapshot at dehydration, outside the checkout, before any removal.
Keep it until rehydration and its verification finish. The nearby catalog
removes the need for marker-only checkout folders or moving markers out of
Git's way.

Proposed versioned snapshot content _(agent-recommended)_:

| Group | Required information |
|-------|----------------------|
| Identity | Repository identity, worktree ID, creation incarnation, resolved checkout and catalog placement |
| Git | Expected local branch ref, exact HEAD commit, required per-worktree/sparse-checkout settings |
| Agency | Lifecycle/disposition, objective locator, sessions, asserted head and succession, controllers and relationship snapshot |
| Relationships | Pair identities, claims/PR references, durable worktree-data locators, revisions needed for reconciliation |
| Operation | Schema version, operation ID, transition stage, record revision and prior successful state |

Retain references to transcripts and domain-owned data, not duplicated transcript
bodies, credentials or arbitrary runtime state. An existing canonical record
remains authoritative. Compare revisions when an operation commits; do not
overwrite newer claims, session links or controls with the snapshot.

When canonical tracking is lost, the catalog must contain enough state to
reconstruct the local record and locate its sessions and relationships. Validate
identity against the repository and retained branch. Reconcile external claim,
pair and controller state through their owners. Unknown, contradictory or
unsupported evidence produces an inspectable recovery hold, not a live owner,
claim release, session promotion or replayed remote effect. Retain the snapshot
until that recovery has a durable disposition.

Write snapshots atomically with the platform's supported durability primitives.
Do not create a second generic data store or require a new daemon.

## 4. Branch preservation

The local branch is the Git reachability root. No mandatory extra retention ref
is needed while it remains intact. Its name need not start with `worktree/`.
Use the branch recorded by the managed allocation or an explicit owner-mediated
branch transition. Detached HEAD, an unborn branch or an unexpected checked-out
branch blocks automatic dehydration; do not silently adopt the observed branch.

Managed branch cleanup, pruning and ref-changing operations honor a dehydrated
record's reservation. Removing Git's worktree entry otherwise frees the branch
for reuse, so Worktrees must not treat it as unowned. Raw external Git can still
change or delete a ref; hydration compares the branch tip with the snapshot and
reports a conflict without force-resetting, recreating a missing branch from an
assumed object, or overwriting another checkout. The marker is not a Git GC root.

Unpushed WIP is eligible: commits remain reachable in the shared repository.
Losing that repository or externally deleting/moving the ref can lose content;
dehydration makes no backup guarantee.

## 5. Eligibility and concurrency

Use the existing Worktrees lifecycle/admission owner to serialize removal,
rehydration and launch. Hold a reservation through final observation and the
storage mutation. A queued or in-flight launch cannot race past that boundary.
Cooperative managed callers share the fence; revalidate live Git content
immediately before removal rather than relying on cached picker state.

Require actual process retirement and released process locks. An agent's
end-of-turn, Stop hook or termination request is not sufficient. Check other
checkout users, including mux shells, builds and external processes. Live or
unknown use refuses; do not kill them as a side effect of dehydration.

Use the existing Git environment-isolation boundary and a bounded command:

```text
git status --porcelain=v1 -z --untracked-files=all --ignore-submodules=none
```

Successful empty output is the content check; failures or unavailable observation
are not clean. Explicit options prevent user settings from hiding untracked
files or submodule changes. Also refuse unfinished merge/rebase/cherry-pick,
bisect or other per-worktree Git state that would not survive removal.

A clean WIP, open PR or unresolved objective is not by itself a blocker:
dehydration preserves responsibility. A resource that actively uses checkout
files or the existing checkout-local durable-data hold is a blocker. Do not
reuse terminal finalization's "all work settled"
admission as the new dehydration gate.

## 6. Dehydrate transition

Proposed owner-mediated sequence _(agent-recommended)_:

1. Acquire lifecycle/launch exclusion; resolve the exact managed checkout.
2. Check configured policy and hold, process/lock state, branch identity, Git
   operation state, live clean status, existing durable-data preservation holds
   and any required pair/consumer conditions.
3. Persist the complete relationship snapshot and removal intent in the catalog.
4. Ask Git to remove this linked worktree from the repository anchor:
   `git worktree remove <path>`. Preserve the branch and all durable data.
5. Verify native registration and checkout removal, then publish the same
   worktree as dehydrated. Keep the catalog entry.

Ignored output is disposable, but a general forced removal after any failure is
not the algorithm. Qualify supported Git versions' ignored-file behavior and
make any required owner-authorized force conditional on the same fresh clean
evidence. Never use terminal cleanup helpers that also delete branches, unlink
pair relationships, retire permissions indiscriminately or release ownership.

Failures keep their operation identity and snapshot. A removal timeout or
partially removed checkout is an unknown/interrupted stage, not successful
dehydration or permission to launch. Reconcile actual path and Git membership
before selecting the next step.

The existing removal helper's retained-data guard remains in force, including
before fallback or retry. Dehydration neither rescues an agenda itself nor
interprets a successful copy or archive receipt as permission to erase its
original source.

## 7. Rehydrate and ordinary entry

1. Acquire the same exclusion. Read the catalog and canonical record, or finish
   validated local-record recovery if the record was lost.
2. Verify repository, allocation and branch identity, expected commit,
   destination availability and absence of conflicting checkout/launch use.
3. Persist rehydration intent. Add the existing branch at the original path with
   `git worktree add <path> <existing-branch>`; never create a new branch or
   silently update from the default branch. Sparse layouts use the supported
   no-checkout/configuration sequence before materialization.
4. Restore declared per-worktree configuration and run the normal preparation
   owners for trust, permissions, projections and pair composition.
5. Verify checkout identity, HEAD, settings and required readiness. Commit the
   hydrated state and durably complete/remove the catalog entry.
6. Only then allow session resume under normal prompt/receipt/lineage rules.

Do not fetch, pull, rebase or reinstall dependencies merely to hydrate. A
partial/blobless repository may still need Git's normal object acquisition for
the exact retained commit; report unavailable objects or authentication rather
than claiming fully offline recovery. Build dependencies remain the normal
build owner's task.

A crash after Git add, after projection preparation or before catalog retirement
is recoverable without another allocation or agent launch. A stale snapshot
beside a valid checkout is reconciled, not interpreted as permission to remove
that checkout. The verified state commit and catalog retirement require a
recoverable ordering; failure to retire the marker cannot become duplicate
launch or silent success.

Entry must ensure hydration before the first path-dependent preflight, trust,
projection or process-CWD action. Keep entry through the existing common
Worktrees owner rather than adding a required preparatory command to every
caller. Pure list/status/transcript/PR inspection does not hydrate.

Paired worktrees retain their reciprocal identity and claims whether one or
both checkouts are absent. Evaluate each checkout's content and users. Entry
hydrates the siblings that normal composition actually requires. Pair
dehydration is not a claim-settlement or both-finalized exception for terminal
cleanup; its reversible storage admission is a separate explicit contract.

## 8. Automation and controls

The intended end state is automatic dehydration after verified agent-process
exit, with automatic hydration on managed mux or Bridge resume. Route post-exit
work to existing bounded resident maintenance. Keep foreground exit callbacks
short and recover missed events or interrupted transitions through the same
owner after restart; do not add another service or blocking full-tree sweep.

Proposed controls _(agent-recommended, exact keys/UI still to choose)_:

- Auto/manual dehydration mode. Manual disables automatic removal, not ordinary
  hydrate-before-resume. Runtime activation is a later release/adoption step.
- Persistent per-worktree dehydration hold with a reason; the same mutation lock
  protects setting a hold and starting removal. Manual Dehydrate does not
  silently override a hold.
- Manager Dehydrate/Rehydrate actions and CLI equivalents, backed by the same
  owner operation, eligibility and structured refusal reasons.
- `(D)` and transition/failure indicators alongside existing lifecycle labels;
  held or blocked candidates state why storage remains.

## 9. Integration and compatibility

Extend existing owners rather than importing another plugin's internals:

| Existing seam | Proposed extension |
|---------------|--------------------|
| `git_ops.py` create/remove helpers | Existing-branch rehydration and removal without branch cleanup |
| Tracking and lifecycle write owner | Hydration sub-state, catalog transitions, branch reservation and holds |
| `resolve_launch_cli.py`, embody/entry admission | Ensure hydration before path-dependent work, once across all entry paths |
| Post-exit and existing resident maintenance | Bounded eligibility work after actual retirement; interrupted-operation reconciliation |
| Roster population, `prune.py`, `gc.py`, claimant reads | Catalog-aware membership, owner liveness and preservation instead of GONE/terminal inference |
| Manager/pivot projection | Additive storage status and provider-owned actions, not a second Git scan |
| Worktree-data, pairing and relocation | Preserve locators and relationships across registered roots and absent checkouts |

Source inspection is a starting map, not a complete call-site audit. The
implementation slices must inventory all removal, branch-mutation and launch
consumers before activation.

Follow [service contract evolution](../../../docs/patterns/service-contract-evolution.md):
declare snapshot/operation support separately from package versions; expand
providers and catalog-aware collectors before enabling automatic removal.
Keep hydrated legacy behavior. Manager and Bridge discover support from the
selected executing provider before using new syntax or fields.

Supported rollback must preserve dehydrated records and refuse unsupported entry
or collection with an actionable diagnostic. An old collector that interprets
absent paths as settled cannot run against enabled dehydration state. Define
and qualify that compatibility boundary before enabling the feature; do not
require an obsolete executable to interpret retained data forever.

## 10. Qualification and remaining decisions

The effort's [Validation Plan](README.md#validation-plan) is the acceptance map.
Use disposable real repositories and processes, never the operator's active
checkout population. Cover committed WIP through Git GC, ignored artifacts,
all dirty forms, branch switches, process and launch races, catalog-only roster
rebuild, pairs, projection restoration and failures at every transition.
Include ignored checkout-local agenda history: removal refuses before owner-led
preservation and explicit local-source retirement, including empty/partial or
unreadable homes and a copy-only attempt. A supported rescue preserves accepted
history before a later eligible round trip; tests must not bypass the hold.

Native Windows and Linux/WSL tests exercise two automatic stop/resume cycles,
both mux and Bridge paths, unknown process evidence and actual process-lock
release. Clean-room first entry, supported old/new consumers and rollback are
separate from unit tests. Measure checkout bytes reclaimed (excluding shared
Git objects), foreground stop cost, cold/warm resume latency, candidate backlog
and bounded roster work. External venues are required when available for the
changed consumer; record a specific reason for any unavailable lane.

Before implementation activation, resolve Git force behavior for ignored files,
submodule support/refusal, exact snapshot schema and durability, common entry
ordering, pair transaction policy, supported rollback boundary, and final
configuration/UX names. These are explicit qualification obligations, not
permission to weaken the agreed branch/content contract.
