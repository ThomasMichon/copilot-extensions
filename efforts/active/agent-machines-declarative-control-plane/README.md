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
- **Sub-issues:** #1455 · #1507 · #1529 · #1627 · #1631 · #1721 · #1961

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
- [x] Define one canonical machine identity plus a deterministic accepted-alias
  set derived from portable topology fields, with raw-host fallback and
  fail-closed ambiguity detection (#1529).
- [x] Define an attributable provider invocation reference for requirement
  modules so one plugin can own restoration behavior without package authors
  embedding installed payload paths or ambiguous PATH commands (#1627).
- [ ] Distinguish portable declarations from discovered facts and generated
  projections.
- [x] Add explicit authority metadata so a more-specific requirement package
  can supersede a lower-authority declaration deterministically while
  equal-authority contradictions remain errors (#1721).
- [x] Resolve supplemental package repositories from generic project
  relationships, preserve source provenance, deduplicate independently adopted
  repositories, and fail clearly when a declared relationship is unresolved
  (#1418).
- [ ] Reject ambiguous ownership and dependency cycles before planning changes.

### Phase 2 - Build deterministic projections

- [ ] Derive agent rosters, service requirements, connectivity expectations,
  and status views from the validated resource graph.
- [x] Resolve machine topology once per command, use the topology key as the
  canonical identity, and use key/hostname/alias/display-name values only as
  accepted match identities for gates, per-machine overlays, and machine
  package directories (#1529).
- [ ] Make every projection reproducible and attributable to its source
  declaration and resource module.
- [x] Preserve both selected and superseded declaration provenance in plans,
  validation, restore results, and drift identity (#1721).
- [ ] Eliminate independently authored shadow inventories after parity is
  proven.

### Phase 3 - Reconcile through bounded modules

- [x] Make `plan`, `validate`, and `restore` default to the adopted project
  containing CWD plus only its required supplemental package repositories.
  Preserve `--repo` as exact single-repository scope and explicit
  `--all-projects` as the full machine union (#1418, #1455).
- [x] Resolve CWD through its canonical registered anchor/worktree identity.
  An adopted project contributes its active required supplements one hop only;
  entering a supplemental repository directly resolves that repository alone
  unless it is independently adopted as a project.
- [x] Require supplements to be explicitly bound and canonically registered.
  A configured but unavailable supplement blocks bare project reconciliation
  with clear `--repo <current>` remediation for intentional physical-repository
  isolation.
- [x] Update CLI help, README, architecture, and remediation text to distinguish
  bare project scope from exact `--repo` and full `--all-projects` scope.
- [x] Add a schema-versioned, conflict-validated settings disposition for exact
  map-key absence, beginning with user-level plugin activation and preserving
  installed inventory (#1507).
- [ ] Give each resource module plan, apply, verify, and report operations with
  explicit privilege and restart boundaries.
- [x] Resolve provider-backed module invocations through payload-attributable
  plugin command metadata, preserving dry-run/apply separation and optional
  sibling independence (#1627).
- [x] Let agent-ssh expose transport-host plan/status/apply behavior through its
  own command and transport metadata; agent-machines remains the optional
  declarative orchestrator rather than learning dtssh internals (#1627).
- [ ] Add an attributable, source-neutral Playwright CLI provisioning command
  that installs `@playwright/cli`, runs its supported skill-registration task,
  verifies both results, and can be consumed through a requirement-package
  invocation module (#1961).
- [ ] Order actions from declared dependencies and preserve idempotence across
  interrupted or repeated runs.
- [x] Define whether imperative modules participate in authority selection or
  remain explicitly opaque; never let module discovery order become hidden
  last-writer behavior (#1721).
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
- [x] Portable-default, operator-policy, and project-policy fixtures prove
  deterministic authority selection independent of package discovery order;
  equal-authority contradictions still fail (#1721).
- [x] Plans and restore results identify the selected declaration and retain
  source-qualified evidence for declarations it superseded (#1721).
- [x] Raw host, topology key, hostname, alias, and display-name inputs resolve
  to one canonical machine and one deterministic accepted-alias set; ambiguous
  cross-entry identities fail before package loading (#1529).
- [x] Gates, per-machine overlays, and machine-scoped directories accept any
  resolved identity while canonical plan/drift output remains stable (#1529).
- [x] Harness-only, harness-plus-supplemental, duplicate-adoption, unresolved
  relationship, and cross-repository conflict fixtures prove #1418.
- [x] Repeated projection from the same declarations produces byte-stable
  derived views.
- [ ] Plan and apply touch only resources named by the selected scope and report
  every skipped or blocked dependency.
- [x] CWD-project-plus-supplemental, explicit-single-repo, and all-projects
  fixtures prove that bootstrap includes required relationship packages without
  executing packages from unrelated projects (#1418, #1455).
- [x] Anchor and linked-worktree CWDs resolve the same adopted project scope.
- [x] Entering a supplemental repository directly does not pull its requiring
  harness back into scope, and supplemental relationships are not traversed
  transitively.
- [x] An unavailable or unregistered required supplement blocks bare reconcile
  before module execution and names exact `--repo` as the local-only escape
  hatch.
- [x] Only an explicit active relationship to a canonical registered repository
  can add a second repository's modules to bare `restore --apply`.
- [ ] Interrupted reconciliation resumes safely and a second successful run is
  a no-op.
- [ ] Platform-specific modules share the same resource lifecycle and expose
  honest unsupported states.
- [x] An agent-ssh dtssh-host provider fixture proves healthy no-op, absent
  dry-run, idempotent apply, missing authentication, unavailable provider,
  unsupported platform, and repeated no-op behavior without hardcoded payload
  paths (#1627).
- [ ] Playwright provisioning fixtures prove prerequisite failures, install and
  registration planning, apply, healthy no-op, repeated idempotence, provider
  invocation, and payload-command synchronization on supported platforms
  (#1961).

## Proposal

Establish the versioned resource graph and deterministic projection contract
first, then migrate reconcilers and retire shadow inventories only after
behavioral parity is demonstrated.

## Journal

### 2026-08-29 - Kickoff

- Established the generic machine-resource, derived-projection, reconciliation,
  and drift-observation campaign.

### 2026-08-31 - Relationship-aware package discovery

- Activated the effort now that #1418 provides the public coordination token.
- Added a focused discovery slice that composes declared supplemental
  repositories into one provenance-preserving package union while retaining
  standalone behavior.

### 2026-08-31 - Explicit reconciliation scope

- Added #1455 to make repository-local reconciliation the safe default while
  preserving the machine-wide union as an explicit `--all-projects` operation.

### 2026-08-31 - Plugin activation scope

- Added #1507 as a bounded schema and settings-reconciler slice: exact desired
  absence for selected user-level plugin activation keys, distinct from
  installed inventory, capture exclusion, and out-of-band pruning.

### 2026-08-31 - Relationship-aware default scope

- Refined the safe default from one physical repository to one adopted project:
  bare reconciliation includes only that project's required supplemental
  package repositories. Explicit `--repo` remains the physical-repository escape
  hatch, and `--all-projects` remains the full machine union.

### 2026-08-31 - Relationship-aware reconcile implemented

- Added a shared one-hop supplement resolver used by full discovery and
  project-local reconciliation, preserving canonical registration, source
  provenance, deduplication, and fail-loud binding behavior.
- Bare `plan`, `validate`, and `restore` now include an adopted project's direct
  required supplement; explicit `--repo` remains exact and `--all-projects`
  remains the full union.
- Added linked-worktree, standalone clone, non-transitive relationship,
  unavailable supplement, and explicit-scope coverage. The full agent-machines
  suite passes.

### 2026-09-01 - Exact activation-key absence

- Added schema-v3 `ensure-absent` for exact source-qualified user activation
  keys with package-union conflict detection, bootstrap protection, dry-run
  reporting, backup-before-write apply, and idempotent reconciliation (#1507).

### 2026-09-01 - Canonical identity and transport restoration

- Added #1529 for canonical machine identity with topology-derived aliases.
- Added #1627 for a payload-attributable provider invocation contract, with
  agent-ssh owning transport-host restoration and agent-machines remaining the
  optional declarative orchestrator.
- Selected a staged implementation: land identity resolution first, then add
  the generic provider invocation seam and the agent-ssh dtssh-host command
  before any private machine declaration depends on it.

### 2026-09-01 - Canonical machine identity implemented

- Added a standalone topology resolver that recognizes canonical keys,
  hostnames, aliases, and display names across established machines.yaml
  locations, with raw-host fallback and fail-closed cross-entry ambiguity.
- Threaded accepted identities through package gates, per-machine overlays,
  nested module/resource gates, machine directories, layout diagnosis, and CLI
  JSON while keeping the canonical key in plan and drift state.
- Malformed topology files degrade with explicit identity warnings rather than
  bricking restore. The full agent-machines suite passes, and a live source
  smoke resolves a generated host name to its stable topology key and aliases.

### 2026-09-01 - Attributable SSH host restoration implemented

- Added source-qualified payload-command invocations for requirement modules,
  resolved through active plugin provenance and validated payload invocation
  descriptors rather than PATH or installed-directory guesses.
- Added agent-ssh `restore-host` with dtssh status dry-runs, noninteractive
  login preflight, idempotent install, bounded post-install health retries, and
  verification of the host, banner, watchdog, persistence, and live tunnel
  connection.
- Fixed #1631 by attaching the completion color argument to the intended
  PowerShell output calls, preventing a successful runtime activation from
  exiting as a formatting failure.
- Full agent-machines and agent-ssh suites plus version, install-contract,
  payload-generation, and focused live dry-run checks pass.

### 2026-09-01 - Provider restoration published

- Merged #1637, closing #1627 and #1631 with agent-machines `0.1.0-dev80`
  and agent-ssh `0.1.0-dev66`.
- Deployed both runtimes and proved a private machine package can dry-run,
  apply, and then report a healthy no-op through the source-qualified provider
  invocation.
- The installed proof restored the transport host, banner, watchdog, startup
  persistence, and live tunnel connection; an end-to-end SSH command completed
  through the restored alias.

### 2026-09-02 - Explicit package authority slice

- Added #1721 to replace implicit overlap avoidance with deterministic,
  source-attributable authority selection.
- Kept the validator's fail-loud contract: contradictory declarations at the
  same authority remain errors, and discovery order never selects a winner.
- Scoped the design across managed settings, declarative resources, and
  imperative modules, with opaque module behavior requiring an explicit
  contract rather than accidental last-writer semantics.

### 2026-09-02 - Explicit package authority implemented

- Added schema-v4 bounded package and declaration authority with fail-closed
  v1-v3 compatibility, manage-overlay fallback, and explicit exclusion of
  plugin activation, tombstone, marketplace, and removal surfaces.
- Added deterministic same-shape settings and safe field-level resource
  selection with equal-highest conflicts, conservative union fields,
  source-qualified supersession findings, and stable plan/restore authority
  decisions. Incompatible settings shapes and managed-block marker changes
  remain explicit hard conflicts.
- Kept modules additive and opaque while reporting effective authority, split
  effective drift identity from full provenance identity, and covered shuffled
  input order, legacy schemas, safety boundaries, every supported resource
  authority field, direct-library restore validation, and loser-only drift
  stability in the agent-machines suite.

### 2026-09-03 - Reusable Playwright CLI provisioning slice

- Added #1961 for a generic-safe, payload-attributable provisioning command
  that owns `@playwright/cli` installation plus the CLI's supported skill
  registration task.
- Kept browser profiles, credentials, navigation maps, and product-specific
  test policy downstream; requirement packages consume only the reusable
  provisioning mechanism through the existing invocation contract.
