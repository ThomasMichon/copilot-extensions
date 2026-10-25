# Phase 5 - Configuration Workbench

- **Status:** Active; wave plan reviewed, remaining implementations require
  their own claims and per-wave design.
- **Parent:** [Worktree Manager control plane](README.md)
- **Coordination:** parent effort Journal and per-wave implementation issues;
  historical acceptance #357; adoption #356; prior enablement premise #543;
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

**Installation and Updates operator request (verbatim, 2026-10-24):**

> Something added an "Automatic maintenance" overlay popup thing in Worktree Manager. That's...interesting. We should add a Configuration section relating to Installation and Updates, to show the complete set of maintained services, their current versions, and provide updaters for each plugin

The bounded implementation is claimed by `ticking-compass` under #8877.
The existing launch callback is not an instruction to remove its status
semantics. Automatic-maintenance #7028 and acquisition/setup/trust/readiness
#8545/#356 retain their owners; historical #357 is closed and its unchecked
acceptance remains here.

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

### Installation and Updates - bounded vertical slice (#8877)

The following implementation/validation decomposition is
**agent-recommended**, realizing the operator request above without taking
over the other waves or automatic-maintenance policy.

- [ ] Reuse standing Configuration list/detail/contextual-action navigation;
  discover Manager and the complete maintained runtime/payload-only set through
  canonical catalogs and installation resolution, never a copied roster.
- [ ] Populate inventory off the UI thread with installed/enabled distinctions,
  payload/selected-runtime/observed-running versions and source provenance.
  Unknown, missing, unsupported and platform-policy outcomes remain actionable.
  Browse/refresh does not install, ensure a daemon, start workers, write sources
  or request credentials.
- [ ] Add genuine target-scoped updates through the existing durable driver and
  owning installers, extending their targeting seam where necessary. Preserve
  configured sources, dependency/readiness, publication, retention, credentials
  and recovery policy; never widen an unsupported request. Manager self-update
  uses its own seam; existing full-suite behavior is unchanged.
- [ ] Confirm the exact affected target; retain attributable acceptance,
  queued/running/terminal outcomes and refreshed actual inventory. Dismissal
  stops observation only, never service-owned execution. Busy/older owners
  expose supported retry, not a second queue or automatic scheduling.
- [ ] Exercise real production navigation and owner forwarding with isolated
  synthetic inventory and installer fixtures: payload-only/absent/unknown,
  exact target and unaffected components, source immutability, failure/retry,
  disconnect/dismiss survival and refreshed versions. Use bounded,
  network-disconnected repository test selection and headless Manager UI.
- [ ] Land runtime/changefiles and directly related documentation through
  current-head automated review and required CI. Journal only this bounded
  landed slice, distinguish release/rollout and preserve broader Phase 5 gates.

**Validation:** the production UI must reach the actual target-aware owner,
not a mock success or a whole-suite updater. Tests compare unaffected
payload/runtime/anchor and source bytes, exercise durable lifecycle results,
and verify observer loss cannot invent cancellation/completion. Run F/E9 lint,
install-contract and module-size guards. Fresh-install/native and installed
rollout remain explicit separately owned gates when unavailable; no production
installation, credentials or host configuration are used for source validation.

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

## Preserved Configurator acceptance from #357

This is an agent-recommended administrative mapping of existing acceptance,
not new implementation scope. This existing Active plan is the canonical home
for the complete original and inherited #357 checklist. #357 remains open until
this record has passed normal review, merged and been read back. Its later
supersession would retire only the tracker, not complete these unchecked items.

Preserve #356's active first-repository CLI/setup/trust/readiness claim and
exclusions, #542's assignment and first-run Picker acceptance, #6379's
backstage/Workers release and installed gate, #6283's remote scope, the native
Repositories adapter/maintenance owner, and every existing domain, lifecycle,
source and rollout owner. Those execution tokens remain open under their own
completion gates; collecting their acceptance here does not retire them.

- [ ] Provide the standalone non-agentic Configurator through its own entry and
  soft bare-harness handoff. Show actual Projects, Repos, Plugins
  (known/installed/enabled per project), prerequisites, machines, configuration
  and accounts. When Manager is absent, preserve trustworthy help/install
  guidance without a hard Worktrees-to-Manager dependency or loss of standalone
  plugin operation.
- [ ] Preserve #542's TTY-gated bare/front-door behavior: non-TTY, piped and ACP
  calls remain legible; fresh-empty contexts land in Setup/Doctor; engine plus
  adoption reaches Worktrees. Missing sources guide Install core, Add machine,
  Adopt repo and Enable plugins through existing owners. Retain its complete
  empty/partial/provisioned and TTY/non-TTY matrix, assignment and claims.
- [ ] Expose real drift, prerequisite, broken/stale launcher and mis-registration
  findings with explicit supported repair. Keep validity, reachability,
  authentication and observed runtime activity distinct. Browsing, rendering
  and status must not provision, start workers or repair state to obtain a read.
