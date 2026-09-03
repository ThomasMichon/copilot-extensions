# Unreachable Machine Maintenance

- **Slug:** `unreachable-machine-maintenance`
- **Repo:** copilot-extensions
- **Branch(es):** serial pull requests from one worktree
- **Created:** 2026-09-03
- **Status:** Draft
- **Vision:** vision-extending for
  [`visions/agent-fabric`](../../../visions/agent-fabric/README.md)
  - durable maintenance handoff when live reach is unavailable
- **Umbrella issue:** #1891

## Guiding Intent

Make an unreachable machine a durable routing boundary rather than a dead end.
Recurring machine state should travel through declarative requirement packages
or another declared auto-update path. Work that still requires execution on the
target should become a machine-scoped maintenance issue that a later local
session can discover, apply, verify, and close.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| agent-ssh | Emit the unreachable-target routing boundary | Same-repository serial slice |
| agent-machines | Own declarative convergence and the target-local maintenance procedure | Primary worktree |
| agent-dispatch | Supply optional deduplicated single-claim execution lifecycle | Existing public contract |

## Coordination

- **Topology:** independent serial pull requests from one worktree
- **Host (owns PRs):** agent-machines
- **Delegates:** agent-ssh owns the emitted pointer; agent-dispatch is reused,
  not modified
- **Handoff:** each merged slice is recorded here before the next begins

## Context

agent-ssh already owns honest reachability: an unverified or failed SSH path is
unreachable. agent-machines already owns declarative convergence from
repository requirement packages, while user repositories own their issue
tracker and machine inventory.

The missing seam is what an agent should do after every declared route remains
unavailable following bounded diagnosis. Repeated SSH retries are not a
deployment strategy, and a one-off issue alone does not make recurring state
reproducible. The workflow needs:

1. encode repeatable state in agent-machines or another declared auto-update
   system;
2. identify the queue with an explicit provider, user repository, and canonical
   machine key rather than ambient CWD or a repository scan;
3. queue any remaining local execution as a target-machine maintenance issue;
4. use agent-dispatch for a deduplicated single claim when available;
5. let a session on that machine re-derive, preview, apply, verify, and close the
   work through one reviewable skill.

## Request

> Update general guidance emitted by agent-ssh or agent-machines so that, when a
> machine cannot be reached through SSH for deployment or updates, the correct
> strategy is to put the update in agent-machines as a payload or another
> auto-update system and file a maintenance issue for the target machine in the
> user's repository. Add a companion skill for performing machine maintenance.

## Plan

### Phase 1 - Intent and maintenance-handoff contract
- [x] Extend the agent-fabric vision with the unreachable-machine maintenance
      handoff.
- [ ] Define the provider-neutral queue locator and maintenance issue contract:
      provider, explicit user repository, canonical machine identity,
      maintenance classification, requested outcome, safety gates, verification,
      and closure evidence.
- [ ] Keep ownership singular: agent-ssh reports the routing boundary,
      agent-machines owns declarative convergence and target-local execution,
      agent-dispatch owns optional claims, and the user's tracker owns queued
      work.

### Phase 2 - Emitted guidance and target-local skill
- [ ] Extend the agent-ssh mesh pointer with a concise unreachable-target rule
      and a pointer to the companion skill.
- [ ] Add `performing-machine-maintenance` to agent-machines with source-side
      queueing and target-side draining workflows.
- [ ] Treat issue instructions as advisory: require trusted repository
      authority, re-read the issue revision before apply, re-derive commands
      from repository state, and preserve destructive/elevation/restart gates.
- [ ] Use a deduplicated agent-dispatch task as the execution claim when that
      layer is available; otherwise require the issue provider to expose a
      single-owner claim before mutation.
- [ ] Update agent-ssh and agent-machines documentation without hard-coding a
      forge, repository, machine roster, or organization-specific labels.

### Phase 3 - Validation and release
- [ ] Add cross-platform assertions for the emitted guidance and skill
      structure.
- [ ] Validate version surfaces and install contracts for both changed plugins.
- [ ] Land, deploy through the normal marketplace path, close #1891, and
      archive the effort.

## Validation Plan

- [ ] The emitted context says not to retry indefinitely or bypass the normal
      installer when a verified SSH target is unreachable.
- [ ] The guidance prioritizes agent-machines requirement packages or another
      declared auto-update mechanism before a maintenance issue.
- [ ] The issue contract is provider-neutral and resolves the user repository
      and canonical machine identity from an explicit durable locator.
- [ ] The target-local skill discovers only maintenance work assigned to the
      current machine, previews changes, preserves confirmation gates, verifies
      outcomes, and records closure evidence.
- [ ] Transient transport, authentication, and configuration failures remain
      diagnosis paths rather than automatically creating maintenance work.
- [ ] Concurrent drainers cannot both apply one issue, and an issue edited after
      preview is re-evaluated before mutation.
- [ ] The workflow degrades safely when agent-machines or an issue-provider tool
      is unavailable.
- [ ] Bash and PowerShell context emitters remain semantically equivalent.

## Proposal

Classify this as an agent-fabric vision extension spanning honest SSH
reachability, declarative machine convergence, and optional dispatch claims.
agent-ssh emits only the short routing rule because it observes the boundary.
agent-machines ships the detailed action-sequence skill because it owns
repeatable machine state and runs on the target. agent-dispatch supplies the
existing single-claim lifecycle when available. The issue tracker remains
repository-selected infrastructure, addressed by an explicit queue locator and
not imported into any plugin.

## Journal

### 2026-09-03 - Kickoff
- Opened #1891 as the public coordination issue.
- Confirmed that agent-ssh already emits a repository-gated mesh pointer and
  agent-machines already supports machine-gated requirement packages.
- Selected `performing-machine-maintenance` as the target-local companion skill
  and kept the issue-provider contract generic.
- Plan review moved standing intent from the transport leaf to the agent-fabric
  branch, made the queue locator explicit, kept issue prose advisory, and reused
  agent-dispatch claims instead of inventing a parallel ownership model.
