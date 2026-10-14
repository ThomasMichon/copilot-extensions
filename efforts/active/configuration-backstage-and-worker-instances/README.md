# Configuration Backstage and Worker Instances

- **Slug:** `configuration-backstage-and-worker-instances`
- **Repo:** copilot-extensions
- **Branch(es):** Planning PR, then dependency-ordered Manager and worker lifecycle PRs
- **Created:** 2026-10-12
- **Status:** Active
- **Vision:** [Configuration Workbench](../../../visions/installer/configurator/README.md): plugin-owned backstage panels, templates-and-presets, navigation-before-content, explicit-target-and-consent; [Picker](../../../visions/picker/README.md): decision-support-before-cost, consequential-vs-browsing-clarity
- **Umbrella issue:** #6379
- **Sub-issues:** Carve bounded implementation slices after proposal review

## Guiding Intent

Make Configuration a responsive, full-content backstage whose plugin sections
can be browsed and understood before taking action. Workers presents configured
instances and available templates as resources, not a single pool dropdown.
Editing, instantiating, activating, and creating agentic work expose their
distinct consequences before submission.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Own reviewed proposal, shared Manager contract, integration, publication, and completion | Current managed planning worktree |
| Backend implementer | Own bounded worker catalog and local instance lifecycle slice after contract review | Dedicated managed worktree allocated by Coordinator |

## Coordination

- **Topology:** Dependency-ordered PRs; separate worktrees for independently owned implementation slices.
- **Host (owns PRs):** Coordinator.
- **Delegates:** Backend implementer owns only its assigned worker lifecycle slice; shared contribution contracts stay Coordinator-owned.
- **Handoff:** Each slice reports cited changes and validation against the reviewed contract; Coordinator integrates and owns the end-to-end completion gate.
- The earlier local override controls remain shipped by #6306. Remote machine selection and second-machine validation remain separately owned by #6283; this effort does not take or close that work.

## Context

The current Workers path queries status and resolves pool options before
opening `CreateActionScreen`. The asynchronous command avoids blocking the
event loop, but the destination itself is withheld until those queries finish.
The existing contribution contract then presents configuration as a modal.

Worker declarations, recipe fragments, local disable overrides, and observed
supervisor activity are different objects. `workers config-section` lists
declarations and toggles overrides; it is not an instance editor. Registrar
recipes provide reusable declaration fragments, while task recipes produce
task/charter fields. Neither is a complete worker instance persistence API.
The provider's declaration validation, recipe composition, supervision, and
coordinator remain the authorities to extend, not duplicate.

Reuse the Manager's contributed list/column/action vocabulary and background
loading machinery. Plugins keep independent CLIs and persistence; the Manager
must not import their domain runtimes or read their private state.

The earlier illustrated-guide request now documents the redesigned surface,
not the superseded modal workflow. Existing Textual compositor capture and
`worktree-manager/scripts/picker-snapshot` supply deterministic renders.

## Request

Operator request, verbatim:

> Great. Ideally, we can update the copilot-exensions docs with a guide for how to set up various built-in configurations, providing render screenshots for each step. Help plan that out, while I check this out locally for self.

Subsequent operator direction, verbatim:

> Opening "Workers" in the Configuration menu doesn't seem to do anything right now. Or at least, when I hit Enter on the "Workers" menu item, nothing loads immediately. We need to fix things so that the Config surface renders immediately, and then loads its inner content. The config panels from plugins should not be dialogs, but rather full-content pivots under Config. We should consider having "Configuration" be a backstage pivot, which has a sub-menu where instead of "Machines" we have each section, either using a horizontal top-nav, a side nav, or a dropdown section-selector nav.
>
> In the Workers config, I don't understand the structure. I intially want to see a list of what is avalable and what is configured, and I either want to edit existing configurations by selecting them, or selecting inactive templates and "instantiating" them, to produce new configurations.

Additional operator direction, verbatim:

> Kind of how worktrees can be browsed upfront, with their status, stats, and descriptions enumerated in a table, with actions lists, we need that here. For flows which are going to create agentic tasks, we need that outcome to be clear

Operator-selected design choices:

- Section navigation: `horizontal` -- horizontal section tabs replacing the ordinary Machines band.
- Effort slug: `configuration-backstage-and-worker-instances`.
- New configuration home: `user-local` -- this machine, not repository source.
- Creation behavior: `save-and-activate` -- offer Save and Activate together, with explicit confirmation. Do not reduce the requested workflow to save-only.

## Plan

### Phase 1 - Review the workspace and lifecycle contract

- [x] Land this proposal and its standing Configuration Workbench intent before runtime implementation.
- [x] Specify the generic section contribution and list/action contract by extending existing Manager vocabulary. _(agent-recommended)_
- [x] Define exact provider-owned states and effects for catalog, template prerequisites, local instance identity, edit, save, and activation; preserve source provenance and read-only boundaries. _(agent-recommended)_

### Phase 2 - Immediately rendered Configuration backstage

- [x] Enter a full-content Configuration workspace immediately; horizontal section navigation occupies the band ordinarily used for Machines.
- [x] Mount a section's content and loading state before starting provider discovery; retain responsive navigation during slow or failed reads.
- [x] Render plugin sections as full-content pivots, including list/detail/edit states, rather than using a dialog as their configuration home.
- [x] Keep Profiles reachable, preserve its existing semantics, and adapt existing configuration contributions without breaking valid peers. _(agent-recommended)_
- [x] Fence late provider results against changed section/project context, preserve list selection across refreshes, and show explicit retryable errors. _(agent-recommended)_

### Phase 3 - Workers resources and local instances

- [x] Initially list configured worker instances and available templates, with descriptions, status, statistics, provenance, and contextual action lists.
- [x] Separate template availability, declaration validity, effective enable/disable state, and observed running activity; unknown activity is not "active". _(agent-recommended)_
- [x] Offer Edit for existing user-local configurations and template instantiation into a new user-local configuration.
- [x] Show source-owned configurations read-only, with supported local controls and a distinct local customization or source-change request where supported; never silently rewrite repositories or installed templates. _(agent-recommended)_
- [x] Discover offered registrar recipes through the owning provider, expose required fields and unmet prerequisites, and reuse declaration validation instead of treating every recipe fragment as immediately runnable. _(agent-recommended)_
- [x] Persist local instances with stable, collision-safe identity and source attribution; use existing discovery/reconciliation owners for effective configuration and activation. _(agent-recommended)_
- [x] Offer Save and Activate with explicit confirmation. Display target, configuration write, activation effects, and any agentic tasks or agents it may create before submission.
- [x] Preserve a separate Save-only choice, cancel-without-write behavior, truthful partial-failure results, and retry-safe mutation/activation receipts. _(agent-recommended)_

### Phase 4 - Illustrated setup guidance

- [x] Publish a task-oriented guide to the shipped built-in configuration sections, with render screenshots for each meaningful setup step.
- [x] Cover Configuration entry, Profiles, configured Workers, template instantiation, editing, effect preview, Save and Activate, and resulting status.
- [x] Reuse the existing production Textual compositor and SVG-to-PNG renderer with identifier-neutral fixtures, captions, alt text, and reproducible generation instructions. _(agent-recommended)_
- [x] Link the guide from the adopter path and configuration ownership page; keep exact schemas and lifecycle commands in provider-owned references. _(agent-recommended)_

### Phase 5 - Release and local outcome

- [ ] Clear current-head review and required CI for every implementation and documentation slice.
- [ ] Confirm normal promotion to main, run the unified local update, and verify the installed Manager/provider versions exercise the redesigned Configuration surface.
- [ ] Reconcile all Plan and Validation Plan items, archive the completed effort, release its binding, and finalize owned worktrees. _(agent-recommended)_

## Validation Plan

