# Precomputed Hook Guard Map

- **Slug:** `precomputed-hook-guard-map`
- **Repo:** copilot-extensions
- **Branch(es):** managed source worktree
- **Created:** 2026-11-01
- **Status:** Active
- **Umbrella issue:** #9911
- **Sub-issues:** None selected.
- **Vision:** [Worktree lifetime and agency authority](../../../visions/plugins/agent-worktrees/README.md)

## Guiding Intent

The existing Worktrees resident prepares a small map of local repository
anchors and their guard facts. Hooks resolve the relevant repository through
bounded local Git metadata and consult that map. No hook event starts topology,
plugin, registry or configuration acquisition, including on a cache miss.
If the map does not exist, the cross-repo guard fails open.

Precompute facts, not permission decisions. Preserve supported write routing,
current controls and recovery access without adding a daemon or requiring a
healthy resident merely to inspect or repair an installation.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Own this bounded projection/consumer change, validation and source PRs | Managed source worktree `steady-blueprint` |
| Existing hook maintainers | Retain unrelated hook, lifecycle and installer work | Existing repository issues and PRs |

## Coordination

- **Topology:** serial, coherent PRs against `dev`.
- **Host (owns PRs):** Source driver.
- **Delegates:** none assigned.
- **Handoff:** this effort and its public issue carry source progress and gates.
- Land the plan before implementation. Preserve existing source owners outside
  this bounded slice. Installation or live activation requires its own scope;
  merge and release are not evidence of deployment.
- The reconciled publication model is **managed push, without watchers**:
  reuse managed related/registry mutation and session preparation/startup
  boundaries, with explicit refresh for out-of-band edits. Do not introduce
  watcher frontiers, TTL expiration or periodic topology recomputation.

## Context

`scripts/cross_repo_guard.py` currently discovers guarded roots through the
`related` CLI on a missing or expired sidecar. The strict command-hook path
can therefore acquire the full plugin/related graph within a short event
deadline. The resident `_ResidentHookPolicy.guarded_roots()` also computes
on a request-time cache miss, while holding the hook request's shared lock.
Switching between those implementations does not remove discovery.

Earlier #7968 bounded/cached acquisition and #9534 batched registry paths into
`related list`. This effort removes hook-triggered acquisition rather than
adding another timeout allowance.

Reuse `RepositoryMapRuntime`'s producer-owned snapshot, generation and
admission pattern and the existing registry/related authorities. The vision
already assigns repository resolution to the producer, not request handlers.
This closes that intent; the specific JSON and Git keys are below its altitude.
Apply the existing circumstantial-hook and service-contract-evolution patterns.

## Request

Operator architecture direction, verbatim:

> Right. Identify what changes we need to make. My vision/architecture recommendation is that we figure out what we can compute ahead of time to preoduce a "quick lookup" for the hook. The agent-worktrees daemon could assist in keeping some fast-access JSON file updated with some dict/keys that contains some key computable by the hook for a set membership test.

Selected simplification and failure behavior, verbatim:

> Great. Make an effort to drive this update. Fortunately, there will never be that many local repos checked out most of the time, so a quick list can be made of every repo anchor and whether or not it needs the guard; the hook can resolve the anchor from git for any arbitrary folder, then check the map. If the map doesn't exist, fail-open

Execution direction, verbatim:

> Great. Help drive and build this, so we can have a fixed hook locally

The bounded source slice is assigned to the Source driver. Bounded local Git
identity is allowed; an earlier zero-Git design suggestion is not a requirement.

## Plan

The decomposition and implementation details below are **agent-recommended**.

### Phase 1 - Reviewed contract and coordination

- [x] Identify both request-time discovery paths and existing producer reuse.
- [ ] Establish a deduplicated public issue and land this plan.
- [ ] Specify repository/common-directory identity, caller/installation scope,
  complete local-repo coverage and unavailable-state behavior.

### Phase 2 - Producer-owned facts

- [ ] Build and atomically publish the small guard map from the existing
  resident lifecycle, outside hook handling and the shared hook lock.
- [ ] Bind complete snapshots to source inputs and elected producer generation;
  invalidate changed registry, related, binding and plugin contribution facts.
- [ ] Publish through existing managed mutation/preparation boundaries and
  expose explicit refresh for out-of-band edits; no watchers or periodic scan.
- [ ] Preserve complete-empty versus unavailable observations and report
  producer health without making reads refresh or start a service.

### Phase 3 - Lookup-only hook consumers

- [ ] Replace standalone discovery and resident lazy acquisition with the same
  bounded map reader and local Git repository-key lookup.
- [ ] Preserve current modes, kill switches, expiring grants, target extraction,
  delegation nudges and installation isolation.
- [ ] Update deployment/reconciliation inventories and compatibility behavior;
  remove the hook's obsolete discovery/runtime-resolution dependencies.

### Phase 4 - Qualification and delivery

- [ ] Complete the Validation Plan and document actual behavior.
- [ ] Land source through required review/CI, then verify release inclusion.
- [ ] Resolve every Plan/Validation item before marking this effort Done.
  Record installed acceptance separately; do not infer it from source delivery.

## Validation Plan

- [ ] A missing map fails open without topology/CLI discovery, refresh,
  installation, daemon start or an infrastructure-shaped deny.
- [ ] Both native and resident consumers fail a test if topology, plugin
  activation, registry acquisition or full config resolution is invoked.
  Git identity uses bounded metadata reads, not a subprocess or RPC.
- [ ] Positive, negative, complete-empty, malformed, stale, incomplete and
  wrong-scope inputs retain distinct truthful outcomes without discovery.
- [ ] Cover ordinary subdirectories, new files, relative/absolute and multiple
  targets, linked worktrees, bare anchors, nested repositories/submodules,
  aliases, UNC paths and platform case/normalization semantics.
- [ ] Verify live mode/kill-switch/grant-expiry changes and existing decisions.
- [ ] Verify coherent input revisions, invalidation, atomic concurrent reads,
  producer failure and superseded-owner publication refusal.
- [ ] Exercise related add/remove/primary and registry mutation publication,
  preparation/startup publication and explicit refresh; prove no watcher,
  TTL-driven or periodic topology acquisition is installed.
- [ ] Exercise the actual cold/warm/missing-map native entry, bounded Git and
  cleanup; preserve one implementation per event and supported ACP behavior.
- [ ] Verify the actual reader dependency closure in a clean disposable install.

## Proposal

Use a bounded, versioned JSON projection owned by the existing resident.
Resolve source and target repository identity locally; distinguish a worktree
root from its common Git directory. Keep caller-specific related policy and
installation scope explicit rather than using a global union of guard rules.

No map, unsupported schema or unusable observations may cause a foreground
rebuild. Current controls and the current operation still determine the result.
Unknown facts are not a complete negative inventory; recovery-capable degraded
evaluation is distinct from authorization or successful lifecycle mutation.

## Journal

### 2026-11-01 - Source slice opened

- Read existing source contracts, resolved the producer/consumer seams and
  checked open guard-map issues and pre-tool PRs.
- Notified the existing hook owner of the bounded source assignment without
  acquiring its checkout or unrelated work.
- Captured the bounded-Git and missing-map fail-open decisions for plan review.
- Reconciled the existing #9911 owner and accepted its managed-push/no-watchers
  design refinement. Closed the subsequently discovered duplicate #9920;
  source work continues against #9911 without acquiring the earlier checkout.
