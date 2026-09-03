# Reciprocal Session-Worktree Metadata

- **Slug:** `reciprocal-session-worktree-metadata`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase worktrees landed serially to `main`
- **Created:** 2026-09-01
- **Status:** Active
- **Vision:** vision-extending for
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md)
  - reciprocal session projections and controller-aware recovery
- **Umbrella issue:** [#1635](https://github.com/ThomasMichon/copilot-extensions/issues/1635)
- **Sub-issues:** [#1643](https://github.com/ThomasMichon/copilot-extensions/issues/1643)
  (bound-session projection core) ·
  [#1671](https://github.com/ThomasMichon/copilot-extensions/issues/1671)
  (controller relations) ·
  [#1681](https://github.com/ThomasMichon/copilot-extensions/issues/1681)
  (bounded controller lineage reconciliation) ·
  [#1688](https://github.com/ThomasMichon/copilot-extensions/issues/1688)
  (restricted rescue portability) ·
  [#1692](https://github.com/ThomasMichon/copilot-extensions/issues/1692)
  (backfill and restored-hint validation) ·
  [#1695](https://github.com/ThomasMichon/copilot-extensions/issues/1695)
  (validated session recovery pointers) ·
  [#1700](https://github.com/ThomasMichon/copilot-extensions/issues/1700)
  (Picker and JSON reciprocal relation state) ·
  [#1706](https://github.com/ThomasMichon/copilot-extensions/issues/1706)
  (worktree and session lineage JSON surfaces) ·
  [#1712](https://github.com/ThomasMichon/copilot-extensions/issues/1712)
  (rollout hardening and convergence validation)

## Guiding Intent

Make the relationship between Copilot sessions and worktrees durable from both
directions without creating two authorities. The worktree record remains the
source of truth for binding, lifecycle, succession, and aggregate state. Each
exact session-state directory carries a small, versioned projection of the
worktree relationships that concern that session.

This reciprocal shape should let a resumed or synchronized session recover its
worktree, let a worktree find the terminal successor in a handoff chain, and let
tools group work across sessions without repeatedly rediscovering relationships
from directory paths or transcript content.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| agent-worktrees | Owns authoritative relations, projections, reconciliation, and recovery surfaces | Primary isolated worktree |
| agent-logger | Preserves projections through session synchronization without repeated copy churn | Same-repository serial slice |
| agent-containers | Preserves projections through restricted session-state rescue | Same-repository serial slice |

## Coordination

- **Topology:** independent serial PRs from one worktree
- **Host (owns PRs):** Primary worktree
- **Delegates:** None initially; focused review agents may inspect individual
  design or implementation slices.
- **Handoff:** The effort README and linked design are the durable continuation
  point. Only one implementation PR is active at a time.

## Context

The agent-worktrees record already owns a session registry, a monotonic
head-transition ledger, explicit predecessor/successor links, numbered
handoffs, and a bounded resident reconciler. Hot reads resolve exact
session-state directories by registered session ID; full session-state sweeps
are quarantined to explicit backfill or a fixed-budget resident cursor.

Several legitimate execution shapes are still difficult to represent:

- A session can control a separate worktree or pull-request vessel without
  being bound to that worktree.
- A stale worktree association may point at a predecessor that explicitly
  handed responsibility to one or more successors.
- Session synchronization preserves Copilot's session-state tree, but the
  synchronized session currently lacks a compact agent-worktrees relationship
  record that can travel with it.
- Historical grouping by worktree often requires comparing mutable CWD strings
  or reconstructing links from transcripts.

The existing `substatus.json` live-pulse sidecar and session-scoped context
spill files establish that plugin-owned, bounded files beneath an exact
session-state directory are an accepted integration surface. The new metadata
must remain a projection: losing it costs recovery speed and analytical
convenience, never authoritative state.

Detailed design: [`design.md`](design.md).

## Request

Public-safe transcription of the operator request:

> Design an inexpensive way for agent-worktrees to reconcile a worktree with
> the rightful terminal session in its handoff lineage, including work
> controlled from another session or worktree. Store reciprocal worktree and
> handoff metadata beneath each exact Copilot session-state directory so
> sessions can re-bind after losing context and synchronized session corpora can
> support worktree-centric visualization and analysis. Keep automatic repair
> bounded, safe for inactive worktrees, and independent of repeated full
> session-state scans.

## Plan

### Phase 1 - Intent, vocabulary, and ownership
- [x] Land the vision extension defining reciprocal session projections and
      controller relationships as distinct from session binding.
- [x] Extend the session-state-access pattern with exact-ID projection reads,
      bounded projection writes, and restored-state trust rules.
- [x] Finalize the schema and invariants in [`design.md`](design.md), including
      authority, cardinality, revision, privacy, and size limits.
- [x] Select the stable project identity used in synchronized projections and
      define cross-machine/session collision semantics before implementation.

### Phase 2 - Projection writer and lifecycle coverage
- [x] Add one atomic writer for the versioned session-state projection.
- [x] Update bound-session projections at session register/bind/end, head
      transitions, and handoff open/link/conclusion.
- [x] Update projections for controller assignment changes in Phase 3.
- [x] Preserve fail-open behavior: a projection write failure must not block
      session launch, handoff, source-control, or worktree lifecycle.
- [x] Refuse writes into session trees identified as restored/foreign until
      current local session provenance is established.
- [x] Never merge down or replace an unsupported newer projection schema.
- [ ] Add POSIX and Windows containment, permissions, symlink/reparse, and
      atomic-replacement tests, including case-folding, extended paths, and
      short-name aliases.

### Phase 3 - Controller relationships
- [x] Add a first-class controller relation distinct from a bound session.
- [x] Support one controller session operating multiple worktrees without
      registering that session as each worktree's bound head.
- [x] Capture controller identity at worktree or PR-vessel creation when the
      caller is known, including callers that start outside a worktree.
- [x] Expose normalized controller information through machine-readable
      worktree/session surfaces without changing head, liveness, occupancy, or
      resume semantics.
- [x] Expose terminal-successor findings after Phase 4 adds bounded,
      ambiguity-aware controller lineage resolution.

### Phase 4 - Bounded reconciliation
- [x] Extend the resident reconciler with a fixed-budget controller/head pass
      that uses record-local links and exact session IDs.
- [x] Follow explicit succession links to a unique terminal session; never
      select a successor by timestamp alone.
- [x] Mutate only dark, inactive worktrees under a nonblocking record lock and
      a compare-before-write revision check.
- [x] Report ambiguity, cycles, missing records, live conflicts, and restored
      foreign state without mutation.
- [x] Add a low-duty, one-shot scheduled backstop for machines with no active
      resident monitor, or document why launch/Picker-triggered reconciliation
      provides sufficient convergence.

### Phase 5 - Backfill, synchronization, and portability
- [x] Extend explicit backfill/doctoring to create missing projections and
      reconcile legacy controller relationships.
- [x] Treat synchronized or restored projections as hints until current local
      project/worktree identity and revisions are validated.
- [x] Preserve projections through session-state synchronization without
      requiring the synchronizer to understand agent-worktrees internals.
- [x] Verify agent-logger synchronization includes the dedicated session-root
      sidecar and does not require it to live in the agent-writable `files/`
      subtree.
- [x] Add the projection to restricted session-state rescue allowlists and
      prove it survives an export/import cycle.
- [x] Avoid semantic no-op rewrites and stage atomic temporary files outside
      synchronized session directories so late lineage repair does not create
      perpetual recopy churn.
- [x] Implement the Phase 1 collision behavior when the same synchronized
      session appears on more than one machine or project.

### Phase 6 - Recovery and presentation
- [x] Let a resumed session read its exact projection and receive a concise
      re-binding/controller recovery pointer when needed.
- [x] Let Picker and JSON consumers distinguish **bound here**, **controlled
      from elsewhere**, **handed off**, **terminal**, and **ambiguous**.
- [x] Add worktree-centric and lineage-centric data surfaces suitable for
      visualization without transcript parsing, CWD joins, or enumeration of
      the live session-state root.
- [x] Update agent-worktrees architecture and CLI documentation.

### Phase 7 - Rollout and convergence
- [ ] Ship schema-version migration and mixed-version behavior under the
      reviewed [`rollout-schema-v2.md`](rollout-schema-v2.md) contract.
- [x] Validate no regression in first-paint/list latency at large session
      counts.
- [ ] Validate Linux, Windows, remote-control, handoff, and synchronized-session
      scenarios.
- [ ] Carve and close phase-specific issues; archive the effort only after the
      implementation and documentation match the revised vision.

## Validation Plan

- [ ] Schema round-trip, deterministic serialization, bounded size, and unknown
      future-field compatibility.
- [ ] Relation-cap behavior preserves the bound relation and nonterminal
      controllers, evicts terminal relations deterministically, and reports
      overflow without losing authority.
- [ ] Atomic writer tests for interruption, partial files, lock contention,
      symlink/reparse escapes, and private POSIX permissions.
- [ ] A bound session remains distinct from a controller session.
- [ ] One controller may reference multiple worktrees without becoming the
      bound head of any child vessel.
- [x] Multi-hop handoff chains resolve through explicit links to one terminal
      successor; cycles and forks remain unresolved.
- [x] Active or mux-attached worktrees are never automatically repointed.
- [x] Inactive unambiguous records converge under fixed per-tick budgets.
- [x] Hot worktree reads never enumerate the session-state root.
- [x] A restored projection cannot override a newer authoritative worktree
      revision or silently bind a foreign project.
- [x] Session-state synchronization preserves metadata and enables grouping by
      stable worktree identity after restore.
- [x] A concluded controller with no successor yields an explicit terminal
      finding rather than an invented recovery target.
- [x] Remote controllers produce a navigable identity/action even when the
      local Picker cannot focus their session directly.
- [ ] Existing session launch, resume, handoff, finalize, and Picker contract
      suites remain green on Windows and POSIX.

## Proposal

Adopt the authority and schema model in [`design.md`](design.md):

- worktree record = authority;
- session-state metadata = reciprocal, rebuildable projection;
- binding and control = separate relations;
- lifecycle events write projections immediately;
- the resident reconciler and optional scheduled one-shot provide bounded
  eventual repair;
- restored metadata is evidence to validate, never authority to trust blindly.

## Journal

### 2026-09-01 - Kickoff
- Opened #1635 as the public coordination issue.
- Reconciled the proposal against the agent-worktrees vision, the
  session-state-access pattern, existing session-scoped sidecars, the monotonic
  head ledger, and the bounded resident reconciler.
- Classified the change as vision-extending: record-first recovery already
  exists, while reciprocal session projections and controller-vs-binding
  identity are new standing intent.
- Architecture review moved the projection from the agent-writable `files/`
  subtree to a dedicated session-root sidecar, keyed lineage per worktree
  relation, made restored-state refusal and newer-schema preservation writer
  invariants, and added explicit agent-logger/agent-containers integration.
- The reviewed design landed through #1638. Phase 1 is complete and the effort
  is Active; implementation begins with the bounded projection writer and
  bound-session lifecycle wiring.
- Opened #1643 and implemented its initial projection core: exact-session
  containment, a dedicated cross-process sidecar lock, external atomic staging,
  deterministic bounded JSON, semantic no-op suppression, newer-schema refusal,
  fail-open lifecycle flushing, and bound-session handoff lineage.
- Focused review replaced worktree-global projection fan-out with per-session
  relation revisions and narrow dirty sets, made stale-writer ordering
  monotonic, preserved overflow evidence, bounded every read, rebuilt corrupt
  same-version projections, and covered non-head handoff predecessors.

### 2026-09-02 - Phase 3 controller relations
- Added a typed, bounded controller relation set to each authoritative
  worktree record, with monotonic revisions, active/ended lifecycle, ClaimRef
  validation, compatibility-preserving legacy derivation, and stable
  assignment/end/removal primitives.
- Creation now derives controller identity from its existing owner, caller
  worktree, and parent-session metadata without registering any controller as a
  bound session or head.
- Exact known controller sessions receive narrow `role=controller` sidecar
  upserts/removals. Bound and controller keys remain independent, so one
  session can control several child worktrees while remaining bound only to its
  own worktree.
- Worktree JSON, `head-session`, scoped `list-sessions`, and Picker
  normalization now carry controller metadata. Automatic controller/head
  reconciliation and terminal-successor findings remain unimplemented for
  Phase 4.
- Controller relations landed through #1672. Phase 4 began under #1681 with a
  shared explicit-successor resolver and a fixed-budget resident projection
  repair pass gated on fresh dark-worktree evidence, nonblocking locks, and
  compare-before-write revisions.
- Phase 4 resolves controller lineage from exact records/projections, emits
  explicit remote, terminal, fork, cycle, missing, restored, and unsupported
  findings, and preserves controller findings through both Picker
  implementations without changing occupancy or resume state.
- The resident queue has independent record/session/projection budgets,
  bounded verification and cooldown caches, and a `reconcile-sessions`
  one-shot suitable for optional low-duty scheduling. Current and permanently
  blocked revisions quiesce; live or unknown-liveness candidates remain
  report-only and retry later.
- Phase 5 began under #1688 by extending restricted container rescue and
  agent-logger rescue ingestion to preserve the dedicated session-root
  projection as bounded, schema-checked, session-ID-matched restored evidence.
- Rescue and synchronization portability landed through #1691. Ordinary
  session sync carries the sidecar opaquely; restricted rescue validates it at
  both boundaries, preserves bounded future schemas, omits invalid optional
  copies without losing transcripts, and marks every imported session as
  restored evidence.
- Opened #1692 for the next Phase 5 slice: explicit backfill/doctoring,
  authoritative restored-hint validation, and collision-safe handling of the
  same synchronized session across machines or projects.
- Phase 5 backfill and restored-hint validation landed through #1694.
  `backfill-sessions` and `doctor` now migrate legacy controller authority and
  inspect exact session projections under a fixed relation budget. Local
  missing or stale projections can be repaired under record and sidecar locks;
  restored, incomplete, foreign, ambiguous, colliding, and newer state remains
  report-only.
- Restored controller hints now resolve only after exact session identity, a
  unique canonical bound project/worktree, and authoritative relation/head
  revisions match. Duplicate bound authorities are rejected before any repair,
  while one controller session may still legitimately project several child
  worktrees.
- Opened #1695 and began Phase 6 with validated recovery pointers for resumed
  sessions and machine-readable recovery/presentation states.
- Validated session recovery pointers landed through #1698. The exact
  `session-recovery` surface and the existing session-start registration
  contributor now classify authoritative bound, controller, handoff, terminal,
  remote, stale, foreign, ambiguous, incomplete, invalid, and unsupported
  projection states without changing a binding.
- Recovery continuation requires a unique active terminal successor; concluded
  sessions and unresolved handoff lineages remain explicit non-navigating
  findings. Record-loader failures fail open to inspect-only context.
- Opened #1700 for the next Phase 6 slice: one normalized reciprocal relation
  state and validated navigation actions across worktree JSON and both Picker
  implementations.
- Normalized reciprocal presentation landed through #1705. Worktree JSON now
  preserves orthogonal binding/control axes plus a compact bound, controlled,
  handed-off, terminal, ambiguous, or unbound summary. The bundled Picker, its
  legacy ANSI fallback, and the standalone Worktree Manager show the same state;
  exact loaded local/remote controller targets gain navigation without changing
  liveness, occupancy, binding, or resume authority.
- Opened #1706 for the remaining Phase 6 data slice: bounded worktree-centric
  and session-lineage JSON surfaces suitable for visualization.
- Implemented `worktree-lineage` and `session-lineage` as separate bounded JSON
  contracts. The worktree surface preserves authoritative sessions, head
  transitions, handoffs, controllers, normalized reciprocal presentation,
  exact-projection health, explicit graph-integrity findings, and per-collection
  overflow. The exact-session surface reads only the requested sidecar, validates
  retained relations against record authority, and keeps restored evidence,
  tombstones, missing records, invalid/newer schemas, and overflow explicit.
- Indexed lineage traversal once per authoritative record so graph generation is
  linear in record size plus fixed traversal budgets rather than repeatedly
  searching long session histories. Full Windows validation passed with 3,522
  tests, 38 skips, and the three #1649 baseline tests deliberately deselected.
- The lineage surfaces landed through #1710, after which #1706 was closed.
  Opened #1712 for the final rollout/hardening slice: platform containment and
  atomicity, mixed-version behavior, large-history latency, cross-scenario
  convergence, remaining validation closure, and effort archival.
- The first #1712 hardening increment landed through #1784. Exact session
  projection access now rejects Windows case-folded and short-name aliases,
  accepts canonical extended paths, defers transient canonicalization failures,
  preserves additive same-schema relation and lineage fields, and compares only
  understood lineage keys during restored-state validation.
- Projection coverage now exercises older-schema authoritative rebuild,
  newer-schema writer fencing, deterministic bounded encoding, interrupted
  replacement cleanup, external staging, POSIX permission repair, and real
  cross-process lock contention on Windows and POSIX. The rebased Windows
  baseline completed with 3,534 passes, 38 skips, and the three #1649 tests
  deliberately deselected; one unrelated timing-sensitive lock assertion
  passed on isolated retry.
- Began the next #1712 increment: define a byte-bounded schema migration and
  mixed-writer contract for truthful relation overflow, and evaluate
  handle-relative replacement so parent-directory identity cannot change
  between validation and commit. No projection format change will land until
  readmission, saturation, deterministic serialization, and old/new writer
  interleavings are covered together.
- Drafted [`rollout-schema-v2.md`](rollout-schema-v2.md) to make the incremental
  information boundary explicit: a writer cannot count identities it discarded
  without storing them or scanning authority. The proposed v2 contract reports
  relation overflow with an unknown (`null`) count, tracks bounded deletion-
  fence loss separately, uses deterministic byte-prefix retention, discards
  inflated v1 counts during migration, and never clears incompleteness from an
  ordinary incremental update.
- The reviewed schema v2 contract landed through #1794. Two unrelated
  current-main suite defects found during the reader-floor baseline were fixed
  separately through #1806 and #1811 rather than expanding the permanent
  exclusion list.
- The schema v2 reader floor landed through #1813 as agent-worktrees
  `1.5.3-dev737`. Readers validate explicit v2 completeness fields and compact
  tombstones, normalize recovery/lineage/controller diagnostics, and block
  every downgrade path while writers continue to emit v1.
- Deployed and verified the reader floor on one Windows writer and its paired
  Linux environment. One additional writer was unavailable and explicitly
  skipped; an outbound-only writer remains on its launch-time self-heal path.
  Writer emission remains gated until the remaining supported writer floor is
  observed or its responsibility is explicitly transferred.
- Large-history latency validation passed on the deployed reader floor. A
  Windows corpus with 1,661 session directories measured a 0.52-second median
  cache-only first-paint and 0.55-second median fresh list. A Linux corpus with
  6,276 session directories measured 0.77 and 1.07 seconds respectively. Both
  remain well below the historical approximately seven-second active-paint
  regression, and the source guard still confines live session-root enumeration
  to explicit backfill and the bounded resident cursor.
