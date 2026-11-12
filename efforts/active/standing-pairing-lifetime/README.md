# Standing Pairing and Lifetime

## Goal

Make standing responsibilities manually assignable without a default worker
pool, then allow explicitly delegated mechanical recovery of that retained
pairing. Local standing declarations use supported native admission rather
than requiring adopters to invent permissive callbacks. Preserve matching
existing agents, workspaces, task obligations and finite-task pool behavior.

- **Slug:** `standing-pairing-lifetime`
- **Repo:** copilot-extensions
- **Created:** 2026-11-11
- **Status:** Draft
- **Vision:** [Agent topology](../../../visions/agent-fabric/agentic-factory/agent-topology/README.md)
- **Umbrella issue:** #12171

## Guiding Intent

Declaration, assignment, acceptance and lifetime authority are different
operations. An unpaired standing task is not demand for a new worker. A
dispatcher discovers matching existing agents and explicitly pairs the native
owner. A granted recovery policy restores that pairing; it never becomes a
second allocator or interprets unknown liveness as an empty seat.

## Participants

| Participant | Role | Reached via |
|---|---|---|
| Source owner | Implementation, evidence and PR stewardship | Managed source worktrees; #12171 |
| Native owners | Worktrees custody, Bridge conversation/lifetime and Dispatch queue/policy | Existing owner APIs and contracts |
| Repository reviewer | Review plan and source against repository gates | Normal PR review |

## Coordination

- **Topology:** Dependency-ordered source PRs, one continuing source owner.
- **Host (owns PRs):** Source owner.
- **Delegates:** None.
- **Handoff:** Preserve the whole objective, exact active source, accepted plan,
  open PRs and native obligations. A completed phase is not completion.

## Context

[Standing charters](../../../plugins/agent-dispatch/docs/standing-charters.md)
already provide durable declarations, explicit reserve/accept/release and
native relationships. The registrar reconciles complete standing source sets
without process registration; ordinary finite claiming excludes standing tasks.
The [source topology integration](../../2026/11/11%20topology-dispatcher-integration/README.md)
adds read-only flow, routing, reconciliation and admission reports.

Ordinary coordinator construction currently requires a separately supplied
Python admission factory for native standing attachment. Existing finite pool
recovery does not supply a complete retained-standing-agent lifetime contract.
Do not treat those implementation gaps as reasons to add a default standing
pool, skip custody checks or auto-create an agent.

## Request

Public-safe statement of the source assignment:

> Standing charters have no pool by default and use manual assignment. A
> dispatcher first looks for a matching agent and explicitly pairs it. Once
> paired, Dispatch can maintain that pairing through boot or confirmed agent
> loss under a granted lifetime policy. Ordinary emitted tasks continue to
> auto-claim their configured pools. Ensure that capability is implemented.

This is the generic source contract; an adopter retains its original private
approval evidence and live scope.

## Scope Approvals

Source design, implementation, focused isolated validation and normal
issue/PR/review/merge delivery in this repository. No live enrollment, agent
creation/restart/termination, service update, monitoring handoff, new access or
deployment is authorized by this source effort. Test effects use contained
synthetic stores and native fixture owners.

## Plan

### Phase 1 - Manual standing responsibility

- [ ] Prove the actual registrar/queue/supervisor path declares an unpaired
  standing responsibility without a pool, auto-claim or worker creation.
- [ ] Preserve independent finite emitter/pool execution; dispatcher-controlled
  reserve/adoption remains the only initial pairing path.

### Phase 2 - Supported local native admission

- [ ] Provide the production owner-supported local admission assembly for
  trusted local standing declarations, with exact workspace/participant,
  materialization, host policy and native head/conclusion checks.
- [ ] Preserve explicit alternate authority/factory selection, capability
  reporting, local denial and unknown/unsupported outcomes. No permissive
  no-op callbacks, redundant user enable flag or implicit launch.

### Phase 3 - Retained pairing lifetime

- [ ] Persist and enforce an explicit bounded recovery grant tied to the
  retained pairing. No recovery grant exists merely because a seat is declared.
- [ ] Compose the existing supervisor and native lifetime owners to restore
  that same agent/workspace after boot or confirmed loss, preserving task,
  charter, relationships, obligations and original pending operation identity.
- [ ] Hold unknown liveness, changed heads/owners/charters, denied or revoked
  policy, concurrent recovery and uncertain effects without reallocation,
  replacement, duplicated start/send or force-cleared claims.

## Validation Plan

- [ ] Focused units for no-pool defaults, explicit pairing, granted versus
  ungranted recovery, revocation and legacy/mixed-version behavior.
- [ ] Real local consumer proof: registrar to native standing task, pairing
  to existing owner and finite-task pools unchanged; input/state conservation
  outside authorized fixture effects.
- [ ] Restart/race/reply-loss qualification preserves one pairing and original
  operation; no new workspace or competing agent on unknown outcomes.
- [ ] Fresh synthetic stores and first-use admission prove the ordinary path,
  not only a test-injected factory. Document unavailable external/live tiers.
- [ ] Affected test packets, F/E9 lint, strict affected type projects,
  install-contract/documentation checks and required changefiles pass.
- [ ] Every source PR clears actual review and required CI, merges and its
  source worktree finalizes. Resolve all Plan/Validation items before Done.

## Journal

### 2026-11-11 - Source gap claimed

- Claimed #12171 after bounded open issue/PR deduplication. Verified the
  existing registrar path is non-spawning and ordinary standing attachment
  currently depends on separately configured admission assembly.
- Proposed native defaults plus separately delegated retained-pairing
  lifetime, preserving the existing owners and no-pool standing behavior.
- Planning only; no implementation or live effects yet.
