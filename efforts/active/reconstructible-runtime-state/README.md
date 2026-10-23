# Reconstructible Runtime State

- **Slug:** `reconstructible-runtime-state`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PR worktrees targeting `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** closes `visions/agent-fabric/README.md` concepts
  `resource-accountability` and `trusted-cooperation-with-economical-evidence`;
  consumes `visions/plugins/agent-bridge/README.md` behaviors
  `readers-expand-before-writers` and `session-contract-survives-default-changes`
- **Umbrella issue:** #7947
- **Related issues:** #1460 · #1915 · #3761 · #7935

## Guiding Intent

Reconstruct disposable world-model projections and executable installations from
surviving authorities and the minimum durable semantic facts. Missing cached
provenance is not proof of a competing owner; neither is a missing record proof
that obligations are settled. Preserve real effect-side authority and user work.

The reviewed plan has **G1-only implementation admission**: disconnected
facts/envelope/reader-preservation primitives and isolated durability fixtures.
No live backfill, deletion, force mode, G2 claim writers, G4 projection loss,
G5 Bridge/installer changes, runtime reset or deployment is admitted.
Force rebuild remains a design option requiring a separate operator decision.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Recovery driver | #7947 G1 isolated primitives/fixtures only | Isolated source PR, public handle `threaded-compass` |
| Worktree ground-state owner | Record transactions, claims, lineage and adoption | #3761 and its existing source claims |
| Compatibility foundation owner | Bridge durable contracts and mixed-version evidence | #1460; prerequisite evidence #1915 |
| Component owners | Installer custody, Dispatch task semantics and provider leases | Their existing issue/PR ownership; no takeover |
| Campaign admission owner | Accept interfaces, assign later slices and decide force-rebuild scope | Owning planning process; no public-private back-link |

## Coordination

- **Topology:** one planning PR; later independent PRs with actual dependency
  edges in [design.md](design.md), never a repository-wide merge lock.
- **Host (owns PRs):** recovery driver owns the proposal and admitted G1. Each
  implementation PR requires a fresh issue/task collision check and named owner.
- **Delegates:** none for this plan. Any required independent review may use
  the repository's forge review or a newly owned native sub-agent.
- **Handoff:** return the reviewed proposal and unresolved decisions for
  campaign admission. #1460 remains the canonical compatibility foundation;
  this effort consumes it, not a replacement or duplicate.
- **Execution boundary:** no commandeering, reading, messaging or collaborating
  with pre-existing Bridge agent sessions; no agent-chat; no remote dispatch.
  Only this effort's native sub-agents or newly created, owned local Bridge
  workers are eligible. Use read-only forge/task records to detect collisions;
  report overlaps to the admission owner without contacting the other agent.
  If review requires an existing Bridge agent, record a blocker instead.
- Preserve all existing PRs and claims. Manager constructor/compose/runner and
  project configuration (#7938), engine bootstrap (#7974), launch maintenance
  (#7788), readiness/guidance preparation (#7943), status feeds/receipts
  (#7936/#7937/#7946), telemetry (#7935/#5900), mention guarding (#7941), and
  handoff receipt observation (#7942) are not implementation scope here.
  Gateway deployment, services, shared installation and rollout are excluded.

## Context

Source baseline: `b2ed7bf3b24e47e82197f36db2ccd7a2292902fb`. The focused trace
and proposed store taxonomy are in [design.md](design.md); the acceptance matrix
is in [validation.md](validation.md). This is not a new suite-wide audit.

Today, worktree records still contain otherwise irreplaceable decisions.
`claim_history.record_event` is PR-only and best-effort, after the record save;
it cannot replace those records. `session_projection.validate_restored_hint`
can report a missing central record as `restored-foreign`, while
`finalize_obligations.assert_obligations_settled(None, ...)` permits cleanup.
Both need a typed recovery-completeness boundary, not blanket guard removal.

Architecture reconciliation: retain `docs/patterns/README.md`'s independent
plugin operation, report-first doctor, marketplace-qualified artifacts and
immutable runtime ownership. Reconstructed installation context must pass the
existing authoritative adoption/installer boundary; arbitrary marker bytes
never grant authority. No new daemon, security framework or telemetry store
is proposed.

## Request

Public-safe excerpts of the bounded request; private coordination identifiers
are intentionally omitted:

> "No functional implementation in this turn; return for admission"
>
> "Must distinguish older elected/current rollback SOFTWARE from a
> revoked/superseded WRITER incarnation; old compatible software should remain
> usable, current-destructive-effect owner/target/revision checks stay."
>
> "Plan preserve-first dry run, no unknown-file deletion; no rebuild from lossy
> telemetry."
>
> "Preserve Git/index/stashes/ignored/untracked edits and all
> session/checkpoints, bindings/accounts/consent/credentials,
> tasks/claims/leases/lineage/obligations, accepted/uncertain external effect
> journals."
>
> "Quiesce/fence or defer live work; recreate projections separately, health
> gate/select/rollback."
>
> "Force-rebuild is DESIGN ONLY until parent/user choice; never run destructive
> live recovery."

The requested tests cover projection loss, crashes at every transaction step
including after acknowledgement, current older readers/writers, revoked newer
writers, revision races, unsupported schema, bounded ordinary-read I/O and
rebuild equivalence. The execution restriction in Coordination applies to the
entire effort, including review and recovery qualification.

### G1 admission

The operator selected **`resource-markers-and-git-common-journal`**: a small
gitignored marker inside each worktree plus the durable journal under the Git
common directory. Admission is isolated fixtures only with lossless readers,
foreign-path refusal, and relocation/backup semantics. Resource markers are
evidence, not a competing semantic writer. The initial claim adapter remains
proposed, with no #3761 takeover; later slices and live activation remain gated.

## Plan

All mechanisms and names below are **agent-recommended proposals**, not
implemented APIs or accepted execution assignments.

### Phase 0 — Review and admission
- [x] Trace the bounded current-source recovery and compatibility seams.
- [x] Capture taxonomy, preservation, transaction and ownership contracts.
- [x] Publish and merge the source-only proposal through repository review (#8025).
- [x] Obtain G1-only campaign admission and approve resource-marker/Git-common placement.
- [ ] Obtain campaign admission of remaining implementation slices and resolve decisions
      in [design.md](design.md#decisions-before-implementation).

### Phase 1 — Durable facts before disposable projections
- [x] G1 admitted slice: disconnected envelope codecs, lossless candidate edits,
      read-only marker/journal placement inspection and isolated durability fixtures.
- [ ] G1: add versioned resource-local identity and surviving per-resource
      semantic snapshots/journal to actual owner paths after further admission;
      isolated codecs/fixtures do not complete production persistence.
- [ ] G2: journal intent/result and tombstones at the owning mutation boundary;
      preserve accepted/uncertain outcomes before acknowledging recoverability.
- [ ] Prove migration completeness before classifying any existing authoritative
      record/table as disposable. See [store taxonomy](design.md#store-taxonomy).

### Phase 2 — Scoped observation and reconstruction
- [ ] G3: add report-first candidate discovery and typed completeness without
      claiming ownership; preserve ordinary observation on a missing cache.
- [ ] G4: rebuild an inactive compatible projection from surviving anchors,
      check equivalence, then fence/quiesce or defer before selecting it.
- [ ] Keep legacy adoption and missing-record cleanup paths under their
      existing owners; consume explicit interfaces rather than edit their lanes.

### Phase 3 — Compatibility and optional recovery publication
- [ ] G5: obtain #1460/#1915 reader/writer evidence; integrate component-owned
      unknown-field preservation and actual incarnation fences.
- [ ] G6: implement scoped doctor only after admission; force-rebuild remains
      disabled/unimplemented unless separately selected and reviewed.
- [ ] Qualify cost, crash/replay, rollback and platform evidence, then request
      any separate deployment authorization. No shared rollout is owned here.

## Validation Plan

- [x] Check plan structure, local references, public-safe scope and exact source
      anchors before publication.
- [x] Obtain independent source-plan review and required CI on the final head (#8025).
- [ ] Execute [the isolated acceptance matrix](validation.md) with no real
      installation deletion, external mutation or contact with existing agents.
- [ ] Prove semantic rebuild equivalence without a hidden old-database copy;
      preserve all protected bytes and uncertain/accepted effect outcomes.
- [ ] Prove older current software remains usable while a revoked newer
      incarnation cannot mutate; unknown required semantics refuse only that effect.
- [ ] Prove ordinary reads have bounded I/O independent of historical
      cardinality; explicit rebuild owns the historical scan.
- [ ] Publish platform and validation-tier results on each implementation PR,
      with explicit reasons for any skipped tier.

## Proposal

G1 plus isolated fixtures is admitted; all remaining G1–G6 gates are not an
executable reset command. G2 must precede any destructive loss test that assumes central
records are disposable. Force-rebuild policy, durable-anchor placement and
supported rollback-reader floor are explicit decisions, not implied approval.

## Journal

### 2026-10-22 — Bounded proposal
- Verified current #7947 and #1460 state, existing compatibility effort and
  read-only task/PR ownership. Claimed #7947's design slice only.
- Traced exact record, session projection, claim acknowledgement/history,
  finalization, lease serialization and installer ownership seams.
- Preserved existing source-owner boundaries and the no-existing-agent-contact
  restriction. No functional changes, live recovery, service work or rollout.
- Captured the requested constraints against the source request; mechanisms and
  staged implementation assignments are explicitly recommendations.

### 2026-10-23 — G1 admission
- #8025 merged as `e4cabc9718077469a4b6ca6ef307a2114914f611`, with final-head
  Copilot approval and green required CI; proposal/review checkboxes reconciled.
- Recorded the explicit resource-marker/Git-common journal decision and G1-only
  admission. No live writer, backfill, deletion or force mode is enabled.
- Fresh #1915 read shows closure by consolidation into #1460, not completed
  durable evidence. A supported durable rollback-runtime floor remains
  unestablished; G1 uses explicit synthetic operation capabilities without
  inventing a historical binary guarantee.
- Implemented the disconnected G1 slice in `recovery_facts.py` and
  `recovery_markers.py`, with no production caller or persistence/election API.
  `tests/test_recovery_facts.py`: **50 passed** through the contained plugin
  runner on Windows. This includes fsynced fixture bytes surviving close,
  relocation/backup copies, interrupted-frame refusal, recursive preservation,
  foreign-path refusal and two exact marker opens at 0/100/1,000 history files.
- Fixed the initial fixture's overlong pytest parameter ID, which exceeded the
  Windows environment-variable limit. The corrected full focused run is green.
  Ruff F/E9, install-contract, effort structure and documentation consistency
  checks pass. No historical binary, power-loss, production transaction or
  Linux qualification is claimed.
- Source review found an unknown optional exponent could decode to infinity and
  then fail re-encoding. Fixed the parser class for facts and markers; positive/
  negative exponent and large-integer regressions bring the final contained
  Windows focused run to **55 passed**, with Ruff F/E9 green.
- Follow-up review identified finite decimal underflow/rounding. Values that
  cannot serialize unchanged now return `number_precision_unsupported` before
  candidate exposure; supported values pass an independent Decimal oracle.
  Final contained Windows run: **66 passed**, with Ruff F/E9 green.
- Addressed subsequent review's Unicode canonical-size and marker-version
  classification findings. Final contained Windows run: **73 passed**; Ruff
  F/E9 remains green. These checks still add no production persistence path.
- Kept marker I/O failures distinct from corrupt metadata and bounded nested
  marker parsing after final classification review. Final contained Windows
  run: **79 passed**; Ruff F/E9 green.
