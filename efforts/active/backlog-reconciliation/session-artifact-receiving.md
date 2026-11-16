# Session-artifact persistence: proposed direct receiving contract

Back to [Backlog Reconciliation](README.md).

## Intent and exact tracking boundary

This is an agent-recommended, research-only proposal to retain #2165 as a
sixth, independently testable track in the existing #10968 artifact-lifecycle
tracker. It does not claim that cleanup, sync exclusion, portable guidance or
the other five tracks implement a persistence boundary.

The shared tracking concern is where suite-created artifacts may enter,
remain in and leave persistent state. The proposed addition extends #10968's
currently stated temporary/regenerable-state boundary to the specific
session-artifact write policy below. It does not make arbitrary durable data
disposable or turn the security/data-handling requirement into disk cleanup.
The existing A-E plans, validations, source-delivery credit and independent
owners remain unchanged. The source #2165 is currently unassigned; this
proposal appoints no implementer and does not add work to another
participant's accepted implementation slice.

No receiver mutation or source retirement follows from this file alone.
Required review, the complete direct issue intake, current qualification and
live readback must precede the separate retirement judgment. If the extended
contract is not accepted, #2165 stays open as its own canonical request.

## Evidence and distinctions

The 2026-11-16 02:52 UTC metadata observation qualified both originals, all
editors, participants, comments and timeline/source identities against two
equal complete twelve-member collaborator rosters. Both issues were OPEN
and unlocked, and two complete metadata observations matched. An initial
query omitted renamed-title actor metadata; that gap was corrected before
text admission. The complete #10968 original and eight discussions and the
complete #2165 original and three discussions were then read as separate
units under unchanged metadata boundaries.

#2165's escalation requires a structural write-time **block OR flag**, not
only guidance or a later sync filter. Its source report includes tool/repo
trees, browser-profile material and raw monitoring captures, and explains
that deleting a sync destination does not settle material still held by an
originating replica. Incident-specific names, source content, profiles and
replicas are not reproduced or inspected here.

#10968 already directly retains legacy checkout-local test state, bytecode,
declared derived outputs, suite scratch/locks and the optional
removal-executor design evaluation. Its later source-delivery reports retain
remaining producer integration, lock migration, installed adoption,
observation and separately authorized application. Those reports are not
newly verified source or runtime evidence in this study, and none of those
obligations is settled by this intake.

The other recently compared originals remain separate: #1886's uninstall
plan/apply and shared-reference protection, #2709's CodeSpace lifecycle
contract, and #2861's authored same-destination ownership protection are not
received by this proposal. A shared artifact-related word does not make
their complete outcomes duplicates.

## Proposed direct issue intake

The following finite addition is intended for #10968 after review. It is
additive to its complete incumbent body and discussions, not a replacement.

### Track F - structural session-artifact persistence boundary (#2165)

This sixth track extends the parent's temporary/regenerable-state scope
only to the session-artifact persistence boundary described here. It is
distinct from cleanup after persistence and from the existing A-E tracks.
Those tracks, their original descriptions, checked/unchecked rows,
participants, delivery records and remaining acceptance stay intact.

#### Plan F

- [ ] **F1:** Define and enforce the session `files/` boundary for small
  curated artifacts such as patches, diffs, short logs and generated
  documents/plans. Do not use it for complete repository checkouts, installed
  SDKs/toolchains, dependency/vendor trees, captured browser profiles/user-data
  directories or raw system-trace/monitoring captures. Include the original
  large archive/evaluation-output case without equating every archive or
  generated document with prohibited content.
- [ ] **F2:** Provide structural **write-time block OR flag** behavior for
  those categories at their originating persistence boundary. Preserve that
  literal choice; size/depth heuristics, session-start guidance and a
  lint/helper/close-hook may assist the selected design but are not substitutes
  for its write-time behavior. A flag-only design must state its remaining
  exposure rather than claim that no bytes were persisted.
