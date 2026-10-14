# Phase 5 - Configuration Workbench

- **Status:** Active; wave plan reviewed, remaining implementations require
  their own claims and per-wave design.
- **Parent:** [Worktree Manager control plane](README.md)
- **Coordination:** #357; adoption #356; prior enablement premise #543;
  completed confirmation prior art #6044/#6096; remaining dispatch toggles owned
  by `agent-dispatch-workers-config-section`.
- **Vision:** [Configuration workbench](../../../visions/installer/configurator/README.md)

## Guiding Intent

Make Configuration the normal human-facing home for effective configuration and
supported machine-local adjustments, without moving source or domain authority
into Worktree Manager. Extend the existing contribution and domain-command
surfaces rather than introduce a second configurator or registry.

## Request

Public-safe summary of the operator-approved direction: direct Configurator
writes are machine-local, including enable/disable and killswitch overrides.
Combined repository, knowledge, plugin, and preset sources are rendered read-only.
Plugins contribute backstage widgets and optional service-backed controls.
Explicit delta requests may be routed through agent-dispatch to maintenance
workers, with clean outcome evaluation. Build effort plans to drive this vision.
No private originating identifiers or transcript are included here.

## Ownership and Prior Art

| Surface | Owner and existing foundation | Boundary |
|---|---|---|
| Configuration shell | Manager contribution contract and production Picker Configuration menu | Extend validated contributions; do not import plugin implementations |
| Effective settings and overrides | Each plugin's configuration resolver/writer | Manager does not invent precedence or write YAML generically |
| Related repos and pairing | agent-worktrees registration, related resolution, binding APIs | Local selection versus read-only versioned policy |
| Worker controls | agent-dispatch workers configuration effort and Tasks-pane Phase 9 | Reuse completed #6044/#6096 confirmation; coordinate remaining toggles with the workers effort |
| SSH mesh | agent-ssh transport/profile and machine-registry owners | No keys, credential editors, or duplicated topology store |
| Container loadouts | agent-containers effective fleet/security policy and lifecycle | Trust boundaries and leases remain provider-owned |
| Delta maintenance | agent-dispatch task, recipe, worker, and evaluation contracts | No new queue; no automatic pool provisioning |
| Presets | Installer Phase 7 harness-plugin onboard-presets scope | One shared adoption contract, not parallel defaults |

The backstage merged through #6427 already supplies immediate navigation,
full-content list/detail/forms, local worker instances and receipts. #6379
retains that shared shell/lifecycle and its release/installed acceptance.
The [Wave 0 source inventory and minimal consumer design](phase-5-effective-configuration-contract.md)
separates those existing capabilities from the missing effective-state and
post-input preview contract; do not rebuild the shell from this older checklist.

## Plan

The following decomposition and gates are **agent-recommended**. Each wave
starts with a fresh ownership/issue check, a bounded implementation issue, and
its own design where contracts are not already defined. The reviewed plan is
permission to plan/claim a wave, not blanket deployment or destructive authority.

### Wave 0 - Review contracts and reconcile owners

