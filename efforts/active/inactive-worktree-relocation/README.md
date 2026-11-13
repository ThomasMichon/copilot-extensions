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

Normal pinned release-dependency acquisition, read-only dependency-code
inspection and the repository's existing isolated native CI are in scope.
This does not authorize upstream repository changes, issues or PR/discussion
participation, shared-host backend startup/query/reaping, or real-target
effects. The earlier dependency-fetch validation hold was superseded; normal
review, required checks and accepted Main publication remain mandatory.

## Plan

### Phase 1 — First complete ordinary relocation flow
- [x] After proposal review and execution admission, qualify the owner/pointer
  inventory and expose a read-only plan plus an explicit native move operation.
- [x] Complete stopped, clean, merged/final ordinary linked-checkout relocation:
  fenced admission, all-content transfer, Git repair, authoritative rebinding,
  actual consumer verification and journaled interruption/recovery.
- [x] Make old/new allocation roots coexist for this flow, retaining project,
  execution-space, Git-family and ledger authority; publish and qualify the
  complete source flow early through normal review/CI, not just scaffolding.
- [x] Record precise supported/unsupported cases and hand the qualified source
  result to separately authorized adoption. Hold widening for initial pilot
  feedback; source approval itself never starts that pilot.

### Phase 1b — Qualified owner observation and receiving continuity

The [scoped prerequisite proposal](receiving-continuity.md) preserves the
old-final-first ordering. It is agent-recommended realization of the original
request, not a new migration or runtime-effect grant.

- Separate pure inspection from explicitly requested bounded live admission.
  Existing owner queries may refresh native activity timestamps where the
  caller permits that effect. Exclude active and dependent owners and retain
  unknown cases individually; do not require every other server to terminate.
  Pure planning never gains live queries, locks or receipt recovery implicitly.
- [x] Qualify bounded native live-owner admission through existing owners,
  preserving mux, bound Copilot, pending launch/lifecycle, process/CWD and
  responsibility fences. Unavailable evidence remains a hold, not inactivity.
- [x] Complete the independent pure planning/refusal source boundary without action
  probes, original-proof recovery or mutation leases. Its local qualification
  is not native inactivity proof. #11706 and reconciliation #11731 are merged;
  run 42696377770 and generated Main #11737 accepted and published that source.
- [x] Qualify the independent first receiving current-location component,
  retaining original concluded proof and settled local creator responsibility;
  fixture-only admission does not resolve complete native-owner evidence.
  #11813 merged after actual approval and required CI; ordinary accepted Main
  qualification/publication remains separately observed.
- [x] Inspect intact original receiving proof without recovery, sidecar/index
  creation or lifecycle/backend admission; invalid/unknown evidence refuses.
- [ ] Reconcile missing original receiving proof explicitly through its owner,
  separately from read-only planning and preserving the original operation.
- [ ] Support the first clean, sessionless, unpaired concluded receiving
  allocation with settled origin responsibility and an owner-serialized
  current-location revision; immutable allocation provenance stays unchanged.
- [ ] Prove the complete native receiving consumer and interrupted/refused paths
  before claiming full receiving support or separately authorized adopter
  practice. Independent component source acceptance does not resolve this gate.

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

## Windows private-stage compatibility correction

The existing access-preservation intent also covers Python 3.13's Windows
`mkdir(0700)` change. A native regression found that its special DACL can
differ from an otherwise uniform checkout and its already-admitted destination
parent. Stage creation now inherits that parent's access on Windows, as older
Python versions did; POSIX retains `0700`. All existing source, parent, stage,
copy, metadata and recovery checks remain mandatory. This closes the reviewed
contract rather than changing security intent.

Focused native controls cover owner-only and owner/administrator parents,
exact post-copy metadata, nonuniform or unsafe access refusal and stage-create
collision retention. A separate fresh-process source consumer covers concluded
receiving creation/settlement, changed synthetic allocation defaults, relocation,
immutable original proof, current-location lookup/mutation and a new receiving
allocation under the new default. Its positive admission is fixture-owned only.
Same-volume source qualification is not released/installed-consumer acceptance,
cross-volume receiving qualification, complete real-owner evidence or permission
for adoption. Those remaining gates stay open.

The fresh-process fixture now removes inherited Git repository selectors and
disables inherited global/system configuration before every child launch.
Its negative control supplies unrelated repository/index selectors and invalid
configuration, exercises the complete flow, and verifies that the unrelated
fixture repository remains byte-for-byte unchanged. The reviewed source
candidate passes all seven Windows controls on Python 3.13.17 through the
canonical contained runner, including preparation and verified cleanup.
This fixture correction does not change production relocation admission.

