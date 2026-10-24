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
| Source-review implementer | Own #8678 source-only read-only Picker inner loop; no installed rollout or service ownership | Dedicated managed source worktree |
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

### Direct #7179 coordination - repository activation truth

Copilot-assisted conditional tracking intake. Continue this finite native
Repositories inspection lane under this SAME Active #6379 ROOT and its
Coordinator/provider boundaries. It does not take #8678's read-only source-review
implementation, #6283 remote UI, #7500 shared writer, #4054 live reload, #5686
engine environment, #5708 executing-user or #7940 supplier-contract work.
Preserve every incumbent participant, checked row, installed Configuration/
Workers acceptance, resident-map/source-review gate, historical release and
actual recovery/update custody. This intake grants no implementation, contact,
configuration write, activation, updater or service action.

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

Subsequent installed-acceptance direction, verbatim:

> Check where we are in the effort, and keep driving from there

After the installed catalog exceeded the Manager's existing deadline:

> registered-repository info should be flattened by the agent-worktrees daemon: we'll want to call something which can quickly give us the whole map

The [resident repository map amendment](resident-repository-map.md) captures
this direction. It retains the existing service and registry owners; it is not
a request for a second daemon or parallel per-repository probes.

Subsequent source-review direction, verbatim:

> We should make sure there is a way for you to load and run a decoupled instance of worktree-manager that can draw from the real data sources but not fight with them. This would allow you to have a proper "inner loop" for eview and validation that doesn't require promotion between rounds

The operator selected **read-only**, rather than edits against an isolated
overlay. The [source-review amendment](source-review-inner-loop.md) proposes
the bounded implementation under #8678. This closes the existing Picker
vision's programmatic-parity and auditable-testable-rendering intent; it does
not replace installed acceptance or authorize deployment.

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
- [x] Audit the monitor drain path and publish bounded diagnostics for its waits, blockers, timing and outcomes. _Audit/source merged in #6807; read-only installed trace inspection confirmed phase timings, category counts and a clean unforced drain. Configuration/Workers acceptance remains separate._
- [ ] Support the generated-main consumer repairs and the reviewed pre-merge candidate-validation slice under #6237/#6838 without duplicating retained PRs. _Source repairs, gate #6851 and bootstrap #6867 merged; candidate #6938 merged, but immutable-main CI `38865507739` failed. Fix-forward and qualified containing-release proof remain open. (agent-recommended implementation approach)_
- [ ] Confirm normal promotion to main, run the unified local update, and verify the installed Manager/provider versions exercise the redesigned Configuration surface.
- [ ] Review and deliver the [resident repository map amendment](resident-repository-map.md), then repeat the installed Workers acceptance without increasing its existing deadline.
- [ ] Reconcile all Plan and Validation Plan items, archive the completed effort, release its binding, and finalize owned worktrees. _(agent-recommended)_

### Source-review slice - Read-only inner loop

- [ ] Review and deliver the [source-review inner loop](source-review-inner-loop.md): run the production Picker from a source candidate against real owner reads, without live mutations or promotion between review rounds.

### Direct retained repository-contribution authority from #7179

<!-- backlog-reconciliation:7556:transfer:7179:to:efforts/active/configuration-backstage-and-worker-instances/README.md -->

Copilot-assisted proposal; every row remains planned. The inspected repository
catalog accepts an empty contribution list when effective plugin activation is
indeterminate. Generic sanitization cannot repair a helper's successful empty
return. Resolve this finite caller/helper authority boundary without rewriting
the workbench, replacing the activation owner or claiming all Configuration is
empty/broken.

#### Local Plan

- [ ] P7179-1. At the actual repos_configuration_catalog.inspect_project ->
  related.installed_plugin_related_anchors boundary, establish/reuse the effective
  repository-activation authority BEFORE accepting a production contribution
  list. Treat indeterminate activation or failed authority resolution as an
  explicit sanitized inspection finding, not known absent/not-declared routes.
- [ ] P7179-2. Preserve the intentional explicit filesystem override: root input
  and AGENT_WORKTREES_INSTALLED_PLUGINS_DIR retain their supported contained/
  diagnostic scan behavior, including required manifests, flat/nested layouts,
  deduplication and ordering. Do not ignore that override or impose an unrelated
  production activation prerequisite on it.
- [ ] P7179-3. Preserve valid known-empty versus unknown activation and known-active
  contribution behavior. Unrelated readable preferences, base/knowledge sources
  or other healthy rows must not hide the unknown plugin-contribution finding
  or turn it into a successful empty catalog. Retain source precedence, project
  context restoration and existing read-only/refusal boundaries.
- [ ] P7179-4. Keep error/finding output bounded, sanitized and meaningful through
  the actual native repository inspection consumer. An exception handler cannot
  catch a success-shaped empty list; carry the authority outcome at the owning
  seam instead of suppressing another error or adding a competing registry.

#### Local Validation

