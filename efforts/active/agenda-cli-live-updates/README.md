# Agenda CLI and Live Updates

- **Slug:** `agenda-cli-live-updates`
- **Repo:** copilot-extensions
- **Branch(es):** Retained managed source worktree; reviewed PRs to `dev`
- **Created:** 2026-11-01
- **Status:** Active
- **Vision:** `visions/plugins/efforts` and `visions/mux-companion`
- **Umbrella issue:** [#9855](https://github.com/ThomasMichon/copilot-extensions/issues/9855)
- **Related:** existing Mux Companion continuation #8811

## Guiding Intent

An adopted agenda should have a small reliable command interface, not require
agents to hand-edit its JSON. Current assignment reads and submitted changes
should share a validated schema, attributable metadata and consistent durable
state/history. Committed changes should become observable by the existing
Manager/resident infrastructure without repeated agenda-file polling.

Planning remains flexible and executor-neutral. Programmatic validation governs
agenda operations, not arbitrary off-agenda behavior, physical permissions or
canonical effort/worktree completion.

## Participants

| Participant | Scope | Reached via |
|-------------|-------|-------------|
| Source coordinator | Cohesive agenda CLI/store, optional live adapter, consumer/catalog and validation | Retained source-only worktree under an eligible external harness |
| Existing Manager/agency custodians | Existing daemon, identity, feed, claims and lifecycle authority | Current public source contracts; not reassigned by this effort |

## Coordination

- The coordinator owns this bounded implementation and its reviewed PRs.
  Existing #8811 UI/native/relay/manual/head acceptance remains with its
  incumbent custodians; this is not whole Companion completion.
- Efforts owns agenda schema/movement and its standalone CLI. Agent-worktrees
  owns agency/source identity, registration, claims and worktree closure.
  Manager owns optional presentation/relay, not a second planning authority.
- Follow the existing contribution path for any touched plugin or Manager
  source. Do not edit installed payloads or create a parallel service.
- Carry current phase, source revision, in-flight PR/delivery state and remaining
  validation through checkpoints. A completed phase is not this effort's gate.

## Context

The efforts plugin currently ships a read-only `next`/`check` inspector and a
manual version-1/2 JSON convention. The existing Manager supports a mux-owned
coalescing daemon and optional plugin-contribution contracts. Its current-roster
consumer also uses the agency resident's held notification feed.

Those mechanisms are prior art, not an existing agenda mutation/publish contract.
The implementation must extend the actual owning seam, preserve its generation,
admission, cancellation and version-skew behavior, and demonstrate a subscribed
consumer. A generic wake or acknowledged mapping alone is not live agenda data.

## Request

Public-safe request transcription, replacing source-specific worktree identity
and filesystem location with generic references:

> An active worktree agent is using the agenda feature by hand-editing JSON.
> Provide a CLI for reading current assignments and submitting updates, enforce
> consistent schema and metadata for all calls, and ensure information becomes
> live in the Worktree Manager daemon so its UX updates immediately without
> excessive polling of a worktree's agenda.
>
> A supplied active agenda can be evaluated read-only to understand the agent's
> comprehension and improve guidance or tooling for future agents.

The original private request and location stay with the originating coordinator.
They are not public source identifiers or permission to mutate the active agent.

## Scope Approvals

- **Allowed:** generic source implementation, bounded synthetic tests, read-only
  evaluation of the explicitly supplied agenda and normal reviewed publication.
- **Excluded:** altering another active agent's state/claims, production Manager
  or service restart, runtime rollout, physical actions, source replay, credentials,
  new polling daemons and implicit migration of current agents.
- **Limits:** preserve finite depth/visit/detour/deferred budgets and valid
  historical unknowns. Time, delivery or a programmatic update is not approval.
- **Standalone:** efforts use must remain viable without Manager or
  agent-worktrees installed/running. Adopted optional delivery failures remain
  explicit and must not falsely claim the UX is live.

## Plan

### Phase 1 - Reviewed command, durability and delivery contract

- [x] Review and merge this implementation plan before code execution.
- [x] Resolve the actual standalone command/store and Manager/resident
  notification/subscription boundaries, including meaningful consumer behavior.
- [x] Record read-only evaluation findings without importing private identifiers
  or another agent's operational authority into public source.

### Phase 2 - CLI-owned agenda mutations

- [x] Refactor/reuse the existing strict board/chain/budget validator; preserve
  existing read-only calls and version-1/2 input compatibility.
- [x] Supply current assignment reads and compact semantic updates for
  initialization/import, plan refinement, selection, outcomes, execution links,
  blocking/deferred rechecks and deliberate returns.
- [x] Require expected revisions and stable operation identities; generate
  schema/version, actor/source qualification and actual timestamp metadata.
- [x] Implement serialized durable state/history publication with a single
  commit barrier, explicit failure/crash behavior and idempotent retry results.
- [x] Preserve original roots, evidence, visits, stricter limits and pending
  decisions. Imported legacy entry times remain unknown rather than invented.

### Phase 3 - Immediate optional live projection

- [x] Extend the actual owning Manager/resident contract with bounded,
  revision/source-qualified agenda observations and a supported held consumer.
- [x] Publish only committed state; retain current generation/head/selection
  fences and reject stale or wrong-source updates.
- [x] Distinguish persisted operation success from live-delivery pending,
  unsupported/offline/not-configured state. Never replay the mutation merely
  because notification failed.
- [x] Reconnect/restart through current snapshots and bounded delivery recovery,
  without making the Manager a rival agenda store or polling every file.
- [x] Wire a real synthetic Manager/Textual consumer so a committed update
  reaches its view without a periodic agenda-file read.

### Phase 4 - Catalog, guidance and completion

- [x] Publish attributable owned CLI invocation/catalog and compact ambient
  transition guidance plus the existing on-demand skill/reference.
- [x] Prefer CLI submission over manual edits; make legacy/manual recovery and
  actual App/CLI/ACP command availability explicit rather than assumed.
- [ ] Run bounded targeted tests and independent critique, add required
  changefiles, and drive all implementation PRs through actual merge.
- [ ] Reconcile every Plan/Validation item, archive the completed source effort,
  and record source/release/rollout responsibility truthfully.

## Validation Plan

- [x] Existing valid manual/read-only usage remains compatible; unsupported
  required new state fails rather than looking complete or silently stale.
- [x] Malformed/oversized/duplicate/nonfinite data, unsafe paths and invalid
  references/revisions fail visibly with no partial accepted mutation.
- [x] Concurrent/stale writes, interrupted publication and duplicate operations
  preserve one consistent state/history and the original accepted result.
- [x] Every supported branch operation preserves roots, returns, evidence and
  finite visit/depth/detour/deferred budgets, including no-progress cycles.
- [x] Generated metadata is truthful; missing native identity and legacy entry
  times remain qualified unknowns, not authenticated labels or fake durations.
- [x] Real synthetic push/held-subscription/consumer tests demonstrate visibility
  only after commit, without repeated agenda-file polling.
- [x] Offline/unsupported delivery, stale/head/generation changes, late results,
  reconnect and daemon retirement retain explicit source/delivery semantics.
- [x] Standalone/no-Manager paths, process-boundary owned invocation and
  supported host/tool surfaces are tested; no deployment claim from source tests.
- [ ] Public artifacts remain generic, required review/CI pass, and source,
  release, installation and actual active-agent migration are distinguished.

## Proposal

Reuse the small existing validator and native publication primitives. A
versioned atomic commit record over durable state/history is recommended;
choose the simplest source-compatible implementation after inspecting current
owners. Expansion precedes independently deployed callers. No API in this
proposal is claimed available merely because its intended name is described.

## Journal

### 2026-11-01 - Source implementation and connected validation

- The standalone command now owns validated state, semantic movements, generated
  metadata, immutable accepted-operation history and independent delivery status.
  Revision/idempotence, finite budgets, source evidence and pending branches are
  preserved. Managed signed-63-bit revision exhaustion refuses before acceptance
  or legacy fencing; the manual inspector's existing behavior remains unchanged.
- Explicit, quiescent POSIX same-home adoption durably preserves original cursor
  bytes and recovery input before installing a version-3 old-reader refusal
  marker, then commits SQLite state/history. Filesystem fencing and database
  acceptance cannot share one atomic commit: interruption may leave an unavailable
  fenced lane, recovered only with the same saved initialization request. Other
  platforms refuse legacy adoption explicitly; fresh standalone stores do not
  require that migration. No active lane was migrated.
- The existing resident serves a separate generation/head/source-qualified agenda
  cache and held feed. The Manager checks actual provider capability before new
  syntax, retains owned process/selection custody and updates its Agenda view
  without reading agenda files. Legacy revisit offsets remain unchanged; generated
  commit times remain UTC. Scalar parked counts are not collection-size limits.
- Scoped standalone, provider and consumer packets passed, including actual old
  reader refusal, interruption recovery, stale/concurrent writes, limits,
  unsupported delivery, retirement and reconnect controls. A real connected
  transaction/publisher/parser/authenticated socket/record lock/held relay/Textual
  witness proves commit-before-delivery, exact observation equality, pushed changes,
  offline pending delivery without replay and canonical-head invalidation.
- Final connected/consumer-contract packet passed 44 cases with verified cleanup.
  Source AST, runtime/headless, module, release-intent, guidance/reference and
  documentation checks passed after classifying the two new caller-contained
  launch sites in the existing lifecycle census. Independent critique found one
  late-exposure ordering issue: fresh owner validation now precedes live
  row/sequence/notification changes, with staged head refreshes and retained
  rejected invalidations. Deterministic owner-loss controls, the 69-case
  provider/CLI packet and the actual connected witness passed; the reviewer
  confirmed the finding closed. Normal source publication remains the next gate;
  this effort is still Active.

### 2026-11-01 - Command and publication boundary

- Keep efforts payload-only: a Python-standard-library command owns an explicit
  lane's transactional state and operation history. SQLite supplies one commit
  barrier and serialized expected-revision writes; legacy manual files remain
  inputs, not independently updated replicas. Managed reads must not silently
  fall back to those files after adoption.
- The mux coalescing server's subscription is a client-retention count, not a
  change stream. Extend the existing agency resident's authenticated control
  plane with a separately negotiated `worktree-agendas-v1` cached projection
  and held feed. Do not alter the existing roster's strict row contract.
- The optional adapter accepts committed, source-qualified agenda observations.
  Agency owns current worktree/head identity and runtime-generation fencing;
  Manager consumes complete bounded frames and updates its existing Companion
  through a cancellation-owned process boundary, without reading agenda files.
- Standalone mutation success and optional delivery status remain separate.
  Notification retry publishes the latest committed snapshot, never repeats
  the agenda mutation. Source tests use synthetic lanes and resident instances;
  installing or migrating active agents remains excluded.

### 2026-11-01 - Reviewed implementation admission

- Plan #9858 received current-head Copilot approval with no open findings,
  passed required CI and merged to `dev`. The same retained source workspace
  synced onto the provider-confirmed merge before code execution.
- The supplied manual agenda already used coarse assignments, explicit exits/
  transitions, finite budgets, review evidence and parked nonblocking work.
  The gap is the shipped interface: no mutation CLI or live agenda contract.
- Legacy input lacks writer-generated occurrence/entry metadata and can carry
  composite source-reference text. Import must preserve that evidence, qualify
  missing identity/timing and avoid interpreting text as a single authoritative
  source or inventing an earlier entry time.
- Continue with the actual command/store and Manager notification/consumer
  boundary, then cohesive implementation. No supplied active state or installed
  runtime was changed.

### 2026-11-01 - Source intake

- Claimed #9855 after the public agenda search found no open matching writer
  implementation. The existing #8811 presentation guidance remains distinct.
- Read the current agenda inspector, Manager package/source/contribution
  contract and resident feed prior art; Manager lives in this repository,
  not a separately registered source project.
- Read-only supplied agenda inspection was structurally valid: coarse assigned
  outcomes, explicit review/merge exits, finite budgets, preserved evidence and
  parked nonblocking discovery. Hand editing reflects the shipped interface,
  not failure to understand the process.
- No active agent state, runtime, daemon or service was modified.
