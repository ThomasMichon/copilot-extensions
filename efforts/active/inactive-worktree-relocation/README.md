# Inactive Worktree Relocation

- **Slug:** `inactive-worktree-relocation`
- **Repo:** copilot-extensions
- **Branch(es):** managed per-slice PR branches targeting `dev`
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [agent-worktrees: identity-preserving-checkout-relocation](../../../visions/plugins/agent-worktrees/README.md#identity-preserving-checkout-relocation), [replay-safe-workspace-allocation](../../../visions/plugins/agent-worktrees/README.md#replay-safe-workspace-allocation)
- **Umbrella issue:** [#10879](https://github.com/ThomasMichon/copilot-extensions/issues/10879)

## Guiding Intent

Make an inactive managed checkout movable without making it a different unit
of agency or losing any checkout content. Deliver a complete, qualified flow
for stopped, clean, merged/final ordinary linked worktrees early; use
separately authorized adopter feedback before widening to inactive WIP,
retained sessions, pairs and other supported cases. A first usable flow is a
milestone, not completion of the broader inactive-worktree capability.

This is the canonical public source effort. It owns generic implementation
and qualification, not an adopter's actual migration, installation or cohort.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner | Planning, implementation and source PR stewardship | #10879 and its linked managed PRs |
| Repository reviewer | Review of proposal and source deltas | Normal repository PR review |

## Coordination

- **Topology:** one source owner; dependency-ordered managed PRs against `dev`.
- **Host (owns PRs):** Source owner.
- **Delegates:** none; existing lifecycle and provider owners retain custody.
- **Handoff:** issue, canonical effort, current slice and unsettled effects;
  no new source-hosted authoring session.
- The source worktree remains needed and the effort remains Active after the
  proposal or first functional PR merges. Do not finalize it at either gate.
- #5293 is module/core decoupling, not relocation. #10863 owns standalone
  Driver extraction; consume its resulting seams, do not pre-patch or compete
  with that contributor's work.

## Context

The [owner and compatibility analysis](design.md) identifies current-root
assumptions separately from retained-path resolution. Allocation defaults are
not authority to invalidate an older checkout, and a recorded path alone is
not authority to mutate an arbitrary directory.

**Intent reconciliation:** extend the existing Worktrees vision with explicit
relocation and mixed-root guarantees; close its stable identity and replay-safe
allocation delta through existing owners. No new top-level vision, allocator,
session host, installer or background service is proposed.

Honor the existing [allocation/session acquisition pattern](../../../docs/patterns/workspace-allocation-and-session-acquisition.md),
[contract evolution pattern](../../../docs/patterns/service-contract-evolution.md)
and [execution-identity contract](../../../plugins/agent-worktrees/docs/execution-identity.md).
The plan changes intended behavior, not today's CLI documentation.

## Request

Public-safe capability request: support an identity-preserving move of
inactive worktrees, discover operational pointers across Worktrees tracking
and Copilot state, rebind to the new location, update Git, and transfer all
actual checkout content.

Supplied follow-up, verbatim:

> Great. Make an effort to support this and then practice on some of the oldest,
> merged or final worktrees, then work toward handling all inactive worktrees.

Scope separation: this effort provides the generic source capability and
qualification. Actual adopter practice is separately owned and authorized.
The ordered intent is retained: completed clean worktrees first, then wider
inactive cases, not a feature-complete monolith before the first useful flow.

The phase decomposition and technical safeguards below are
**agent-recommended realization**, not additional operator effect authority.

## Scope Approvals

The reviewed proposal is merged. Subsequent execution admission authorizes the
first source implementation/qualification stretch: mixed-root prerequisites,
independently configured companion allocation/recovery and old-active-record
coexistence, plus the first complete stopped-clean merged/final ordinary
linked-worktree relocation flow. Focused source tests and newly owned isolated
native/clean-room fixtures are included, followed by normal source
PR/review/required CI/merge. Additional commits preserve proposal history.

No current permission to move real retained worktrees, change
allocation defaults, install/update a runtime, stop owners/services, clear
claims, or perform an adopter pilot. Review and source merge grant none of
those effects. Fixture qualification in later source phases must use isolated,
authorized resources and the existing runner/lease/cleanup boundaries.

## Plan

### Phase 1 — First complete ordinary relocation flow
- [x] After proposal review and execution admission, qualify the owner/pointer
  inventory and expose a read-only plan plus an explicit native move operation.
- [x] Complete stopped, clean, merged/final ordinary linked-checkout relocation:
  fenced admission, all-content transfer, Git repair, authoritative rebinding,
  actual consumer verification and journaled interruption/recovery.
- [ ] Make old/new allocation roots coexist for this flow, retaining project,
  execution-space, Git-family and ledger authority; publish and qualify the
  complete source flow early through normal review/CI, not just scaffolding.
- [ ] Record precise supported/unsupported cases and hand the qualified source
  result to separately authorized adoption. Hold widening for initial pilot
  feedback; source approval itself never starts that pilot.

### Phase 2 — Broader inactive continuity
- [ ] Incorporate generic first-flow feedback; support inactive WIP with exact
  index and dirty/untracked/ignored preservation, not forced commit or discard.
- [ ] Qualify retained-session rebinding and actual resume; preserve conversation
  IDs/history and unrelated live session state through supported owner semantics.
- [ ] Support paired independent allocation roots and original interrupted
  creation/repair intent across default-root changes without replacement
  allocations, rewritten immutable receipts or authority weakening.
- [ ] Extend supported filesystem/provider cases through their owners and
  explicitly refuse those that cannot yet preserve identity and content.

### Phase 3 — Broad source acceptance
- [ ] Prove the [validation matrix](validation.md), including real native
  Windows cross-volume and POSIX flows, recovery, supported version skew and
  relocation followed by real resume/mutation/publication consumers.
- [ ] Publish accurate operating/recovery/support documentation with source
  changefiles for every changed plugin, then reconcile every Plan and
  Validation item or transfer it to a named tracked objective before Done.

## Validation Plan

The [focused validation matrix](validation.md) defines assertions and tier
boundaries. It is not a claim that runtime checks have already passed.

- [x] Proposal: effort/vision structure, documentation consistency, public-safety
  audit, actual repository review verdict and required CI before merge.
- [x] First flow: real linked Git checkout, stopped-clean acceptance,
  complete transfer and post-move consumers on same and different volumes;
  blockers and injected interruption preserve original/recovery custody.
- [ ] Mixed roots: existing resume/mutation/publication plus new allocations;
  foreign project/host/platform/Git-family/ledger and stale evidence still refuse.
- [ ] Broader cases: dirty bytes/index, retained-session resume, paired roots,
  original pending-intent recovery, path-sensitive artifacts and unrelated live
  records, with unsupported semantics explicitly gated.
- [ ] Native/compatibility: real Windows and POSIX filesystem semantics,
  version-skew/rollback controls and honest unexercised lanes; no installed
  rollout or real-data migration implied by fixture or source acceptance.

## Proposal

Reviewed proposal merged in [#10882](https://github.com/ThomasMichon/copilot-extensions/pull/10882)
after Copilot approval and required CI. This satisfies only the source planning
gate. The source objective remains open; execution admission and adopter
effects remain separate gates. Phase 1 source implementation is admitted;
adopter pilot feedback gates subsequent widening.

## Journal

### 2026-11-05 — Planning and coordination
- Rechecked public issue/PR title searches; no relocation claimant found.
  Created and claimed #10879; kept unrelated #5293 and #10863 separate.
- Inspected actual retained-path, receiving-namespace, source-publication and
  paired-allocation seams. Captured original-intent recovery, session-schema
  qualification and all-content preservation as explicit obligations.
- Captured and rechecked the supplied request and old-merged/final-first
  sequencing. Proposal is source-only; implementation and all live effects held.

### 2026-11-05 — Proposal accepted; execution held
- #10882 merged to `dev` at `7ec813010fb90a25b02d984624c5120fd6b60655`;
  Copilot approved the exact published head with no active review issues.
  Required CI, attribution and identifier checks succeeded.
- Pulled the existing source worktree forward with managed Git sync. #10879
  remains open; the effort stays Active and bound to Phase 1. No finalization.
- Structure, documentation consistency and runbook-reference checks passed.
  Runtime/native/installed qualification was not run for this documentation-only
  gate. No implementation, relocation, rollout or default change occurred.
- Next action: await explicit execution admission, then prove the first
  complete stopped-clean ordinary relocation flow before broadening support.
  The location-evidence, session-reader, filesystem and original-intent
  decisions in [design](design.md) retain their [validation](validation.md)
  obligations.

### 2026-11-05 — First source stretch admitted
- Explicit subsequent admission permits Phase 1 source work and newly owned
  isolated qualification fixtures, through source merge, without live
  relocation, installed-runtime changes, service termination or default changes.
- Keep the initial complete flow early; retain the effort/worktree for initial
  adopter feedback before broader inactive support. Source qualification,
  release, running adoption and real-data movement remain distinct.

### 2026-11-05 — Candidate and qualification boundaries
- Implemented the first conservative CLI flow, original-operation recovery,
  pre-publication cancellation, immutable completed-operation history,
  permission/record rebinding and retained-root Git/ledger authority.
- Added selected-runtime discovery and a passive resident-writer generation
  refusal. Source activation is not inferred from a test environment.
- Native fixture revisions proved actual CLI creation/movement, ignored data,
  read-only refusal, receipt replay and independent companion-root recovery.
  Subsequent changes require final revalidation; these earlier passes are not
  acceptance of the final candidate.
- The existing Windows editable-input retention policy deliberately stages
  beside same-volume source inputs. Preparation on a capacity-constrained
  authoring volume failed before collection. No installer/dependency-policy
  source was changed and no retained worktree or runtime was moved.
- Next permitted approach: freeze a source commit into a newly owned disposable
  input on approved test scratch, verify exact commit identity, and run the
  existing isolated wrapper there. This is test input, not another authoring
  venue or an operator-worktree migration.
- A disposable second filesystem is not enrolled; real cross-volume acceptance
  remains open. Keep #10879, the effort and source worktree active through this
  gate and subsequent adoption feedback.

### 2026-11-05 — Frozen-input proof; cross-volume gate still open
- The current candidate's native flow, companion-root and existing writer
  contract packet passed **71 controls**, with **one explicit skip**: no
  disposable second filesystem was enrolled. Real CLI New/move, cancellation,
  completed-history preservation, unchanged unrelated live database rows,
  read-only refusal and original companion intent were exercised.
- Existing allocation-replay, source-publication and execution-space packets
  passed **190 controls**, with three platform/selection skips. These are
  isolated source results, not installed-runtime acceptance.
- Source lint, effort structure, documentation consistency, install contract
  and the source module-size boundary passed. Kept the new paired policy in
  its focused owner instead of widening the legacy module ceiling.
- Used exact-commit disposable source inputs on approved scratch to satisfy
  the existing same-volume editable-input preparation rule without changing
  that rule, allocation defaults or the real authoring workspace.
- **Genuine remaining first-slice gate:** enroll an explicitly approved private
  disposable parent on a second filesystem, run the real native cross-volume
  packet, then clear source PR/review/required CI/merge. No first-slice code PR,
  release, selected-runtime activation or operator migration is claimed.
- Keep the candidate, ownership, active effort and authoring worktree for that
  gate. Do not broaden to every inactive case before the first qualified flow
  and initial separately owned adoption feedback.

### 2026-11-05 — Bounded native cross-volume roundtrip
- The focused native case passed **1 control with no skips** on two distinct
  Windows filesystems, transferring the same newly owned synthetic linked
  checkout in both directions. The fixture enforced a 1 MiB content bound and
  preserved its identity, nonce, Git registration, content/metadata and history.
- Both transfer legs used the production move path. Successful cleanup retired
  only the newly owned fixture; no retained user worktree, runtime, service or
  allocation default changed. This qualifies the generic volume branch, not
  any adopter's source ACL, filesystem features or target cohort.
- The source first-flow gate can now proceed through normal code PR, actual
  review verdict, required CI and merge. Selected-runtime activation and an
  adopter pilot remain independently owned; broader inactive support stays open.

### 2026-11-06 — Review corrections qualified
- Addressed Git lock protection, full initial receiving identity/ledger checks,
  copied-data and pointer/recovery durability, exited-caller recovery,
  inherit-only native ACE semantics, permission aliases, genuinely read-only
  seed inspection and the environment pairing opt-out.
- Added real separate-process recovery and interruption controls after Git
  repair, permission update, ledger update, partial source retirement and a
  failed durability barrier. A native descriptor control distinguishes
  inherit-only access without mocking past security-descriptor acquisition.
- The final native/companion/writer packet passed **82 controls, no skips**.
  It included the bounded real two-filesystem roundtrip after the safety
  corrections; successful cleanup removed only the owned fixture.
- The complete multi-process New/plan/apply consumer took 38.84 seconds under
  observed load, exceeding the runner's default 30-second item budget.
  Kept finite child custody/deadlines and gave only that combined native item
  a 120-second budget; no runtime behavior was changed to hide a failure.
- Source review convergence, required CI and merge remain open. The effort
  stays Active; selected-runtime activation, actual migration and initial
  adopter feedback are still separate gates.

### 2026-11-06 — Publication and journal-rotation corrections
- The next review identified three further defects. Added atomic no-replace
  publication for supported POSIX kernels (otherwise refuse), retained the
  current terminal journal until its atomic replacement, and used a read-only
  descriptor for POSIX file synchronization without changing preserved modes.
- The focused affected-consumer packet passed **12 controls** with one explicit
  Windows skip for the POSIX read-only-file control. Native collision refusal,
  journal-rotation crash fencing, interrupted effects, fresh-process recovery
  and the complete CLI flow were exercised.
- Re-ran the real bounded Windows cross-filesystem roundtrip after the journal
  correction: **1 passed, no skips**; exact owned fixture cleanup confirmed.
  POSIX native qualification remains open, not inferred from Windows proof.
- The separate trusted identifier scanner encountered a GitHub API HTTP 503,
  not a reported identifier leak. Required source CI passed. Preserve a fresh
  scanner result and an actual passing current-head review before merge;
  neither the infrastructure error nor resolved threads substitute for them.

### 2026-11-06 — Operational-reference alias review
- A subsequent review's summary listed four substantive findings despite its
  “No open issues” heading. Treated the findings, not that heading, as the gate.
- Synchronized both new history-directory ancestors before journal rotation;
  normalized native reference containment, including parent-component and
  filesystem aliases; rechecked nested permission bindings under owner locks;
  and used a bounded, read-only database predicate for retained client paths.
  Unqualified network and relative-parent references refuse explicitly.
- The complete native packet passed **35 controls**, with explicit skips for
  the unenrolled second volume and the POSIX-only descriptor control.
  Subsequently the corrected production path passed **1 real bounded
  cross-filesystem roundtrip, no skips**; exact owned cleanup was confirmed.
  Parent-alias workspace/database references, capture–modify–apply races and
  history-ancestor barriers have focused regression coverage.
- Review convergence and source merge are still required. No runtime
  activation, real retained-checkout move or allocation-default change follows
  from this source qualification.

### 2026-11-06 — Source-location admission closure
- The current-head review returned Comment with no concrete open findings.
  Final recovery-invariant reconciliation identified one narrow admission gap:
  reject a redirected/noncanonical recorded source before writing a journal,
  rather than creating custody that the recovery reader must reject.
- The affected refusal, archive-fence and complete native/CLI consumers passed
  **15 controls**. The resulting exact source also passed **1 real bounded
  cross-filesystem roundtrip, no skips**, with exact owned cleanup confirmed.
- Publish this final admission correction through a fresh review and required
  CI. Keep source merge, release, selected-runtime activation and adopter
  feedback distinct; the umbrella and broader effort remain Active.

### 2026-11-06 — Clarified custody findings
- Requested concrete details rather than accepting a review summary that
  referred to unpublished critical findings. The clarified review identified
  permission-file link replacement, non-ID source basenames and POSIX
  read-only staged-file retry.
- Rechecked ordinary single-link permission files under all owner locks before
  any rewrite; aligned source basename admission with recovery; validated
  partial-stage entries/access before copying; and made owned POSIX stage
  retries preserve source permissions and copied metadata.
- The complete native packet passed **39 controls**, including actual Windows
  symlink/hardlink custody controls. Four explicit skips covered three POSIX
  controls and the unenrolled second volume. The corrected production flow
  then passed **1 real bounded Windows cross-filesystem roundtrip, no skips**,
  with exact owned cleanup confirmed.
- Fresh current-head review and required CI still gate merge. POSIX native
  acceptance, selected-runtime activation, adopter migration and broader
  inactive support remain open.

### 2026-11-06 — Initial access and eligibility qualification
- Addressed restrictive-directory risk by qualifying access before custody:
  Windows uses non-mutating access-handle probes; POSIX source directories and
  their parent must be owned and writable. Required metadata must also be
  writable. Restrictive cases refuse instead of entering an unfinished move.
- Pinned the record bytes used to validate eligibility, rather than hashing a
  later record after slow probes. A new responsibility acquired during planning
  now refuses before journal publication and remains intact.
- A full native packet passed **41 controls** with six explicit skips; its
  additional negative ACL fixture incorrectly assumed a file DELETE deny
  cannot be granted by parent DELETE_CHILD. Corrected that fixture to test
  effective source-write and destination-creation denial, not a false platform
  assumption. The affected packet passed **7 controls** after correction.
- Unrelated read-only client settings are neither rewritten nor opened for
  write during durability verification. This was proved through a complete
  move with byte-identical settings, not a mocked writer.
- The exact resulting source passed **1 real bounded Windows cross-filesystem
  roundtrip, no skips**; exact owned cleanup confirmed. Source review/CI/merge,
  POSIX native acceptance and separately authorized adoption remain distinct.