## Validation Plan

The [focused validation matrix](validation.md) defines assertions and tier
boundaries. It is not a claim that runtime checks have already passed.

- [x] Proposal: effort/vision structure, documentation consistency, public-safety
  audit, actual repository review verdict and required CI before merge.
- [x] First flow: real linked Git checkout, stopped-clean acceptance,
  complete transfer and post-move consumers on same and different volumes;
  blockers and injected interruption preserve original/recovery custody.
- [x] First-slice mixed-root source contracts: existing path/resume-entry,
  mutation/publication plus new allocations;
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

### 2026-11-12 — Bounded operational-reference scan work
- A metadata-layout regression reproduces the database reference timeout
  below its entry cap: per-file native normalization repeatedly walks the
  same candidate root and shared parent directories.
- Prepare those directory resolutions only within the existing bounded
  read and revalidate identity/type/canonical destinations before a negative
  result. Preserve complete row coverage, real aliases and unsupported inputs,
  the original entry/time budgets and true retained-reference refusal.
- This closes the existing reference-discovery implementation rather than
  qualifying historical session migration. Real retained Copilot references
  remain an explicit owner-contract gate; no database repair, history rewrite,
  installed rollout or real target move follows from source qualification.

### 2026-11-11 — Explicit live admission and cross-volume receiving source flow