- [ ] Preserve #356's adopt-local, clone-remote-into-chosen-root and skip choices,
  consent-driven registration/discovery and actual knowledge linking/pairing.
  Keep its narrow CLI/setup owner. Ordinary discovery and session launch must
  not enroll automatic maintenance.
- [ ] Show per-plugin effective values and conventions from each repository and
  project versus user-only scope, with provenance, precedence, masks and
  configured/live distinctions. Existing backstage and bounded native adapter
  delivery are partial evidence, not the whole workbench.
- [ ] Preserve the reviewed #6178/#6227 refinement: repository, knowledge,
  plugin and preset source views are read-only. Direct changes use only owning
  machine-local preview/apply/reset/killswitch APIs with exact target, baseline,
  effects and consent, preserving unrelated settings and non-overridable policy.
  Do not restore an unrestricted source YAML/Git editor.
- [ ] Source maintenance retains explicit target/baseline/delta/acceptance,
  deduplication, cancellation, authorized worktree/review/merge ownership,
  conflict handling, refreshed effective proof and independent evaluation.
  No task, agent or pool starts by browse; disclose agentic effects before
  explicit submission.
- [ ] Preserve #111's explicit selected-project Configuration/Maintenance
  Backfill session titles action through the existing engine, with registry/
  title update counts and visible failures. Never scan/write titles on launch,
  paint or Configuration browsing.
- [ ] Preserve #358's revised shared harness-plugin-carried onboard-preset
  contract across Phase 7 and Wave 4, not arbitrary Git-reference ingestion or
  parallel engines. Discover/merge upgraded defaults with provenance,
  conflict/effect preview and authorized local adoption. Retain explicit
  choices, related-repo/venue defaults and repository identity/account authority;
  expose provenance in state and doctor.
- [ ] Preserve #399's one-time idempotent optional payload link/create/list/
  remove lifecycle, stable plugin/source identity and selected current
  payload/config resolution on every use. Prove upgrades avoid stale copied
  configuration or repeated agent setup; explain unavailable, disabled and
  ambiguous inputs, including Manager-absent operation. Reuse canonical
  contribution/configuration owners, not a parallel registry.
- [ ] Preserve #544's minimum Add machine identity/host/transport flow with
  explicit consent through existing machine/SSH owners and actual switcher/
  resolver consumption. Separate validity, reachability, authentication and live
  health. Direct writes remain supported machine-local state; versioned
  topology/knowledge changes require explicit source maintenance. Do not
  duplicate credential/topology editors or absorb richer #63/#67 scope.
- [ ] Complete applicable owner-consumer, source-byte immutability, local reset/
  inheritance, stale/invalid/absent provider, legacy compatibility, upgrade,
  Manager-absent, fresh-install and actual UI/readiness acceptance. Keep native
  and installed gates with existing owners; identify unavailable/inapplicable
  tiers. Plans, parser tests and screenshots are not full capability proof.

All unfinished implementation and adoption remain planned. This record changes
no source execution, configuration, queue, credentials, service, assignment or
custody.

<!-- backlog-reconciliation:7556:transfer:357:to:efforts/active/worktree-manager-control-plane/phase-5-configuration-workbench.md -->

## Coordination

Claim exact waves through the parent Journal and owning issues before editing.
Land independently useful slices serially through `dev` PRs. No shared feature
branch or standing delegates are assigned. Re-read recent sibling effort claims
before taking a domain panel; preserve others' open PRs and runtime ownership.
If no existing open issue owns the chosen wave, record its bounded implementation
issue and owner in the parent Journal before implementation. Historical #357
is acceptance provenance, not a replacement for those live execution tokens.

## Journal

- **2026-10-24** - Proposed #8877's bounded Installation and Updates slice,
  claimed by `ticking-compass`, and extended the Configurator vision with its
  standing inventory and component-scoped update intent. Planning only;
  runtime implementation begins after this amendment's reviewed merge.
  Existing wave, automatic-maintenance and setup owners retain custody.

- **2026-10-22** - Recorded the complete original/inherited #357 acceptance
  for reviewed tracker consolidation and routed live coordination through the
  existing parent Journal and per-wave issues. All waves and declared
  validation remain unresolved; #356/#542/#6379/#6283 and domain owners retain
  custody. This documentation record does not itself close #357.

- **2026-10-14** - Claimed #6711 as a bounded Wave 0 continuation; inventory
  confirms #6427's existing backstage and preserves #6379/#6283 ownership.
  Proposed a provider-row effective-state convention, one optional
  Configuration form preview step, and native Repositories preference
  set/reset as the first real consumer. Runtime, source-immutability and
  installed UI proof remain follow-on gates; no implementation or rollout.

- **2026-10-11** - Proposed wave plan following merged Configuration vision
  #6178. Planning only; no implementation, pool, or daemon activated.
