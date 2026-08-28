# Restricted Venue Targets

- **Slug:** `restricted-venue-targets`
- **Repo:** copilot-extensions
- **Branch(es):** independent serial PRs from one managed worktree
- **Created:** 2026-08-26
- **Status:** Active
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
- [x] Extend agent-containers provider resolve output with a stable,
      provider-owned target identity and explicit venue metadata: provider,
      venue kind, target id, fleet, workspace, security profile, readiness, and
      effective capability envelope. Namespace listing retains its compatible
      name/state shape until agent-bridge can consume structured list metadata.
- [x] Make the restricted ACP execution operation fail closed against the live
      container posture. It must never project credentials, relay variables,
      SSH keys/config, host paths, extra networks, or gateway reach.
- [x] Keep the payload additive and transport-neutral so older bridge consumers
      continue to use the existing spawn command.
- [x] Add focused resolver, CLI, manifest, and Docker-argv tests; document the
      contract and bump agent-containers.

### Phase 2 — Provider target in agent-bridge
- [x] Preserve provider/target/workspace identity in `SpawnTarget`, session
      records, liveness reads, and provider refreshes.
- [x] Retain the provider's stable target identity while preserving the existing
      `container:` address. Cross-provider/host dedup remains a later consumer
      responsibility because target IDs are explicitly provider-instance scoped.
- [x] Treat venue metadata as description, never as permission to widen launch
      authority.
- [x] Add provider reconstruction, persistence, and compatibility
      tests; bump agent-bridge.

### Phase 3 — Restricted session-state rescue
- [ ] Add one provider-owned replacement choke point used by `up --recreate`,
      remove, and future lifecycle callers. Acquire an exclusive deploy hold
      before checking liveness so a concurrent borrow/session cannot race the
      check and destruction.
- [ ] Determine session/turn state from host-observable provider/bridge evidence,
      not container cooperation. Require positively confirmed idle/ended state;
      unknown/unavailable/unparseable liveness defers replacement as active.
      Leases are one input, never the sole authority. The provider probe reads
      Copilot `inuse.<pid>.lock` session markers for live-session presence and
      the append-only `events.jsonl` tail for a completed turn boundary, covering
      sessions not registered with agent-bridge.
- [ ] Add a short-TTL, heartbeated session-liveness record distinct from the
      existing 24-hour advisory effort lease. Dead session holders clear in
      minutes; an effort lease alone neither authorizes nor indefinitely blocks
      replacement.
- [ ] Put a draining container under admission control so no new session/borrow
      can land. Request drain/end at the next turn boundary and use a bounded
      drain window rather than passively waiting forever.
- [ ] Classify drift. Benign image/config drift may remain running until a safe
      boundary. Security-profile/policy drift blocks new dispatch immediately,
      requests urgent drain, and surfaces the bounded operator choice to
      tolerate or terminate if it cannot drain.
- [ ] Add a host-driven provider operation that streams only
      `~/.copilot/session-state/` from a restricted container to a caller-owned
      destination; the container receives no host path or storage credential.
- [ ] Bind each rescue to provider target/instance, session IDs, timestamps, and
      source repo/assignment metadata plus a monotonic capture generation. Stream
      into a host-owned temporary artifact, compute hashes over the received
      bytes, fsync, then publish atomically; never trust a container-supplied
      digest.
- [ ] Integrate rescue into planned destructive lifecycle: ordinary recreate/
      remove first checks provider + bridge liveness and active leases. A live
      turn/session/lease blocks replacement. After a turn-boundary drain/end,
      rescue session-state **while the container is still running** and refuse
      stop/remove on export or verification failure. An explicit force/abandon
      flag is required to accept loss.
- [ ] Before destruction, separately verify repository work preservation: a
      dirty/ahead workspace must have its intended proposal pushed or be
      explicitly abandoned. Keep this hard work-preservation gate distinct from
      session telemetry rescue.
- [ ] Add an incremental checkpoint operation suitable for periodic and
      turn-boundary callers, so an unexpected container loss sacrifices only the
      evidence tail since the latest successful checkpoint. Capture
      `events.jsonl` by host-held byte offset with whole-line framing; a trailing
      partial line is re-sent next time. Publish by session UUID plus monotonic
      sequence/offset compare-and-set so a late writer cannot rewind a longer
      host copy. Surface the configured evidence-completeness objective and
      checkpoint staleness/failure; do not imply work or in-memory recovery.
- [ ] Keep workspace, source roots, worktrees, settings, and credentials
      ephemeral. A replacement receives a fresh clone/runtime and does not
      restore the prior Copilot session.
- [ ] Treat rescued bytes as untrusted evidence. Keep the archive opaque at the
      provider boundary; downstream analysis uses safe, allowlisted readers and
      never executes extracted hooks/configuration.
- [ ] Keep the member manifest deny-by-default. Include the append-only event
      stream and minimum provenance/checkpoint index; exclude high-growth
      `files/`, `rewind-file-snapshots/`, research, and unknown members, and
      report exclusions. Bound those excluded scratch surfaces in-venue so the
      512 MB home tmpfs cannot be exhausted before rescue.
- [ ] Bound host archive retention by assignment/generation and total bytes;
      retain the newest verified capture needed for live analysis, then reclaim
      superseded captures without touching a live container.
- [ ] Report last rescue/checkpoint status and session counts in provider posture
      without exposing host paths or transcript content.
- [ ] Keep deploy/update non-destructive: it may build the new image, sync policy,
      and report a drifted running member, but never implicitly recreate it.