- [x] With a deliberately blocked provider, Config/section chrome and loading content render before the provider returns; section switching and returning to the prior operational pivot remain responsive.
- [x] Full-content sections use the horizontal section band, not Machines navigation or a plugin configuration modal; Profiles remains reachable.
- [x] Provider failure, empty catalog, unsupported section, rapid section switching, and stale late responses have explicit, correctly scoped outcomes. _(agent-recommended)_
- [x] Workers renders configured instances and available templates with accurate status/statistics/descriptions and the correct per-row actions.
- [x] An isolated template-to-instance round-trip persists a distinct local instance; editing it changes the same identity; templates and repository/plugin declarations remain byte-for-byte unchanged.
- [x] Missing recipe inputs, unavailable evaluators/fleet transport, duplicate identities, stale edit baselines, invalid fields, and ambiguous provenance fail before unauthorized effects. Sleeping fleet hosts retain FleetSpawner's existing defer-without-reservation behavior rather than becoming invalid configurations. _(agent-recommended)_
- [x] Save-only creates no tasks or agents. Save and Activate requires a reviewed effects preview, produces the provider's acknowledged outcome, and reports activation failure separately from any persisted configuration.
- [x] Cancel performs no configuration write, activation, task creation, or agent launch; repeated submission/retry cannot create duplicate instances or work. _(agent-recommended)_
- [x] Desired enablement, observed live state, and task/agent outcomes remain independently visible; Enable never claims a running pool merely because a disable override was cleared.
- [x] Reproducible renders cover the walkthrough steps, loading, populated tables, edit/instantiate, effect confirmation, success, and failure, including a compact terminal size. _(agent-recommended)_
- [ ] Targeted tests and real consumer-boundary integration exercise the combined Manager/provider path; record Windows and Linux coverage and the reason for any skipped clean-room or live-venue lane. _(agent-recommended)_
- [ ] Documentation navigation/assets pass existing guards, and the final installed local surface matches the promoted source.

## Proposal

The operator selected horizontal section navigation and user-local instance
creation with an explicit Save and Activate option. Implement a standing
Configuration backstage, then extend Workers from an override selector to a
resource catalog and validated instance lifecycle. Reuse the existing provider
boundaries; do not create a second registrar, queue, supervisor, or screenshot
renderer. The sequence and safety/compatibility details marked
agent-recommended are proposed implementation constraints, not additional
operator requests.

This is a reviewed local configuration redesign, not an expansion into remote
configuration, arbitrary source editing, or automatic maintenance dispatch.

## Journal

### 2026-10-12 - Kickoff

- Captured the illustrated-guide request and the subsequent local UX findings verbatim.
- Confirmed horizontal section navigation, the effort slug, user-local creation, and the Save and Activate option with the operator.
- Traced delayed opening to status/options discovery before mounting the current dialog.
- Inventory found existing declaration/recipe/override primitives but no complete worker instance edit/instantiate API.
- Existing remote-machine follow-up #6283 remains separately owned.
- Proposal drafted; runtime implementation has not started.

### 2026-10-12 - Proposal reviewed

- Planning PR #6382 merged after current-head approval with zero findings and required CI success.
- Synced the implementation worktree onto the reviewed dev state.
- Begin the shared section/list/action contract and provider lifecycle specification; no runtime behavior has changed yet.

### 2026-10-12 - Implementation and consumer-boundary checks

- Implementation is in managed worktrees, **not yet published**. The Manager
  adds additive `placement: config` list contributions, immediate Configuration
  entry, horizontal sections, full-content detail/editor/receipt screens, and
  inert browsing of legacy one-shot contributions.
- Workers now separates templates, source-owned declarations, and user-local
  drafts. Save-only leaves the published version untouched; explicit activation
  publishes through the existing registrar and reports pending or partial failure
  separately from persistence. No second supervisor or task-steering draft path
  is introduced.
- Recovered the backend worker's staged implementation after its runtime reported
  failure; independently corrected evaluator-reference lookup and visible
  discovery errors, then committed and integrated it as `e8de5b077`.
- Provider checks: 187 selected catalog/instance/registrar/override tests and 274
  loop-expansion consumer tests passed. These include first-touch CLI save,
  existing-supervisor reconciliation, stable retries, stale edits, missing
  evaluators, failed publication, and unchanged source bytes.
- Manager checks: the broader pivot/Profiles/region selection passed, followed
  by 51 focused checks with the actual provider interpreter. The real
  list-to-form-to-CLI round-trip proves Cancel, Save-only, Edit, explicit Save
  and Activate, and a pending receipt in isolated state. Standard-width fitting
  explicitly retains status, activity, lane statistics, provenance, and
  descriptions at 118 columns; blocked-provider tests cover 118x40 and 80x24.
- The standalone test runner installs non-editable code and uses Python `-I`;
  `--prepare` is required after source changes. The earlier apparent Escape
  regression was stale installed test code, not a reason to change keyboard
  semantics.
