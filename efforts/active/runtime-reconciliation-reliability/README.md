# Runtime Reconciliation Reliability

- **Slug:** `runtime-reconciliation-reliability`
- **Repo:** copilot-extensions
- **Branch(es):** serial plan, implementation, and completion PRs
- **Created:** 2026-09-01
- **Status:** Draft
- **Vision:** installation-cells `provenance-carried-end-to-end` and
  plugin-services `launch-time-version-reconciliation`
- **Sub-issues:** #1591 · #1592 · #1593

## Guiding Intent

Make the unified runtime update path reliably advance every enabled runtime
through plugin-owned, provenance-attributable installation boundaries. Genuine
legacy or unattributed mutations must remain fail-closed, while installer
failures expose the underlying actionable cause instead of a generic wrapper
error.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| implementation driver | Owns diagnosis, serial PRs, deployment, and issue closure | isolated Windows worktree |

## Coordination

- **Topology:** one implementation driver with serial, independently reviewable
  PRs.
- **Host (owns PRs):** implementation driver.
- **Delegates:** none initially; a bounded platform-parity slice may be
  delegated only after its ownership is recorded here.
- **Handoff:** every handoff names the next unresolved Plan item and preserves
  the three-issue completion gate.

## Context

The unified update flow discovers enabled runtime plugins and invokes their
declared installer-readiness modules. Agent Index and Agent Machines currently
reach a legacy mutation path that installation governance correctly rejects as
`provenance-blocked` (#1591 and #1592). Agent Containers reaches its package
installation step but reports only a generic venv-install failure, leaving the
active runtime stale and hiding the package manager's actionable diagnostic
(#1593).

This effort is a focused reliability slice beneath the marketplace-scoped
installation and runtime self-provisioning contracts. It does not weaken
provenance checks or make cross-plugin reconciliation a dependency for
standalone plugin operation.

## Request

Resolve #1591, #1592, and #1593 end to end: diagnose the selected runtime
reconciliation paths, fix them with focused regression coverage, publish the
required plugin versions, deploy through the unified update flow, verify active
runtimes match their enabled payloads, and close all three issues with evidence.

## Plan

### Phase 1 — Diagnose the registered-runtime paths

- [ ] Trace installer-readiness discovery and invocation for Agent Index,
  Agent Machines, and Agent Containers from enabled payload metadata to the
  selected plugin-owned command.
- [ ] Identify why Agent Index and Agent Machines lose installation provenance
  or select a legacy mutation path, and determine the narrow shared seam that
  preserves fail-closed behavior for genuinely unattributed calls.
- [ ] Reproduce the Agent Containers package installation with the smallest
  plugin-owned invocation and capture the suppressed package-manager failure.

### Phase 2 — Repair attributable reconciliation

- [ ] Route Agent Index reconciliation through its attributable, plugin-owned
  installer path without relaxing legacy-entrypoint governance.
- [ ] Route Agent Machines reconciliation through its attributable,
  plugin-owned installer path without relaxing legacy-entrypoint governance.
- [ ] Add focused reference and Windows/POSIX adapter coverage for the
  registered-runtime provenance contract.

### Phase 3 — Repair Agent Containers installation reporting

- [ ] Preserve and surface bounded package-manager stderr and the failing
  command context through the Agent Containers installer and unified
  reconciliation summary.
- [ ] Fix the underlying package, dependency, platform, or version-slot defect
  that prevents the enabled Agent Containers payload from installing.
- [ ] Add regression coverage for both the real failure and actionable error
  propagation without leaking credentials or unbounded output.

### Phase 4 — Publish, deploy, and close

- [ ] Bump every changed plugin and catalog version consistently and land all
  implementation through reviewed PRs.
- [ ] Run the unified update flow after merge and verify Agent Index, Agent
  Machines, and Agent Containers reconcile successfully to their enabled
  payload versions.
- [ ] Close #1591, #1592, and #1593 with merged and deployed evidence, then
  archive this effort in a completion-only PR.

## Validation Plan

- [ ] Installer-readiness planning selects attributable plugin-owned commands
  for Agent Index and Agent Machines on Windows and POSIX.
- [ ] Direct legacy or unattributed mutation attempts remain
  `provenance-blocked` and create no installation-owned state.
- [ ] Agent Containers package-install failures report the bounded underlying
  package-manager diagnostic through both direct and unified update paths.
- [ ] Targeted plugin suites and installer-readiness tests pass.
- [ ] `ruff check --select F,E9`, install-contract, version-consistency,
  version-bump, payload-generation, and installation-context synchronization
  gates pass for the changed surfaces.
- [ ] A post-merge unified update advances all three active runtimes to the
  enabled payload versions with no issue-specific reconciliation failures.

## Proposal

Diagnose the shared provenance loss before changing either exemplar, then land
the smallest common reconciliation correction with issue-specific acceptance
coverage. Diagnose Agent Containers independently at its package-manager
boundary, preserving safe stderr through every wrapper before correcting the
underlying install defect. Publish and deploy only after each focused regression
suite is green.

## Journal

### 2026-09-01 — Kickoff

- Created a focused public effort for #1591, #1592, and #1593.
- Kept provenance fail-closed semantics and actionable installer diagnostics as
  explicit acceptance requirements.
