# Restricted Venue Targets

- **Slug:** `restricted-venue-targets`
- **Repo:** copilot-extensions
- **Branch(es):** independent serial PRs from one managed worktree
- **Created:** 2026-08-26
- **Status:** Draft
- **Vision:** `visions/plugins/agent-containers` (`repository-shaped workspace`,
  `coordination-layer face`, `same-fabric-contract`,
  `policy-legible-before-dispatch`) · `visions/agent-fabric`
  (`one-fabric-many-venues`, `survivable-work`, `recover-not-lose`) ·
  `visions/plugins/agent-worktrees`
  (tracking authority across transports) · `visions/plugins/agent-ssh`
  (named transport profiles) · `visions/plugins/agent-dispatch`
  (routed embodiment and durable recovery) · `visions/venue-parity`
  (`single-ssh-transport`, within its restricted-venue boundary)
- **Umbrella issue:** #1188

## Guiding Intent

Make a restricted local-container venue a named, worktree-centered participant
in the agent fabric without projecting trusted-host authority into it. A caller
should be able to select the venue, assign one container-local repository
workspace, dispatch a durable goal, open the same session through an
SSH-compatible provider transport, and recover its state after venue
replacement.

The provider remains the source of venue, posture, and lease truth.
agent-worktrees owns the represented workspace identity. agent-bridge and
agent-dispatch consume those facts. agent-logger brokers the narrow session
checkpoint. No layer creates a synthetic physical machine, shared host worktree,
ambient credential, or parallel lifecycle store.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| agent-containers | Restricted venue posture, stable target identity, lease, provider exec, checkpoint boundary | `plugins/agent-containers` |
| agent-bridge | Provider-target resolution, session transport, liveness identity | `plugins/agent-bridge` |
| agent-worktrees / Picker | Represented workspace authority and provider-backed source UX | `plugins/agent-worktrees` |
| agent-ssh | Named SSH-compatible provider transport contract | `plugins/agent-ssh` |
| agent-dispatch | Task-to-venue ownership, supervised embodiment, recovery | `plugins/agent-dispatch` |
| agent-logger | Allowlisted session-state export/restore | `plugins/agent-logger` |

## Coordination

- **Topology:** one managed worktree; serial per-slice PRs.
- **Host (owns PRs):** the driver of #1188.
- **Delegates:** none; each plugin slice lands before its consumers.
- **Handoff:** every merged slice updates this effort with the durable PR and
  version before the next slice begins.
