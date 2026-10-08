# agent-index standalone service

- **Slug:** `agent-index-standalone-service`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs against `dev`
- **Created:** 2026-10-08
- **Status:** Draft
- **Vision:** [agent-index](../../../../visions/plugins/agent-index/README.md):
  `lightweight-client-and-declared-host-service`,
  `independently-packaged-indexer-service`,
  `released-version-controller-contract`, `deployment-backend-parity`
- **Umbrella issue:** #5768
- **Sub-issues:** #5769 (existing host recovery)

## Guiding Intent

Give the hosted indexer its own portable service-package and lifecycle boundary,
without making a Copilot plugin or a particular container controller the service
owner. Preserve lightweight clients, native host deployment, the durable
embedding engine, corpus state and task sequencing.

The current slice is a reviewed vision and architecture proposal, plus the
bounded repair of the existing local recovery path. Starting this effort does
not authorize an unreviewed live migration or implement the proposed service
split in the planning PR.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Proposal, integration and existing recovery repair | Managed upstream worktree |
| Service implementer | Future independently packaged service slices | Separate managed worktree after proposal review |

## Coordination

- **Topology:** independent per-slice PRs.
- **Host (owns PRs):** Coordinator.
- **Delegates:** bounded read-only packaging and vision evidence; no delegated git changes.
- **Handoff:** this README and [architecture proposal](architecture.md) are the
  canonical portable plan. Deployment-specific elaboration remains downstream;
  the public plan never links to private state.

## Context

- The independently installable `agent-index-engine` is the embedding RPC
  program, not the complete hosted indexer. The HTTP/query surface, queue,
  source ingestion and detached workers still live in the plugin's base package.
- Reuse the existing durable-engine and server-package-split work rather than
  recreating their model/runtime boundary.
- `worktree-manager` supplies the standalone version-slot installer precedent.
  Existing versioned-runtime and zdd contracts supply candidate completeness,
  activation and active/passive service cutover.
- The existing native host has two recovery defects tracked in #5769:
  installer activation ignores effective configuration layers, and a detached
  successor can remain inside a Windows management Job.
- The architecture proposal is future design, not a description of a service
  distribution already shipped.

## Request

Operator request, with deployment-specific names redacted; the portable intent
and qualifications are preserved:

> Help fix the routing for the index service locally first. We want to make sure
> it self-heals. We'll want to start an effort to get agent-index's indexing
> service build out in the [operator's container platform] container.
>
> I think the correct approach will be to define agent-indexer's indexer service
> as its own service package, not a plugin, and install and update it the same way
> as worktree-manager (though with a container spec). The [deployment controller]
> device will need to check upstream periodically for `main` releases, and then
> pull down and deploy the updated service version.
>
> Ideally, we'll maintain back-compat with the lightweight non-container install
> if possible.
>
> Help build out a vision and architecture plan stream in copilot-extensions.
> Ideally what's upstream is agnostic to the factory-specifics for now, any
> mostly just defines the standalone service, with its version-slot installer
> and config support, and (maybe) a generic dockerfile.

Subsequent operator decision: do **not** enable the optional Windows Scheduled
Task tier for the current host. The recovery repair and proposal must not
silently introduce that tier.

## Plan

### Phase 1 - Existing recovery and reviewed proposal
- [x] Restore the existing host using its durable launcher; verify live routing
  and preserve the warm engine and queue.
- [x] Fix effective-config activation parity and management-Job survival (#5769);
  source regressions and isolated real-process checks pass, deployment pending.
- [ ] Revise the vision and review the [architecture proposal](architecture.md).
- [ ] Land the proposal/recovery PR, observe release promotion and deploy the repair.
- [ ] Record which recovery is event-driven and which requires a continuously
  running lifecycle authority; do not claim unattended restart from logon
  registration alone. _(agent-recommended validation clarification)_

### Phase 2 - Independent service distribution
- [ ] Define the service package, executable and portable release descriptor
  outside the plugin marketplace.
- [ ] Extract or compose the existing hosted query, indexing and worker code
  without rebuilding a parallel implementation.
- [ ] Implement the version-slot installer and durable configuration/state
  contract, including health-gated activation and rollback.
- [ ] Preserve existing CLI, configuration and non-container host compatibility.
- [ ] Define and test schema/queue rollback limits explicitly rather than assuming
  immutable executable slots make data rollback safe. _(agent-recommended)_

### Phase 3 - Deployment adapters and release reconciliation
- [ ] Supply the portable discover/install/activate/status/rollback contract for
  an external released-`main` reconciler; keep its deployment platform downstream.
- [ ] Decide whether a generic Dockerfile materially improves the reference
  deployment; if included, keep model/cache/data and controller ownership explicit.
- [ ] Integrate standalone-service versioning and release validation without
  declaring a second Copilot plugin.
- [ ] Prove native-host and optional-container parity before any downstream
  placement/routing migration.

## Validation Plan

- [x] Unit regressions for layered designation, unconfigured/client nonactivation
  and daemon detachment while retaining contained-test ownership.
- [x] A real management-process-exit check and stale-route recovery check, not
  just mocked spawn flags.
- [ ] Lightweight client install/import tests: no store, server or model stack
  acquired by ordinary read commands.
- [ ] First-touch native installation, interrupted/incomplete candidate rejection,
  configuration preservation and idempotent reconcile.
- [ ] Existing-client/new-service and new-client/existing-service compatibility,
  with explicit protocol/config schema expectations.
- [ ] Queued full/incremental sequencing, worker adoption and corpus preservation
  through update, failed activation and supported rollback.
- [ ] Optional container restart, persistent volumes and graceful termination.
- [ ] Released snapshot/artifact pinning, digest/version verification, controller
  outage, repeated poll idempotence and concurrent-updater exclusion.
- [ ] Live target deployment belongs to a separately authorized downstream slice;
  no private venue provisioning in the portable proposal.

## Proposal

[Architecture proposal](architecture.md). It separates intended package
boundaries from implementation decisions and deployment-specific policy.

## Journal

### 2026-10-08 - Kickoff
- Operator confirmed the slug. Public coordination opened in #5768; the bounded
  current-runtime recovery defects are tracked in #5769.
- Existing packaging and vision evidence was reviewed. The embedding program
  already has an independent package; the hosted indexer does not yet.
- Operator declined Windows Scheduled Task supervision. No task registration
  is part of this effort's current repair.
- Recovery validation: 20 focused regression tests passed, followed by 221
  adjacent activation/configuration/installer/deploy tests (23 platform skips).
  The real layered resolver selects the same host role as the deployed CLI.
- Isolated Windows integration: a deployed successor remained healthy after its
  real kill-on-close management Job was closed. After terminating that owned
  test instance and retaining its stale routing advertisement, a second
  deployment replaced the route and survived another Job closure. Temporary
  data and both test processes were cleaned up.
- Validation tiers: unit and simple real-process integration exercised.
  Full installer clean-room and live external-venue execution are deferred to
  the standalone implementation slices; the current recovery was reproduced
  and checked on a Windows native host. No external deployment was provisioned.