- Intent [#12178](https://github.com/ThomasMichon/copilot-extensions/pull/12178)
  cleared actual approval and required CI before implementation. Pure planning
  stays effect-free; separately requested live admission uses existing native
  owners and participating operation locks, not an activity-neutral prerequisite.
- The focused native source flow uses real existing-server observations without
  starting or stopping a server, distinct physical source/destination volumes,
  a concluded receiving allocation and settled creator responsibility. It
  preserves original proof through native copy, Git repair, location publication,
  fresh-process consumers and a new allocation under the changed synthetic
  default. Checkout payload is bounded to 1 MiB.
- Busy/unknown bound-process admission, existing-move refusal, owner changes
  after copy and pure-plan separation retain focused controls. A live result is
  point-in-time evidence, never a cached permission for apply. The native case
  includes exact owned Git and lock-sidecar cleanup; a cleanup failure is not
  reported as complete qualification.
- This qualifies the supported source component, not installed rollout, a real
  retained-worktree pilot, complete unreadable-process coverage, retained
  conversation resume or the broader inactive classes. Keep this effort and
  #10879 Active; normal source review/CI/merge and accepted Main release remain
  independent gates.

[2026-11-09 — First receiving current-location component](journal/2026/11.09%20Receiving%20current-location%20component.md)
records independent component qualification and the remaining native-owner gate.

[2026-11-08 — Owner observation and receiving proof](journal/2026/11.08%20Owner%20observation%20and%20receiving%20proof.md)
records the source-confirmed boundary and current prerequisite proposal.
Earlier inline entries below remain historical source evidence.

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

### 2026-11-06 — Receiving fixtures corrected; baseline blocker isolated
- Corrected the paired-carve test doubles for the new receiving-project
  configuration seam without weakening production validation. All **27**
  paired-carve controls passed. Updated the actual-knowledge tuple fixture to
  supply its own receiving configuration and assert its independent allocation
  root; the **486** receiving/tuple controls passed across the affected packets.
- Creation-tail expansion passed **139** controls but exposed one unchanged
  concurrent-output failure:
  `test_creation_retains_companion_lifecycle_through_guidance_against_cmd_embody`.
  The isolated test fails both on the candidate and the unchanged pre-feature
  base `dc1c5fdd26ab0693c181793f0d4ff57bc5eaff6a`, with `JSONDecodeError`
  from an empty captured JSON string.
- The parent doctor and child embodiment both enter the process-global
  `capture_json_output`, which swaps `sys.__stdout__`; the output and embodiment
  receipt modules are unchanged by this effort. No unrelated runtime output
  redesign or silent failing-test exception was applied.
- **Merge remains held** pending bounded triage disposition: stabilize only
  the concurrent test's parent-report capture while preserving its lifecycle
  assertions, obtain a separately owned fix, or obtain a repository-approved
  tracked exception. The recommended smallest scope is the fixture-only
  correction. Review/CI success alone does not waive this observed failure.
- The first-flow runtime/real-volume proof remains retained. The source
  worktree, umbrella and broader effort remain Active; no rollout or real
  retained-worktree move is implied.

### 2026-11-06 — Bounded test prerequisite resolved
- Applied the admitted test-only correction: the concurrent doctor stores its
  parent report through the existing interceptor, while child emissions still
  reach the real embodiment receipt capture. No production output code changed.
  Every lifecycle, waiting/spawn, seed, concurrency and result assertion remains;
  seed redaction and single parent-report emission are explicitly asserted.
- The isolated previously failing control passed. Its affected continuation
  packet passed **44 controls**, including all subsequent creation-repair
  assertions, without skipping or waiving the original baseline failure.
- Grounded the unpublished JSON concern at the execution-space fence:
  relocation refusal now retains the existing `ExecutionSpaceError` contract
  instead of leaking raw `ValueError`/`OSError` past structured callers.
  Actual interrupted-move and execution-space controls passed **144 controls**
  with one explicit POSIX skip. Refreshed only that admitted source digest;
  unit node/effect/coverage inventory is unchanged.
- Requested the exact unpublished permission-lockout scenario rather than
  treating its summary as clearance. Initial restrictive-directory/metadata
  admission and non-private destination refusal remain explicit v1 boundaries.
- The old baseline-capture hold is resolved. Fresh review/required CI and any
  concrete remaining source findings still gate merge; release, activation,
  actual migration and broader inactive support remain separately open.

### 2026-11-06 — Current contract capture and post-sync qualification
- Pulled the owned PR onto current `dev` with a durable pre-sync backup.
  Upstream expanded the Bridge source registry to include the Worktrees writer.
  Its current semantic-owner rule requires fresh HTTP-52 capture, even though
  the relocation addition is only a resident-lock capability advertisement.
  Did not weaken that owner rule or alter historical evidence to silence CI.
- Used the canonical source-only capture lane at
  `e5ff19e01e7cc900cfd642459b1f2bcb8f86a83b`:
  [run 42287539825](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42287539825).
  All **252 controlled cases**, independent no-write verification, immutable
  historical guards and the contract guard passed. Materialized only its 140
  source-bound current outputs through the canonical publication owner.
  Local validation passed **2 contracts / 1051 fixtures**; HTTP generations,
  capability floors and HTTP-51/earlier historical evidence are unchanged.
- Post-sync source controls passed: **44 native**, **44 affected creation**,
  **143 execution-space**, **27 paired-carve**, **50 writer** and **11 Bridge
  owner-record consumer** controls. Native skips remain explicit; no installed
  or adopter qualification is inferred.
- The extra external-volume roundtrip completed its first transfer, then hit
  the runner's 30-second item limit during return-leg process inspection. The
  external synthetic copy was retained, but runner teardown retired the
  original primary Git/journal. Preserved its diagnostic/payload evidence and
  did not silently delete or replay the failed external fixture.
- Corrected the rig: external qualification now requires an explicitly enrolled
  persistent primary home, retaining Git/record/journal beyond runner teardown.
  The combined roundtrip has a focused 120-second budget; production probes
  are unchanged. Its fixture-lifetime and unchanged native CLI packet passed
  **3 controls** without creating a fresh external fixture.
- Retained external-fixture disposition and the corrected real-volume retry
  remain an explicit owner gate. Source review/CI/merge, actual runtime
  selection, adopter pilot and broader inactive support are still separate.

### 2026-11-06 — Concrete custody and sweep findings closed
- Refused ignored nested Git metadata before custody, including Windows
  case-equivalent markers, preserving both outer and nested registrations.
  Included itemized open/pending-transfer follow-ups in responsibility checks.
- Made non-object permission JSON and malformed workspace YAML controlled
  refusals, and used the existing validation output helper. The real nested
  worktree, obligation, schema-race and CLI packet passed **11 controls**.
- Preserved the lifecycle guard's existing `TimeoutError` refusal contract:
  interrupted relocation now skips only its record rather than aborting an
  entire cleanup sweep. Batch and sweep controls proved an unrelated eligible
  record is still processed while the pending record/journal stay unchanged.
  Its focused packet passed **5 controls**, with one explicit POSIX skip.
- These are qualification/safety corrections to the conservative first flow,
  not broader WIP/session/pair support. Current source review/CI/merge and the
  retained external-fixture disposition remain open.

### 2026-11-06 — Final publication ownership boundary
- Reconciled the next upstream change without overwriting its newer contract
  evidence or effort entries. Kept the relocation advertisement in the
  Worktrees status-publication composer, only after its writer server is
  actually published. The externally observed generation-1 capability is
  unchanged; the early ownership stamp still does not advertise readiness.
- The Bridge-owned `tracking_write.py` and canonical upstream contract corpus
  are byte-identical to current `dev`. The earlier source-only capture remains
  a verified experiment in durable backup/history and its public workflow,
  not a competing final contract refresh. Removed the unnecessary Bridge
  changefile; no Bridge runtime/protocol change is included.
- The actual isolated monitor publication plus writer/refusal packet passed
  **52 controls**. Canonical contract validation passed **2 contracts / 1054
  fixtures** against the preserved upstream evidence. No historical capture,
  version floor or semantic-owner guard was weakened.
- The whole feature/umbrella remain Active. Current-head source review/CI/merge,
  retained failed-fixture disposition and the corrected real-volume retry are
  still explicit gates; actual activation and adopter migration remain held.

### 2026-11-06 — Mounted storage and malformed-ledger review controls
- Made POSIX manifest admission fail closed on missing, malformed or oversized
  native mount inventories. Root/subtree and file bind mounts are refused
  independently of device numbers, including escaped mountpoint names.
  The existing retirement manifest rechecks this before deleting the source;
  cancellation now inventories its owned stage before deleting that as well.
- Caught malformed tracking-ledger YAML in the structured move CLI boundary,
  including original-operation recovery. Negative controls preserve the exact
  ledger and existing journal and never publish a destination.
- The focused Windows packet passed **8 controls**. The Linux-only isolated
  same-device bind-mount control is present but unrun on this host; it requires
  an admitted unprivileged private mount namespace, never an elevation fallback.
  POSIX native qualification remains open. Missing Linux mount-table support
  is an explicit refusal, not an assumption that no mounted data exists.
- Exact-source lint, the existing module budgets, documentation consistency
  and the source-history install-contract guard passed. The source umbrella,
  current-head review/CI/merge and retained-fixture disposition remain open.

### 2026-11-06 — Native fixture ownership reconciliation
- The expanded native packet initially refused unscoped mux queries through
  the test runner's existing command-safety boundary. Read-only diagnostics
  identified that exact `UnsafeTestCommand`; session-lock resolution was empty.
  No production inactivity check was weakened or bypassed.
- Scoped the fixture's real mux transport through the runner-owned private
  namespace helper. Namespace enrollment stays inside the runner sandbox even
  when separately enrolled primary Git/recovery evidence is retained elsewhere.
  Only read-only mux queries were requested, never server launch/stop;
  no surviving private test mux process was observed after the bounded attempt.
- The complete native move, prepublication cancellation, malformed-ledger
  plan/recovery and mount-inventory packet then passed **10 controls** with a
  selected 120-second native-case budget and bounded file wall time. Production
  probe budgets and every lifecycle/data/identity assertion are unchanged.
- The approved concurrent creation-repair capture prerequisite is resolved by
  its previously recorded isolated and 44-case continuation evidence. The
  source effort/umbrella stay Active; current-head review/CI/merge, the retained
  external-fixture disposition and corrected real-volume retry remain open.

### 2026-11-06 — Literal metadata durability and permission assessment
- Did not treat the latest “No open issues” overview as clearance: it also
  alleged a critical crash-recovery gap and a permission-check bypass without
  publishing locations. Requested concrete details and inspected its completed
  reviewer diagnostics, which identified the checkpoint advance and permission
  writer sites.
- Persist Git administrative/checkout pointers before advancing to
  `git-repaired`, and persist the ledger and affected permission files before
  advancing to `rebound`. Failed barriers retain the preceding retryable
  checkpoint and the same original stage/identity; recovery does not strand
  the operation behind a phase that outran durable effects.
- Recheck actual metadata mutation access under the existing permission-owner
  locks immediately before writing. Withdrawal after capture now refuses
  without silently replacing the protected permission file.
- Current-source native CLI, both new durability cuts, both permission-access
  withdrawals, all five post-effect interruption cuts, exited-caller recovery
  and the previously timed-out copy-recovery case passed **13 controls**, with
  one explicit POSIX skip. A bounded Git-probe expiry also now produces a
  structured refusal rather than a traceback; production Git budgets remain
  unchanged. The earlier expanded run's single Windows pipe timeout is not
  relabeled as an understood or fixed global Git defect.
- Reconciled upstream through the backed, one-to-one replay, retaining every
  owned commit and current upstream admission inventory. Source publication
  still awaits the exact current-head required CI and real review assessment.
  The umbrella/effort and retained-fixture disposition remain open; no activation
  or adopter data effects are implied.

### 2026-11-07 — Literal path, publication replay and archive controls
- Current-head required CI passed, but the next actual review identified three
  additional recovery defects; CI success was not treated as review clearance.
- Reject literal recorded spellings normalized by `Path` before custody,
  including dot components and trailing separators. The journal and exact
  ledger spelling cannot diverge after publication.
- Synchronize the destination parent before the `published` checkpoint even
  when recovery finds that rename already succeeded. A post-rename sync failure
  now retries that barrier rather than silently skipping it.
- Archive through a synchronized temporary file and atomic exclusive
  publication. Partial writes never occupy the immutable final history name;
  the complete current terminal receipt remains present throughout failure
  and retry.
- The dedicated native recovery packet passed **9 controls**, including the
  new literal aliases, post-rename retry and partial archive write, alongside
  the preceding durability/access/schema controls. The source objective remains
  Active and publication still requires fresh exact-head review and CI.

### 2026-11-07 — Destination references before consequential effects
- The next real review had no published open threads but alleged missed retained
  destination references. Independent source inspection confirmed that planning
  checked only the old location, while the destination check occurred after
  publication and rebinding. Did not interpret the empty issue list as clearance.
- Check the destination's retained workspace/database references during read-only
  admission and again before publishing the copied checkout. Previously known
  references now refuse without custody; a reference appearing during copy
  remains on the cancellable side of publication.
- The dedicated native recovery/admission packet passed **12 controls**. It
  includes byte-preserving workspace and database refusals and a post-copy
  destination reference followed by successful prepublication abort. The prior
  exact-source CLI/roundtrip/history consumer packet passed **3 controls**.
  No session database, retained workspace, actual adopter record, service or
  default was changed. Broader session support and the whole effort remain open.

### 2026-11-07 — Durable original-name retirement and retained-proof refusal
- The next real overview again had no published issues but explicitly named
  unpublished retirement-durability and resume-timeout concerns. Completed
  diagnostics identified original-name removal before `complete` and the
  retained Git-proof subprocess boundary. Did not infer review clearance.
- Retire the verified original through an exclusive, native write-through
  rename to an original-stage-token name before removing its unchanged
  duplicate. Recheck that copy and synchronize the old parent before completion.
  Recovery handles both interruption after rename and after removal without
  allocating a replacement operation or silently dropping original custody.
- Read-only Windows admission now proves the old parent's required mutation
  access as well, before custody. The existing partial-retirement injection
  still removes one duplicate file and verifies recovery, destination bytes,
  nonce preservation and removal of the positively owned retirement copy.
- Convert retained Git-proof expiry to an explicit unavailable-authority refusal:
  no fabricated namespace proof and no raw timeout from the resume predicate.
- The dedicated native recovery packet passed **15 controls**. Current complete
  native/API and actual CLI/roundtrip flows, all five post-effect interruption
  cuts and all three real Windows access-denial cases passed **11 controls**.
  Production probe deadlines and the concurrent creation-repair assertions are
  unchanged. Current-head CI/review/merge, the retained external-fixture
  disposition and broader inactive support remain open.

### 2026-11-07 — Platform-independent retirement test cut
- Addressed the next concrete review finding by targeting the later
  post-deletion parent barrier explicitly. POSIX's earlier synchronization
  inside rename is allowed to finish while the retirement copy still exists;
  the test retains its exact post-removal and original-operation assertions.
- Both focused native retirement cuts passed. This is a test-only correction;
  production behavior/deadlines, the existing creation-repair concurrency/seed
  assertions and the broader Active completion gate are unchanged.

### 2026-11-07 — Structured output during stale-lock recovery
- Independently verified the next unpublished overview concern: the shared
  finalization lock's default stale warning can precede a move's JSON result.
  Added an optional diagnostic sink without changing existing lock consumers,
  claim semantics, output helpers or process-global streams. Move and abort
  direct only that warning to stderr.
- An actual CLI move recovered a newly owned stale fixture lock, completed the
  real flow and emitted one parseable JSON object on stdout with its diagnostic
  on stderr. The focused native control passed. This is not a production
  output redesign or a change to the concurrent creation-repair assertions.

### 2026-11-07 — Fresh interpreter transport custody
- Addressed the next concrete review finding: parent-only mux scoping did not
  isolate fresh CLI/recovery interpreters. A fixture-owned shared bootstrap now
  validates the exact runner namespace and installs the command guard before
  application work, preserving the actual module CLI entry point. Invalid or
  missing scope exits before native work rather than continuing unguarded.
- Every fixture child inherits the scoped transport and bootstrap; no runtime
  routing policy, operator endpoint, service, default or production module was
  changed. The Linux namespace fixture uses the same explicit bootstrap.
- Actual CLI, exited-caller recovery, workspace/ledger JSON refusals,
  stale-lock output and fresh-child guard/refusal controls passed **9 cases**.
  The negative guard control uses a nonexistent fixture executable, so a failed
  bootstrap cannot accidentally query another host namespace.

### 2026-11-07 — Source deletion intent versus owned cleanup
- Independently confirmed the next unpublished overview concern: accepting a
  subset before retirement can mistake a post-copy ignored-file deletion for
  already completed cleanup. Source equality is now exact before publication
  and before retiring the old name; no deleted source content is silently
  restored from the earlier copy.
- A complete retirement copy and its parent barrier precede durable `retiring`
  custody. Only that checkpoint permits subset verification during recovery
  of an actually started owned removal. Original stage identity remains fixed.
- The dedicated native packet passed **20 controls**, including deletion
  immediately after copying, after pointer changes, and after rename but before
  verified cleanup. Prepublication deletion remains cancellable; later drift
  preserves both views and refuses completion/retry without guessing intent.
  Positive partial cleanup still recovers through the original operation.
- The source umbrella and effort remain Active. This safety correction does
  not add inactive WIP support, authorize activation or settle the separately
  held external-fixture disposition.

### 2026-11-07 — Missing originals and durable companion reservations
- Completed reviewer diagnostics identified a missing-original recovery path,
  stat-only stream bounds and companion reservation publication without a
  durable parent barrier. Corrections stay within the existing first-flow and
  mixed-root prerequisites, not additional inactive categories.
- Missing source data before owned retirement now refuses rather than adopting
  an earlier destination snapshot. Actual stream reads must match the admitted
  stat size, so concurrent growth/shrink cannot evade the bounded inventory.
- Companion intent is completely written/synchronized under a temporary name,
  published exclusively and parent-synchronized before returning to allocation.
  Exact retries reuse the immutable original; changed-default conflicts refuse
  rather than silently selecting another companion root.
- The six focused native failure controls passed. Existing companion consumers,
  original creation-receipt replay and actual CLI/roundtrip acceptance passed
  **30 controls**. No operator data, runtime, default or real worktree moved.
  The source objective remains Active and actual current-head review/CI/merge
  are still required; no disposition or rollout authority is inferred.

### 2026-11-07 — Complete inventories and cancellation checkpoint ordering
- Completed reviewer diagnostics identified swallowed directory errors in
  session/launch inventories and the absent post-cancellation parent barrier.
  Operational matching now uses bounded explicit enumeration; unreadable,
  redirected or incomplete directories refuse instead of appearing empty.
- Applied the same fail-closed enumeration to checkout, access, copy-security
  and durability inventories. Unknown subtrees cannot be omitted from the
  qualified content or synchronization set.
- The cancellation parent barrier now precedes `cancelled` and repeats during
  recovery even when the stage has already disappeared. Source and destination
  authority, original operation and source edits remain unchanged.
- Focused inventory/cancellation controls and preserved companion retry
  assertions passed **9 cases**. The resulting strict-inventory, actual
  CLI/roundtrip and all five existing effect-cut packet passed **14 cases**.
  A local test-collection indentation error was corrected without deleting
  assertions before these passing runs. Current source review/CI/merge remain
  mandatory; this does not widen inactive scope or authorize operator effects.

### 2026-11-07 — Qualified first platform and historical receiver root
- The next completed diagnostics identified unbounded inline POSIX copying and
  historical no-intent receiver selection. Kept the first complete source slice
  narrow rather than adding new copy infrastructure: v1 relocation advertises
  qualified native Windows and explicitly refuses other native platforms before
  receiving-record/custody operations. The native owned Windows copier retains
  its deadline and output budgets; bounded POSIX copying/native parity remain
  open source acceptance gates, not silently enabled support.
- Existing parent ledgers without a new intent retain their original
  anchor-derived companion allocation even when the caller supplied no explicit
  retained path. A real reserved new intent still selects its independently
  configured original root. No immutable receipt is rewritten.
- Platform refusal, historical/new intent distinction, existing companion
  consumers and the actual Windows CLI/roundtrip packet passed **31 controls**.
  The design and public scope now name this staged platform boundary explicitly.
  The generic umbrella/effort stay Active; review/CI/merge and subsequent adopter
  qualification remain separate, with no rollout or broader inactive work.

### 2026-11-07 — Current-source native cross-volume crash requalification
- Settled the prior failed synthetic fixture through its resource owner without
  fabricating the lost original Git/ledger/journal. Its protected failure
  evidence remains intact; no adopter data or retained worktree was involved.
- The corrected focused test at source `24728105d2` completed a real Windows
  two-volume outward/return flow with original Git, ledger and journal retained
  outside runner teardown through terminal result.
- Abruptly exited a newly owned child after destination publication, verified
  dead/reused caller incarnation and read-only recovery, refused cancellation
  after publication, then completed the original operation without a new stage.
  On return, abruptly exited after source-retirement rename and recovered that
  same original operation from its retained retirement copy.
- The focused native case passed. Final raw content manifest, ignored bytes,
  branch/HEAD, worktree identity and creation nonce matched the original;
  terminal journal was `complete`. Successful external fixture cleanup was
  confirmed while the complete primary authority and proof remained retained.
- This closes the previously held source fixture requalification item, not
  the broader objective or an adopter/runtime approval. Fresh exact-head CI and
  actual review remain required before merging the existing first-slice PR.

### 2026-11-07 — Retained publication stays in its native execution space
- Independently confirmed the next overview concern: owner/Git/ledger agreement
  did not by itself reject a retained foreign-platform record. The retained
  predicate now requires both record and configuration to match the actual
  native platform and retains the current execution-space/full typed tuple
  check before granting path authority.
- Both foreign-record and foreign-configuration publication controls refused
  without record changes. Valid old-root publication and current actual
  CLI/roundtrip acceptance remained passing: **5 focused native controls**.
  This tightens the original receiving-authority invariant; it adds no
  inactive category, runtime effect or allocation preference.

### 2026-11-07 — Native relative Git registration paths
- The next diagnostics identified relative Git administration backpointers
  resolved against the invoking process rather than their owning administrative
  directory. Resolve those paths from Git's actual owner without weakening the
  final recorded-checkout/Git-family comparison.
- Native Git relative-worktree repair produced a real relative backpointer;
  retained authority and old-root publication passed, while a redirected
  relative backpointer refused. Together with platform-boundary and actual CLI
  controls the focused packet passed **5 cases**, with no simulated native lane.
  The first-slice scope and wider Active completion gate remain unchanged.

### 2026-11-07 — First source slice merged; objective remains Active
- [#10943](https://github.com/ThomasMichon/copilot-extensions/pull/10943) merged
  into `dev` at `0614ce52de5c21fbeb6eb193464ba1c644aa0e9e`
  (`2026-11-07T05:53:44Z`). Exact reviewed head
  `2f261333b3315a0371aa24fb115e5fe2048d968d` had required CI green,
  resolved threads and no stored/published code findings in actual review
  `5810404347`. The remaining native-platform scrutiny was explicitly assessed
  against the retained real Windows two-volume/abrupt-crash proof, not inferred
  from an empty finding count. Used the normal role-scoped managed self-merge;
  no failed check or unresolved code finding was bypassed.
- Re-read this effort and resolved only the first-source-slice outcomes.
  Pulled the retained worktree onto the merged `dev` through the native owner.
  Installed-generation qualification, actual adopter resume/pilot, POSIX,
  broader WIP/session/pair/location-authority acceptance and version/rollback
  coverage remain in this effort's open Phase 2/3 and validation gates.
- This journal/checklist reconciliation is source-only post-merge bookkeeping,
  published through the normal small documentation review/CI/merge flow. It
  does not start another implementation stretch or imply runtime activation.
- Keep #10879, the canonical source effort and source worktree/parent ownership
  Active. Await separately owned first-flow adoption feedback or explicit next
  source steering before widening. No installed runtime, service, allocation
  default or real retained worktree was changed by source delivery.

### 2026-11-07 — Bounded repository-wide release prerequisite
- Claimed only the distinct native-executable fixture follow-up on #10945 and
  #10946 after checking current source, open/merged PRs and existing ownership.
  The earlier dependency and private-mux repairs remain separate accepted work.
- The synthetic Python owner must be a native executable, preserve literal
  argv/stdin/exit status, and delegate the current finite Driver contract probe
  to the actual selected test runtime instead of forcing an older fallback.
  Its Framework stdin writer must not insert a UTF-8 preamble when a native
  mux query changes the console code page. Production launchers remain unchanged.
- The corrected complete native Windows packet passes all seven cases,
  including both real four-concurrent Resume/prewarming variants, copied setup,
  argument receipts, ConPTY attachment/detach and exact owned retirement.
  The additional control exercises the actual admission function before and
  after a real private mux query, plus native argv/stdin/nonzero exit status.
- This below-altitude fixture repair is a source release prerequisite, not
  broader relocation acceptance or installed-generation qualification.
  Keep this effort and #10879 Active; normal source review/CI/merge and actual
  automatic Main qualification/publication remain independent gates.
- A later pull-forward changed independent shared-source dependencies. The fresh
  current-source owner/admission control passed, but both concurrent cases
  reported unconfirmed native mux creation. Preserve that exact-source gate;
  the initial complete pass is not a replacement for current qualification.
  Use the bounded hosted Windows lane before treating the repair as reviewable.
- The generic hosted selection was exercised, but lacked PSMux: its five native
  prerequisite assertions failed and two dependency controls passed. It is not
  qualification; no skip or controller expansion was made. After the owned
  source scans finished, exact public candidate `6ed22481da81a370d6849a685385f9e7284d275f`
  passed all three affected controls, including both complete concurrent
  prewarming flows, with unchanged code/assertions/budgets. The independent
  unchanged controls retain their earlier full-packet evidence; these disjoint
  runs do not claim a current aggregate budget. Normal review/required CI and
  full exact-candidate Main qualification/publication remain required.

### 2026-11-07 — Release prerequisite source merged; acceptance remains scoped
- [#11278](https://github.com/ThomasMichon/copilot-extensions/pull/11278) merged
  into `dev` at `b445fec25666fc21e43070ebf22b181eb401f7e3`
  (`2026-11-07T08:16:53Z`). The actual current-head Copilot approval
  `5811005168` and required CI passed. Its Medium ConPTY output-reader race
  was corrected, validated in the real native control and resolved before merge.
- The final correction only moves the new final-output assertions after
  console close/reader join. Existing concurrent Resume assertions and
  production launchers remain unchanged. The source worktree is pulled forward;
  this effort, #10879 and the receiving source ownership remain Active.
- Ordinary Dev CI and automatic Main native qualification, attestation and
  publication remain separate from this source merge. No installed selection,
  allocation default, shared service or real retained worktree was changed.

### 2026-11-09 — Pure refusal source merged; release acceptance remains separate
- Reconciled normal pinned dependency acquisition and isolated native CI as
  permitted validation, without requesting an upstream contribution or any
  shared-host/real-target operation. The previous fetch-scope hold is cleared,
  not replaced with a skipped gate or artifact workaround.
- [#11706](https://github.com/ThomasMichon/copilot-extensions/pull/11706) merged
  at `5f481011e3c682b417fe8816acda87317ad9f29f`
  (`2026-11-09T20:36:00Z`). Actual Copilot approval `5832301655` is bound to
  reviewed head `6f089865ad5e642054c0f1e8b840fe0d47d234be`; required checks
  passed and the inline thread was resolved. The configured Maintainer
  exception concerns only the additional CODEOWNER vote, not review or CI.
- The exact-source packet passed 14 controls: passive native CLI refusal with
  unchanged state/file identity and forbidden-invocation poison, canonical
  lifecycle marker location, strict Git-timeout refusal and retained strict
  recovery-fixture routing. No new cross-volume fixture, positive native
  inactivity proof or real move is claimed.
- Resolved only the independent pure-refusal source leaf after re-reading this
  effort. The ordinary automatic Main qualification/publication is still
  observed separately. Native complete-owner proof, original receiving
  continuity, wider inactive categories and adopter pilot/cohort remain open.
- Retain this Active effort, #10879, its source workspace and ownership for
  subsequent acceptance reconciliation. No installation, runtime/service
  change, allocation-default update or real checkout movement follows.

### 2026-11-12 — Ordinary admission compatibility correction
- Reproduced two first-flow compatibility defects with three focused
  before-fix regressions: generic parent ownership plus a nonce was mistaken
  for a missing receiving receipt, and literal workspace metadata discovery
  enumerated unrelated session artifacts against the reference budget.
- This closes the existing ordinary v1 contract and identity-preserving
  relocation vision; it does not introduce another allocation shape or
  weaken receiving continuity. Require original journal classification and
  exact settled generic parent responsibility; known receiving, ambiguous,
  malformed, busy and unsupported evidence stays held without recovery.
- Direct known-leaf lookup retains bounded wildcard enumeration, unchanged
  reference consumers and changing/redirected/unavailable-directory refusals.
  Checkout-data limits and native owner admission remain unchanged.
- Source qualification, review, required checks and accepted Main publication
  remain separate gates. The real-Git component control is not native host
  inactivity proof; no installation, real target reconciliation or migration
  follows from it. This effort and #10879 remain Active.