- [x] Inventory current production section/action/form contracts, effective
  configuration resolvers, supported local writers, and helper lifecycles.
  See the [source-cited inventory](phase-5-effective-configuration-contract.md#inventory-existing-behavior-not-missing-work).
- [x] Specify typed source provenance, editable scope, override/reset semantics,
  validation/findings, and preview/apply outcomes; version existing contracts
  compatibly rather than add a parallel manifest registry.
  See the [consumer contract](phase-5-effective-configuration-contract.md).
- [x] Reconcile #543 with the read-only source boundary; treat #6044/#6096 as
  completed confirmation prior art and coordinate remaining dispatch toggles
  with `agent-dispatch-workers-config-section`. Do not close implementation
  issues on a planning amendment.
  #6379/#6283 retain the existing Workers/remote ownership; #543 remains open.
- [x] Resolve whether helper-backed widgets need a new capability or can use
  current process/service boundaries; keep static reads daemon-independent.
  Bounded provider commands suffice for the proposed first consumer; no helper
  service is added.

**Exit:** reviewed design identifies concrete owners and consumer contracts,
old-provider degradation, supported local writes, and rejection behavior.

### Wave 1 - Effective state and local-write foundation

- [ ] Render effective values, contributing sources, precedence, masked values,
  and configured versus live state without editing any source.
- [ ] Expose only owner-declared machine-local preview/apply/reset operations.
  Confirm target and consequences; reject stale revisions and invalid changes.
- [ ] Apply enable/disable/killswitch overrides without copying source settings;
  preserve non-overridable safety policy and keep reads non-mutating.
- [ ] Preserve focus, responsiveness, and explicit errors with absent, slow,
  malformed, disabled, or incompatible contributors.

**Dependency:** Wave 0. **Exit:** one synthetic provider proves rendering and
real owner-bound local writes/reset, including source-byte immutability.

### Wave 2 - Related repo / venue / pairing vertical slice

- [ ] Compose registration, related resolution, and binding owners into one
  panel with visible local selection and read-only policy.
- [ ] Distinguish repository participation, plugin enablement, checkout
  registration, preferred venue, and pairing; do not overload one toggle.
- [ ] Preview invalid/unavailable venue, unresolved binding, and unsupported
  local override states; never substitute an inferred venue or source write.
- [ ] Verify a changed preference affects the actual next resolved operation,
  while existing leases and live workers are not implicitly moved or stopped.

**Dependency:** Wave 1. **Exit:** exercised local choices, reset, and effective
resolution; no repository creation, anchor write, or account switch.

### Wave 3 - Domain panels

- [ ] Coordinate dispatch declaration/status/local override controls with its
  existing workers/Registrars effort, including explicit target machine.
- [ ] Integrate SSH mesh preferences and bounded connection checks through
  transport owners; redact secrets and separate reachability from validity.
- [ ] Integrate container loadout/override controls with security-profile,
  forwarding, lifecycle, and lease checks; destructive actions require consent.
- [ ] Adopt any necessary plugin helper service using its install/update/
  health/cutover owner; no ad hoc persistent processes.

**Dependency:** Wave 1; each panel is a separate domain-owned slice.
**Exit:** every panel has real owner-path evidence, absent-service behavior,
and a bounded preview/apply contract. No fleet or supervisor activated by browse.

### Wave 4 - Presets and optional source maintenance

- [ ] Reconcile Phase 7 presets into one attributable discovery/preview/local
  adoption path; preserve explicit choices and read-only source declarations.
- [ ] Define delta-request target/baseline/desired-change/acceptance and dedup,
  consent, cancellation, authorization, and conflict behavior using dispatch.
- [ ] Define a maintenance recipe/pool charter without enabling it: allowed
  source scopes, worktree/review/merge flow, resource bounds, and escalation.
- [ ] Define an independent evaluator: requested source delta landed, required
  gates passed, effective configuration refreshed, masking overrides reported.
- [ ] Run a contained synthetic repository/task pilot only after explicit
  authorization; prove rejection of stale requests and unapproved changes.

**Dependency:** Wave 1; presets share Phase 7 ownership. Worker execution needs
an adopted, authorized pool. **Exit:** requests and evaluated results are
traceable; unavailable pool leaves local controls working and sources read-only.

### Wave 5 - Acceptance and documentation

- [ ] Complete the matrix below; journal evidence and every skipped tier reason.
- [ ] Update actual configuration/contribution/operator docs as capabilities
  land; supply sanitized real CLI UX captures for downstream guide authors.
- [ ] Report published versus installed/runtime-observed versions separately;
  transfer any remaining rollout or domain implementation to a named tracker.

## Validation Plan

| Gate | Required evidence |
|---|---|
| Contracts | Unit tests for malformed/legacy manifests, provenance, scope, reset, validation, stale revisions, secret redaction |
| Real consumer | Production Configuration navigation/form/confirmation invokes an actual bounded owning adapter; cancel produces no write |
| Source safety | Byte/tree comparison proves browse, refresh, local apply/reset never edit repository, knowledge, plugin, or preset sources |
| Overrides | Local change affects actual resolution; reset restores inheritance; safety floors and masked values remain explicit |
| Isolation | No shared-machine config touched in unit/fixture tests; contained temporary roots and synthetic identities only |
| Compatibility | Old provider/Manager combinations disable unsupported writes explicitly without breaking unrelated panels |
| Fresh install | Clean-room first-touch owner installation, contribution discovery, absent daemon, restart and rerun paths |
| Live UI | Authorized dedicated environment verifies real Manager and plugin runtime versions, async responsiveness, and refreshed results |
| Delta worker | Synthetic pilot proves dedup, baseline conflict, exact target, review/merge gating, evaluator independence, and no false completion from open PR |
| Documentation | Captures show actual shipped controls with sanitized values and text alternatives; no planned UI passed off as live |

Unit and real-consumer gates apply to changed code. Fresh-install and live gates
apply where the wave changes provisioning or a genuinely available runtime
surface. Record an explicit reason for inapplicable/unavailable tiers, not a
silent skip. Completion requires every applicable gate resolved or transferred
to a named tracked objective; a plan merge does not complete this phase.

## Coordination

Claim exact waves through the parent Journal and owning issues before editing.
Land independently useful slices serially through `dev` PRs. No shared feature
branch or standing delegates are assigned. Re-read recent sibling effort claims
before taking a domain panel; preserve others' open PRs and runtime ownership.

## Journal

- **2026-10-14** - Claimed #6711 as a bounded Wave 0 continuation; inventory
  confirms #6427's existing backstage and preserves #6379/#6283 ownership.
  Proposed a provider-row effective-state convention, one optional
  Configuration form preview step, and native Repositories preference
  set/reset as the first real consumer. Runtime, source-immutability and
  installed UI proof remain follow-on gates; no implementation or rollout.

- **2026-10-11** - Proposed wave plan following merged Configuration vision
  #6178. Planning only; no implementation, pool, or daemon activated.