- [ ] V7179-1. Reproduce the original malformed repository activation settings
  case through the real inspection/helper boundary with NO managed config.d
  pointers, and assert a sanitized unknown/unavailable finding rather than
  absent/not-declared plugin routes. Do not mock after the list was accepted.
- [ ] V7179-2. Include unrelated readable preferences in that same no-pointer
  fixture and prove they cannot mask activation indeterminacy. Retain the
  finding even if other independent sections have useful results.
- [ ] V7179-3. Cover valid known-empty and known-active activation as distinct
  controls, preserving intended contributions, source precedence and honest
  absence. Failed authority resolution must remain distinguishable.
- [ ] V7179-4. Qualify explicit root/environment filesystem overrides through
  the supported scan/consumer path, preserving valid manifests/layouts and
  existing source/refusal behavior rather than forcing both discovery modes.
- [ ] V7179-5. Retain project-context restoration and privacy-safe diagnostics;
  live operator settings/activation are never test fixtures. Use the existing
  bounded source/consumer portfolio and the original appropriate finite tier,
  not a new installed UI, every-host or full-parent native campaign.

#### Continuing local Coordination and receiving gate

- [ ] C7179-1. Preserve all #6379 original/current Configuration/Workers behavior,
  existing source/native/installed/release holds and complete historical Journal.
  This finite repository inspection family can be qualified independently of
  unrelated remaining parent implementation; intake is not parent completion.
- [ ] C7179-2. Before #7179 administrative supersession, obtain a NEW ordinary
  reviewed ACTUAL merge delivering this complete direct local Plan/Validation/
  Coordination, the ACTUAL level-two global Validation entry and dated Journal
  record in this SAME existing Active ROOT. Read back the FULL CURRENT ROOT,
  every incumbent section/checked row and all linked historical Journal entries
  with current eligible #6379 custody. Old creation, source milestones, a child
  link, proposed payload or this artifact is not a receiving receipt. If fit,
  baseline, authority or any clause/hold cannot be preserved, KEEP #7179.

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
- [ ] A single bounded read returns the daemon-produced registered-repository map; Workers does not issue a lookup per repository, and stale/unavailable inventory is not accepted as current empty inventory.
- [ ] Source-review validation proves real owner data reaches the candidate UI, actions cannot mutate live state, candidate identity is visible, and installed selectors/service ownership remain unchanged; fixture captures alone do not satisfy this check.

- [ ] G7179-1. Resolve EVERY direct P7179/V7179/C7179 repository-activation
  contribution-authority clause at its original finite scope: indeterminate
  production activation becomes a sanitized finding before list acceptance;
  explicit filesystem overrides and valid known-empty/active controls remain;
  malformed NO-managed-pointer and unrelated-preference masking regressions
  reach the actual consumer. Preserve all incumbent #6379 participants,
  checked rows, Plan/Validation/Journal and independent source/native/installed/
  release/custody gates. Administrative intake is not implementation or parent
  completion; require the new reviewed actual merge and complete current ROOT/
  linked-Journal readback before #7179 supersession.

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

### 2026-10-24 - Conditional repository activation-truth intake (#7179)

- Proposed a separate finite repository-inspection acceptance lane after reading
  the complete eligible #7179/#6379 bodies/discussions, SAME Active ROOT Plan,
  actual global Validation, Participants/Coordination and all seven linked
  historical journals. Source inspection is pinned; no product was executed.
- Preserve indeterminate activation as an explicit sanitized finding, explicit
  filesystem overrides, known-empty/active controls and the exact malformed
  no-managed-pointer regression with unrelated preferences unable to mask it.
- All incumbent checked rows, source milestones, historical qualifications and
  later ownership transfers remain unchanged. #6379 retains its own installed
  Configuration/Workers/resident-map/source-review/closeout gates; this entry
  does not release an updater, backend, provider or #8678 claim.
- This is proposed planned intake, NOT accepted receiving delivery, implementation
  or closure. A NEW normal reviewed ACTUAL merge and complete current ROOT plus
  linked-Journal/custody readback must deliver every local/global row before
  #7179 may be administratively superseded; otherwise retain it.


Historical entries are preserved in dated journals. The Plan and Validation Plan
above remain the completion gate; journal milestones do not close the effort.

- [2026-10-23 - installed acceptance and resident repository map](journal/2026/10.23-installed-acceptance-and-repository-map.md)
- [2026-10-19 - reviewed documentation corrections](journal/2026/10.19-reviewed-documentation-corrections.md)
- [2026-10-16 - configuration acceptance](journal/2026/10.16-configuration-acceptance.md)
- [2026-10-15 - diagnostics and release qualification](journal/2026/10.15-diagnostics-and-release-qualification.md)
- [2026-10-14 - transport and deployment gates](journal/2026/10.14-transport-and-deployment-gates.md)
- [2026-10-13 - release and consumer repairs](journal/2026/10.13-release-and-consumer-repairs.md)
- [2026-10-12 - implementation and guide](journal/2026/10.12-implementation-and-guide.md)