- Remaining: finish recipe prerequisite coverage, illustrated guide and renders,
  review/CI/promotion, unified local update and installed-surface proof, then
  reconcile the full Validation Plan and archive/finalize.

### 2026-10-12 - Guide, prerequisites, and pre-submit review

- Added the linked Configuration workbench walkthrough with 13 identifier-neutral
  PNG renders: Profiles/Apply, Workers loading/catalog/actions/details,
  instantiate/declaration/edit, effect confirmation, pending receipt, failure,
  and compact 80x24. The existing production compositor and Fira Code rasterizer
  generate all steps with one command; screenshots are fixtures, not live claims.
- Catalog details now expose missing dependencies. A fleet requires SSH
  transport before saving, but browsing/saving never probes remote hosts.
  FleetSpawner still owns liveness admission and defers sleeping hosts without
  reserving tasks. There is no separate local fleet-alias registry to invent.
- A cohesive pre-submit code review identified three correctness gaps, all fixed:
  full Configuration stdout/stderr survives successful and failed commands;
  other instances' still-published declaration names remain reserved after
  Save-only edits; and Enable clears the exact target's logical and concrete
  masks while reporting running state unknown. Ordinary action status-line
  behavior and permissive legacy toggle semantics remain unchanged.
- Windows checks: 294 provider instance/CLI/fleet/recipe/discovery tests and
  95 Manager Configuration/forms/Profiles/region tests passed. The latter
  includes all 13 reproducible guide scenarios, real provider round-trips,
  and real subprocess receipts longer than 200 characters on success and
  partial failure. Documentation navigation/assets pass the existing guard.
- Validation tiers: unit and lightweight real CLI/Manager consumer checks
  exercised; fresh isolated first-touch roots exercised without changing
  operator configuration. Linux coverage awaits required CI. A live remote
  venue is not applicable to this local-only configuration surface; remote
  configuration remains #6283's separately owned work. Installed local proof
  follows normal promotion and the unified update; it is still pending.

### 2026-10-12 - Required CI size correction

- Opened implementation PR #6427. Its first guards job rejected the expanded
  rendering module's hard line cap. Mechanically extracted machine/section
  navigation-band rendering into a focused mixin, preserving the existing
  rendering class's inherited public methods and character-grid behavior.
- Module-size and documentation guards now pass locally. Sixty-seven focused
  Config/Profiles/region/navigation checks, including the thirteen walkthrough
  captures and real provider round-trip, passed after reinstalling the changed
  Manager test package. No publication gate was bypassed.

### 2026-10-12 - Current-head review corrections

- The first GitHub review on #6427 identified exact-case stored identity,
  draft-versus-published runtime reporting, and selection during a slow
  post-submit refresh. Addressed all three rather than merging past them.
- Every instance read verifies its stored immutable name/ID/revision against
  the requested filename; Windows cannot rewrite an existing identity through
  a differently cased path. Runtime state, lane statistics and local controls
  now derive from the active snapshot, while draft validity/capacity/revision
  remain separately visible. Configuration post-submit reads swap in place.
- Ninety-six Manager and 296 provider checks passed. New regressions exercise
  a real case-insensitive Windows edit, a published pool beneath a differently
  shaped reviewer draft, and selected-row preservation while the reload is
  deliberately blocked and subsequently reordered. Current-head review and
  required CI are still release gates, followed by installed proof.

### 2026-10-12 - Explicit Configuration targeting

- A fresh GitHub review confirmed the three previous corrections and identified
  one remaining generic contribution-boundary gap: machine-scoped configuration
  pivots could inherit a hidden operational target after removal of Machines
  navigation. Parsing now requires explicit account/global scope for config
  placement; ordinary pivots keep their prior default machine scope.
- Configuration row actions no longer inherit the previously selected machine.
  Legacy section compatibility receives the local identity instead. Updated
  the contribution contract and added parser/context regressions; Workers'
  own global CLI-local contract is unchanged.
- The changed Configuration/compatibility selection produced 96 passes and one
  auxiliary real-CLI verification startup timeout on the shared Windows host.
  That verification now uses the existing product action deadline rather than
  a shorter test-only deadline; the real override round-trip and both new scope
  regressions passed on the focused rerun.
