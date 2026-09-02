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
  (controller relations)

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
- [ ] Refuse writes into session trees identified as restored/foreign until
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
- [ ] Expose terminal-successor findings after Phase 4 adds bounded,
      ambiguity-aware controller lineage resolution.

### Phase 4 - Bounded reconciliation
- [ ] Extend the resident reconciler with a fixed-budget controller/head pass
      that uses record-local links and exact session IDs.
- [ ] Follow explicit succession links to a unique terminal session; never
      select a successor by timestamp alone.
- [ ] Mutate only dark, inactive worktrees under a nonblocking record lock and
      a compare-before-write revision check.
- [ ] Report ambiguity, cycles, missing records, live conflicts, and restored
      foreign state without mutation.
- [ ] Add a low-duty, one-shot scheduled backstop for machines with no active
      resident monitor, or document why launch/Picker-triggered reconciliation
      provides sufficient convergence.

### Phase 5 - Backfill, synchronization, and portability
- [ ] Extend explicit backfill/doctoring to create missing projections and
      reconcile legacy controller relationships.
- [ ] Treat synchronized or restored projections as hints until current local
      project/worktree identity and revisions are validated.
- [ ] Preserve projections through session-state synchronization without
      requiring the synchronizer to understand agent-worktrees internals.
- [ ] Verify agent-logger synchronization includes the dedicated session-root
      sidecar and does not require it to live in the agent-writable `files/`
      subtree.
- [ ] Add the projection to restricted session-state rescue allowlists and
      prove it survives an export/import cycle.
- [ ] Avoid semantic no-op rewrites and stage atomic temporary files outside
      synchronized session directories so late lineage repair does not create
      perpetual recopy churn.
- [ ] Implement the Phase 1 collision behavior when the same synchronized
      session appears on more than one machine or project.

### Phase 6 - Recovery and presentation
- [ ] Let a resumed session read its exact projection and receive a concise
      re-binding/controller recovery pointer when needed.
- [ ] Let Picker and JSON consumers distinguish **bound here**, **controlled
      from elsewhere**, **handed off**, and **ambiguous**.
- [ ] Add worktree-centric and lineage-centric data surfaces suitable for
      visualization without transcript parsing, CWD joins, or enumeration of
      the live session-state root.
- [ ] Update agent-worktrees architecture and CLI documentation.

### Phase 7 - Rollout and convergence
- [ ] Ship schema-version migration and mixed-version behavior.
- [ ] Validate no regression in first-paint/list latency at large session
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
- [ ] Multi-hop handoff chains resolve through explicit links to one terminal
      successor; cycles and forks remain unresolved.
- [ ] Active or mux-attached worktrees are never automatically repointed.
- [ ] Inactive unambiguous records converge under fixed per-tick budgets.
- [ ] Hot worktree reads never enumerate the session-state root.
- [ ] A restored projection cannot override a newer authoritative worktree
      revision or silently bind a foreign project.
- [ ] Session-state synchronization preserves metadata and enables grouping by
      stable worktree identity after restore.
- [ ] A concluded controller with no successor yields an explicit terminal
      finding rather than an invented recovery target.
- [ ] Remote controllers produce a navigable identity/action even when the
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
