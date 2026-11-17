# Verified User-Output Delivery

## Goal

Give a producing worktree a complete, explicitly selected user-output flow:
resolve a segmented destination, register responsibility before returning a
writable allocation, verify the finished deliverable and close that
responsibility with a bounded historical receipt.

**Kind:** completion; a finite source-only slice of #10968.
**Acceptance:** U0-U3 and UV1-UV5 below, normal source review and required CI.
**Finalization criterion:** source delivery is merged, all scoped validation is
resolved and the source workspace's obligations are settled. This does not
require deleting user output or retained history.
**Now:** the bounded source flow is reviewed, qualified and merged.
**Return:** complete archive publication and native workspace settlement;
preserve the broader lifecycle, release, installed and operational owners.

- **Slug:** `verified-user-output-delivery`
- **Repo:** copilot-extensions
- **Branch(es):** one managed source worktree; sequential intent, implementation and completion PRs
- **Created:** 2026-11-17
- **Status:** Done
- **Vision:** extends [artifact disposition](../../../../visions/plugins/agent-worktrees/README.md#artifact-disposition)
- **Umbrella issue:** #13040; part of #10968

## Guiding Intent

User output is not disposable scratch, a reusable cache or a live history store.
The user may organize or move a completed deliverable. The producing worktree
must not keep responsibility open forever or maintain a second inventory of
delivered files.

An allocation or copy attempt is not successful delivery. Failed or unknown
verification keeps the original responsibility visible.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Plan, implementation, synthetic qualification and PR stewardship | This effort and #13040 |

## Coordination

- **Topology:** sequential source PRs, with one writer in one managed worktree.
- **Host (owns PRs):** Source driver.
- **Delegates:** none.
- **Handoff:** continue from this Goal, the current unchecked item and its evidence.
- **Existing owners:** configuration-context/layers select configuration;
  Worktrees owns the resource ledger, exact owner and finalization; the selected
  storage provider owns actual account/root discovery. Logger remains an
  optional history archivist, not a delivery or destination-registry owner.

## Context

The [artifact lifecycle guide](../../../../plugins/agent-conduct-guidance/docs/artifact-lifecycle.md)
already defines segmented user output, explicit destination precedence, verified
handover and historical receipts without a destination ledger.
[Artifact storage](../../../../plugins/agent-worktrees/docs/artifact-storage.md)
currently composes scratch policy, not user-output preferences.
The [producer contract](../../../../plugins/agent-worktrees/docs/resource-producer.md)
already supplies reserve/begin/bind fencing and exact registered-provider
verification. Its retained lifetime describes disposable scratch, not completed
user handover; that meaning must not be silently broadened.

This slice reuses those owners. It does not reopen the history collection work
or claim the broader [worktree-data program](../../../active/mux-companion-manual-cutover-diagnostics/worktree-state-lifetime.md)
complete.

## Request

Public-safe source-admission answer, verbatim:

> Approve source-only user-output slice

The admitted outcome is destination resolution, verified delivery and bounded
history receipts, with source review, implementation, synthetic Windows/Linux
validation and normal PR delivery. This is a source contract, not authority to
operate on real user directories or activate an installed runtime.

## Scope Approvals

The source driver may implement and correct this source flow, run private
synthetic fixtures and steward its PRs under normal review and CI.
Excluded effects remain real user-file writes/moves, account/path configuration,
installed updates, activation, collection, migration, historical deletion, cache
eviction and other owners' resources.

## Plan

### U0 - Review the intent

- [x] Reconcile the new disposition intent and this plan through source review.

### U1 - Resolve and produce the complete flow

- [x] Extend the existing storage owner with portable user-output preferences
  and exact machine-root overrides. Keep resolution inert.
- [x] Add an explicitly negotiated Worktrees user-output allocator and delivery
  operation over the existing producer ledger. Register before returning a
  writable destination and complete one real producer flow.
- [x] Keep failed, interrupted and unknown handover under the same obligation.
  Successful exact retries return the stored historical result without
  rescanning or repairing files the user may have moved later.

### U2 - Qualify the source consumers

- [x] Prove the controls in UV1-UV5 using private synthetic Windows/Linux roots
  and the normal contained test owners. Record actual platform coverage.

### U3 - Deliver and close the bounded source work

- [x] Update authoritative usage/configuration documentation and the artifact
  skill; pass focused tests, source checks, review and required CI.
- [x] Merge the source, record its actual release state, resolve this source
  issue and archive the completed effort. Keep #10968 and unrelated owners open.

## Validation Plan

- [x] UV1. Prove precedence, portable organization templates, exact machine
  roots and explicit destination behavior. Missing or invalid required
  OneDrive selection never silently becomes Downloads, scratch or source.
- [x] UV2. Prove exclusive folder creation, portable topic/member names,
  collision handling, no link/reparse traversal, protected-home refusal and
  reserve-before-writable ordering through the real allocator.
- [x] UV3. Prove success and actual finalization with a retained deliverable;
  also prove incomplete, changed, unreadable and over-budget content cannot
  close the producing obligation.
- [x] UV4. Fault the allocation, provider verification and ledger-publication
  boundaries. Preserve the same identity and responsibility across unknown
  replies; exact completed retries must not require a destination to remain
  present or recreate a user-moved folder.
- [x] UV5. Prove old/new capability refusal and unchanged disposable/retained
  scratch semantics. Use focused unit controls and opt-in real-process/native
  qualification rather than enlarging required CI into an exhaustive matrix.

## Proposal

### Configuration and destination ownership

Use the existing artifact-storage source selection and layers. Portable fields
describe target choice and relative organization templates; physical roots
remain exact machine-scoped values or explicit inputs from the selected storage
owner. Do not infer the intended OneDrive account from an environment variable
or import a destination-specific sibling runtime.

Precedence is explicit destination, exact machine override, portable preference,
then capability default. Default organization remains
`Apps/Copilot/Artifacts/<YYYY-MM-DD> <Topic>` under an owner-resolved OneDrive,
or `Copilot/Artifacts/<YYYY-MM-DD> <Topic>` under owner-resolved Downloads.
Templates may express a different portable organization, not arbitrary path
execution or traversal. An explicit destination is not silently rewritten.

An unspecified target may use an available owner-resolved default. An explicit
or configured required target must resolve or fail visibly. Resolution neither
creates folders nor selects/configures an account.

### One producing obligation, then a historical receipt

Add a separately advertised user-deliverable lifetime/disposition to the
existing producer owner. Do not repurpose scratch retention, generic claim
release or absence as proof of delivery. Default producer requests and callback
envelopes keep their current shape. Older providers are unsupported for this
feature, without bootstrap or fallback.

The first complete producer writes into its newly allocated final compartment.
This avoids an unnecessary second staging copy or an arbitrary copy/move API.
Register its exact creator/incarnation and fenced allocation before returning
the writable path. Bind the real filesystem identity, use exclusive creation,
and retain unresolved responsibility across partial creation or unknown replies.
Do not overwrite or adopt a pre-existing user folder.

The integrated producer ends its own writes before requesting delivery.
The native provider verifies the exact allocation and bounded declared output
through real filesystem reads, not a caller-supplied success boolean.
The serialized ledger transition rechecks the same subject and persists the
verified result before responsibility becomes terminal.

A receipt identifies the actual selected destination, delivery time, content
summary/integrity and result within explicit count/byte limits. Successful
delivery is omitted from current resource-ownership inventory, while its bounded
receipt remains in the producing worktree's retained history. No destination
registry, periodic scan, recipient handshake or later-user-move repair is added.
Local verified handover to a sync folder is not remote cloud acknowledgment.

### Boundaries and compatibility

Use the current OS identity/access boundary and pinned filesystem identities
to prevent wrong-target traversal and replacement. Do not weaken existing root,
record, handoff, finalization or permission checks. Do not repair user-directory
permissions, follow links, scan arbitrary roots or delete user deliverables.
Unsupported cross-owner handoff remains an explicit refusal until its receiving
contract is separately implemented.

The behavior adds no daemon, scheduler, service, network call or installed
default activation. Cost is explicit-operation work bounded by the selected
allocation and manifest limits, not a repeated history or destination scan.
Source tests use newly created private fixture roots only; installed and actual
user-storage adoption remain outside this finite effort.

## Journal

- [2026-11-17 - Source delivery and bounded completion](journal/2026/11.17%20Source%20delivery.md):
  #13071 merged after current-head approval and required CI. Final native
  evidence, review corrections and the pending Main release are recorded there.

### 2026-11-17 - Windows and Linux source qualification

The rebased source `33e7dc699679cfff29c504adcc996fe9821d2bdf` passed
426 Linux controls across 14 files. Six Windows-only cases skipped there;
they are not Linux evidence. The
[source-pinned hosted Windows run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44080296833)
qualified all 323 selected cases on Windows Server 2025, AMD64, Python 3.12.10.
Its selection excludes only three explicitly POSIX-only root cases, which
passed in the Linux packet. The Windows flow covers actual CLI execution,
exclusive allocation, file integrity, native access checks, timestamp semantics,
unknown replies, retained receipts and finalization. Neither platform result
qualifies actual user storage or installed adoption.

The initial Windows file check compared two different meanings of `st_ctime`.
The correction compares creation time across APIs and keeps change-time
stability checks within the held descriptor. Identity, size, modification-time
and content checks remain required. Positive collision fixtures now use the
existing private-directory owner.

The complete admitted packet passed 5,272 actor-free units with coverage and
effect checks in 125.119 seconds. Nine new proof-decision cases reuse its
existing reference-owner fixture; native and filesystem cases remain outside
required unit CI. Whole-file strict MyPy passed 21 changed Python files on
both analysis platforms. Review, required CI, source merge and closure remain
open; this effort is not Done.

### 2026-11-17 - First complete source flow

Implemented inert target/template/root composition and a separate
`user-deliverable` lifetime over the existing producer ledger. The native
consumer writes directly into its exclusive final compartment. Real reads
verify exact membership and integrity before terminal handover. Completed
retries use the saved original request and receipt, without current policy or
destination access. The first complete Linux flow, actual finalization and
the opt-in child-process consumer passed focused source controls.

The whole-file strict ratchet passed all 18 changed Python files on both
declared analysis platforms. The wider compatibility packet found a changed
scratch error message; restored the original private-root diagnostic.
Native Windows first-touch testing rejected the ordinary inherited test-folder
ACL. Corrected the positive fixtures to create private roots through the existing
owner instead of weakening admission. Windows and complete compatibility
qualification remain open; an intermediate pass is not the final delivery gate.

### 2026-11-17 - Reviewed intent

#13042 merged after Copilot approved the exact source head and required CI
passed. The source worktree synchronized onto the reviewed `dev` state.
U0 is complete. U1 now owns inert resolution and the complete producing flow.
No installed runtime or real user-storage effect is admitted.

### 2026-11-17 - Source intent

Claimed #13040 after searching current public issues and open PRs. Reconciled
storage policy, producer custody and artifact guidance. The proposed first
consumer produces directly into an exclusively allocated destination and keeps
completed receipts only in the existing worktree history. No behavioral
implementation or real user-directory operation has occurred.