- [ ] **F3:** Give rejected or flagged heavyweight work an actionable,
  appropriately owned alternative: an existing tool cache, ephemeral working
  location, or the supported repository/worktree flow, as applicable. Preserve
  legitimate small curated artifacts and the session framework's own required
  state. This is not a request to relocate all session state or create another
  storage/cleanup service.
- [ ] **F4:** Address the source-to-sync data-handling boundary explicitly.
  Destination exclusion/deletion is not proof of source prevention, cleanup
  across originating replicas, or protection against reappearance from a
  surviving writer. Preserve the escalated sensitive-source/browser-auth-state
  risk separately from the original dummy-certificate/DLP false-positive
  example; do not reduce it to storage clutter.
- [ ] **F5:** Retain #1807/#2271's reported sync-time exclusions/detection and
  #4742's reported scratch guidance as scoped positive delivery, not proof of
  F2. Keep any necessary remediation of already-persisted or exported data a
  separately authorized owner operation. This tracking intake authorizes no
  inspection, copying, deletion or cleanup of real incident material.

#### Validation F

- [ ] **FV1:** Use safe synthetic controls for each prohibited category:
  checkout, dependency/vendor tree, SDK/toolchain, browser profile/user-data,
  raw trace/capture and the heavyweight archive/evaluation-output case.
  Demonstrate the selected block OR flag at write time, and positive controls
  for legitimate small patches/diffs/logs/documents and required framework
  state. Do not use real private code, browser credentials or monitoring data.
- [ ] **FV2:** Show that guidance-only behavior or exclusion first applied
  during sync cannot satisfy the write-time control. If flagging is selected,
  record what remains persisted and the warning's actionable handling rather
  than asserting prevention from a warning.
- [ ] **FV3:** Exercise the originating-writer and sync-round-trip distinction
  with fixtures, including destination removal followed by a surviving source
  writer. State exactly what the implemented control establishes and what
  would still need separately authorized replica remediation.
- [ ] **FV4:** Before technical completion, verify the full implemented
  contract rather than infer it from an earlier cleanup, guidance or sync
  delivery. Before tracking retirement, read back the accepted direct F/FV
  clauses and preserve all incumbent A-E/AV-EV/GV/TV rows and owners.
  Administrative retirement is `NOT_PLANNED`, not a claim that F or the parent
  is technically completed. No new native, installed-rollout or public-receipt
  prerequisite is added to that tracking judgment.

## Whole-source crosswalk

| Source obligation | Direct receiving clause | Retained distinction |
|---|---|---|
| Small curated artifacts, not installs/checkouts/dependencies | F1, F3, FV1 | Required framework state and legitimate artifacts remain usable |
| Original size/depth, guidance and lint/helper/close-hook alternatives | F2, FV2 | Supporting choices do not replace the stronger escalation |
| All escalated categories, including browser profiles and raw captures | F1-F2, FV1 | Category controls are not merely a filename/DLP filter |
| Structural block **OR** flag at originating write time | F2, FV1-FV2 | Flagging does not establish non-persistence |
| Source material may repopulate a cleared destination | F4, FV3 | Destination deletion is not whole-replica remediation |
| Sensitive-source/auth-state risk versus initial false positives | F4, FV1 | No incident-specific content is reproduced |
| Useful delivered sync exclusions and scratch guidance | F5, FV2, FV4 | Reported partial delivery is not reverified implementation or whole fulfillment |
| Actual technical acceptance and current ownership | F5, FV4 | No implementation assignment, cleanup or resource release follows |

## Publication and completion

The publication owner must preserve the whole current receiving body, qualify
changed inputs, append the reviewed direct track without altering incumbent
content, and verify the actual accepted issue text. Only then may the complete
#2165 source receive an independently reasoned `NOT_PLANNED` tracking
retirement. Its original and discussions remain available, with a neutral
explanation that the stronger write-time/data-handling work remains planned.

The current campaign's fixed whole-queue target, all parent Plan/Validation
items and every existing source/resource hold remain independent. This
proposal provides no closure credit, implementation proof, real-data
remediation, installed adoption or custody release.