- [ ] Reconcile fleet members independently. Recreate confirmed-idle members,
      leave active/unknown members running and reported, and return a first-class
      partial/deferred result rather than failing or half-removing the whole
      fleet.
- [ ] Add path-boundary, hash/atomicity, interrupted-export, recreate-block,
      active-turn/session/lease guard, explicit-abandon, incremental-checkpoint,
      and fresh-replacement tests; bump agent-containers.
- [ ] Prove rescue does not weaken the restricted runtime: policy version,
      container creation argv, no binds/mounts, and the exact tmpfs set remain
      unchanged. Preserve trusted-fleet lifecycle behavior.

### Phase 4 — SSH-compatible restricted provider exec
- [ ] Define an agent-ssh provider-exec transport that maps OpenSSH stdio to the
      venue provider boundary without preparing authorized keys or starting
      sshd.
- [ ] Keep the container on its existing network set with no published port,
      host gateway, or relay reverse-forward.
- [ ] Emit a named profile from stable provider-target metadata and fail loudly
      when the target is absent, unready, or no longer restricted.
- [ ] Add synthetic transport-provider and forbidden-projection tests; bump
      agent-containers and agent-ssh as required.

### Phase 5 — Provider-backed worktree sources and Picker
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

### Phase 6 — Session corpus ingestion
- [ ] Teach agent-logger/session-sync to ingest the provider's rescued
      session-state under a stable venue/source identity without requiring the
      container to reach shared storage.
- [ ] Preserve repo, provider target/instance, model, interface, origin, and
      assignment provenance for asynchronous analysis.
- [ ] Keep repo scope fail-closed and treat incomplete/unverified rescues as
      unavailable rather than valid sessions.
- [ ] Add incremental ingest, dedup, partial-rescue, and retention tests; bump
      agent-logger plus the provider consumer contract as needed.

### Phase 7 — Atomic dispatch ownership and recovery
- [ ] Add a venue selector/binding to tasks without changing repository-lane
      semantics.
- [ ] Atomically bind one task owner to one restricted venue lease and workspace
      identity; heartbeat and completion validate the same generation.
- [ ] Add a supervisor venue body that acquires the provider target, restores
      no prior container state, starts a fresh ACP session/workspace, records the
      concrete session id, and checkpoints session evidence at meaningful
      boundaries.
- [ ] Requeue only on confirmed-gone liveness; unknown stays owned. Replacement
      retains task goal, progress log, workspace identity, session lineage, and
      attempt limits.
- [ ] Add queue, coordinator, supervisor, liveness, lease-race, checkpoint, and
      replacement tests; bump agent-dispatch and agent-containers.

### Phase 8 — Clean-room acceptance and docs
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
- [ ] Planned replacement atomically rescues and hashes every available
      session-state directory before **stop or removal**; a failed rescue blocks
      replacement unless loss is explicitly accepted.
- [ ] A deploy against an active turn/session leaves that container running and
      drifted; recreation succeeds only after drain/end, rescue verification, and
      lease release.
- [ ] A concurrent borrow cannot enter after the deploy hold; unknown liveness
      defers; a dead/stale lease alone neither authorizes nor blocks replacement.
- [ ] Benign and security-relevant drift follow distinct policies, and each fleet
      member can complete or defer independently.
- [ ] Incremental checkpoints are idempotent and bound the evidence lost after an
      unexpected container failure; monotonic byte offsets cannot rewind, partial
      JSON lines are retried, and checkpoint age/failure is observable.
- [ ] Workspace, settings, credentials, and prior Copilot session execution state
      are not restored into the replacement container.
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
- Phase 1 implementation adds a versioned provider `venue` block with stable
  target id plus replaceable instance id, and splits restricted launch onto a
  command builder that cannot accept host credential or relay projection. The
  existing live-posture reinspection remains the gate immediately before exec.
- Review made the contract explicitly fail-closed: `venue.security_profile`
  remains backward-compatible, configured/observed mismatches resolve to the
  stricter posture and are unready, readiness is distinct from authoritative
  `ensure_ready` policy verification, credential capabilities come from the
  effective fleet policy, and target IDs are scoped to one provider instance.
- Phase 1 merged in PR #1194 as agent-containers `0.1.2-dev87` and was deployed
  through the plugin's versioned provision path. Deployment exposed the generic
  stale-runtime self-provisioning defect tracked separately in #1195.
- Phase 2 preserves the complete provider-owned `venue` object across the CLI
  process boundary and SpawnTarget JSON persistence. Legacy providers retain the
  original workspace/profile shape; the bridge adds no restricted-only
  ownership store and continues to ride Venue Parity's shared Session Host
  records.
- Bridge review closed two process-boundary downgrade paths: conflicting
  workspace identities now fail, trust conflicts can only resolve toward
  `restricted`, and a successful provider response with malformed venue
  metadata is rejected rather than replaced by a potentially different legacy
  fallback target.
- CLI provider targets now also validate their executable shape: only
  `type=command` with a non-empty string argv is accepted, preventing malformed
  provider data from redirecting a restricted venue into a local or machine-SSH
  host launch.
- 2026-08-27 operator clarification: preserve evidence, not execution state.
  Restricted home/workspace remain bounded tmpfs and replacements start fresh.
  Phase 3 narrows to host-driven, atomic, hashed rescue of
  `~/.copilot/session-state` before planned destruction plus incremental
  checkpoints for unexpected loss; Phase 6 ingests those rescues for asynchronous
  analysis. No mount, worktree restore, or session rehydration is required.
