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
| Release-cooperation peer | Completed #6758/#6759 coordination through their writers; no source edits, merge authority, or updater | Existing #6421 consumer driver |

## Coordination

- **Topology:** Dependency-ordered PRs; separate worktrees for independently owned implementation slices.
- **Host (owns PRs):** Coordinator.
- **Delegates:** Backend implementer owns only its assigned worker lifecycle slice; shared contribution contracts stay Coordinator-owned.
- **Release cooperation:** The peer's bounded public claim deduplication and owner follow-through for #6758/#6759 completed when #6763 merged. Original writers retain code, PR and merge custody. All further recovery/update execution remains with the retained #6600 owner; Coordinator retains installed Configuration/Workers acceptance and effort completion, not updater execution.
- **Handoff:** Each slice reports cited changes and validation against the reviewed contract; Coordinator integrates and owns the end-to-end completion gate.
- The earlier local override controls remain shipped by #6306. Remote machine selection and second-machine validation remain separately owned by #6283; this effort does not take or close that work.
- **Cross-surface alignment:** The Manager and Companion clients share the
  Configuration/Workers goals: resource and template information, provenance,
  configured versus observed state, supported actions and explicit effects and
  consent. Both consume the owning capabilities rather than creating separate
  configuration or lifecycle authorities. Presentation may suit each surface;
  the meaning of the information and actions must agree. The upstream vision
  update and Companion implementation remain with their existing owners.

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

Follow-through direction, verbatim:

> Monitor, ensure deployment, then return to driving this Worktree's effort
>
> Monitor the rollout, get deployed, help unblock as needed, and keep driving this effort

Deployment diagnostic direction, verbatim:

> Focus on unblocking deployment. What's broken? What can we fix
>
> We clearly need logs, and we need to audit what its flow entails during draining

Subsequent CI-repair direction, verbatim:

> Make the necesary fixes, and figure out how to make this more robust

Subsequent ownership-focus direction, verbatim:

> Focus in work exclusively owned by this worktree

This coordinator therefore focuses on its Configuration/Workers acceptance
harness and effort lifecycle. Shared release-control and consumer repairs
remain with their existing owners; this direction grants no updater,
activation, source takeover or overlapping implementation authority.

Subsequent cross-surface direction, verbatim with only the downstream
repository name redacted:

> There a vision being updated upstream for also showing the same information in the Companion App from [downstream harness]. So we have the same goals across both surfaces

Apply that shared intent to this effort's Manager acceptance work; do not
silently add Companion implementation or claim its acceptance is complete.

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

- [x] Clear current-head review and required CI for every implementation and documentation slice.
- [ ] Audit the monitor drain path and publish bounded diagnostics for its waits, blockers, timing and outcomes. _Audit/source merged in #6807; normal promotion and installed trace receipt remain pending._
- [ ] Support the generated-main consumer repairs and the reviewed pre-merge candidate-validation slice under #6237/#6838 without duplicating retained PRs. _Source repairs, gate #6851 and bootstrap #6867 merged; candidate #6938 merged, but immutable-main CI `38865507739` failed. Fix-forward and qualified containing-release proof remain open. (agent-recommended implementation approach)_
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
- [x] Targeted tests and real consumer-boundary integration exercise the combined Manager/provider path; record Windows and Linux coverage and the reason for any skipped clean-room or live-venue lane. _(agent-recommended)_
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

Historical entries are preserved in dated journals. The Plan and Validation Plan
above remain the completion gate; journal milestones do not close the effort.

- [2026-10-16 - configuration acceptance](journal/2026/10.16-configuration-acceptance.md)
- [2026-10-15 - diagnostics and release qualification](journal/2026/10.15-diagnostics-and-release-qualification.md)
- [2026-10-14 - transport and deployment gates](journal/2026/10.14-transport-and-deployment-gates.md)
- [2026-10-13 - release and consumer repairs](journal/2026/10.13-release-and-consumer-repairs.md)
- [2026-10-12 - implementation and guide](journal/2026/10.12-implementation-and-guide.md)