- A broader local `manifest` selector also reached unchanged pane-launch and
  self-install fixtures: one PowerShell startup timeout and three missing
  fallback provider-reference fixtures in the non-editable cached test package.
  Their implementation/test files match the publication base; this is not a
  claim that the broader suite passed. Required CI remains authoritative, with
  no failed gate waived.

### 2026-10-12 - Catalog discovery snapshot and path diagnostics

- The current-head review confirmed the explicit Configuration scope fix.
  Its full agentic lane timed out waiting for a GitHub runner; the fallback
  review identified repeated registrar scans and raw path-resolution errors.
- Dependency validation now reuses one discovery snapshot across declarations;
  catalog validation shares the already-fetched snapshot across all instances.
  An explicitly empty snapshot remains authoritative and still rejects missing
  evaluators. No extra registry or cache lifetime is introduced.
- Local instance envelope discovery reports filesystem and symlink-resolution
  failures as contextual indeterminate registrar errors instead of leaking
  unrelated exception types.
- Forty-two worker-instance/catalog guard checks passed, including seven new
  regression cases for snapshot reuse and both source/active path failures.
  Required CI, fresh review, promotion and installed local proof remain pending.

### 2026-10-12 - Publication repair visibility and integration refresh

- The next review confirmed the discovery/path corrections and identified a
  corrupt published snapshot aborting the whole browse command. Workers now
  retains that instance as an explicit repair row, with path/reason errors,
  unknown published runtime/capacity and no local toggle. Its valid draft,
  other instances and templates remain visible; draft capacity never stands
  in for an unreadable publication.
- Dependency checks for source-owned declarations also share the catalog's
  discovery snapshot. Regression coverage includes source declarations as well
  as multiple local instances, and malformed JSON, encoding, schema, incomplete
  envelopes and permission failures.
- Rebased onto current dev, preserving the peer's new effort-index row. The
  only conflict was that index; Manager merges were mechanical. Fixed a test's
  eager dictionary lookup before its deferred initial load, retaining its
  error/retry and stale-response assertions.
- Forty-seven provider checks and 73 Manager configuration/Profiles/region
  checks passed, including the real provider round-trip. A fresh provider venv
  restore is blocked by the newly merged FastAPI >=0.143.0 floor (#6362):
  the governed feed currently exposes at most 0.142.2. The provider checks used
  the already-provisioned backend test environment with approved 0.142.2 and
  current worktree source/shared libraries, not a feed bypass or manifest edit.
  This proves the changed catalog/lifecycle code, not packaging against the
  new dependency floor. Required CI and final promoted installation still
  must pass their own declared dependencies; neither gate is waived.

### 2026-10-12 - Required CI integration corrections

- Required CI's Linux Manager suite exposed ten failures after 1,918 passes.
  Corrected the real Profiles monolithic/split rendering mismatch by including
  section chrome in both paths. Updated the isolated setup-race fixture's
  local/config context and registered-pivot fixture's placement, and removed
  an obsolete Configuration-dialog assertion while retaining Maintenance's
  native OptionList contract. No failing check was waived or retried unchanged.
- A fresh balanced review confirmed publication repair handling and identified
  corrupt drafts, custom choice defaults, and behavioral tests in the guard
  lane. Catalog browsing now retains healthy drafts/templates and emits repair
  rows for malformed draft JSON, encoding, identity, revision, configuration
  shape and source metadata. Mutation readers remain strict.
- Custom `allow_other` defaults restore through the shared field restoration
  mechanics. Configuration supports one custom multichoice value per line,
  preserving multiple saved members and literal commas; ordinary create and
  steering forms retain their existing Other semantics.
- Removed the behavioral module's blanket guard marker. Only the inexpensive
  inert catalog contract is a guard; real CLI and supervisor reconciliation
  remain full-suite integration tests. That guard passed in 0.65 seconds.
- Fifty-three provider checks passed in the existing governed dependency
  environment. All 159 selected Manager checks passed, including the ten
  Linux CI failure paths, Config round-trips, Profiles parity, setup reload
  races and shared create/steering behavior. The FastAPI floor restore limitation
  remains recorded above; fresh-head CI/review and final installed proof remain
  release gates.
