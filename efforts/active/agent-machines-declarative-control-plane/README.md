# Agent Machines Declarative Control Plane

- **Slug:** `agent-machines-declarative-control-plane`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-08-29
- **Status:** Active
- **Vision:** agent-fabric `derive-dont-duplicate` and agent-ssh
  `declared-mesh-adoption`, `derived-agent-roster`, and
  `live-machine-introspection`
- **Umbrella issue:** #1418 (closed; historical -- all listed sub-issues are
  also closed, and no successor umbrella has been opened yet). Reviewed Plan,
  Validation and Journal coordinate the retained #1417/#1437 slices below;
  implementation still needs an eligible open bounded-source claim.
- **Sub-issues:** #1455 · #1507 · #1529 · #1627 · #1631 · #1721 · #1961
  (all closed)

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
- **Delegates:** the package-discovery owner implemented #1418 (closed);
  module owners implement only their declared resource type.
- **Handoff:** each module contributes validation, plan/apply behavior, and
  observable status to the shared control plane.
- **Public coordination token:** #1418 (closed; historical) for the
  already-delivered relationship-aware package discovery slice; later slices
  use their own dedicated issues. For the retained #1417/#1437 objectives,
  this reviewed Active effort's Plan, Validation and Journal are the continuing
  coordination record, with the existing schema-host review custody and
  module/provider/parity roles unchanged. Their tracker consolidation creates
  no implementation assignee or resource authority. Before code work, record
  the exact bounded slice under an eligible OPEN public source claim according
  to CONTRIBUTING.md and preserve existing source/resource/rollout holds.
  Closed #1418 and consolidated source issues are history, not live claim
  tokens. This narrow amendment does not dispose of #3115's separate umbrella
  requirement or any other objective.

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
- [x] Add an attributable, source-neutral Playwright CLI provisioning command
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

### Phase 3a - Bounded filtered prelaunch retained from #1437

<!-- backlog-reconciliation:7556:transfer:1437:to:efforts/active/agent-machines-declarative-control-plane/README.md -->

These are unresolved orchestration outcomes, not a hang diagnosis or completed
repair. Normal review, actual merge and current full readback precede source
consolidation. Existing source/resource owners and the Coordination contract
above continue; #1438's WSL/SSH validation report remains distinct.

- [ ] Attribute the actual pre-module blocked operation with bounded evidence;
  retain unknown cause until proved. Direct module success or a current PATH
  resource does not prove bounded orchestration.
- [ ] Make filtered apply start the selected module OR return bounded
  actionable setup/orchestration failure under a declared budget. An already-
  spawned child timeout does not bound prelaunch; do not hide hangs by raising
  limits.
- [ ] Emit useful live discovery/package-union/validation/planning/launch/
  blocked-phase progress before waits complete, preserving JSON/quiet/native
  contracts and sanitized diagnostics.
- [ ] Bound/cancel subprocess, stdio and coordination waits; settle only owned
  descendants/readers and expose incomplete cleanup or uncertainty rather than
  conflicting retries or false success.
- [ ] Preserve only-filter scope, machine/platform/package authority,
  validation, owner/process guards and restore/dry-run semantics. Never execute
  unselected modules or bypass governance.
- [ ] Qualify actual native Windows filtered-apply/setup/module boundaries and
  supported counterparts with exact source/recipe/phase/exit/cleanup evidence.
  Missing capacity and child-only success are not orchestration proof.

### Phase 4 - Observe drift and recovery

- [ ] Report desired, observed, planned, applied, blocked, and drifted states in
  machine-readable and concise human views.
- [ ] Provide dry-run, targeted repair, rollback guidance, and partial-failure
  recovery without hiding failed resources.
- [ ] Validate representative resource modules on Windows and POSIX hosts.

### Phase 4a - Owned diagnostic reversal retained from #1417

<!-- backlog-reconciliation:7556:transfer:1417:to:efforts/active/agent-machines-declarative-control-plane/README.md -->

These are the original unresolved reset outcomes, not implemented reversal.
Normal review, actual merge and full current Plan/Validation/Coordination
readback precede administrative source consolidation. Preserve schema host,
module/provider/parity and all concrete resource/rollout owners. This section
authorizes no reset, test, OS mutation or source handoff.

- [ ] Persist per-application receipts distinguishing newly installed, updated,
  operator-pre-existing and unchanged resources, with source/resource/
  application identity and captured prior state. Desired presence, backups/
  markers and runtime provenance are not historical introduction ownership.
- [ ] Make diagnostic preview the default, with explicit apply and targeted-only
  selection. Reset command spelling is illustrative; preserve destructive
  confirmation, privilege and process guards, not normal convergence uninstall.
- [ ] Remove packages only when receipts prove this restore introduced them.
  Preserve shared/pre-existing dependencies, unknown ownership and foreign/
  concurrent changes; presence never establishes permission.
- [ ] Restore captured file/registry/config prior state where safe and report
  irreversibility. Preserve production convergence and source/package/authority
  boundaries instead of disabling handlers globally.
- [ ] Reverse resources/modules in dependency order, distinguishing updated
  from new state, with interruption/retry idempotence. Preserve durable partial,
  blocked, uncertain and failed results across later retries.
- [ ] Stage a bounded Windows PowerShell finalizer while the runtime is alive,
  then execute outside it. Remove owned agent-machines, active venv, uv and
  Python LAST, with proven introduction and explicit authority; preserve shared
  tools and never execute from resources being removed.
- [ ] Persist source-bound clean-room result/recovery logs without secrets,
  retaining primary/secondary failures and finalizer/child cleanup outcomes.
- [ ] Prove isolated seed -> restore -> owned reset -> base-state equivalence,
  protecting pre-existing/unchanged/shared/unknown resources and recording
  partial/interrupted/error paths. Future destructive-feature acceptance is not
  authorization to exercise it on a host.

