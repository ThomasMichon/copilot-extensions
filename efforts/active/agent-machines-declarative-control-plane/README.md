# Agent Machines Declarative Control Plane

- **Slug:** `agent-machines-declarative-control-plane`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-08-29
- **Status:** Active
- **Vision:** agent-fabric `derive-dont-duplicate` and agent-ssh
  `declared-mesh-adoption`, `derived-agent-roster`, and
  `live-machine-introspection`
- **Umbrella issue:** #1418

## Guiding Intent

Treat desired machine capabilities as declarative resources that are validated,
projected, reconciled, and observed through one control plane. Machine records
remain portable declarations; runtime rosters, services, health, and setup
actions are derived views rather than separately maintained inventories.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| schema host | Owns resource and projection contracts | isolated worktree |
| package-discovery owner | Owns relationship-aware package source discovery | isolated worktree |
| module owners | Implement bounded resource reconcilers | independent slice PRs |
| parity validator | Proves equivalent behavior across supported platforms | clean-room scenario |

## Coordination

- **Topology:** schema-first host with independently reviewable resource modules.
- **Host (owns PRs):** schema host.
- **Delegates:** the package-discovery owner implements #1418; module owners
  implement only their declared resource type.
- **Handoff:** each module contributes validation, plan/apply behavior, and
  observable status to the shared control plane.
- **Public coordination token:** #1418 for relationship-aware package discovery;
  later slices use their own dedicated issues.

## Context

Machine setup commonly drifts into parallel inventories: declared hosts,
service lists, runtime-specific rosters, and one-off setup scripts. A
declarative control plane can keep the stable desired state in one schema,
derive downstream views, and reconcile only through explicit, bounded resource
modules.

## Request

Unify machine capability declarations, derived runtime views, and idempotent
reconciliation behind a portable resource model without embedding a particular
fleet, topology, or operating environment.

## Plan

### Phase 1 - Define the resource model

- [ ] Specify versioned machine, capability, dependency, scope, and desired-state
  contracts with strict validation and forward-compatible extension seams.
- [ ] Distinguish portable declarations from discovered facts and generated
  projections.
- [ ] Resolve supplemental package repositories from generic project
  relationships, preserve source provenance, deduplicate independently adopted
  repositories, and fail clearly when a declared relationship is unresolved
  (#1418).
- [ ] Reject ambiguous ownership and dependency cycles before planning changes.

### Phase 2 - Build deterministic projections

- [ ] Derive agent rosters, service requirements, connectivity expectations,
  and status views from the validated resource graph.
- [ ] Make every projection reproducible and attributable to its source
  declaration and resource module.
- [ ] Eliminate independently authored shadow inventories after parity is
  proven.

### Phase 3 - Reconcile through bounded modules

- [ ] Give each resource module plan, apply, verify, and report operations with
  explicit privilege and restart boundaries.
- [ ] Order actions from declared dependencies and preserve idempotence across
  interrupted or repeated runs.
- [ ] Fail loudly on unsupported platforms, unavailable prerequisites, and
  unsafe mutations.

### Phase 4 - Observe drift and recovery

- [ ] Report desired, observed, planned, applied, blocked, and drifted states in
  machine-readable and concise human views.
- [ ] Provide dry-run, targeted repair, rollback guidance, and partial-failure
  recovery without hiding failed resources.
- [ ] Validate representative resource modules on Windows and POSIX hosts.

## Validation Plan

- [ ] Invalid schemas, dependency cycles, duplicate ownership, and unknown
  resource types fail before mutation.
- [ ] Harness-only, harness-plus-supplemental, duplicate-adoption, unresolved
  relationship, and cross-repository conflict fixtures prove #1418.
- [ ] Repeated projection from the same declarations produces byte-stable
  derived views.
- [ ] Plan and apply touch only resources named by the selected scope and report
  every skipped or blocked dependency.
- [ ] Interrupted reconciliation resumes safely and a second successful run is
  a no-op.
- [ ] Platform-specific modules share the same resource lifecycle and expose
  honest unsupported states.

## Proposal

Establish the versioned resource graph and deterministic projection contract
first, then migrate reconcilers and retire shadow inventories only after
behavioral parity is demonstrated.

## Journal

### 2026-08-29 - Kickoff

- Established the generic machine-resource, derived-projection, reconciliation,
  and drift-observation campaign.

### 2026-08-30 - Relationship-aware package discovery

- Activated the effort now that #1418 provides the public coordination token.
- Added a focused discovery slice that composes declared supplemental
  repositories into one provenance-preserving package union while retaining
  standalone behavior.