- **Cross-effort sequencing:** the active Venue Parity effort (#954) owns the
  shared agent-bridge Session Host, target-ownership, authority-recovery, and
  generic remote-spawner records. This effort waits for and consumes those
  shared seams; it adds restricted provider posture and target metadata, never a
  restricted-only identity or session-ownership store. Venue Parity continues
  to exclude restricted fleets from trusted full-harness projection.

## Context

`agent-containers` already supports a first-class `restricted` security profile:
container-local repository state, no shared host worktree, no host credential
relay, explicit network and tool grants, bounded resources, and machine-readable
posture. It registers a `container:` provider with agent-bridge and can launch an
ACP process through `docker exec`.

The remaining gap is composition. Provider resolution exposes a mutable
container name and spawn command, but not one stable target/workspace identity
consumers can persist. Leases are advisory rather than atomically tied to task
ownership. The Picker enumerates only machine/SSH sources. The trusted container
SSH path requires sshd and projected keys, which is intentionally wrong for a
restricted venue. Session sync can archive local Copilot state but has no narrow
host broker for an ephemeral restricted home.

This effort extends existing owners rather than creating a new orchestrator:

- venue and lease facts stay with agent-containers;
- workspace/session facts stay with agent-worktrees and agent-bridge;
- durable goals stay with agent-dispatch;
- session artifacts stay with agent-logger.

## Request

Support restricted container venues as named worktree and dispatch targets while
preserving deny-by-construction. A restricted venue needs a container-local
workspace, SSH-compatible provider transport, named trust-profiled discovery,
durable dispatch ownership, and brokered session recovery. It must not gain an
in-container sshd, published port, second network, host gateway, host worktree
mount, credential relay, merge authority, or deployment authority.

## Plan

### Phase 1 — Stable provider target contract
- [ ] Extend agent-containers provider list/resolve output with a stable,
      provider-owned target identity and explicit venue metadata: provider,
      venue kind, target id, fleet, workspace, security profile, readiness, and
      effective capability envelope.
- [ ] Make the restricted ACP execution operation fail closed against the live
      container posture. It must never project credentials, relay variables,
      SSH keys/config, host paths, extra networks, or gateway reach.
- [ ] Keep the payload additive and transport-neutral so older bridge consumers
      continue to use the existing spawn command.
- [ ] Add focused resolver, CLI, manifest, and Docker-argv tests; document the
      contract and bump agent-containers.

### Phase 2 — Provider target in agent-bridge
- [ ] Preserve provider/target/workspace identity in `SpawnTarget`, session
      records, liveness reads, and provider refreshes.
- [ ] Deduplicate by stable provider target rather than mutable display/container
      name while retaining the `container:` address.
- [ ] Treat venue metadata as description, never as permission to widen launch
      authority.
- [ ] Add provider reconstruction, persistence, replacement, and compatibility
      tests; bump agent-bridge.

### Phase 3 — SSH-compatible restricted provider exec
- [ ] Define an agent-ssh provider-exec transport that maps OpenSSH stdio to the
      venue provider boundary without preparing authorized keys or starting
      sshd.
- [ ] Keep the container on its existing network set with no published port,
      host gateway, or relay reverse-forward.
- [ ] Emit a named profile from stable provider-target metadata and fail loudly
      when the target is absent, unready, or no longer restricted.
- [ ] Add synthetic transport-provider and forbidden-projection tests; bump
      agent-containers and agent-ssh as required.

### Phase 4 — Provider-backed worktree sources and Picker
- [ ] Generalize Picker sources from machine-SSH-only to an explicit source kind
      (`machine-ssh` or `provider-exec`) with one canonical source identity.
- [ ] Route list/session/status and exact-worktree lifecycle verbs to the owning
      provider; never create a local synthetic machine/worktree record.
- [ ] Render venue name, readiness, and trust posture, and disable unsupported
      actions rather than falling back to local behavior.
- [ ] Preserve all existing physical-machine source behavior and engine/Picker
      compatibility; bump the contract only if additive compatibility cannot be
      maintained.
- [ ] Add source, cache-key, rendering, version-skew, lineage, and ownership
      tests; bump agent-worktrees.

### Phase 5 — Brokered session checkpoint and restore
- [ ] Define an allowlisted checkpoint manifest for the minimum Copilot session
      artifacts and workspace/origin metadata needed to resume.
- [ ] Export/import through a host-owned provider operation. The restricted
      worker receives no host path, archive path, storage credential, or ambient
      network access.
- [ ] Fence checkpoints by provider target, workspace owner, session id, and
      generation; reject path traversal, cross-target restore, partial publish,
      and stale overwrite.
- [ ] Checkpoint before release/replacement and restore before resumed
      embodiment.
- [ ] Add interrupted-transfer, replacement, safe-extraction, and repo-scope
      tests; bump agent-logger plus the consuming provider/dispatch plugins.

### Phase 6 — Atomic dispatch ownership and recovery
- [ ] Add a venue selector/binding to tasks without changing repository-lane
      semantics.
- [ ] Atomically bind one task owner to one restricted venue lease and workspace
      identity; heartbeat and completion validate the same generation.
- [ ] Add a supervisor venue body that acquires the provider target, restores
      the latest valid checkpoint, starts the ACP session, records the concrete
      session id, and releases only after a checkpoint or proposal boundary.
- [ ] Requeue only on confirmed-gone liveness; unknown stays owned. Replacement
      retains task goal, progress log, workspace identity, session lineage, and
      attempt limits.
- [ ] Add queue, coordinator, supervisor, liveness, lease-race, checkpoint, and
      replacement tests; bump agent-dispatch and agent-containers.

### Phase 7 — Clean-room acceptance and docs
- [ ] Add a disposable restricted-venue scenario that provisions from a fresh
      install, creates one workspace, dispatches one task, connects through the
      named transport, checkpoints the session, replaces the container, and
      resumes the same task/workspace.
- [ ] Assert the forbidden surfaces remain absent before and after recovery:
      credentials, relay, host mounts, sshd, ports, second network, host gateway,
      merge, and deploy.
- [ ] Update architecture, transport, command catalogs, install contracts, and
      public operator docs.

## Validation Plan

- [ ] Provider list/resolve returns one stable target identity and effective
      restricted posture across refresh and container replacement.
- [ ] Restricted ACP/provider-exec Docker argv contains no token, relay, SSH,
      host-path, network, or gateway projection.
- [ ] The named transport reaches only the provider target and fails closed if
      live posture no longer matches the restricted contract.
- [ ] Picker and bridge show one venue/workspace owner, never a duplicate
      physical machine or local worktree.
- [ ] Restricted provider targets reuse Venue Parity's shared Session Host and
      target-ownership records; no restricted-only ownership store exists.
- [ ] Concurrent dispatchers cannot acquire the same task, lease, or workspace;
      liveness recovery never reclaims an unknown or merely slow owner.
- [ ] Checkpoint/restore transfers only allowlisted session members and rejects
      traversal, cross-target, stale-generation, and partial-state cases.
- [ ] Container replacement resumes the same task, progress, workspace, and
      session lineage.
- [ ] Existing machine SSH sources, trusted container SSH/relay behavior,
      CodeSpace venues, repository lanes, and local session sync remain
      unchanged.
- [ ] Relevant plugin suites, fast guards, install-contract validation, and the
      restricted clean-room scenario pass for every slice.

## Proposal

The first PR should be agent-containers-only: stable provider target metadata,
an explicit restricted execution contract, and fail-closed tests proving no
credential/relay/SSH projection. Consumers should not infer target identity from
the mutable Docker name or duplicate fleet configuration.

The SSH-compatible path is a provider transport over stdio, not an in-container
SSH service. Trusted container SSH remains a separate posture with its existing
key and relay projection; restricted venues never enter that path.

## Journal

### 2026-08-26 - Kickoff
- Claimed #1188 after confirming no open duplicate.
- Reconciled the request to the agent-containers, agent-worktrees, agent-ssh,
  agent-dispatch, and venue-parity visions. The delta is vision-closing; no
  standing intent revision is required.
- Traced current provider, Picker, dispatch, liveness, and session archive
  contracts. Settled on serial owner-first delivery: provider contract, bridge
  identity, restricted transport, Picker ownership, session checkpoint, dispatch
  binding/recovery, clean-room acceptance.
- Plan review reconciled ownership with Venue Parity #954, moved checkpointing
  ahead of dispatch recovery, and anchored the agent-logger implementation slice
  to the agent-fabric `survivable-work` / `recover-not-lose` intent rather than
  extending the chronicler-specific agent-logger vision.