### Phase 5 - Reconcile deferred backlog

- [ ] Accept declarative-control-plane candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- [ ] Revalidate accepted technical scope against the current resource-model
  and reconciliation contract; return obsolete or unsafe candidates for
  explicit disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
  extending this plan before implementation when necessary.
- [ ] Non-Windows (systemd/launchd) scheduler parity for the self-update
  resource: define and apply an equivalent unattended self-update scheduling
  mechanism on POSIX hosts, matching the Windows Scheduled Task tier model.
  Tracked in
  [#3115](https://github.com/ThomasMichon/copilot-extensions/issues/3115),
  currently a standalone issue: this effort's own umbrella
  ([#1418](https://github.com/ThomasMichon/copilot-extensions/issues/1418))
  and all listed sub-issues are closed, so there is no active umbrella to
  attach it to yet -- needs a live umbrella issue for this effort before
  Phase 3 acceptance is complete.
- [ ] Keep examples synthetic and repository-neutral.

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [ ] **#1437** agent-machines: Filtered apply hangs before module launch
  - Full retained acceptance is Phase 3a above, not just this bug-title link;
    the objective remains unresolved after administrative consolidation.
- [ ] **#1438** agent-machines: WSL restore hangs in ssh -G validation
  - A hang during WSL restore's ssh -G validation is directly this effort's control-plane scope.

## Validation Plan

- [ ] Prove Phase 4a receipt classification, preview/apply/only, unknown/
  pre-existing/shared dependencies, prior state, foreign/concurrent changes,
  dependency order and repeat/interruption outcomes.
- [ ] Prove bounded external-runtime Windows finalizer ordering/exit/process
  behavior with safe synthetic owned tools before separately authorized
  destructive adoption.
- [ ] Prove durable reset/recovery evidence and isolated base equivalence with
  partial/failure/uncertain negatives and no host effects.
- [ ] Exercise Phase 3a prelaunch separately from children, including filtered
  selection, live phase diagnostics, native cancellation/reader cleanup and
  actionable failure at declared bounds.
- [ ] Preserve source/platform/protocol identity and meaningful negative tests;
  source-only, title-link, collected/skipped or broad unrelated passes cannot
  complete either retained objective.
- [ ] After the reviewed intake merges, read back every existing Plan and
  Validation row plus both retained sections and continuing Coordination.
  Keep this effort Active until actual effect proof or explicit named tracked
  transfer resolves its complete Plan and Validation.

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
- [x] Playwright provisioning fixtures prove user-contained prefix selection,
  registry-latest convergence, prefix-local command resolution, complete
  bundled-vs-registered skill-tree verification, structured failures, healthy
  no-op, provider invocation, and payload-command synchronization on supported
  platforms (#1961).

## Proposal

Establish the versioned resource graph and deterministic projection contract
first, then migrate reconcilers and retire shadow inventories only after
behavioral parity is demonstrated.

## Journal

### 2026-10-22 - Retained reset and filtered-prelaunch acceptance

- Materialized #1417's owned diagnostic reversal in Phase 4a and #1437's bounded
  prelaunch in Phase 3a, with their full safety, native and negative validation
  obligations unchecked. This is planning intake, not implementation.
- Recorded continuing coordination in the reviewed Plan/Validation/Journal,
  preserving schema/module/provider/parity roles and eligible open source
  claims before code work. Closed #1418 is history; #3115's distinct umbrella
  gate is unchanged. Actual merge and current readback precede any source
  tracker supersession.

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

### 2026-09-04 - Reusable Playwright CLI provisioning implemented

- Added `agent-machines provision-playwright-cli` with default and explicit
  dry-run behavior, explicit apply, stable JSON, prerequisite and global-package
  detection, bounded command evidence, postcondition verification, and
  user-home skill registration.
- Kept the existing attributable payload command as the only invocation
  surface. Requirement modules compose the subcommand on `windows`, `linux`,
  and `wsl`; Playwright workspace initialization always runs from the target
  user's home and never registers repository content.
- Covered prerequisite, install, registration, postcondition, no-op,
  serialization, command-resolution, and provider-invocation behavior. The
  full agent-machines suite passes with 411 tests, and lint, documentation,
  install-contract, payload-generation, and version-consistency gates pass.

### 2026-09-04 - Playwright provisioning postconditions hardened

- Reworked #1961 around one normalized, user-contained npm prefix without
  changing npm configuration; all package, registry, install, and root queries
  now use that explicit prefix.
- Made registry `latest`, the prefix-local CLI, the package-bundled skill, and
  complete registered-skill tree equality required postconditions. Package
  updates force skill refresh, and every failed command or postcondition stays
  structured with bounded evidence.
- Kept dry-run read-only apart from the required registry lookup, kept every
  apply command in the user home, and preserved the attributable requirement
  invocation contract without project checkout mutation.

### 2026-09-04 - Playwright provisioning final review hardened

- Rejected symlink, junction, and reparse-point traversal across managed
  user-home paths, registered and bundled skill entries, npm package roots, and
  Playwright package content.
- Anchored npm trust to the resolved Node installation prefix and replaced
  Windows PID-tree cleanup with a gated, kill-on-close Job Object while keeping
  bounded process-group cleanup on POSIX.
- Added one shared 1500-second provision deadline, a 600-second per-command cap,
  and explicit cleanup margin beneath the module runner's 1800-second envelope.
- Covered unsafe path trees, escaped npm layouts, deadline hierarchy, Job Object
  ordering and descendant containment, bounded fallback, and post-registration
  validation. The final agent-machines suite passes with 447 tests (15 skipped),
  with lint, documentation, install-contract, payload-generation, version, and
  diff guards passing.
