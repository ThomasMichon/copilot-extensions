# Agenda CLI and Live Updates

- **Slug:** `agenda-cli-live-updates`
- **Repo:** copilot-extensions
- **Branch(es):** Retained managed source worktree; reviewed PRs to `dev`
- **Created:** 2026-11-01
- **Status:** Draft
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

- [ ] Review and merge this implementation plan before code execution.
- [ ] Resolve the actual standalone command/store and Manager/resident
  notification/subscription boundaries, including meaningful consumer behavior.
- [ ] Record read-only evaluation findings without importing private identifiers
  or another agent's operational authority into public source.

### Phase 2 - CLI-owned agenda mutations

- [ ] Refactor/reuse the existing strict board/chain/budget validator; preserve
  existing read-only calls and version-1/2 input compatibility.
- [ ] Supply current assignment reads and compact semantic updates for
  initialization/import, plan refinement, selection, outcomes, execution links,
  blocking/deferred rechecks and deliberate returns.
- [ ] Require expected revisions and stable operation identities; generate
  schema/version, actor/source qualification and actual timestamp metadata.
- [ ] Implement serialized durable state/history publication with a single
  commit barrier, explicit failure/crash behavior and idempotent retry results.
- [ ] Preserve original roots, evidence, visits, stricter limits and pending
  decisions. Imported legacy entry times remain unknown rather than invented.

### Phase 3 - Immediate optional live projection

- [ ] Extend the actual owning Manager/resident contract with bounded,
  revision/source-qualified agenda observations and a supported held consumer.
- [ ] Publish only committed state; retain current generation/head/selection
  fences and reject stale or wrong-source updates.
- [ ] Distinguish persisted operation success from live-delivery pending,
  unsupported/offline/not-configured state. Never replay the mutation merely
  because notification failed.
- [ ] Reconnect/restart through current snapshots and bounded delivery recovery,
  without making the Manager a rival agenda store or polling every file.
- [ ] Wire a real synthetic Manager/Textual consumer so a committed update
  reaches its view without a periodic agenda-file read.

### Phase 4 - Catalog, guidance and completion

- [ ] Publish attributable owned CLI invocation/catalog and compact ambient
  transition guidance plus the existing on-demand skill/reference.
- [ ] Prefer CLI submission over manual edits; make legacy/manual recovery and
  actual App/CLI/ACP command availability explicit rather than assumed.
- [ ] Run bounded targeted tests and independent critique, add required
  changefiles, and drive all implementation PRs through actual merge.
- [ ] Reconcile every Plan/Validation item, archive the completed source effort,
  and record source/release/rollout responsibility truthfully.

## Validation Plan

- [ ] Existing valid manual/read-only usage remains compatible; unsupported
  required new state fails rather than looking complete or silently stale.
- [ ] Malformed/oversized/duplicate/nonfinite data, unsafe paths and invalid
  references/revisions fail visibly with no partial accepted mutation.
- [ ] Concurrent/stale writes, interrupted publication and duplicate operations
  preserve one consistent state/history and the original accepted result.
- [ ] Every supported branch operation preserves roots, returns, evidence and
  finite visit/depth/detour/deferred budgets, including no-progress cycles.
- [ ] Generated metadata is truthful; missing native identity and legacy entry
  times remain qualified unknowns, not authenticated labels or fake durations.
- [ ] Real synthetic push/held-subscription/consumer tests demonstrate visibility
  only after commit, without repeated agenda-file polling.
- [ ] Offline/unsupported delivery, stale/head/generation changes, late results,
  reconnect and daemon retirement retain explicit source/delivery semantics.
- [ ] Standalone/no-Manager paths, process-boundary owned invocation and
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
